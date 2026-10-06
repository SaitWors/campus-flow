import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Query, Request
from pydantic import Field, model_validator
from sqlalchemy import and_, case, delete, func, or_, select, update

from services.common.core import (
    Input, database, digest, fail, identity, manager, migrate,
    now, remote, service_secret, setup_app,
)
from services.notifications.models import (
    Announcement, Audit, Base, Cursor, Delivery, Guard, Notification,
    Preference, PushKey, Subscription, Question, QuestionMessage,
)
from services.notifications import telegram as tg
from services.notifications.events import assignment_data, relevant, schedule_data
from services.notifications.questions import STAFF, accessible, install as install_questions, migrate_questions
from services.notifications.push import DEFAULTS, generate_keys, quiet, send, validate_subscription

engine, DB = database('notifications')
logger = logging.getLogger('campus.notifications')
telegram_transport = tg.Transport()


def auth(path, **kwargs):
    return remote(os.getenv('AUTH_URL', 'http://auth:8000'), path, **kwargs)


def source_url(source):
    return os.getenv(source.upper() + '_URL', 'http://' + source + ':8000')


def guard(db):
    db.execute(select(Guard).where(Guard.id == 1).with_for_update()).scalar_one()


def preferences(db, user_id):
    item = db.get(Preference, user_id)
    return {**DEFAULTS, **({k:v for k,v in item.data.items() if k in DEFAULTS} if item else {}), 'revision': item.revision if item else 0}


def visible(data, user):
    if data.get('audience') == 'question':
        return accessible(data, user)
    audience = data.get('audience', 'all')
    return (audience == 'all' or
            (audience == 'managers' and user['role'] in ('admin', 'head', 'deputy')) or
            audience == 'subgroup' + str(user['subgroup']))


def serialize(item):
    return {
        'id': item.id, 'category': item.category, **item.data,
        'created_at': item.created_at.isoformat() + 'Z',
        'expires_at': item.expires_at.isoformat() + 'Z',
        'read': item.read_at is not None,
    }


def notify(db, user, source_key, category, data, expires_at, announcement_id='', channels=('push', 'telegram')):
    existing = db.scalar(select(Notification).where(
        Notification.user_id == user['id'], Notification.source_key == source_key))
    if existing:
        return existing
    item = Notification(user_id=user['id'], source_key=source_key, category=category,
                        data=data, expires_at=expires_at, announcement_id=announcement_id)
    db.add(item)
    db.flush()
    pref = preferences(db, user['id'])
    if 'push' in channels and (pref.get(category) or data.get('manual_test') == 'push'):
        for sub in db.scalars(select(Subscription).where(Subscription.user_id == user['id'])):
            db.add(Delivery(notification_id=item.id, subscription_id=sub.id))
    if 'telegram' in channels:
        tg.enqueue(db, item, pref)
    return item


def push_config():
    subject = os.getenv('VAPID_SUBJECT', '').strip() or os.getenv('APP_ORIGIN', '').split(',')[0]
    enabled = os.getenv('PUSH_ENABLED', 'true').lower() == 'true'
    enabled = enabled and (subject.startswith('https://') or subject.startswith('mailto:'))
    return enabled, subject


def consume(source):
    if source != 'schedule':
        return
    with DB() as db:
        cursor = db.get(Cursor, source)
        after = cursor.seq if cursor else None
    # On first deployment skip historical events; subsequent restarts resume.
    if after is None:
        head = remote(source_url(source), '/internal/events/head')['seq']
        with DB.begin() as db:
            guard(db)
            if not db.get(Cursor, source):
                db.add(Cursor(source=source, seq=head))
        return
    events = remote(source_url(source), '/internal/events?after=' + str(after))
    if not events:
        return
    users = auth('/internal/notification-recipients')
    assignments = assignment_audiences(e['data'].get('assignment_id') for e in events if e['type'].startswith('assignment.'))
    with DB.begin() as db:
        guard(db)
        cursor = db.get(Cursor, source)
        batch = [e for e in events if e['seq'] > cursor.seq]
        if not batch:
            return
        stamp = now()
        recent = [e for e in batch if not e['type'].startswith('assignment.') and
                  datetime.fromisoformat(e['at'].replace('Z', '+00:00')).replace(tzinfo=None) > stamp - timedelta(hours=1)]
        for user in users:
            data = schedule_data(recent, user)
            if data:
                notify(db, user, 'schedule:' + str(batch[-1]['seq']), 'schedule', data, stamp + timedelta(days=30))
        for event in batch:
            if event['type'] not in ('assignment.created', 'assignment.updated', 'assignment.archived'):
                continue
            current = assignments.get(event['data'].get('assignment_id'))
            if not current or (current['archived'] and event['type'] != 'assignment.archived'):
                continue
            data = assignment_data({**event['data'], 'subgroup':current['subgroup']}, event['type'].split('.')[1])
            for user in users:
                if relevant(current['subgroup'], user):
                    # Managers may receive subgroup updates; stored audience must
                    # also reflect that permission for later role changes.
                    info = {**data, 'audience':'managers'} if user['role'] in STAFF and not visible(data, user) else data
                    notify(db, user, 'assignment:' + event['id'], 'assignments', info, stamp + timedelta(days=30))
        cursor.seq = batch[-1]['seq']


