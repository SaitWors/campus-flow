"""Optional private Telegram delivery. Never persist or log bearer link tokens."""
import logging
import os
import re
import secrets
import threading
from datetime import timedelta
from urllib.parse import urlsplit

import httpx
from sqlalchemy import func, delete, inspect, select, text, update

from services.common.core import digest, fail, now
from services.notifications.models import (
    Cursor, Delivery, Notification, Preference, Subscription,
    TelegramBinding, TelegramDelivery, TelegramLink, TelegramState,
)
from services.notifications.push import DEFAULTS, quiet


def configuration():
    token = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
    username = os.getenv('TELEGRAM_BOT_USERNAME', '').strip().lstrip('@')
    enabled = bool(re.fullmatch(r'[0-9]{5,20}:[A-Za-z0-9_-]{20,200}', token) and
                   re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{4,31}', username))
    return {'enabled': enabled, 'bot_username': username if enabled else '',
            'bot_id': token.split(':')[0] if enabled else '', 'token': token if enabled else ''}


def required():
    config = configuration()
    if not config['enabled']:
        fail('telegram_not_configured', 503)
    return config


def migrate_notifications(conn):
    for table in (TelegramLink, TelegramBinding, TelegramDelivery, TelegramState):
        table.__table__.create(conn, checkfirst=True)
    if 'expires_at' not in {c['name'] for c in inspect(conn).get_columns('subscriptions')}:
        conn.execute(text('ALTER TABLE subscriptions ADD COLUMN expires_at TIMESTAMP NULL'))
    # Remove only the retired subsystem; preserve all private threads and inbox data.
    old_ids = select(Notification.id).where(Notification.category == 'queue')
    conn.execute(delete(Delivery).where(Delivery.notification_id.in_(old_ids)))
    conn.execute(delete(TelegramDelivery).where(TelegramDelivery.notification_id.in_(old_ids)))
    conn.execute(delete(Notification).where(Notification.category == 'queue'))
    conn.execute(delete(Cursor).where(Cursor.source == 'queue'))
    for row in conn.execute(select(Preference.user_id, Preference.data)):
        cleaned = {k: v for k, v in row.data.items() if k in DEFAULTS}
        conn.execute(update(Preference).where(Preference.user_id == row.user_id).values(data=cleaned))


class _NoCredentialLogs(logging.Filter):
    def filter(self, record):
        # httpx INFO logs otherwise contain the token-bearing request URL.
        return 'api.telegram.org/bot' not in record.getMessage()


class Transport:
    def __init__(self, client=None):
        self.client = client
        self.lock = threading.Lock()
        for name in ('httpx', 'httpcore.http11', 'httpcore.http2', 'httpcore.connection'):
            target = logging.getLogger(name)
            if not any(isinstance(f, _NoCredentialLogs) for f in target.filters):
                target.addFilter(_NoCredentialLogs())

    def call(self, method, data):
        config = configuration()
        if not config['enabled'] or method not in ('getUpdates', 'sendMessage'):
            return {'status': 503, 'result': None, 'retry_after': 0}
        try:
            with self.lock:
                if self.client is None:
                    self.client = httpx.Client(timeout=httpx.Timeout(15, connect=3), trust_env=False,
                                               follow_redirects=False)
            response = self.client.post('https://api.telegram.org/bot' + config['token'] + '/' + method,
                                        json=data)
            body = response.json()
            result = body.get('result')
            valid_result = isinstance(result, list if method == 'getUpdates' else dict)
            success = response.is_success and body.get('ok') is True and valid_result
            status = 200 if success else body.get('error_code', response.status_code if not response.is_success else 503)
            if not isinstance(status, int) or status < 100 or status > 599:
                status = 503
            if not success and 200 <= status < 300:
                status = 503
            wait = body.get('parameters', {}).get('retry_after', 0)
            wait = wait if isinstance(wait, int) and 0 < wait <= 604800 else 0
            return {'status': status, 'result': body.get('result') if status == 200 else None,
                    'retry_after': wait}
        except (httpx.HTTPError, ValueError, TypeError, AttributeError):
            # Exception text and Telegram descriptions can contain credentials.
            return {'status': 503, 'result': None, 'retry_after': 0}

    def close(self):
        if self.client is not None:
            self.client.close()
            self.client = None


def issue_link(db, user_id):
    config = required()
    count = db.scalar(select(func.count()).select_from(TelegramLink).where(
        TelegramLink.user_id == user_id, TelegramLink.created_at > now() - timedelta(minutes=10)))
    if count >= 5:
        fail('too_many_attempts', 429)
    for _ in range(5):
        token = secrets.token_urlsafe(32)
        hashed = digest(token)
        if db.get(TelegramLink, hashed) is None:
            break
    else:
        fail('telegram_link_unavailable', 503)
    # A newly requested link supersedes all earlier unused links for this account.
    db.execute(update(TelegramLink).where(TelegramLink.user_id == user_id, TelegramLink.used_at == None)
               .values(used_at=now()))
    expiry = now() + timedelta(seconds=600)
    db.add(TelegramLink(token_hash=hashed, user_id=user_id, bot_id=config['bot_id'], expires_at=expiry))
    return {'url': 'https://t.me/' + config['bot_username'] + '?start=' + token,
            'expires_at': expiry.isoformat() + 'Z'}


def enqueue(db, item, pref):
    config = configuration()
    binding = db.get(TelegramBinding, item.user_id)
    if (config['enabled'] and binding and binding.active and binding.bot_id == config['bot_id'] and
            pref.get('telegram_enabled') and (pref.get(item.category) or item.data.get('manual_test') == 'telegram')):
        db.add(TelegramDelivery(notification_id=item.id, user_id=item.user_id))


def set_enabled(db, user_id, value):
    pref = db.get(Preference, user_id)
    if not pref:
        pref = Preference(user_id=user_id, data={}, revision=0)
        db.add(pref)
    pref.data = {k: v for k, v in pref.data.items() if k in DEFAULTS} | {'telegram_enabled': value}
    pref.revision += 1


def disconnect(db, user_id):
    db.execute(delete(TelegramDelivery).where(TelegramDelivery.user_id == user_id))
    db.execute(delete(TelegramBinding).where(TelegramBinding.user_id == user_id))
    db.execute(update(TelegramLink).where(TelegramLink.user_id == user_id, TelegramLink.used_at == None)
               .values(used_at=now()))
    set_enabled(db, user_id, False)


def command(update_data):
    message = update_data.get('message', {})
    chat, actor = message.get('chat', {}), message.get('from', {})
    cid, aid = chat.get('id'), actor.get('id')
    if (chat.get('type') != 'private' or not isinstance(cid, int) or isinstance(cid, bool) or cid <= 0 or
            cid != aid or actor.get('is_bot', True) or
            any(k in message for k in ('forward_origin', 'forward_from', 'via_bot', 'sender_chat'))):
        return None
    value = message.get('text', '')
    match = re.fullmatch(r'/start(?:@([A-Za-z0-9_]+))? ([A-Za-z0-9_-]{32,64})', value)
    config = configuration()
    if not match or (match[1] and match[1].lower() != config['bot_username'].lower()):
        return None
    label = ('@' + actor['username']) if actor.get('username') else actor.get('first_name', 'Telegram')
    label = ''.join(c for c in str(label) if ord(c) >= 32)[:80] or 'Telegram'
    return digest(match[2]), cid, aid, label


def poll(DB, guard, auth, transport):
    config = configuration()
    if not config['enabled']:
        return False
    with DB.begin() as db:
        guard(db)
        state = db.get(TelegramState, 1)
        if not state:
            state = TelegramState(id=1, bot_id=config['bot_id'])
            db.add(state); db.flush()
        elif state.bot_id != config['bot_id']:
            state.bot_id, state.offset, state.attempts = config['bot_id'], 0, 0
            state.due_at, state.lease_until, state.send_after = now(), now(), now()
        if state.due_at > now() or state.lease_until > now():
            return False
        state.lease_until = now() + timedelta(seconds=30)
        offset = state.offset
    result = transport.call('getUpdates', {'offset': offset, 'limit': 100, 'timeout': 10,
                                          'allowed_updates': ['message']})
    if result['status'] != 200 or not isinstance(result['result'], list):
        with DB.begin() as db:
            guard(db)
            state = db.get(TelegramState, 1)
            state.attempts += 1
            state.last_error = ('webhook_conflict' if result['status'] == 409 else
                                'bot_unavailable' if result['status'] in (401, 404) else
                                'rate_limited' if result['status'] == 429 else 'delivery_delayed')
            delay = result['retry_after'] or min(3600, 3 * 2 ** min(state.attempts, 10))
            state.due_at, state.lease_until = now() + timedelta(seconds=delay), now()
        return True
    for item in sorted(result['result'], key=lambda e: e.get('update_id', -1)):
        seq = item.get('update_id')
        if not isinstance(seq, int) or seq < offset:
            continue
        parsed = command(item)
        member = None
        if parsed:
            user_id = None
            with DB() as db:
                link = db.get(TelegramLink, parsed[0])
                if link and not link.used_at and link.expires_at > now() and link.bot_id == config['bot_id']:
                    user_id = link.user_id
            if user_id:
                member = auth('/internal/notification-check', method='POST', json={'user_id': user_id})
        # Binding + consuming the token + cursor advance commit atomically. A crash
        # before commit safely retries; after commit a duplicate cannot rebind.
        with DB.begin() as db:
            guard(db)
            state = db.get(TelegramState, 1)
            if seq < state.offset:
                continue
            if parsed and member and member.get('active'):
                hashed, cid, aid, label = parsed
                link = db.get(TelegramLink, hashed)
                if link and not link.used_at and link.expires_at > now() and link.bot_id == config['bot_id']:
                    own = db.get(TelegramBinding, link.user_id)
                    chat_owner = db.scalar(select(TelegramBinding).where(TelegramBinding.chat_id == cid))
                    actor_owner = db.scalar(select(TelegramBinding).where(TelegramBinding.telegram_user_id == aid))
                    # Consume a rejected valid link as well; it must never become
                    # reusable after the current owner disconnects later.
                    link.used_at = now()
                    collision = any(x and x.user_id != link.user_id for x in (chat_owner, actor_owner))
                    replacement = own and (own.chat_id != cid or own.telegram_user_id != aid or own.bot_id != config['bot_id'])
                    if not collision and not replacement:
                        if not own:
                            own = TelegramBinding(user_id=link.user_id, bot_id=config['bot_id'], chat_id=cid,
                                                  telegram_user_id=aid, chat_label=label)
                            db.add(own)
                        own.active, own.last_error, own.chat_label, own.updated_at = True, '', label, now()
                        set_enabled(db, link.user_id, True)
            state.offset = seq + 1
    with DB.begin() as db:
        guard(db)
        state = db.get(TelegramState, 1)
        state.attempts, state.due_at, state.lease_until = 0, now(), now()
        state.last_error = ''
    return True


def message_text(data, pref):
    english = pref['language'] == 'en'
    details = pref['show_details'] and data['category'] != 'questions'
    title = ((data.get('title_en') or data['title']) if english else data['title']) if details else 'Campus Flow'
    body = ((data.get('body_en') or data['body']) if english else data['body']) if details else (
        'Open Campus Flow to read the update.' if english else 'Откройте Campus Flow, чтобы прочитать обновление.')
    origin = os.getenv('APP_ORIGIN', '').split(',')[0].strip().rstrip('/')
    url = urlsplit(origin)
    safe_origin = url.scheme in ('https', 'http') and url.hostname and not url.username and not url.password
    route = data.get('route', '#notifications')
    route = route if route in ('#schedule', '#assignments', '#notifications', '#questions', '#today') else '#notifications'
    link = origin + '/' + route if safe_origin else ''
    return (str(title)[:200] + '\n' + str(body)[:3500] + ('\n\n' + link if link else ''))[:4096]


def deliver(DB, guard, auth, preferences, visible, serialize, delivery_eligible, transport):
    config = configuration()
    if not config['enabled']:
        return False
    with DB.begin() as db:
        state = db.get(TelegramState, 1)
        if state and state.send_after > now():
            return False
        delivery = db.scalar(select(TelegramDelivery).where(TelegramDelivery.state == 'pending',
                             TelegramDelivery.due_at <= now()).order_by(TelegramDelivery.id)
                             .with_for_update(skip_locked=True).limit(1))
        if not delivery:
            return False
        delivery.due_at = now() + timedelta(minutes=2)
        item = db.get(Notification, delivery.notification_id)
        binding = db.get(TelegramBinding, delivery.user_id)
        pref = preferences(db, delivery.user_id)
        manual = bool(item and item.data.get('manual_test') == 'telegram')
        if (not item or not binding or not binding.active or binding.bot_id != config['bot_id'] or
                item.expires_at <= now() or item.read_at or not pref.get('telegram_enabled') or (not pref.get(item.category) and not manual)):
            delivery.state = 'skipped'
            return True
        if quiet(pref) and not manual:
            delivery.due_at = now() + timedelta(minutes=1)
            return True
        did, uid, cid = delivery.id, delivery.user_id, binding.chat_id
        info = {**serialize(item), 'user_id': uid}
    member = auth('/internal/notification-check', method='POST', json={'user_id': uid})
    eligible = member.get('active') and visible(info, member)
    if eligible:
        eligible = delivery_eligible(info, member)
    with DB.begin() as db:
        guard(db)
        delivery = db.get(TelegramDelivery, did)
        binding = db.get(TelegramBinding, uid)
        item = db.get(Notification, info['id'])
        if not delivery:
            return True
        pref = preferences(db, uid)
        if not member.get('active') and binding:
            binding.active, binding.last_error = False, 'account_inactive'
            db.execute(delete(TelegramDelivery).where(TelegramDelivery.user_id == uid))
            return True
        if (not eligible or not item or not binding or not binding.active or binding.chat_id != cid or
                binding.bot_id != config['bot_id'] or item.expires_at <= now() or item.read_at or
                not pref.get('telegram_enabled') or (not pref.get(info['category']) and not manual)):
            delivery.state = 'skipped'
            return True
        if quiet(pref) and not manual:
            delivery.due_at = now() + timedelta(minutes=1)
            return True
        text_value = message_text(info, pref)
    result = transport.call('sendMessage', {'chat_id': cid, 'text': text_value,
                                           'link_preview_options': {'is_disabled': True}})
    with DB.begin() as db:
        guard(db)
        delivery, binding = db.get(TelegramDelivery, did), db.get(TelegramBinding, uid)
        if not delivery:
            return True
        delivery.attempts += 1
        status = result['status']
        if status == 403:
            if binding:
                binding.active, binding.last_error = False, 'bot_blocked'
            db.execute(delete(TelegramDelivery).where(TelegramDelivery.user_id == uid))
        elif 200 <= status < 300:
            delivery.state = 'sent'
            if binding:
                binding.last_error = ''
        elif (status == 429 or status >= 500) and delivery.attempts < 5:
            wait = result['retry_after'] or min(3600, 30 * 2 ** delivery.attempts)
            delivery.due_at = now() + timedelta(seconds=wait)
            if status == 429:
                state = db.get(TelegramState, 1)
                if state:
                    state.send_after = delivery.due_at
            if binding:
                binding.last_error = 'rate_limited' if status == 429 else 'delivery_delayed'
        else:
            delivery.state = 'failed'
            if binding:
                binding.last_error = 'delivery_failed'
    return True
