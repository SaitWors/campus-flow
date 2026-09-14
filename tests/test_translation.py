import json
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import MetaData, inspect, text

from services.common.core import database, migrate, now
from services.schedule.models import Base, Rule, Occurrence, TitleTranslation
from services.schedule.translation import TitleTranslator, request_translation, with_translation, TRANSLATE_URL

@pytest.fixture
def db_factory(tmp_path, monkeypatch):
    monkeypatch.setenv('DATABASE_URL', 'sqlite:///' + str(tmp_path/'translation.db'))
    engine, db = database('schedule')
    migrate(engine, Base, (lambda conn: TitleTranslation.__table__.create(conn, checkfirst=True),))
    yield engine, db
    engine.dispose()

def test_migrate_existing_v1_preserves_data(tmp_path, monkeypatch):
    monkeypatch.setenv('DATABASE_URL', 'sqlite:///' + str(tmp_path/'legacy.db'))
    engine, db_factory = database('schedule')
    legacy = MetaData()
    for name, table in Base.metadata.tables.items():
        if name != 'title_translations':
            table.to_metadata(legacy)
    migrate(engine, SimpleNamespace(metadata=legacy))
    with db_factory.begin() as db:
        db.add(Rule(id='existing', data={'title':'Базы данных','title_en':''}, revision=7))
    for _ in range(2):
        migrate(engine, Base, (lambda conn: TitleTranslation.__table__.create(conn, checkfirst=True),))
    assert 'title_translations' in inspect(engine).get_table_names()
    with db_factory() as db:
        assert db.get(Rule, 'existing').revision == 7
        assert db.scalar(text('SELECT MAX(version) FROM schema_migrations')) == 2
    with pytest.raises(RuntimeError):
        migrate(engine, Base)
    engine.dispose()

def test_auto_manual_rename_and_no_queue_side_effects(db_factory):
    _, db_factory = db_factory
    calls = []
    worker = TitleTranslator(db_factory, lambda title: calls.append(title) or 'Database systems')
    with db_factory.begin() as db:
        db.add(Rule(id='r', data={'title':'Базы данных','title_en':''}, revision=4))
        db.add(Occurrence(id='o',rule_id='r',original_date='2026-09-14',date='2026-09-14',
                          data={'title':'Базы данных','title_en':''},revision=8,overridden=False))
        db.add(Rule(id='manual',data={'title':'Особый курс','title_en':'My own wording'},revision=1))
    worker.fill_missing()
    worker.fill_missing()
    assert calls == ['Базы данных']
    with db_factory() as db:
        item = db.get(Occurrence, 'o')
        result = with_translation(item.data, db)
        assert result['title_en'] == ''
        assert result['title_en_auto'] == 'Database systems'
        assert not result['translation_pending']
        assert (item.id, item.date, item.revision, item.overridden) == ('o','2026-09-14',8,False)
        assert db.get(Rule, 'r').revision == 4
        assert not list(db.scalars(text('SELECT id FROM events')))
        manual = with_translation({'title':'Базы данных','title_en':'Custom title'},db)
        assert manual['title_en'] == 'Custom title' and manual['title_en_auto'] == ''
        renamed = with_translation({'title':'Новое название','title_en':''},db)
        assert renamed['translation_pending'] and not renamed['title_en_auto']
    assert TitleTranslator(db_factory,lambda _: pytest.fail('Cache miss')).resolve('Базы данных') == 'Database systems'

def test_failure_cooldown_and_recovery(db_factory):
    _, db_factory = db_factory
    calls = []
    def fail_request(title):
        calls.append(title)
        raise httpx.ConnectError('offline')
    worker = TitleTranslator(db_factory,fail_request)
    assert worker.resolve('Базы данных') == ''
    assert worker.resolve('Базы данных') == ''
    assert len(calls) == 1
    with db_factory.begin() as db:
        db.get(TitleTranslation,'Базы данных').updated_at=now()-timedelta(seconds=61)
    worker.translate=lambda _: 'Databases'
    assert worker.resolve('Базы данных') == 'Databases'
    worker.lock.acquire()
    try:
        assert worker.resolve('Другое название') == ''
    finally:
        worker.lock.release()

def test_preview_rate_limit(db_factory):
    worker=TitleTranslator(db_factory[1])
    for _ in range(20):
        worker.limit_preview('admin')
    with pytest.raises(HTTPException) as error:
        worker.limit_preview('admin')
    assert error.value.status_code == 429
    worker.limit_preview('deputy')

def test_request_is_fixed_private_text_only(monkeypatch):
    real_client=httpx.Client
    def handle(request):
        assert str(request.url)==TRANSLATE_URL
        assert json.loads(request.content)=={'q':'Базы данных','source':'ru','target':'en','format':'text'}
        assert not any(k in request.headers for k in ('cookie','authorization','x-csrf-token','x-internal-token'))
        return httpx.Response(200,json={'translatedText':'Databases'})
    def client(**kwargs):
        assert kwargs['trust_env'] is False and kwargs['follow_redirects'] is False
        return real_client(**kwargs,transport=httpx.MockTransport(handle))
    monkeypatch.setattr(httpx,'Client',client)
    assert request_translation('Базы данных')=='Databases'

@pytest.mark.parametrize('status,payload', [
    (307, {'translatedText':'Redirect'}),
    (200, {'translatedText':123}),
    (200, {'translatedText':'x'*121}),
    (200, {'translatedText':'Базы данных'}),
    (200, {'translatedText':'x'*5000}),
    (200, ['not an object']),
])
def test_untrusted_provider_response_is_rejected(monkeypatch,status,payload):
    real_client=httpx.Client
    monkeypatch.setattr(httpx,'Client',lambda **kw: real_client(**kw,transport=httpx.MockTransport(
        lambda request:httpx.Response(status,json=payload,headers={'location':'https://example.invalid/'}))))
    with pytest.raises((ValueError,httpx.HTTPError)):
        request_translation('Базы данных')