def utc_stamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc).replace(tzinfo=None)


def assignment_reminders():
    reminders = remote(source_url('schedule'), '/internal/assignments/reminders')
    if not reminders:
        return
    users = auth('/internal/notification-recipients')
    with DB.begin() as db:
        guard(db)
        for item in reminders:
            due = utc_stamp(item['due_at'])
            if not now() < due <= now() + timedelta(hours=24):
                continue
            data = assignment_data(item, reminder=True)
            source = 'assignment:reminder:' + str(item['id']) + ':' + str(item['revision']) + ':' + item['due_at']
            for user in users:
                if user['id'] in item.get('completed_user_ids', []) or not relevant(item.get('subgroup', 0), user):
                    continue
                info = {**data, 'audience':'managers'} if user['role'] in STAFF and not visible(data, user) else data
                notify(db, user, source, 'assignments', info, due)


def assignment_audiences(ids):
    ids = sorted({i for i in ids if isinstance(i, str) and 1 <= len(i) <= 36})
    items = {}
    for start in range(0, len(ids), 200):
        batch = ids[start:start + 200]
        result = remote(source_url('schedule'), '/internal/assignments/audiences', method='POST', json={'ids':batch})
        for item in result['items']:
            if item['id'] in batch and item['subgroup'] in (0, 1, 2):
                items[item['id']] = item
    return items


def delivery_eligible(info, member):
    if info['category'] != 'assignments':
        return True
    current = assignment_audiences([info.get('assignment_id')]).get(info.get('assignment_id'))
    if not current or not relevant(current['subgroup'], member):
        return False
    if current['archived'] and not info.get('assignment_archived'):
        return False
    return reminder_eligible(info, member['id'])


def reminder_eligible(info, user_id):
    if not info.get('reminder'):
        return True
    return any(item['id'] == info.get('assignment_id') and item['revision'] == info.get('revision') and
               item['due_at'] == info.get('due_at') and user_id not in item.get('completed_user_ids', [])
               for item in remote(source_url('schedule'), '/internal/assignments/reminders'))


def poll_telegram():
    return tg.poll(DB, guard, auth, telegram_transport)


def deliver_telegram():
    return tg.deliver(DB, guard, auth, preferences, visible, serialize, delivery_eligible, telegram_transport)


