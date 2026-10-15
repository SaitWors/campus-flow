"""Real HTTP contract for owner-only TOTP Bot Manager, against an isolated SQLite DB."""
import base64
import secrets
import time
from datetime import timedelta

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from services.common.core import digest, now
from services.auth.bots import install_bot_manager
from services.auth.models import Base, User, Session, Audit, Gate, TwoFactor
from services.auth.security import cipher, totp

IMAGE = 'ghcr.io/saitwors/testbot@sha256:' + 'a' * 64
BOT_TOKEN = '123456789:' + 'x' * 35


def test_owner_mfa_step_up_guards_all_bot_operations(tmp_path, monkeypatch):
    monkeypatch.setenv('INTERNAL_TOKEN', 'for-bot-tests-the-secret-is-at-least-forty-characters')
    monkeypatch.setenv('BOT_RUNNER_TOKEN', 'f' * 64)
    monkeypatch.setenv('COOKIE_SECURE', 'false')

    engine = create_engine('sqlite://', poolclass=StaticPool,
                           connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    DB = sessionmaker(engine, expire_on_commit=False)
    password_session = 'existing-ordinary-session'
    unrelated_session = 'other-admin-session'
    secret = base64.b32encode(secrets.token_bytes(20)).decode().rstrip('=')
    with DB.begin() as db:
        owner = User(id='10000000-0000-0000-0000-000000000001', email='owner@example.test',
                     name='Owner', password_hash='hash', role='admin', status='active')
        another = User(id='20000000-0000-0000-0000-000000000002', email='other@example.test',
                       name='Other', password_hash='hash', role='admin', status='active')
        db.add_all([owner, another])
        db.flush()
        db.add(Audit(actor=owner.name, target=owner.id, action='setup'))
        db.add(Gate(id='admin-guard'))
        db.add(TwoFactor(user_id=owner.id, enabled=True,
                         secret=cipher().encrypt(secret.encode()).decode()))
        db.add(Session(token_hash=digest(password_session), user_id=owner.id,
                       csrf='old-csrf', expires=now() + timedelta(days=1)))
        db.add(Session(token_hash=digest(unrelated_session), user_id=another.id,
                       csrf='other-csrf', expires=now() + timedelta(days=1)))
    app = FastAPI()

    def check_session(db, token, csrf='', mutation=False):
        session = db.get(Session, digest(token))
        if not session or session.expires < now():
            raise HTTPException(status_code=401)
        if mutation and csrf != session.csrf:
            raise HTTPException(status_code=403)
        user = db.get(User, session.user_id)
        return user, session

    install_bot_manager(app, DB, check_session, lambda *a, **kw: None,
                        lambda *a, **kw: None)

    with TestClient(app) as client:
        client.cookies.set('cf_session', unrelated_session)
        assert client.get('/api/auth/bots/guard').json()['owner'] is False
        assert client.get('/api/auth/bots').status_code == 403
        assert client.post('/api/auth/bots/unlock', json={'code':'000000'},
                           headers={'X-CSRF-Token':'other-csrf'}).status_code == 403

        client.cookies.set('cf_session', password_session)
        assert client.get('/api/auth/bots').status_code == 403
        assert client.post('/api/auth/bots',json={'name':'bot','image':IMAGE,'token':BOT_TOKEN},
                           headers={'X-CSRF-Token':'old-csrf'}).status_code == 403
        code = totp(secret, int(time.time()) // 30)
        opened = client.post('/api/auth/bots/unlock', json={'code':code},
                             headers={'X-CSRF-Token':'old-csrf'})
        assert opened.status_code == 200, opened.text
        assert opened.json()['expires_in'] == 600
        new_csrf = opened.json()['csrf']
        assert new_csrf != 'old-csrf'
        assert client.cookies.get('cf_session') != password_session
        assert client.get('/api/auth/bots').status_code == 200
        assert client.post('/api/auth/bots',json={'name':'bot','image':IMAGE,'token':BOT_TOKEN},
                           headers={'X-CSRF-Token':'old-csrf'}).status_code == 403

        created = client.post('/api/auth/bots',
                              json={'name':'Demo','image':IMAGE,'token':BOT_TOKEN},
                              headers={'X-CSRF-Token':new_csrf})
        assert created.status_code == 201,created.text
        assert BOT_TOKEN not in created.text
        bot_id = created.json()['id']
        assert len(client.get('/api/auth/bots').json()) == 1
        assert client.post('/api/auth/bots/'+bot_id+'/state',
                           json={'desired_state':'running'},
                           headers={'X-CSRF-Token':new_csrf}).status_code == 200
        assert client.get('/api/auth/bot-runner/state').status_code == 403

        with DB.begin() as db:
            current = db.get(Session, digest(client.cookies.get('cf_session')))
            current.bot_access_until = now() - timedelta(minutes=1)
        assert client.get('/api/auth/bots').status_code == 403
        assert client.delete('/api/auth/bots/'+bot_id,
                             headers={'X-CSRF-Token':new_csrf}).status_code == 403
    engine.dispose()
