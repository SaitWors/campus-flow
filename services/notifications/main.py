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
from sqlalchemy import delete, func, select

from services.common.core import (
    Input, database, digest, fail, identity, manager, migrate,
    now, remote, service_secret, setup_app,
)
from services.notifications.models import (
    Announcement, Audit, Base, Cursor, Delivery, Guard, Notification,
    Preference, PushKey, Subscription,
)
from services.notifications.push import DEFAULTS, generate_keys, quiet, send, validate_subscription

engine, DB = database('notifications')
logger = logging.getLogger('campus.notifications')


def auth(path, **kwargs):
    return remote(os.getenv('AUTH_URL', 'http://auth:8000'), path, **kwargs)


def source_url(source):
    return os.getenv(source.upper() + '_URL', 'http://' + source + ':8000')


def guard(db):
    db.execute(select(Guard).where(Guard.id == 1).with_for_update()).scalar_one()


def preferences(db, user_id):
    item = db.get(Preference, user_id)
    return {**DEFAULTS, **(item.data if item else {}), 'revision': item.revision if item else 0}


def visible(data, user):
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


def notify(db, user, source_key, category, data, expires_at, announcement_id=''):
    existing = db.scalar(select(Notification).where(
        Notification.user_id == user['id'], Notification.source_key == source_key))
    if existing:
        return existing
    item = Notification(user_id=user['id'], source_key=source_key, category=category,
                        data=data, expires_at=expires_at, announcement_id=announcement_id)
    db.add(item)
    db.flush()
    if preferences(db, user['id'])[category]:
        for sub in db.scalars(select(Subscription).where(Subscription.user_id == user['id'])):
            db.add(Delivery(notification_id=item.id, subscription_id=sub.id))
    return item


def push_config():
    subject = os.getenv('VAPID_SUBJECT', '').strip() or os.getenv('APP_ORIGIN', '').split(',')[0]
    enabled = os.getenv('PUSH_ENABLED', 'true').lower() == 'true'
    enabled = enabled and (subject.startswith('https://') or subject.startswith('mailto:'))
    return enabled, subject


def consume(source):
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
    calls = {}
    if source == 'queue':
        for event in events:
            if event['type'] == 'queue.called':
                entry_id = event['data']['entry_id']
                calls[entry_id] = remote(source_url('queue'), '/internal/calls/' + entry_id)
    with DB.begin() as db:
        guard(db)
        cursor = db.get(Cursor, source)
        batch = [e for e in events if e['seq'] > cursor.seq]
        if not batch:
            return
        stamp = now()
        if source == 'schedule':
            recent = [e for e in batch if datetime.fromisoformat(e['at'].replace('Z', '+00:00')).replace(tzinfo=None) > stamp - timedelta(hours=1)]
            for user in users:
                relevant = [e for e in recent if e['data'].get('subgroup', 0) in (0, user['subgroup'])]
                if relevant:
                    notify(db, user, 'schedule:' + str(batch[-1]['seq']), 'schedule', {
                        'title': 'Расписание обновлено', 'title_en': 'Timetable updated',
                        'body': 'Проверьте время, аудиторию и статус занятий.',
                        'body_en': 'Check class times, rooms and status.', 'route': '#schedule',
                        'important': False, 'audience': 'all',
                    }, stamp + timedelta(days=30))
        else:
            members = {u['id']: u for u in users}
            for event in batch:
                data = event['data']
                call = calls.get(data.get('entry_id'), {})
                user = members.get(data.get('user_id'))
                if not user or not call.get('active'):
                    continue
                expiry = min(stamp + timedelta(minutes=10), datetime.fromisoformat(call['ends_at']).astimezone(timezone.utc).replace(tzinfo=None))
                notify(db, user, 'queue:' + event['id'], 'queue', {
                    'title': 'Ваша очередь', 'title_en': 'It is your turn',
                    'body': 'Вас вызвали на сдачу. Откройте очередь.',
                    'body_en': 'You have been called. Open your queue.', 'route': '#queues',
                    'important': False, 'audience': 'all', 'entry_id': data['entry_id'],
                }, expiry)
        cursor.seq = batch[-1]['seq']


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
        if not pref[item.category]:
            delivery.state = 'skipped'
            return True
        if quiet(pref):
            delivery.due_at = now() + timedelta(minutes=1)
            return True
        key = db.get(PushKey, 1).private_key
        info = {**serialize(item), 'user_id': item.user_id}
        sub_id, session_hash, sub_data = sub.id, sub.session_hash, sub.data
    # No network calls inside transactions. Check sessions, roles and current call.
    member = auth('/internal/push-check', method='POST', json={
        'user_id': info['user_id'], 'session_hash': session_hash,
    })
    if not member.get('active'):
        with DB.begin() as db:
            db.execute(delete(Subscription).where(Subscription.id == sub_id))
            db.execute(delete(Delivery).where(Delivery.subscription_id == sub_id))
        return True
    eligible = visible(info, member)
    if eligible and info['category'] == 'queue':
        eligible = remote(source_url('queue'), '/internal/calls/' + info['entry_id'])['active']
    # Recheck withdrawals, device deletion and preference changes after auth.
    with DB.begin() as db:
        delivery = db.get(Delivery, did)
        item = db.get(Notification, info['id'])
        sub = db.get(Subscription, sub_id)
        if not delivery:
            return True
        pref = preferences(db, info['user_id'])
        if not eligible or not item or not sub or item.read_at or item.expires_at <= now() or not pref[info['category']]:
            delivery.state = 'skipped'
            return True
        if quiet(pref):
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
        db.execute(delete(Notification).where(Notification.expires_at <= now()))
        db.execute(delete(Subscription).where(Subscription.updated_at < now() - timedelta(days=8)))
        db.execute(delete(Delivery).where(~Delivery.subscription_id.in_(select(Subscription.id))))
        db.execute(delete(Announcement).where(Announcement.expires_at < now() - timedelta(days=90)))
        db.execute(delete(Audit).where(Audit.at < now() - timedelta(days=90)))


