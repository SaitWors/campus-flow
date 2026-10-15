"""Owner-only, step-up protected control plane for prebuilt Telegram bot images.

No Docker socket, shell execution or GitHub fetching in this API process.
The optional host runner is a separate, explicitly installed component.
"""
import hmac
import os
import re
import secrets
from datetime import timedelta

from fastapi import Request, Response
from pydantic import Field
from sqlalchemy import inspect, select, text

from services.common.core import Input, digest, fail, now
from services.auth.models import Audit, Bot, Session, TwoFactor
from services.auth.security import cipher, consume_code

IMAGE_PATTERN = re.compile(r"ghcr\.io/saitwors/[a-z0-9][a-z0-9._/-]*@sha256:[a-f0-9]{64}\Z")
TOKEN_PATTERN = re.compile(r"[0-9]{5,15}:[a-zA-Z0-9_-]{30,}\Z")
BOT_ID_PATTERN = re.compile(r"[0-9a-f-]{36}\Z")


def migrate_bots(conn):
    Bot.__table__.create(conn, checkfirst=True)
    if 'bot_access_until' not in {c['name'] for c in inspect(conn).get_columns('sessions')}:
        conn.execute(text('ALTER TABLE sessions ADD COLUMN bot_access_until TIMESTAMP'))


def is_owner(db, user):
    # The ID from the one-time initial setup cannot be reassigned by promoting another admin.
    original = db.scalar(select(Audit.target).where(Audit.action == 'setup').order_by(Audit.at).limit(1))
    return bool(original and user.id == original and user.role == 'admin' and user.status == 'active')


def valid_image(image):
    if not IMAGE_PATTERN.fullmatch(image):
        fail('bot_image_must_be_owned_pinned_ghcr_digest', 422)
    return image


def runner_secret():
    value = os.environ.get('BOT_RUNNER_TOKEN', '')
    if len(value) < 40:
        fail('bot_runner_not_configured', 503)
    return value


def as_json(bot):
    return {'id': bot.id, 'name': bot.name, 'image': bot.image,
            'desired_state': bot.desired_state, 'observed_state': bot.observed_state,
            'revision': bot.revision, 'observed_revision': bot.observed_revision,
            'last_error': bot.last_error, 'last_logs': bot.last_logs,
            'last_checked': bot.last_checked.isoformat() + 'Z' if bot.last_checked else None}


class UnlockInput(Input):
    code: str = Field(pattern=r'^[0-9]{6}$')


class BotInput(Input):
    name: str = Field(min_length=2, max_length=60)
    image: str = Field(min_length=85, max_length=260)
    token: str = Field(default='', max_length=160)


class BotState(Input):
    desired_state: str = Field(pattern=r'^(running|stopped)$')


class RunnerReport(Input):
    id: str = Field(pattern=r'^[0-9a-f-]{36}$')
    observed_revision: int = Field(ge=1)
    observed_state: str = Field(pattern=r'^(running|stopped|error)$')
    last_error: str = Field(default='', max_length=600)
    last_logs: str = Field(default='', max_length=6000)


