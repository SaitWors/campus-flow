import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from services.common.core import database
from services.auth.passwords import BoundedPasswordHasher


def test_postgres_pool_applies_environment_without_connecting(monkeypatch):
    monkeypatch.setenv('DATABASE_URL', 'postgresql+psycopg://fixture:fixture@localhost/fixture')
    for key, value in {'SIZE':'2', 'TIMEOUT':'10', 'RECYCLE':'1800', 'PRE_PING':'true'}.items():
        monkeypatch.setenv('DB_POOL_'+key, value)
    monkeypatch.setenv('DB_MAX_OVERFLOW', '2')
    engine, _ = database('fixture')
    assert engine.pool.size() == 2
    assert engine.pool._max_overflow == 2
    assert engine.pool.timeout() == 10
    assert engine.pool._recycle == 1800 and engine.pool._pre_ping
    engine.dispose()
    monkeypatch.setenv('DB_MAX_OVERFLOW', '-1')
    with pytest.raises(RuntimeError, match='DB_MAX_OVERFLOW'):
        database('fixture')


def test_sqlite_keeps_local_transaction_support(tmp_path, monkeypatch):
    monkeypatch.setenv('DATABASE_URL', 'sqlite:///'+str(tmp_path/'test.db'))
    monkeypatch.setenv('DB_POOL_SIZE', 'invalid-but-not-used-by-sqlite')
    engine, _ = database('fixture')
    with engine.begin() as connection:
        assert connection.exec_driver_sql('PRAGMA foreign_keys').scalar() == 1
    engine.dispose()


def test_argon_memory_gate_serializes_hash_and_verify_and_releases_errors(monkeypatch):
    monkeypatch.setenv('AUTH_HASH_CONCURRENCY', '1')
    entered = threading.Event(); release = threading.Event(); second = threading.Event()
    class Probe:
        def hash(self, password):
            entered.set()
            assert release.wait(3)
            raise ValueError('fixture failure')
        def verify(self, encoded, password):
            second.set()
            return True
    hasher = BoundedPasswordHasher(Probe())
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(hasher.hash, 'fixture')
        assert entered.wait(3)
        other = pool.submit(hasher.verify, 'encoded', 'fixture')
        assert not second.wait(.05)
        release.set()
        with pytest.raises(ValueError): first.result(timeout=3)
        assert other.result(timeout=3)


def production_config():
    services = {}
    for name in ['auth','schedule','notifications','web','postgres']:
        services[name] = {'image':'campus-'+name+':'+'a'*40, 'mem_limit':160*1024**2,
            'cpus':.5, 'pids_limit':96, 'user':'10001:10001', 'read_only':True,
            'logging':{'driver':'json-file','options':{'max-size':'10m','max-file':'3'}}}
    services['web']['ports'] = [{'host_ip':'127.0.0.1','target':8080}]
    services['postgres']['environment'] = {key:'p'+str(i)*32 for i,key in enumerate([
        'POSTGRES_PASSWORD','AUTH_DB_PASSWORD','SCHEDULE_DB_PASSWORD','NOTIFICATIONS_DB_PASSWORD'])}
    for name in ['auth','schedule','notifications']:
        services[name]['environment'] = {'DB_POOL_SIZE':'2','DB_MAX_OVERFLOW':'2','DB_POOL_PRE_PING':'true'}
    services['auth']['environment'].update({'APP_ORIGIN':'https://campus.example','COOKIE_SECURE':'true',
        'INTERNAL_TOKEN':'a'*32,'SETUP_KEY':'b'*32})
    services['schedule']['environment']['TRANSLATION_WORKER'] = 'false'
    return {'services':services}


def test_production_requires_private_persistent_material_storage():
    from scripts import production as p
    config = production_config()
    with pytest.raises(RuntimeError, match='material'):
        p.validate_config(config)
    config['services']['schedule']['environment']['MATERIALS_DIR'] = '/var/lib/campus/materials'
    config['services']['schedule']['volumes'] = [{'type':'volume','source':'fixture_materials',
        'target':'/var/lib/campus/materials','read_only':False}]
    assert p.validate_config(config) == 'a'*40
    config['services']['auth']['volumes'] = [{'type':'volume','source':'fixture_materials',
        'target':'/private','read_only':True}]
    with pytest.raises(RuntimeError, match='material'):
        p.validate_config(config)


@pytest.mark.parametrize('field,value', [('read_only',False),('user','0:0')])
def test_material_writer_keeps_container_readonly_and_unprivileged(field, value):
    from scripts import production as p
    config = production_config(); schedule = config['services']['schedule']
    schedule['environment']['MATERIALS_DIR'] = '/var/lib/campus/materials'
    schedule['volumes'] = [{'type':'volume','source':'fixture_materials','target':'/var/lib/campus/materials','read_only':False}]
    schedule[field] = value
    with pytest.raises(RuntimeError, match='material'):
        p.validate_config(config)