def deliver_one():
    enabled, subject = push_config()
    if not enabled:
        return False
    with DB.begin() as db:
        delivery = db.scalar(select(Delivery).where(
            Delivery.state == 'pending', Delivery.due_at <= now()
        ).order_by(Delivery.id).with_for_update(skip_locked=True).limit(1))
        if not delivery:
            return False
        # Lease survives crashes. Stable device tags replace duplicate delivery.
        delivery.due_at = now() + timedelta(minutes=2)
        did = delivery.id
        item = db.get(Notification, delivery.notification_id)
        sub = db.get(Subscription, delivery.subscription_id)
        if not item or not sub or item.expires_at <= now() or item.read_at:
            delivery.state = 'skipped'
            return True
        pref = preferences(db, item.user_id)
        manual = item.data.get('manual_test') == 'push'
        if not pref.get(item.category) and not manual:
            delivery.state = 'skipped'
            return True
        if quiet(pref) and not manual:
            delivery.due_at = now() + timedelta(minutes=1)
            return True
        key = db.get(PushKey, 1).private_key
        info = {**serialize(item), 'user_id': item.user_id}
        sub_id, session_hash, sub_data = sub.id, sub.session_hash, sub.data
    # No network calls inside transactions. Check current sessions and audience.
    member = auth('/internal/push-check', method='POST', json={
        'user_id': info['user_id'], 'session_hash': session_hash,
    })
    if not member.get('active'):
        with DB.begin() as db:
            db.execute(delete(Subscription).where(Subscription.id == sub_id))
            db.execute(delete(Delivery).where(Delivery.subscription_id == sub_id))
        return True
    eligible = visible(info, member)
    if eligible:
        eligible = delivery_eligible(info, member)
    # Recheck withdrawals, device deletion and preference changes after auth.
    with DB.begin() as db:
        delivery = db.get(Delivery, did)
        item = db.get(Notification, info['id'])
        sub = db.get(Subscription, sub_id)
        if not delivery:
            return True
        pref = preferences(db, info['user_id'])
        if not eligible or not item or not sub or item.read_at or item.expires_at <= now() or (not pref.get(info['category']) and not manual):
            delivery.state = 'skipped'
            return True
        if quiet(pref) and not manual:
            delivery.due_at = now() + timedelta(minutes=1)
            return True
    english, details = pref['language'] == 'en', pref['show_details']
    payload = {
        'id': info['id'], 'user_id': info['user_id'], 'route': info['route'],
        'title': ((info.get('title_en') or info['title']) if english else info['title']) if details else 'Campus Flow',
        'body': ((info.get('body_en') or info['body']) if english else info['body'])[:600] if details else
                ('Open Campus Flow to read the update.' if english else 'Откройте Campus Flow, чтобы прочитать обновление.'),
    }
    ttl = max(1, min(86400, int((datetime.fromisoformat(info['expires_at'].replace('Z', '')) - now()).total_seconds())))
    try:
        status = send(sub_data, payload, key, subject, ttl)
    except Exception:
        status = 503
    with DB.begin() as db:
        delivery = db.get(Delivery, did)
        if not delivery:
            return True
        delivery.attempts += 1
        if status in (404, 410):
            db.execute(delete(Subscription).where(Subscription.id == sub_id))
            db.execute(delete(Delivery).where(Delivery.subscription_id == sub_id))
        elif 200 <= status < 300:
            delivery.state = 'sent'
        elif (status == 429 or status >= 500) and delivery.attempts < 5:
            delivery.due_at = now() + timedelta(seconds=min(3600, 30 * 2 ** delivery.attempts))
        else:
            delivery.state = 'failed'
    return True


def cleanup():
    with DB.begin() as db:
        guard(db)
        expired = select(Notification.id).where(Notification.expires_at <= now())
        db.execute(delete(Delivery).where(Delivery.notification_id.in_(expired)))
        db.execute(delete(tg.TelegramDelivery).where(tg.TelegramDelivery.notification_id.in_(expired)))
        db.execute(delete(tg.TelegramLink).where(tg.TelegramLink.expires_at < now() - timedelta(days=1)))
        db.execute(delete(Notification).where(Notification.expires_at <= now()))
        db.execute(delete(Subscription).where(Subscription.updated_at < now() - timedelta(days=8)))
        db.execute(delete(Delivery).where(~Delivery.subscription_id.in_(select(Subscription.id))))
        db.execute(delete(Announcement).where(Announcement.expires_at < now() - timedelta(days=90)))
        db.execute(delete(Audit).where(Audit.at < now() - timedelta(days=90)))
        old_questions = select(Question.id).where(Question.closed == True, Question.updated_at < now() - timedelta(days=180))
        db.execute(delete(QuestionMessage).where(QuestionMessage.question_id.in_(old_questions)))
        db.execute(delete(Question).where(Question.id.in_(old_questions)))