async def worker():
    turns = 0
    while True:
        for source in ('schedule', 'queue'):
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
        if turns % 120 == 0:
            try:
                await asyncio.to_thread(cleanup)
            except Exception:
                logger.warning('Notification cleanup postponed')
        await asyncio.sleep(3)


@asynccontextmanager
async def lifespan(app):
    service_secret()
    migrate(engine, Base)
    with DB.begin() as db:
        if not db.get(Guard, 1):
            db.add(Guard(id=1))
        if not db.get(PushKey, 1):
            private, public = generate_keys()
            db.add(PushKey(id=1, private_key=private, public_key=public))
    task = asyncio.create_task(worker()) if os.getenv('NOTIFICATION_WORKER', 'true') == 'true' else None
    try:
        yield
    finally:
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


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
    queue: bool
    announcements: bool
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


def inbox_items(db, user):
    return [n for n in db.scalars(select(Notification).where(
        Notification.user_id == user['id'], Notification.expires_at > now()
    ).order_by(Notification.created_at.desc(), Notification.id.desc())) if visible(n.data, user)]


@app.get('/api/notifications/inbox')
def inbox(request: Request, offset: int = Query(default=0, ge=0, le=10000), unread_only: bool = False):
    user = identity(request)
    with DB() as db:
        stamp = now()
        items = inbox_items(db, user)
        pref = preferences(db, user['id'])
        popups = [n for n in items if not n.popup_dismissed and not n.read_at and n.data.get('important')] if pref['important_popups'] else []
        filtered = [n for n in items if not n.read_at] if unread_only else items
        return {'items': [serialize(n) for n in filtered[offset:offset + 30]],
                'unread': sum(n.read_at is None for n in items), 'total': len(filtered),
                'popups': [serialize(n) for n in popups[:3]], 'as_of': stamp.isoformat() + 'Z'}


class ReadInput(Input):
    ids: list[str] = Field(default_factory=list, max_length=100)
    before: datetime | None = None


@app.post('/api/notifications/read')
def read(data: ReadInput, request: Request):
    user = identity(request)
    with DB.begin() as db:
        guard(db)
        before = data.before.astimezone(timezone.utc).replace(tzinfo=None) if data.before else None
        for item in inbox_items(db, user):
            if item.id in data.ids or (before and item.created_at <= before):
                item.read_at, item.popup_dismissed = now(), True
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
        sub.session_hash = digest(request.cookies.get('cf_session', ''))
        sub.updated_at = now()
        db.flush()
        return {'id': sub.id}


@app.get('/api/notifications/subscriptions')
def devices(request: Request):
    user = identity(request)
    with DB() as db:
        return [{'id': s.id, 'label': s.label, 'updated_at': s.updated_at.isoformat() + 'Z'}
                for s in db.scalars(select(Subscription).where(Subscription.user_id == user['id']))]


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
        item = notify(db, user, 'test:' + minute, 'announcements', {
            'title': 'Уведомления подключены', 'title_en': 'Notifications connected',
            'body': 'Campus Flow готов присылать обновления.',
            'body_en': 'Campus Flow is ready to send updates.', 'route': '#notifications',
            'important': False, 'audience': 'all',
        }, now() + timedelta(minutes=10))
        return {'id': item.id}


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
