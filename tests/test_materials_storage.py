"""Storage failure paths and upgrades using a real database and filesystem."""
from pathlib import Path
from types import SimpleNamespace
import asyncio
import threading
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import MetaData, event, select, text
from starlette.requests import Request

from services.common.core import database, migrate


@pytest.fixture
def stored_materials(tmp_path,monkeypatch):
    monkeypatch.setenv('DATABASE_URL','sqlite:///'+str(tmp_path/'schedule.db'))
    monkeypatch.setenv('INTERNAL_TOKEN','material-test-internal-token-at-least32')
    monkeypatch.setenv('TRANSLATION_WORKER','false')
    monkeypatch.setenv('MATERIALS_DIR',str(tmp_path/'files'))
    from services.schedule import main as m, materials as files
    from services.schedule.models import Subject
    engine,DB=database('schedule')
    monkeypatch.setattr(m,'engine',engine);monkeypatch.setattr(m,'DB',DB)
    monkeypatch.setattr(m,'translator',m.TitleTranslator(DB))
    monkeypatch.setattr(files,'identity',lambda request:{'id':'manager','name':'Manager','role':'head','subgroup':1})
    with TestClient(m.app,raise_server_exceptions=False) as client:
        with DB.begin() as db:db.add(Subject(key='a'*64,title='Test subject',title_en=''))
        yield m,client,tmp_path/'files'
    engine.dispose()


def create(client):
    return client.post('/api/schedule/subjects/'+'a'*64+'/materials',
        params={'filename':'notes.txt','title':'Private notes','category':'notes'},content=b'Private notes')


def test_failed_commit_removes_published_file_and_temporary_bytes(stored_materials):
    m,client,root=stored_materials
    def reject(session):raise RuntimeError('Injected transaction failure')
    event.listen(m.DB.class_,'before_commit',reject)
    try:assert create(client).status_code==500
    finally:event.remove(m.DB.class_,'before_commit',reject)
    assert list(root.iterdir())==[]
    assert client.get('/api/schedule/subjects/'+'a'*64+'/materials').json()['total']==0
    assert create(client).status_code==201


def test_symlink_cannot_serve_a_file_outside_storage(stored_materials,tmp_path):
    _,client,root=stored_materials
    item=create(client).json();file=root/(item['id']+'.blob')
    outside=tmp_path/'private-config';outside.write_bytes(b'Private notes')
    file.unlink();file.symlink_to(outside)
    assert client.get('/api/schedule/materials/'+item['id']+'/file').status_code==404
    assert client.get('/api/schedule/materials/not-a-uuid/file').status_code==404
    assert client.delete('/api/schedule/materials/'+item['id']+'?revision=1').status_code==200
    assert outside.read_bytes()==b'Private notes'


def test_schema4_upgrade_is_idempotent_and_keeps_assignment_data(tmp_path,monkeypatch):
    monkeypatch.setenv('DATABASE_URL','sqlite:///'+str(tmp_path/'schema4.db'))
    monkeypatch.setenv('INTERNAL_TOKEN','material-migration-internal-token32chars')
    monkeypatch.setenv('TRANSLATION_WORKER','false')
    monkeypatch.setenv('MATERIALS_DIR',str(tmp_path/'files'))
    from services.schedule import main as m
    from services.schedule.models import Base,Subject,Assignment
    engine,DB=database('schedule');legacy=MetaData()
    for name,table in Base.metadata.tables.items():
        if name not in ('materials','material_storage'):table.to_metadata(legacy)
    migrate(engine,SimpleNamespace(metadata=legacy),(lambda _:None,lambda _:None,lambda _:None))
    with DB.begin() as db:
        db.add(Subject(key='a'*64,title='Existing subject',title_en=''));db.flush()
        db.add(Assignment(id='existing',subject_key='a'*64,title='Existing assignment',subgroup=0))
    monkeypatch.setattr(m,'engine',engine);monkeypatch.setattr(m,'DB',DB)
    monkeypatch.setattr(m,'translator',m.TitleTranslator(DB))
    for _ in range(2):
        with TestClient(m.app):pass
    with DB() as db:
        assert db.scalar(text('SELECT MAX(version) FROM schema_migrations'))==5
        assert db.get(Assignment,'existing').title=='Existing assignment'
        assert db.scalar(text('SELECT COUNT(*) FROM material_storage'))==1
        assert db.scalar(text('SELECT COUNT(*) FROM materials'))==0
    engine.dispose()


