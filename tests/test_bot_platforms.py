"""Platform, credential and runner contracts with real HTTP and encrypted storage."""
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from services.auth.bots import install_bot_manager
from services.auth.models import Base, User, Session, Audit, Gate, TwoFactor, Bot
from services.auth.security import cipher
from services.common.core import digest, now
from scripts import bot_runner as runner

IMAGE = 'ghcr.io/saitwors/testbot@sha256:' + 'a' * 64
TELEGRAM = '123456789:' + 'x' * 35
DISCORD = 'MTAxMjM0NTY3ODkwMTIzNDU2Nzg5.abCDef.' + 'y' * 38


@pytest.fixture
def workspace(monkeypatch):
    monkeypatch.setenv('INTERNAL_TOKEN', 'bot-platform-tests-at-least-forty-characters')
    monkeypatch.setenv('BOT_RUNNER_TOKEN', 'f' * 64)
    engine = create_engine('sqlite://', poolclass=StaticPool,
                           connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    DB = sessionmaker(engine, expire_on_commit=False)
    with DB.begin() as db:
        db.add(User(id='owner', email='owner@example.test', name='Owner',
                    password_hash='hash', role='admin', status='active'))
        db.add(Audit(actor='Owner', action='setup', target='owner'))
        db.add(Gate(id='admin-guard'))
        db.add(TwoFactor(user_id='owner', enabled=True))
        db.add(Session(token_hash=digest('session'), user_id='owner', csrf='csrf',
                       expires=now() + timedelta(days=1),
                       bot_access_until=now() + timedelta(minutes=10)))
    app = FastAPI()

    def check(db, token, csrf='', mutation=False):
        s = db.get(Session, digest(token))
        if not s:
            raise HTTPException(401)
        if mutation and csrf != s.csrf:
            raise HTTPException(403)
        return db.get(User, s.user_id), s

    install_bot_manager(app, DB, check, lambda *a: None, lambda *a: None)
    with TestClient(app) as client:
        client.cookies.set('cf_session', 'session')
        client.headers['X-CSRF-Token'] = 'csrf'
        yield client, DB
    engine.dispose()


def add(client, **overrides):
    return client.post('/api/auth/bots', json={
        'name': 'Test bot', 'image': IMAGE, 'token': TELEGRAM, **overrides})


def test_telegram_default_and_discord_secret_roundtrip(workspace):
    client, DB = workspace
    old = add(client)
    discord = add(client, platform='discord', token=DISCORD)
    assert old.status_code == 201
    assert discord.status_code == 201, discord.text
    assert old.json()['platform'] == 'telegram'
    bot_id = discord.json()['id']
    assert discord.json()['platform'] == 'discord'
    listing = client.get('/api/auth/bots')
    assert TELEGRAM not in listing.text and DISCORD not in listing.text
    with DB() as db:
        bot = db.get(Bot, bot_id)
        assert bot.token_cipher != DISCORD
        assert cipher().decrypt(bot.token_cipher.encode()).decode() == DISCORD
    internal = client.get('/api/auth/bot-runner/state', headers={'X-Bot-Runner-Token': 'f' * 64})
    assert internal.json()[1]['platform'] == 'discord'
    assert internal.json()[1]['token'] == DISCORD


@pytest.mark.parametrize('fields', [
    {'platform': 'shell'}, {'platform': 'discord', 'token': TELEGRAM},
    {'platform': 'telegram', 'token': DISCORD},
    {'platform': 'discord', 'token': 'x' * 30 + '\nBOT_TOKEN=bad'},
    {'name': '  '},
])
def test_invalid_bot_input_is_rejected(workspace, fields):
    assert add(workspace[0], **fields).status_code == 422


def test_platform_change_requires_new_token_and_clears_old_diagnostics(workspace):
    client, DB = workspace
    bot_id = add(client).json()['id']
    with DB.begin() as db:
        bot = db.get(Bot, bot_id)
        bot.last_logs = 'old diagnostics'
        bot.last_error = 'old error'
    payload = {'name': 'Discord bot', 'image': IMAGE, 'platform': 'discord'}
    assert client.put('/api/auth/bots/' + bot_id, json=payload).status_code == 422
    changed = client.put('/api/auth/bots/' + bot_id, json={**payload, 'token': DISCORD})
    assert changed.status_code == 200, changed.text
    assert changed.json()['last_logs'] == ''
    assert changed.json()['last_error'] == ''
    assert changed.json()['revision'] == 2


def test_reports_redact_token_and_ignore_old_revision_after_rotation(workspace):
    client, _ = workspace
    bot_id = add(client, platform='discord', token=DISCORD).json()['id']
    headers = {'X-Bot-Runner-Token': 'f' * 64}
    report = {'id': bot_id, 'observed_revision': 1, 'observed_state': 'error',
              'last_logs': 'secret=' + DISCORD, 'last_error': DISCORD}
    assert client.post('/api/auth/bot-runner/report', json=report, headers=headers).status_code == 200
    listing = client.get('/api/auth/bots').json()[0]
    assert DISCORD not in str(listing) and '[REDACTED]' in listing['last_logs']
    assert client.put('/api/auth/bots/' + bot_id, json={
        'name': 'Changed', 'image': IMAGE, 'platform': 'discord', 'token': 'z' * 60}).status_code == 200
    assert client.post('/api/auth/bot-runner/report', json=report, headers=headers).json()['ok'] is False
    current = client.get('/api/auth/bots').json()[0]
    assert current['last_logs'] == '' and DISCORD not in str(current)


def test_guard_has_expiry_and_manual_lock_revokes_all_bot_operations(workspace):
    client, _ = workspace
    bot_id = add(client).json()['id']
    guard = client.get('/api/auth/bots/guard').json()
    assert guard['access_expires_at'].endswith('Z')
    assert guard['server_time'].endswith('Z')
    assert guard['limits'] == {'max_bots': 3, 'memory_mb': 192, 'cpus': 0.35}
    assert client.post('/api/auth/bots/lock', json={}).status_code == 200
    assert client.get('/api/auth/bots/guard').json()['access_expires_at'] is None
    for method, path, body in [
        ('GET', '/api/auth/bots', None),
        ('POST', '/api/auth/bots', {'name': 'Bot', 'image': IMAGE, 'token': TELEGRAM}),
        ('POST', '/api/auth/bots/' + bot_id + '/restart', {}),
        ('DELETE', '/api/auth/bots/' + bot_id, None),
    ]:
        assert client.request(method, path, json=body).status_code == 403


def test_restart_creates_new_revision_and_keeps_platform_and_secret(workspace):
    client, _ = workspace
    bot_id = add(client, platform='discord', token=DISCORD).json()['id']
    restarted = client.post('/api/auth/bots/' + bot_id + '/restart', json={})
    assert restarted.status_code == 200
    assert restarted.json()['revision'] == 2
    assert restarted.json()['desired_state'] == 'running'
    assert restarted.json()['platform'] == 'discord'
    assert DISCORD not in restarted.text


def test_status_only_report_preserves_last_safe_logs_after_stop(workspace):
    client, _ = workspace
    bot_id = add(client).json()['id']
    headers = {'X-Bot-Runner-Token': 'f' * 64}
    base = {'id': bot_id, 'observed_revision': 1, 'observed_state': 'running'}
    client.post('/api/auth/bot-runner/report', json={**base, 'last_logs': 'Last safe output'}, headers=headers)
    changed = client.post('/api/auth/bots/' + bot_id + '/restart', json={}).json()
    assert changed['revision'] == 2
    client.post('/api/auth/bot-runner/report', json={**base, 'observed_revision': 2,
                'observed_state': 'stopped'}, headers=headers)
    assert client.get('/api/auth/bots').json()[0]['last_logs'] == 'Last safe output'


@pytest.mark.parametrize('platform,variable', [('telegram', 'TELEGRAM_BOT_TOKEN'), ('discord', 'DISCORD_TOKEN')])
def test_runner_injects_platform_token_without_exposing_command_line(monkeypatch, tmp_path, platform, variable):
    from pathlib import Path
    calls, files = [], []
    token = TELEGRAM if platform == 'telegram' else DISCORD
    bot = {'id': '10000000-0000-0000-0000-000000000001', 'revision': 1,
           'image': IMAGE, 'token': token, 'platform': platform}

    def docker(*args, **kw):
        calls.append(args)
        path = Path(args[args.index('--env-file') + 1])
        files.append((path, path.read_text(), path.stat().st_mode & 0o777))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(runner, 'docker', docker)
    mkstemp = runner.tempfile.mkstemp
    monkeypatch.setattr(runner.tempfile, 'mkstemp', lambda **kw: mkstemp(prefix=kw['prefix'], dir=tmp_path))
    runner.create(bot, 'cfbot-' + bot['id'], pre_pulled=True)
    assert f'{variable}={token}\n' in files[0][1]
    assert f'BOT_TOKEN={token}\n' in files[0][1]
    assert files[0][2] == 0o600 and not files[0][0].exists()
    assert token not in str(calls)
    assert '--read-only' in calls[0] and '--memory' in calls[0]


def test_runner_rejects_unknown_platform_before_docker(monkeypatch):
    monkeypatch.setattr(runner, 'details', lambda *a: pytest.fail('invalid platform reached Docker'))
    with pytest.raises(ValueError, match='platform'):
        runner.reconcile({'id': '10000000-0000-0000-0000-000000000001', 'image': IMAGE,
                          'token': TELEGRAM, 'revision': 1, 'desired_state': 'running', 'platform': 'shell'})


def test_stopped_rotated_bot_does_not_publish_old_container_secrets(monkeypatch):
    bot = {'id': '10000000-0000-0000-0000-000000000001', 'image': IMAGE,
           'token': DISCORD, 'revision': 2, 'desired_state': 'stopped', 'platform': 'discord'}
    reports, commands = [], []
    monkeypatch.setattr(runner, 'details', lambda name: {
        'running': False, 'labels': {'campus-flow.bot-managed': 'true',
                                    'campus-flow.bot-id': bot['id'], 'campus-flow.bot-revision': '1'}})
    def docker(*args, **kw):
        commands.append(args)
        return SimpleNamespace(returncode=0, stdout='old token=' + TELEGRAM, stderr='')
    monkeypatch.setattr(runner, 'docker', docker)
    monkeypatch.setattr(runner, 'request', lambda path, payload: reports.append(payload))
    runner.reconcile(bot)
    assert TELEGRAM not in str(reports)
    assert not any(c[0] == 'logs' for c in commands)
    assert reports[0]['observed_state'] == 'stopped'


def test_v4_migration_preserves_existing_bots_and_is_idempotent():
    from services.auth.bots import migrate_bot_platforms
    engine = create_engine('sqlite://')
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE managed_bots (id VARCHAR(36) PRIMARY KEY, token_cipher VARCHAR(512))'))
        conn.execute(text("INSERT INTO managed_bots VALUES ('old-bot', 'keep-encrypted-token')"))
        migrate_bot_platforms(conn)
        migrate_bot_platforms(conn)
        row = conn.execute(text('SELECT id, token_cipher, platform FROM managed_bots')).one()
        assert tuple(row) == ('old-bot', 'keep-encrypted-token', 'telegram')
    engine.dispose()