async def worker():
    turns = 0
    while True:
        for source in ('schedule',):
            try:
                await asyncio.to_thread(consume, source)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning('Notification event sync postponed')
        for _ in range(20):
            try:
                if not await asyncio.to_thread(deliver_one):
                    break
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning('Push delivery postponed')
                break
        turns += 1
        if turns % 20 == 1:
            try:
                await asyncio.to_thread(assignment_reminders)
            except Exception:
                logger.warning('Assignment reminder sync postponed')
        if turns % 120 == 0:
            try:
                await asyncio.to_thread(cleanup)
            except Exception:
                logger.warning('Notification cleanup postponed')
        await asyncio.sleep(3)


async def telegram_call(job):
    # Cancel the async worker only after its bounded HTTP call exits. Closing the
    # shared transport while its thread is still running can lose its result.
    pending = asyncio.create_task(asyncio.to_thread(job))
    try:
        return await asyncio.shield(pending)
    except asyncio.CancelledError:
        try:
            await pending
        except Exception:
            pass
        raise


async def telegram_worker(polling=False):
    while True:
        try:
            if polling:
                await telegram_call(poll_telegram)
            else:
                for _ in range(20):
                    if not await telegram_call(deliver_telegram):
                        break
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning('Telegram sync postponed')
        await asyncio.sleep(1 if polling else 3)


@asynccontextmanager
async def lifespan(app):
    service_secret()
    migrate(engine, Base, (migrate_questions, tg.migrate_notifications))
    with DB.begin() as db:
        if not db.get(Guard, 1):
            db.add(Guard(id=1))
        if not db.get(PushKey, 1):
            private, public = generate_keys()
            db.add(PushKey(id=1, private_key=private, public_key=public))
    tasks = []
    if os.getenv('NOTIFICATION_WORKER', 'true') == 'true':
        tasks.append(asyncio.create_task(worker()))
        if tg.configuration()['enabled']:
            tasks.extend((asyncio.create_task(telegram_worker(True)), asyncio.create_task(telegram_worker())))
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        telegram_transport.close()


app = setup_app('Campus Flow · Notifications', engine, lifespan)


@app.get('/api/notifications/config')
def config(request: Request):
    identity(request)
    with DB() as db:
        return {'enabled': push_config()[0], 'public_key': db.get(PushKey, 1).public_key}


@app.get('/api/notifications/preferences')
def get_preferences(request: Request):
    user = identity(request)
    with DB() as db:
        return preferences(db, user['id'])