def install_bot_manager(app, DB, check_session, rate, audit):
    def owner_session(db, request, *, unlocked=True):
        u, session = check_session(
            db, request.cookies.get('cf_session', ''),
            request.headers.get('X-CSRF-Token', ''),
            request.method not in ('GET', 'HEAD', 'OPTIONS'))
        if not is_owner(db, u):
            fail('bot_owner_only', 403)
        factor = db.get(TwoFactor, u.id)
        if not factor or not factor.enabled:
            fail('bot_mfa_required', 403)
        if unlocked and (not session.bot_access_until or session.bot_access_until <= now()):
            fail('bot_mfa_step_up_required', 403)
        return u, session

    def runner_check(request):
        token = runner_secret()
        supplied = request.headers.get('X-Bot-Runner-Token', '')
        if not hmac.compare_digest(token, supplied):
            fail('forbidden', 403)

    @app.get('/api/auth/bots/guard')
    def guard(request: Request):
        with DB() as db:
            u, session = check_session(db, request.cookies.get('cf_session', ''))
            owner = is_owner(db, u)
            factor = db.get(TwoFactor, u.id) if owner else None
            enabled = bool(factor and factor.enabled)
            expiry = session.bot_access_until
            return {'owner': owner, 'mfa_enabled': enabled,
                    'unlocked': bool(owner and enabled and expiry and expiry > now()),
                    'runner_ready': bool(len(os.getenv('BOT_RUNNER_TOKEN', '')) >= 40)}

    @app.post('/api/auth/bots/unlock')
    def unlock(data: UnlockInput, request: Request, response: Response):
        rate(request, 'bot-manager-unlock', 12)
        with DB.begin() as db:
            u, session = owner_session(db, request, unlocked=False)
            factor = db.scalar(select(TwoFactor).where(TwoFactor.user_id == u.id).with_for_update())
            if not factor or not consume_code(factor, data.code):
                fail('mfa_invalid', 401)
            session.bot_access_until = now() + timedelta(minutes=10)
            # Rotate the original cookie and CSRF secret at privilege elevation.
            new_token = secrets.token_urlsafe(40)
            session.token_hash = digest(new_token)
            session.csrf = secrets.token_urlsafe(32)
            response.set_cookie('cf_session', new_token, httponly=True,
                                secure=os.getenv('COOKIE_SECURE', 'false').lower() == 'true',
                                samesite='lax', max_age=604800, path='/')
            audit(db, u.name, 'bot_manager_unlocked', u.id)
            return {'ok': True, 'csrf': session.csrf, 'expires_in': 600}

    @app.get('/api/auth/bots')
    def list_bots(request: Request):
        with DB() as db:
            owner_session(db, request)
            return [as_json(b) for b in db.scalars(select(Bot).order_by(Bot.created_at, Bot.id))]

    @app.post('/api/auth/bots', status_code=201)
    def add_bot(data: BotInput, request: Request):
        with DB.begin() as db:
            u, _ = owner_session(db, request)
            if db.query(Bot).count() >= 5:
                fail('bot_limit_reached', 409)
            if not TOKEN_PATTERN.fullmatch(data.token):
                fail('invalid_bot_token', 422)
            bot = Bot(name=data.name.strip(), image=valid_image(data.image),
                      token_cipher=cipher().encrypt(data.token.encode()).decode())
            db.add(bot)
            db.flush()
            audit(db, u.name, 'bot_created', bot.id, {'name': bot.name})
            return as_json(bot)

    @app.put('/api/auth/bots/{bot_id}')
    def update_bot(bot_id: str, data: BotInput, request: Request):
        with DB.begin() as db:
            u, _ = owner_session(db, request)
            bot = db.get(Bot, bot_id)
            if not bot:
                fail('not_found', 404)
            bot.name = data.name.strip()
            bot.image = valid_image(data.image)
            if data.token:
                if not TOKEN_PATTERN.fullmatch(data.token):
                    fail('invalid_bot_token', 422)
                bot.token_cipher = cipher().encrypt(data.token.encode()).decode()
            bot.revision += 1
            audit(db, u.name, 'bot_updated', bot.id, {'revision': bot.revision})
            return as_json(bot)

    @app.post('/api/auth/bots/{bot_id}/state')
    def set_bot_state(bot_id: str, data: BotState, request: Request):
        with DB.begin() as db:
            u, _ = owner_session(db, request)
            bot = db.get(Bot, bot_id)
            if not bot:
                fail('not_found', 404)
            if bot.desired_state != data.desired_state:
                bot.desired_state = data.desired_state
                bot.revision += 1
                audit(db, u.name, 'bot_state_changed', bot.id, {'state': bot.desired_state})
            return as_json(bot)

    @app.delete('/api/auth/bots/{bot_id}')
    def delete_bot(bot_id: str, request: Request):
        with DB.begin() as db:
            u, _ = owner_session(db, request)
            bot = db.get(Bot, bot_id)
            if not bot:
                fail('not_found', 404)
            db.delete(bot)
            audit(db, u.name, 'bot_deleted', bot_id)
            return {'ok': True}

    @app.get('/api/auth/bot-runner/state')
    def runner_state(request: Request):
        runner_check(request)
        with DB() as db:
            return [{'id': b.id, 'image': b.image,
                     'desired_state': b.desired_state, 'revision': b.revision,
                     'token': cipher().decrypt(b.token_cipher.encode()).decode()}
                    for b in db.scalars(select(Bot))]

    @app.post('/api/auth/bot-runner/report')
    def runner_report(data: RunnerReport, request: Request):
        runner_check(request)
        with DB.begin() as db:
            bot = db.get(Bot, data.id)
            if not bot:
                return {'ok': False}
            # Never reveal tokens through diagnostics even if a bot prints its environment.
            token = cipher().decrypt(bot.token_cipher.encode()).decode()
            log = data.last_logs.replace(token, '[REDACTED]')
            err = data.last_error.replace(token, '[REDACTED]')
            if (bot.observed_state != data.observed_state or
                    bot.observed_revision != data.observed_revision or
                    bot.last_error != err or bot.last_logs != log):
                bot.observed_state = data.observed_state
                bot.observed_revision = data.observed_revision
                bot.last_error = err
                bot.last_logs = log[-4000:]
            bot.last_checked = now()
            return {'ok': True}
