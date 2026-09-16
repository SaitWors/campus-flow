import json
import logging
from contextlib import asynccontextmanager

from fastapi.testclient import TestClient
from sqlalchemy import text
from services.common.core import database, setup_app, request_logger


def test_public_version_and_request_logs_do_not_expose_request_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv('DATABASE_URL', 'sqlite:///'+str(tmp_path/'version.db'))
    monkeypatch.setenv('APP_COMMIT', 'a'*40)
    monkeypatch.setenv('APP_BUILD_DATE', '2026-09-16T00:00:00Z')
    monkeypatch.setenv('REQUEST_LOGGING', 'true')
    engine, _ = database('fixture')
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE schema_migrations(version INTEGER)'))
        conn.execute(text('INSERT INTO schema_migrations VALUES(3)'))
    @asynccontextmanager
    async def lifespan(_): yield
    app = setup_app('fixture', engine, lifespan)
    messages = []
    class Capture(logging.Handler):
        def emit(self, record): messages.append(record.getMessage())
    handler = Capture(); request_logger.addHandler(handler)
    try:
        with TestClient(app) as client:
            r = client.get('/version?password=PRIVATE_QUERY', headers={
                'Cookie':'secret=PRIVATE_COOKIE', 'Authorization':'Bearer PRIVATE_TOKEN',
                'X-Request-ID':'b'*32})
            assert r.json() == {'commit':'a'*40,'build':'2026-09-16T00:00:00Z','schema':3}
            assert r.headers['X-Request-ID'] == 'b'*32
            denied = client.post('/version', json={'code':'PRIVATE_CODE'}, headers={'Origin':'https://other.example'})
            assert denied.status_code == 403 and denied.headers['Cache-Control'] == 'no-store'
        assert messages and 'PRIVATE_' not in ''.join(messages)
        assert json.loads(messages[0])['route'] == '/version'
    finally:
        request_logger.removeHandler(handler)
        engine.dispose()