def test_materials_persist_across_service_startups(stored_materials):
    m,client,_=stored_materials
    item=create(client).json()
    from services.schedule.materials import initialize_materials
    initialize_materials(m.app)
    response=client.get('/api/schedule/materials/'+item['id']+'/file')
    assert response.status_code==200 and response.content==b'Private notes'


def test_custom_file_limit_preserves_material_error_code(stored_materials,monkeypatch):
    _,client,_=stored_materials
    monkeypatch.setenv('MATERIAL_MAX_FILE_BYTES','65536')
    response=client.post('/api/schedule/subjects/'+'a'*64+'/materials',
        params={'filename':'notes.txt','title':'Too large'},content=b'x'*65537)
    assert response.status_code==413
    assert response.json()['detail']=='material_too_large'


def test_after_commit_exception_does_not_delete_the_committed_file(stored_materials):
    m,client,root=stored_materials
    def reject(session):raise RuntimeError('Injected post-commit exception')
    event.listen(m.DB.class_,'after_commit',reject)
    try:assert create(client).status_code==500
    finally:event.remove(m.DB.class_,'after_commit',reject)
    listed=client.get('/api/schedule/subjects/'+'a'*64+'/materials').json()
    assert listed['total']==1
    item=listed['items'][0]
    assert (root/(item['id']+'.blob')).read_bytes()==b'Private notes'
    assert client.get('/api/schedule/materials/'+item['id']+'/file').status_code==200


def test_cancelled_request_cannot_remove_a_file_owned_by_commit_worker(stored_materials):
    m,_,root=stored_materials
    entered,released,committed=threading.Event(),threading.Event(),threading.Event()
    def pause(session):entered.set();assert released.wait(10)
    def done(session):committed.set()
    event.listen(m.DB.class_,'before_commit',pause);event.listen(m.DB.class_,'after_commit',done)
    endpoint=next(route.endpoint for route in m.app.routes if getattr(route,'path','')=='/api/schedule/subjects/{key}/materials' and 'POST' in getattr(route,'methods',[]))
    async def receive():return {'type':'http.request','body':b'Private notes','more_body':False}
    request=Request({'type':'http','method':'POST','path':'/api/schedule/subjects/'+'a'*64+'/materials','headers':[]},receive)
    async def invoke():
        task=asyncio.create_task(endpoint('a'*64,request,filename='notes.txt',title='Cancelled notes',description='',category='notes'))
        assert await asyncio.to_thread(entered.wait,10)
        task.cancel()
        await asyncio.sleep(.02)
        released.set()
        try:await task
        except asyncio.CancelledError:pass
        assert await asyncio.to_thread(committed.wait,10)
    try:asyncio.run(invoke())
    finally:
        released.set();event.remove(m.DB.class_,'before_commit',pause);event.remove(m.DB.class_,'after_commit',done)
    from services.schedule.models import Material
    with m.DB() as db:
        item=db.scalar(select(Material))
        assert item is not None
        assert (root/(item.id+'.blob')).read_bytes()==b'Private notes'


def test_startup_reclaims_interrupted_parts_and_unreferenced_blobs(stored_materials):
    m,client,root=stored_materials
    referenced=create(client).json()
    part=root/'00000000-0000-0000-0000-000000000011.part';part.write_bytes(b'interrupted')
    orphan=root/'00000000-0000-0000-0000-000000000012.blob';orphan.write_bytes(b'uncommitted')
    from services.schedule.materials import initialize_materials
    initialize_materials(m.app,m.DB)
    assert not part.exists() and not orphan.exists()
    assert (root/(referenced['id']+'.blob')).read_bytes()==b'Private notes'


def test_startup_skips_a_different_active_publisher(stored_materials):
    m,_,root=stored_materials
    identifier='00000000-0000-0000-0000-000000000014'
    part=root/(identifier+'.part');part.write_bytes(b'Uploading bytes')
    from services.schedule.materials import acquire_file_lock,release_file_lock,initialize_materials
    fd=acquire_file_lock(root,identifier)
    try:
        initialize_materials(m.app,m.DB)
        assert part.read_bytes()==b'Uploading bytes'
    finally:release_file_lock(root,identifier,fd)
    initialize_materials(m.app,m.DB)
    assert not part.exists()
