import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from scripts import production as p


def fixture_backup(folder, token='test-internal-key'):
    folder.mkdir()
    files = {}
    for name in [*(s+'.dump' for s in p.SERVICES), 'config.env', 'effective-config.json']:
        path = folder/name; path.write_text('synthetic fixture '+name)
        files[name] = {'sha256':p.sha256(path), 'bytes':path.stat().st_size}
    p.private_json(folder/'manifest.json', {'format':1, 'schema_versions':p.SCHEMAS,
        'files':files, 'internal_token_sha256':hashlib.sha256(token.encode()).hexdigest()})
    (folder/'COMPLETE').touch()
    return folder


def test_corrupt_restore_is_rejected_before_stopping_or_touching_data(tmp_path, monkeypatch):
    folder = fixture_backup(tmp_path/'backup')
    (folder/'auth.dump').write_text('corrupted')
    def forbidden(*a, **kw): pytest.fail('A mutation was attempted before backup validation')
    stack = SimpleNamespace(token=lambda:'test-internal-key', dc=forbidden, psql=forbidden)
    monkeypatch.setattr(p, 'backup', forbidden)
    monkeypatch.setattr(p, 'confirm', forbidden)
    with pytest.raises(RuntimeError, match='checksum mismatch'): p.restore(stack, folder)


def test_wrong_totp_key_and_newer_schema_are_rejected(tmp_path):
    folder = fixture_backup(tmp_path/'backup')
    with pytest.raises(RuntimeError, match='original INTERNAL_TOKEN'):
        p.verify_backup(folder, 'different-key')
    manifest = json.loads((folder/'manifest.json').read_text())
    manifest['schema_versions']['auth'] = 4
    p.private_json(folder/'manifest.json', manifest)
    with pytest.raises(RuntimeError, match='incompatible'):
        p.verify_backup(folder, 'test-internal-key')


def test_backup_failure_resumes_only_previously_running_containers(tmp_path, monkeypatch):
    monkeypatch.setattr(p, 'ROOT', tmp_path)
    env = tmp_path/'config'; env.write_text('synthetic config')
    calls = []
    def broken(*a, **kw): raise RuntimeError('fixture dump failure')
    stack = SimpleNamespace(check_live_secret=lambda:None, running_apps=lambda:['auth','web'],
        psql=lambda *a, **kw:'1024', dc=lambda *a, **kw:calls.append(a), env_file=env,
        config={}, snapshot=broken)
    with pytest.raises(RuntimeError, match='fixture dump failure'): p.backup(stack, resume=False)
    assert calls == [('stop', *p.APPS), ('start', 'auth', 'web')]
    assert not list((tmp_path/'backups').glob('*/COMPLETE'))


def test_snapshot_verification_detects_changed_content_even_with_same_count():
    before = {'schema':2, 'tables':{'users':{'count':1,'digest':'original','columns':['id','password_hash']}}}
    after = {'schema':3, 'tables':{'users':{'count':1,'digest':'changed','columns':['id','password_hash']}}}
    with pytest.raises(RuntimeError, match='users'): p.compare_snapshots(before, after, upgraded=True)
    after['tables'] = before['tables']
    p.compare_snapshots(before, after, upgraded=True)


def test_rollback_rejects_incompatible_image_before_any_database_write(monkeypatch):
    stack = SimpleNamespace(tag='a'*40, config={'services':{'auth':{'image':'fixture'}}})
    image = {'Config':{'Labels':{'org.opencontainers.image.revision':'a'*40,
        'io.campus-flow.schemas':json.dumps({s:2 for s in p.SERVICES})}}}
    monkeypatch.setattr(p, 'run', lambda *a, **kw:json.dumps([image]))
    with pytest.raises(RuntimeError, match='incompatible'): p.inspect_images(stack, schemas=p.SCHEMAS)


def test_saved_release_preserves_secrets_and_private_permissions(tmp_path):
    env = tmp_path/'config.env'; env.write_text('INTERNAL_TOKEN=unchanged\nIMAGE_TAG='+('a'*40)+'\nSETUP_KEY=unchanged-too\n'); env.chmod(0o600)
    stack = SimpleNamespace(env_file=env, env_digest=p.sha256(env), tag='b'*40)
    p.persist_release(stack)
    assert env.read_text() == 'INTERNAL_TOKEN=unchanged\nIMAGE_TAG='+('b'*40)+'\nSETUP_KEY=unchanged-too\n'
    assert env.stat().st_mode & 0o777 == 0o600
    assert stack.env_digest == p.sha256(env)
    env.write_text('operator changed the file')
    with pytest.raises(RuntimeError, match='changed during deployment'): p.persist_release(stack)
    assert env.read_text() == 'operator changed the file'


def test_concurrent_operations_are_rejected_and_lock_is_released(tmp_path):
    stack = SimpleNamespace(state=tmp_path, project='fixture')
    with p.operation_lock(stack):
        with pytest.raises(RuntimeError, match='already running'):
            with p.operation_lock(stack): pytest.fail('A second writer acquired the lock')
    with p.operation_lock(stack): pass