class PreferencesInput(Input):
    revision: int = Field(ge=0)
    schedule: bool
    assignments: bool = True
    telegram_enabled: bool = False
    announcements: bool
    questions: bool = True
    important_popups: bool
    show_details: bool
    quiet_enabled: bool
    quiet_start: str = Field(pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    quiet_end: str = Field(pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    timezone: str = Field(max_length=60)
    language: Literal['ru', 'en']

    @model_validator(mode='after')
    def valid(self):
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError('invalid_timezone')
        if self.quiet_enabled and self.quiet_start == self.quiet_end:
            raise ValueError('quiet_hours_equal')
        return self


@app.put('/api/notifications/preferences')
def set_preferences(data: PreferencesInput, request: Request):
    user = identity(request)
    with DB.begin() as db:
        guard(db)
        if preferences(db, user['id'])['revision'] != data.revision:
            fail('revision_conflict', 409)
        pref = db.get(Preference, user['id'])
        if not pref:
            pref = Preference(user_id=user['id'], revision=0, data={})
            db.add(pref)
        pref.data = data.model_dump(exclude={'revision'})
        pref.revision += 1
        db.flush()
        return preferences(db, user['id'])


def inbox_scope(user, stamp):
    audience = func.coalesce(Notification.data['audience'].as_string(), 'all')
    allowed = or_(audience == 'all', audience == 'subgroup' + str(user['subgroup']))
    question = Notification.data['owner_id'].as_string() == user['id']
    if user['role'] in STAFF:
        allowed = or_(allowed, audience == 'managers')
        question = or_(question, Notification.data['recipient_id'].as_string() == user['id'])
    allowed = or_(allowed, and_(audience == 'question', question))
    return and_(Notification.user_id == user['id'], Notification.expires_at > stamp,
                Notification.category.in_(('schedule', 'assignments', 'announcements', 'questions')), allowed)


def current_assignment_scope(user, stamp):
    # Fetch only distinct IDs, never notification bodies or other users' rows.
    assignment_id = Notification.data['assignment_id'].as_string()
    with DB() as db:
        ids = list(db.scalars(select(assignment_id).distinct().where(
            inbox_scope(user, stamp), Notification.category == 'assignments')))
    current = assignment_audiences(ids)
    active, archived = [], []
    for item in current.values():
        if relevant(item['subgroup'], user):
            (archived if item['archived'] else active).append(item['id'])
    allowed = or_(assignment_id.in_(active), and_(assignment_id.in_(archived),
                  Notification.data['assignment_archived'].as_boolean() == True))
    return or_(Notification.category != 'assignments', allowed)


@app.get('/api/notifications/inbox')
def inbox(request: Request, offset: int = Query(default=0, ge=0, le=10000), unread_only: bool = False):
    user = identity(request)
    stamp = now()
    assignments = current_assignment_scope(user, stamp)
    with DB() as db:
        scope = and_(inbox_scope(user, stamp), assignments)
        total_all, unread = db.execute(select(func.count(), func.coalesce(func.sum(case((Notification.read_at == None, 1), else_=0)), 0))
                                      .select_from(Notification).where(scope)).one()
        query = select(Notification).where(scope)
        if unread_only:
            query = query.where(Notification.read_at == None)
        order = (Notification.created_at.desc(), Notification.id.desc())
        items = db.scalars(query.order_by(*order).offset(offset).limit(30))
        pref = preferences(db, user['id'])
        popups = db.scalars(select(Notification).where(scope, Notification.read_at == None,
            Notification.popup_dismissed == False, Notification.data['important'].as_boolean() == True)
            .order_by(*order).limit(3)) if pref['important_popups'] else []
        return {'items': [serialize(n) for n in items], 'unread': unread,
                'total': unread if unread_only else total_all,
                'popups': [serialize(n) for n in popups], 'as_of': stamp.isoformat() + 'Z'}


class ReadInput(Input):
    ids: list[str] = Field(default_factory=list, max_length=100)
    before: datetime | None = None


@app.post('/api/notifications/read')
def read(data: ReadInput, request: Request):
    user = identity(request)
    assignments = current_assignment_scope(user, now())
    with DB.begin() as db:
        guard(db)
        before = data.before.astimezone(timezone.utc).replace(tzinfo=None) if data.before else None
        requested = Notification.id.in_(data.ids)
        if before:
            requested = or_(requested, Notification.created_at <= before)
        db.execute(update(Notification).where(inbox_scope(user, now()), assignments, requested)
                   .values(read_at=now(), popup_dismissed=True))
    return {'ok': True}


class PushKeys(Input):
    auth: str = Field(min_length=20, max_length=30)
    p256dh: str = Field(min_length=80, max_length=100)


class Subscribe(Input):
    endpoint: str = Field(max_length=2048)
    keys: PushKeys
    label: str = Field(default='Browser', min_length=1, max_length=60)

    @model_validator(mode='after')
    def valid(self):
        validate_subscription(self.model_dump(exclude={'label'}))
        if any(ord(ch) < 32 for ch in self.label):
            raise ValueError('invalid_device_label')
        return self


@app.post('/api/notifications/subscriptions', status_code=201)
def subscribe(data: Subscribe, request: Request):
    user = identity(request)
    if not push_config()[0]:
        fail('push_not_configured', 503)
    session_hash = digest(request.cookies.get('cf_session', ''))
    session = auth('/internal/push-check', method='POST', json={'user_id':user['id'], 'session_hash':session_hash})
    if not session.get('active'):
        fail('unauthorized', 401)
    expiry = utc_stamp(session['expires_at']) if session.get('expires_at') else None
    with DB.begin() as db:
        guard(db)
        hashed = digest(data.endpoint)
        sub = db.scalar(select(Subscription).where(Subscription.endpoint_hash == hashed))
        if sub and sub.user_id != user['id']:
            fail('push_account_conflict', 409)
        if not sub:
            if db.scalar(select(func.count()).select_from(Subscription).where(Subscription.user_id == user['id'])) >= 5:
                fail('push_device_limit', 409)
            sub = Subscription(user_id=user['id'], endpoint_hash=hashed)
            db.add(sub)
        sub.data = data.model_dump(exclude={'label'})
        sub.label = data.label
        sub.session_hash = session_hash
        sub.expires_at = expiry
        sub.updated_at = now()
        db.flush()
        return {'id': sub.id, 'expires_at':expiry.isoformat() + 'Z' if expiry else None, 'active':True}


@app.get('/api/notifications/subscriptions')
def devices(request: Request):
    user = identity(request)
    with DB() as db:
        subscriptions = list(db.scalars(select(Subscription).where(Subscription.user_id == user['id'])))
    devices = []
    for sub in subscriptions:
        member = auth('/internal/push-check', method='POST', json={'user_id':user['id'], 'session_hash':sub.session_hash})
        expiry = member.get('expires_at') or (sub.expires_at.isoformat() + 'Z' if sub.expires_at else None)
        active = bool(member.get('active') and (not expiry or utc_stamp(expiry) > now()))
        devices.append({'id':sub.id, 'label':sub.label, 'updated_at':sub.updated_at.isoformat() + 'Z',
                        'expires_at':expiry, 'active':active})
    return devices


@app.delete('/api/notifications/subscriptions/{sid}')
def unsubscribe(sid: str, request: Request):
    user = identity(request)
    with DB.begin() as db:
        guard(db)
        sub = db.get(Subscription, sid)
        if not sub or sub.user_id != user['id']:
            fail('not_found', 404)
        db.execute(delete(Delivery).where(Delivery.subscription_id == sid))
        db.delete(sub)
    return {'ok': True}


@app.post('/api/notifications/test')
def test_push(request: Request):
    user = identity(request)
    with DB.begin() as db:
        guard(db)
        minute = now().strftime('%Y%m%d%H%M')
        item = notify(db, user, 'push:test:' + minute, 'announcements', {
            'title': 'Уведомления подключены', 'title_en': 'Notifications connected',
            'body': 'Campus Flow готов присылать обновления.',
            'body_en': 'Campus Flow is ready to send updates.', 'route': '#notifications',
            'important': False, 'audience': 'all', 'manual_test':'push',
        }, now() + timedelta(minutes=10), channels=('push',))
        return {'id': item.id}


@app.get('/api/notifications/telegram')
def telegram_status(request: Request):
    user = identity(request)
    config = tg.configuration()
    with DB() as db:
        binding = db.get(tg.TelegramBinding, user['id'])
        connected = bool(config['enabled'] and binding and binding.active and binding.bot_id == config['bot_id'])
        result = {'enabled':config['enabled'], 'bot_username':config['bot_username'], 'connected':connected}
        state = db.get(tg.TelegramState, 1)
        if state and state.bot_id == config['bot_id'] and state.last_error:
            result['last_error'] = state.last_error
        if binding:
            result['chat_label'] = binding.chat_label
            if binding.last_error:
                result['last_error'] = binding.last_error
            elif config['enabled'] and binding.bot_id != config['bot_id']:
                result['last_error'] = 'bot_changed'
        return result


@app.post('/api/notifications/telegram/link')
def telegram_link(request: Request):
    user = identity(request)
    tg.required()
    with DB.begin() as db:
        guard(db)
        return tg.issue_link(db, user['id'])


@app.delete('/api/notifications/telegram')
def telegram_disconnect(request: Request):
    user = identity(request)
    with DB.begin() as db:
        guard(db)
        tg.disconnect(db, user['id'])
    return {'ok':True}


@app.post('/api/notifications/telegram/test')
def telegram_test(request: Request):
    user = identity(request)
    config = tg.required()
    with DB.begin() as db:
        guard(db)
        binding = db.get(tg.TelegramBinding, user['id'])
        if not binding or not binding.active or binding.bot_id != config['bot_id']:
            fail('telegram_not_connected', 409)
        if not preferences(db, user['id'])['telegram_enabled']:
            fail('telegram_disabled', 409)
        if binding.last_test_at and binding.last_test_at > now() - timedelta(minutes=1):
            fail('too_many_attempts', 429)
        binding.last_test_at = now()
        notify(db, user, 'telegram:test:' + now().strftime('%Y%m%d%H%M'), 'announcements', {
            'title':'Telegram подключён', 'title_en':'Telegram connected',
            'body':'Campus Flow готов присылать обновления.', 'body_en':'Campus Flow is ready to send updates.',
            'route':'#notifications', 'important':False, 'audience':'all',
            'manual_test':'telegram',
        }, now() + timedelta(minutes=10), channels=('telegram',))
    return {'ok':True}


class Publish(Input):
    title: str = Field(min_length=2, max_length=120)
    body: str = Field(min_length=2, max_length=2000)
    title_en: str = Field(default='', max_length=120)
    body_en: str = Field(default='', max_length=2000)
    audience: Literal['all', 'managers', 'subgroup1', 'subgroup2'] = 'all'
    important: bool = True
    hours: int = Field(default=24, ge=1, le=168)

    @model_validator(mode='after')
    def valid(self):
        for value in (self.title, self.body, self.title_en, self.body_en):
            if any(ord(ch) < 32 and ch not in '\n\r\t' for ch in value):
                raise ValueError('invalid_announcement_text')
        return self


@app.post('/api/notifications/announcements', status_code=201)
def publish(data: Publish, request: Request):
    user = identity(request)
    manager(user)
    key = request.headers.get('Idempotency-Key', '')
    if not 16 <= len(key) <= 100:
        fail('idempotency_key_required', 422)
    fingerprint = digest(json.dumps(data.model_dump(), sort_keys=True))
    recipients = auth('/internal/notification-recipients')
    with DB.begin() as db:
        guard(db)
        request_key = user['id'] + ':' + key
        existing = db.scalar(select(Announcement).where(Announcement.request_key == request_key))
        if existing:
            if existing.fingerprint != fingerprint:
                fail('idempotency_conflict', 409)
            return {'id': existing.id}
        recent = db.scalar(select(func.count()).select_from(Announcement).where(
            Announcement.actor_id == user['id'], Announcement.created_at > now() - timedelta(minutes=10)))
        if recent >= 5:
            fail('too_many_attempts', 429)
        announcement = Announcement(
            request_key=request_key, fingerprint=fingerprint, actor_id=user['id'],
            actor_name=user['name'], data=data.model_dump(), expires_at=now() + timedelta(hours=data.hours))
        db.add(announcement)
        db.flush()
        for member in recipients:
            if visible(announcement.data, member):
                notify(db, member, 'announcement:' + announcement.id, 'announcements', {
                    **data.model_dump(exclude={'hours'}), 'route': '#notifications', 'author': user['name'],
                }, announcement.expires_at, announcement.id)
        db.add(Audit(actor=user['name'], action='published', target=announcement.id))
        return {'id': announcement.id}


@app.get('/api/notifications/announcements')
def announcements(request: Request):
    user = identity(request)
    manager(user)
    with DB() as db:
        return [{'id': a.id, **a.data, 'author': a.actor_name,
                 'created_at': a.created_at.isoformat() + 'Z',
                 'expires_at': a.expires_at.isoformat() + 'Z', 'withdrawn': a.withdrawn}
                for a in db.scalars(select(Announcement).order_by(Announcement.created_at.desc()).limit(100))]


@app.delete('/api/notifications/announcements/{aid}')
def withdraw(aid: str, request: Request):
    user = identity(request)
    manager(user)
    with DB.begin() as db:
        guard(db)
        announcement = db.get(Announcement, aid)
        if not announcement:
            fail('not_found', 404)
        if not announcement.withdrawn:
            announcement.withdrawn = True
            ids = select(Notification.id).where(Notification.announcement_id == aid)
            db.execute(delete(Delivery).where(Delivery.notification_id.in_(ids)))
            db.execute(delete(tg.TelegramDelivery).where(tg.TelegramDelivery.notification_id.in_(ids)))
            db.execute(delete(Notification).where(Notification.announcement_id == aid))
            db.add(Audit(actor=user['name'], action='withdrawn', target=aid))
    return {'ok': True}


@app.get('/api/notifications/audit')
def audit_log(request: Request):
    user = identity(request)
    manager(user)
    with DB() as db:
        return [{'id': a.id, 'actor': a.actor, 'action': a.action, 'target': a.target,
                 'at': a.at.isoformat() + 'Z'} for a in db.scalars(select(Audit).order_by(Audit.at.desc()).limit(100))]


class QuestionDB:
    def __call__(self):
        return DB()

    def begin(self):
        return DB.begin()


install_questions(app, QuestionDB(), lambda r: identity(r), lambda *a, **k: auth(*a, **k), guard, notify)
