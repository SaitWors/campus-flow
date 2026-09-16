"""Small shared infrastructure. Services never query each other's databases."""
import hashlib
import hmac
import os
import uuid
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def uid():
    return str(uuid.uuid4())


def digest(value: str):
    return hashlib.sha256(value.encode()).hexdigest()


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


def fail(code, status=400):
    raise HTTPException(status_code=status, detail=code)


def database(service):
    url = os.getenv('DATABASE_URL', f'sqlite:///./{service}.db')
    options = {'pool_pre_ping': True}
    if url.startswith('sqlite'):
        options['connect_args'] = {'check_same_thread': False, 'timeout': 30}
    else:
        options.update(pool_size=env_int('DB_POOL_SIZE', 5, 1, 30),
                       max_overflow=env_int('DB_MAX_OVERFLOW', 10, 0, 30),
                       pool_timeout=env_int('DB_POOL_TIMEOUT', 10, 1, 120),
                       pool_recycle=env_int('DB_POOL_RECYCLE', 1800, 30, 86400))
        ping = os.getenv('DB_POOL_PRE_PING', 'true').lower()
        if ping not in ('true', 'false'):
            raise RuntimeError('DB_POOL_PRE_PING must be true or false')
        options['pool_pre_ping'] = ping == 'true'
    engine = create_engine(url, **options)
    if engine.dialect.name == 'sqlite':
        @event.listens_for(engine, 'connect')
        def connect(dbapi, _):
            dbapi.isolation_level = None
            dbapi.execute('PRAGMA foreign_keys=ON')
            dbapi.execute('PRAGMA busy_timeout=30000')
        @event.listens_for(engine, 'begin')
        def begin(conn):
            # SQLite development/test mode serializes writers explicitly.
            conn.exec_driver_sql('BEGIN IMMEDIATE')
    return engine, sessionmaker(engine, expire_on_commit=False)


def env_int(name, default, minimum, maximum):
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        raise RuntimeError(f'{name} must be an integer') from None
    if not minimum <= value <= maximum:
        raise RuntimeError(f'{name} must be between {minimum} and {maximum}')
    return value


def migrate(engine, base, migrations=()):
    with engine.begin() as conn:
        if engine.dialect.name == 'postgresql':
            conn.execute(text('SELECT pg_advisory_xact_lock(7402302)'))
        conn.execute(text('CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TIMESTAMP NOT NULL)'))
        version = conn.execute(text('SELECT MAX(version) FROM schema_migrations')).scalar() or 0
        if version > 1 + len(migrations):
            raise RuntimeError('Database schema is newer than this application. Restore the matching application version.')
        if version < 1:
            base.metadata.create_all(conn)
            conn.execute(text('INSERT INTO schema_migrations(version, applied_at) VALUES (1, :at)'), {'at': now()})


        for target, migration in enumerate(migrations, start=2):
            if version < target:
                migration(conn)
                conn.execute(text('INSERT INTO schema_migrations(version, applied_at) VALUES (:version, :at)'), {'version': target, 'at': now()})


def service_secret():
    secret = os.getenv('INTERNAL_TOKEN', '')
    if len(secret) < 32:
        raise RuntimeError('Run setup first: INTERNAL_TOKEN must have at least 32 characters')
    return secret


def internal(request: Request):
    if not hmac.compare_digest(request.headers.get('X-Internal-Token', ''), service_secret()):
        fail('forbidden', 403)


def remote(base, path, **kwargs):
    try:
        # These URLs address only our private service network, never the Internet.
        with httpx.Client(timeout=httpx.Timeout(5, connect=2), trust_env=False) as client:
            response = client.request(kwargs.pop('method', 'GET'), base + path, headers={'X-Internal-Token': service_secret()}, **kwargs)
        if response.status_code >= 400:
            if response.status_code in (401, 403, 404, 409, 422):
                try:
                    detail = response.json().get('detail', 'upstream_error')
                except ValueError:
                    detail = 'upstream_error'
                fail(detail, response.status_code)
            fail('service_unavailable', 503)
        return response.json()
    except httpx.HTTPError:
        fail('service_unavailable', 503)


def identity(request: Request):
    return remote(os.getenv('AUTH_URL', 'http://auth:8000'), '/internal/verify', method='POST', json={
        'token': request.cookies.get('cf_session', ''),
        'csrf': request.headers.get('X-CSRF-Token', ''),
        'mutation': request.method not in ('GET', 'HEAD', 'OPTIONS'),
    })


def manager(user):
    if user['role'] not in ('admin', 'head', 'deputy'):
        fail('forbidden', 403)


def setup_app(title, engine, lifespan):
    app = FastAPI(title=title, version='1.0.0', lifespan=lifespan, docs_url='/docs', redoc_url=None)

    @app.middleware('http')
    async def boundary(request: Request, call_next):
        request_id = str(uuid.uuid4())
        if request.method not in ('GET', 'HEAD', 'OPTIONS') and not request.url.path.startswith('/internal/'):
            origin = request.headers.get('origin')
            allowed = [v.strip().rstrip('/') for v in os.getenv('APP_ORIGIN', 'http://localhost:8080').split(',')]
            if origin and origin.rstrip('/') not in allowed:
                return JSONResponse({'detail': 'origin_forbidden'}, status_code=403)
            if request.headers.get('sec-fetch-site') == 'cross-site':
                return JSONResponse({'detail': 'origin_forbidden'}, status_code=403)
            if request.headers.get('content-length', '0').isdigit() and int(request.headers.get('content-length', '0')) > 65536:
                return JSONResponse({'detail': 'body_too_large'}, status_code=413)
        response = await call_next(request)
        response.headers['X-Request-ID'] = request_id
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    @app.get('/health')
    def health():
        with engine.connect() as conn:
            conn.execute(text('SELECT 1'))
        return {'status': 'ok', 'service': title, 'version': '1.0.0'}

    return app
