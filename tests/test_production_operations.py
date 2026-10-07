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
    p.private_json(folder/'manifest.json', {'format':1, 'schema_versions':{**p.SCHEMAS, 'schedule':4},
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


def test_old_four_database_backup_is_verified_before_retired_data_is_ignored(tmp_path):
    folder = fixture_backup(tmp_path/'legacy')
    manifest = json.loads((folder/'manifest.json').read_text())
    old = folder/'queue.dump'
    old.write_text('Retired history retained in the original backup')
    manifest['schema_versions']['queue'] = 2
    manifest['files']['queue.dump'] = {'sha256':p.sha256(old), 'bytes':old.stat().st_size}
    p.private_json(folder/'manifest.json', manifest)
    assert p.verify_backup(folder, 'test-internal-key')['schema_versions']['queue'] == 2
    old.write_text('corrupted')
    with pytest.raises(RuntimeError, match='checksum mismatch'):
        p.verify_backup(folder, 'test-internal-key')


@pytest.mark.parametrize('legacy', [False, True])
def test_backup_failure_resumes_only_previously_running_containers(tmp_path, monkeypatch, legacy):
    monkeypatch.setattr(p, 'ROOT', tmp_path)
    env = tmp_path/'config'; env.write_text('synthetic config')
    calls = []
    def broken(*a, **kw): raise RuntimeError('fixture dump failure')
    active = ['auth','web'] + (['queue'] if legacy else [])
    stack = SimpleNamespace(check_live_secret=lambda:None, running_apps=lambda:active,
        psql=lambda *a, **kw:'1024', dc=lambda *a, **kw:calls.append(a), env_file=env,
        config={'services':{'queue':{}}} if legacy else {}, legacy=legacy, snapshot=broken)
    with pytest.raises(RuntimeError, match='fixture dump failure'): p.backup(stack, resume=False)
    assert calls == [('stop', *p.APPS, *(['queue'] if legacy else [])), ('start', *active)]
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


def material_archive(folder, *, name='12345678-1234-4234-8234-123456789abc.blob', content=b'lecture bytes'):
    import io
    import tarfile
    path = folder/'materials.tar'
    with tarfile.open(path, 'w') as stream:
        entry = tarfile.TarInfo(name); entry.size = len(content)
        stream.addfile(entry, io.BytesIO(content))
    return path


def add_materials(folder, *, schema=5, name='12345678-1234-4234-8234-123456789abc.blob'):
    archive = material_archive(folder, name=name)
    manifest = json.loads((folder/'manifest.json').read_text())
    manifest['schema_versions']['schedule'] = schema
    manifest['files']['materials.tar'] = {'sha256':p.sha256(archive), 'bytes':archive.stat().st_size}
    manifest['materials'] = {name:{'sha256':hashlib.sha256(b'lecture bytes').hexdigest(), 'bytes':len(b'lecture bytes')}}
    p.private_json(folder/'manifest.json', manifest)
    return manifest


def test_schema_five_requires_material_archive_before_any_restore_mutation(tmp_path, monkeypatch):
    folder = fixture_backup(tmp_path/'backup')
    manifest = json.loads((folder/'manifest.json').read_text())
    manifest['schema_versions']['schedule'] = 5
    p.private_json(folder/'manifest.json', manifest)
    def forbidden(*a, **kw): pytest.fail('Restore mutated state before validating the archive')
    stack = SimpleNamespace(token=lambda:'test-internal-key', dc=forbidden, psql=forbidden)
    monkeypatch.setattr(p, 'inspect_images', forbidden)
    with pytest.raises(RuntimeError, match='materials'):
        p.restore(stack, folder)


def test_valid_material_archive_is_verified_and_archive_tampering_is_rejected(tmp_path):
    folder = fixture_backup(tmp_path/'backup'); add_materials(folder)
    assert p.verify_backup(folder, 'test-internal-key')['materials']
    (folder/'materials.tar').write_bytes(b'changed archive')
    with pytest.raises(RuntimeError, match='checksum mismatch.*materials'):
        p.verify_backup(folder, 'test-internal-key')


@pytest.mark.parametrize('name', ['../escape.blob','/tmp/escape.blob','nested/12345678-1234-4234-8234-123456789abc.blob'])
def test_tar_traversal_is_rejected_before_any_restore_mutation_even_with_updated_archive_checksum(tmp_path, monkeypatch, name):
    folder = fixture_backup(tmp_path/'backup'); add_materials(folder, name=name)
    def forbidden(*a, **kw): pytest.fail('Untrusted archive reached a mutation')
    monkeypatch.setattr(p, 'inspect_images', forbidden)
    stack = SimpleNamespace(token=lambda:'test-internal-key', dc=forbidden, psql=forbidden)
    with pytest.raises(RuntimeError, match='material.*(member|path|filename)'):
        p.restore(stack, folder)
    assert not (tmp_path/'escape.blob').exists()


def test_pre_materials_backup_remains_supported_but_newer_image_schema_is_five(tmp_path):
    folder = fixture_backup(tmp_path/'old')
    manifest = json.loads((folder/'manifest.json').read_text())
    manifest['schema_versions']['schedule'] = 4
    p.private_json(folder/'manifest.json', manifest)
    assert p.SCHEMAS == {'auth':3,'schedule':5,'notifications':3}
    assert p.verify_backup(folder, 'test-internal-key')['schema_versions']['schedule'] == 4


def test_material_archive_roundtrip_publishes_only_complete_files_and_clears_previous_snapshot(tmp_path):
    from scripts import materials_archive as a
    source = tmp_path/'source'; source.mkdir()
    name = '12345678-1234-4234-8234-123456789abc.blob'
    data = b'A'*(1024*1024+17); (source/name).write_bytes(data)
    (source/'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa.part').write_bytes(b'partial upload')
    (source/'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa.lock').touch()
    archive = tmp_path/'materials.tar'
    with archive.open('wb') as output: inventory = a.create_archive(source, output)
    assert a.validate_archive(archive, inventory) == inventory
    current = tmp_path/'current'; current.mkdir()
    old = current/'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb.blob'; old.write_bytes(b'old point')
    stage = a.stage_archive(current, archive, inventory)
    assert old.read_bytes() == b'old point' and not (current/name).exists()
    a.publish_stage(current, stage)
    assert (current/name).read_bytes() == data and not old.exists()
    assert a.storage_inventory(current) == {'files':1, 'bytes':len(data)}
    assert list(current.glob('.recovered-*/'+old.name))[0].read_bytes() == b'old point'
    # Restoring a pre-materials point must empty files from the later snapshot.
    stage = a.stage_archive(current, None, {})
    a.publish_stage(current, stage)
    assert a.storage_inventory(current) == {'files':0, 'bytes':0}


@pytest.mark.parametrize('kind', ['symlink','hardlink','directory','duplicate'])
def test_material_archive_rejects_nonregular_or_duplicate_members_without_changing_files(tmp_path, kind):
    from scripts import materials_archive as a
    import io
    import tarfile
    name = '12345678-1234-4234-8234-123456789abc.blob'
    archive = tmp_path/'materials.tar'
    with tarfile.open(archive, 'w') as output:
        entry = tarfile.TarInfo(name)
        if kind == 'symlink': entry.type = tarfile.SYMTYPE; entry.linkname = '/etc/passwd'
        elif kind == 'hardlink': entry.type = tarfile.LNKTYPE; entry.linkname = '../secret'
        elif kind == 'directory': entry.type = tarfile.DIRTYPE
        else:
            entry.size = 1; output.addfile(entry, io.BytesIO(b'a'))
        output.addfile(entry, io.BytesIO(b'a') if kind == 'duplicate' else None)
    current = tmp_path/'current'; current.mkdir()
    old = current/name; old.write_bytes(b'unchanged')
    with pytest.raises(RuntimeError, match='material'):
        a.stage_archive(current, archive)
    assert old.read_bytes() == b'unchanged'
    assert sorted(p.name for p in current.iterdir()) == [name]


def test_material_member_checksum_is_checked_before_staging(tmp_path):
    from scripts import materials_archive as a
    folder = tmp_path/'backup'; folder.mkdir(); archive = material_archive(folder)
    current = tmp_path/'current'; current.mkdir()
    expected = {'12345678-1234-4234-8234-123456789abc.blob':{'sha256':'0'*64,'bytes':13}}
    with pytest.raises(RuntimeError, match='checksum'):
        a.stage_archive(current, archive, expected)
    assert list(current.iterdir()) == []


def test_export_refuses_material_symlink_without_reading_target(tmp_path):
    from scripts import materials_archive as a
    import io
    current = tmp_path/'current'; current.mkdir()
    secret = tmp_path/'private'; secret.write_bytes(b'do not archive')
    (current/'12345678-1234-4234-8234-123456789abc.blob').symlink_to(secret)
    output = io.BytesIO()
    with pytest.raises(RuntimeError, match='material'):
        a.create_archive(current, output)
    assert b'do not archive' not in output.getvalue()


def test_material_publication_error_rolls_back_working_files(tmp_path, monkeypatch):
    from scripts import materials_archive as a
    import io
    import tarfile
    archive = tmp_path/'materials.tar'
    new_names = ['aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa.blob','bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb.blob']
    with tarfile.open(archive, 'w') as output:
        for name in new_names:
            entry = tarfile.TarInfo(name); entry.size=3; output.addfile(entry, io.BytesIO(b'new'))
    current = tmp_path/'current'; current.mkdir()
    old = current/'cccccccc-cccc-4ccc-8ccc-cccccccccccc.blob'; old.write_bytes(b'old')
    stage = a.stage_archive(current, archive)
    replace = Path.replace
    def disk_failure(path, target):
        if path.parent.name == stage and path.name == new_names[1]:
            raise OSError('fixture disk failure')
        return replace(path, target)
    monkeypatch.setattr(Path, 'replace', disk_failure)
    with pytest.raises(OSError, match='disk failure'): a.publish_stage(current, stage)
    assert old.read_bytes() == b'old'
    assert all(not (current/name).exists() for name in new_names)
    a.discard_stage(current, stage)
    assert a.storage_inventory(current) == {'files':1, 'bytes':3}


@pytest.mark.parametrize('failure', [False, True])
def test_restore_stages_files_before_db_import_and_publishes_only_after_all_databases_match(tmp_path, monkeypatch, failure):
    from scripts import materials_archive as a
    folder = fixture_backup(tmp_path/'backup'); manifest = add_materials(folder)
    snapshot = {'schema':3,'tables':{}}
    manifest['snapshots'] = {s:{**snapshot,'schema':manifest['schema_versions'][s]} for s in p.SERVICES}
    p.private_json(folder/'manifest.json', manifest)
    current = tmp_path/'current'; current.mkdir()
    old = current/'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb.blob'; old.write_bytes(b'prior snapshot')
    events = []
    class Stack:
        token = lambda self:'test-internal-key'
        def stage_materials(self, path, selected):
            events.append('stage')
            return a.stage_archive(current, path/'materials.tar', selected['materials'])
        def publish_materials(self, stage):
            events.append('publish'); a.publish_stage(current, stage)
        def discard_materials(self, stage):
            events.append('discard'); a.discard_stage(current, stage)
        def psql(self, *args, **kwargs): events.append('database mutation')
        def restore_dump(self, service, path):
            events.append('import '+service)
            if failure and service == 'schedule': raise RuntimeError('fixture import failure')
        def snapshot(self, service): return manifest['snapshots'][service]
        def material_metadata(self, schema): return manifest['materials']
        def dc(self, *args, **kwargs): events.append('start')
    monkeypatch.setattr(p, 'inspect_images', lambda stack:None)
    monkeypatch.setattr(p, 'confirm', lambda word:None)
    monkeypatch.setattr(p, 'smoke', lambda stack:None)
    def safety_backup(stack, *, resume):
        assert resume is False; events.append('safety backup'); return tmp_path/'safety'
    monkeypatch.setattr(p, 'backup', safety_backup)
    if failure:
        with pytest.raises(RuntimeError, match='import failure'): p.restore(Stack(), folder)
        assert old.read_bytes() == b'prior snapshot'
        assert 'publish' not in events and 'start' not in events
        assert a.storage_inventory(current) == {'files':1, 'bytes':len(b'prior snapshot')}
    else:
        p.restore(Stack(), folder)
        assert not old.exists()
        assert (current/'12345678-1234-4234-8234-123456789abc.blob').read_bytes() == b'lecture bytes'
        assert events.index('publish') > events.index('import notifications')
        assert events[-1] == 'start'
    assert events.index('stage') < events.index('database mutation')


def test_streamed_restore_uses_volume_stage_and_rejects_tampering_before_publication(tmp_path):
    from scripts import materials_archive as a
    import io
    folder = tmp_path/'backup'; folder.mkdir(); archive = material_archive(folder)
    inventory = a.validate_archive(archive)
    current = tmp_path/'current'; current.mkdir()
    header = json.dumps({'has_archive':True,'materials':inventory}).encode()+b'\n'
    stage = a.stage_stream(current, io.BytesIO(header+archive.read_bytes()))
    assert not list(current.glob('*.blob'))
    a.publish_stage(current, stage)
    name = next(iter(inventory)); assert (current/name).read_bytes() == b'lecture bytes'
    corrupted = archive.read_bytes().replace(b'lecture bytes', b'tampered data')
    with pytest.raises(RuntimeError, match='checksum'):
        a.stage_stream(current, io.BytesIO(header+corrupted))
    assert (current/name).read_bytes() == b'lecture bytes'
    assert a.storage_inventory(current) == {'files':1, 'bytes':len(b'lecture bytes')}


def test_local_backup_rejects_missing_database_material_files_before_marking_complete(tmp_path):
    from scripts import materials_archive as a
    folder = fixture_backup(tmp_path/'backup'); (folder/'COMPLETE').unlink()
    material_archive(folder)
    expected = {'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa.blob':{'sha256':'a'*64,'bytes':123}}
    with pytest.raises(RuntimeError, match='metadata'):
        a.finish_backup(folder, p.SCHEMAS, 'test-internal-key', expected)
    assert not (folder/'COMPLETE').exists()


def test_migration_rejects_invalid_material_archive_before_initializing_destination_volumes(tmp_path, monkeypatch):
    folder = fixture_backup(tmp_path/'backup'); add_materials(folder, name='../outside.blob')
    source_calls = []
    def forbidden(*args, **kwargs): pytest.fail('Destination data/volume mutated before archive validation')
    source = SimpleNamespace(project='source', token=lambda:'test-internal-key',
        psql=lambda *a,**kw:'1', running_apps=lambda:['auth','schedule','web'],
        containers=lambda:[], dc=lambda *a,**kw:source_calls.append(a),config={'services':{}})
    target = SimpleNamespace(project='target', token=lambda:'test-internal-key',
        running_apps=lambda:[], pull=lambda:None, material_state=forbidden,dc=forbidden,psql=forbidden)
    monkeypatch.setattr(p, 'disk_check', lambda:None)
    monkeypatch.setattr(p, 'inspect_images', lambda stack:None)
    monkeypatch.setattr(p, 'confirm', lambda word:None)
    monkeypatch.setattr(p, 'backup', lambda stack,**kw:folder)
    with pytest.raises(RuntimeError, match='material'):
        p.migrate_databases(source, target)
    assert ('start','auth','schedule','web') in source_calls


def historical_schema_dump(version=4, newline='\n'):
    # The real schema_migrations table has version + applied_at; pg_restore
    # emits COPY rows with a tab, not a line containing only the integer.
    return newline.join([
        '-- PostgreSQL database dump',
        '\\restrict bCKDcdEo0hAuthFixture',
        'SET statement_timeout = 0;',
        'SET client_encoding = \'UTF8\';',
        '-- Data for Name: schema_migrations; Type: TABLE DATA; Schema: public; Owner: schedule',
        'COPY public.schema_migrations (version, applied_at) FROM stdin;',
        '1\t2026-08-01 12:00:00',
        '2\t2026-08-15 12:00:01',
        '3\t2026-08-20 12:00:02',
        *(['4\t2026-09-01 12:00:03'] if version >= 4 else []),
        *(['5\t2026-10-07 12:00:04'] if version >= 5 else []),
        '\\.',
        '-- PostgreSQL database dump complete',
        '\\unrestrict bCKDcdEo0hAuthFixture',
        '',
    ])


@pytest.mark.parametrize('newline', ['\n','\r\n'])
def test_historical_schema_parser_reads_version_tab_field_in_real_pg_restore_copy_output(newline):
    from scripts import materials_archive as a
    import io
    # Numbers in an unrelated COPY block must never become schema versions.
    unrelated = newline.join(['COPY public.other_table (id) FROM stdin;', '999', '\\.', ''])
    assert a.parse_schema_dump(io.StringIO(unrelated+historical_schema_dump(newline=newline))) == 4


@pytest.mark.parametrize('output', [
    '4\n',
    'COPY public.schema_migrations (version, applied_at) FROM stdin;\n4\t2026-09-01 12:00:03\n',
    'COPY public.schema_migrations (version, applied_at) FROM stdin;\nwrong\t2026-09-01 12:00:03\n\\.\n',
    'COPY public.schema_migrations (applied_at, version) FROM stdin;\n2026-09-01 12:00:03\t4\n\\.\n',
])
def test_historical_schema_parser_rejects_missing_incomplete_or_malformed_copy_block(output):
    from scripts import materials_archive as a
    import io
    with pytest.raises(RuntimeError, match='schema'):
        a.parse_schema_dump(io.StringIO(output))


@pytest.mark.parametrize('schema,accepted', [(4,True),(5,False)])
def test_historical_two_column_dump_does_not_weaken_schema_five_manifest_requirement(tmp_path, schema, accepted):
    from scripts import materials_archive as a
    import io
    folder = fixture_backup(tmp_path/'historical')
    (folder/'manifest.json').unlink()
    (folder/'config.env').write_text('INTERNAL_TOKEN=test-internal-key\n')
    versions = {'auth':3,'schedule':a.parse_schema_dump(io.StringIO(historical_schema_dump(schema))), 'notifications':3}
    if accepted:
        assert a.verify_local_backup(folder, 'test-internal-key', versions)['schema_versions']['schedule'] == 4
    else:
        with pytest.raises(RuntimeError, match='manifest.*schema 5'):
            a.verify_local_backup(folder, 'test-internal-key', versions)


def kill_material_stage(current, inventory, tar_bytes):
    import subprocess
    import sys
    import time
    process = subprocess.Popen([sys.executable,'-m','scripts.materials_archive','stage-stream',str(current)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        process.stdin.write(json.dumps({'has_archive':True,'materials':inventory}).encode()+b'\n')
        process.stdin.write(tar_bytes[:1024*1024+512]); process.stdin.flush()
        deadline = time.monotonic()+5
        while not list(current.glob('.restore-*')) and process.poll() is None and time.monotonic()<deadline:
            time.sleep(.01)
        assert process.poll() is None, process.stderr.read().decode()
        assert list(current.glob('.restore-*'))
        return process
    except BaseException:
        process.kill(); process.wait(); raise


def test_killed_stream_stage_is_safely_reclaimed_before_backup_and_retry(tmp_path):
    from scripts import materials_archive as a
    import io
    current = tmp_path/'current'; current.mkdir()
    old = current/'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb.blob'; old.write_bytes(b'old bytes')
    source = tmp_path/'source'; source.mkdir()
    name = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa.blob'
    (source/name).write_bytes(b'N'*(2*1024*1024))
    output = io.BytesIO(); inventory = a.create_archive(source, output)
    process = kill_material_stage(current, inventory, output.getvalue())
    process.kill(); process.wait(); process.stdin.close()
    assert old.read_bytes() == b'old bytes'
    backup = io.BytesIO(); assert set(a.create_archive(current, backup)) == {old.name}
    assert not list(current.glob('.restore-*'))
    stage = a.stage_stream(current, io.BytesIO(json.dumps({'has_archive':True,'materials':inventory}).encode()+b'\n'+output.getvalue()))
    a.publish_stage(current, stage)
    assert (current/name).read_bytes() == b'N'*(2*1024*1024)


def test_active_stream_stage_is_not_reclaimed_by_another_operation(tmp_path):
    from scripts import materials_archive as a
    import io
    current = tmp_path/'current'; current.mkdir()
    source = tmp_path/'source'; source.mkdir()
    (source/'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa.blob').write_bytes(b'N'*(2*1024*1024))
    output = io.BytesIO(); inventory = a.create_archive(source, output)
    process = kill_material_stage(current, inventory, output.getvalue())
    try:
        with pytest.raises(RuntimeError, match='running|locked'):
            a.create_archive(current, io.BytesIO())
        assert list(current.glob('.restore-*')) and process.poll() is None
    finally:
        process.kill(); process.wait(); process.stdin.close()


def test_killed_publication_requires_explicit_recovery_and_preserves_previous_bytes(tmp_path):
    from scripts import materials_archive as a
    import io
    import subprocess
    import sys
    current = tmp_path/'current'; current.mkdir()
    old = current/'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb.blob'; old.write_bytes(b'old bytes')
    source = tmp_path/'source'; source.mkdir()
    new_name = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa.blob'; (source/new_name).write_bytes(b'new bytes')
    archive = tmp_path/'materials.tar'
    with archive.open('wb') as output: a.create_archive(source, output)
    code = '''import os,signal,sys
from pathlib import Path
from scripts import materials_archive as a
root=Path(sys.argv[1]); archive=Path(sys.argv[2])
stage=a.stage_archive(root,archive)
replace=Path.replace
def killed(path,target):
    if path.parent.name==stage and a.BLOB_NAME.fullmatch(path.name):
        os.kill(os.getpid(),signal.SIGKILL)
    return replace(path,target)
Path.replace=killed
a.publish_stage(root,stage)
'''
    result = subprocess.run([sys.executable,'-c',code,str(current),str(archive)], capture_output=True)
    assert result.returncode == -9, result.stderr.decode()
    with pytest.raises(RuntimeError, match='recover'):
        a.create_archive(current, io.BytesIO())
    a.recover_storage(current)
    assert (current/new_name).read_bytes() == b'new bytes'
    preserved = list(current.glob('.recovered-*/'+old.name))
    assert len(preserved) == 1 and preserved[0].read_bytes() == b'old bytes'
    assert set(a.create_archive(current, io.BytesIO())) == {new_name}


@pytest.mark.parametrize('moment', ['before-old','after-old','after-new','before-certificate','before-retain','after-retain'])
def test_publication_recovery_handles_partial_moves_and_commit_cleanup_kill_windows(tmp_path, moment):
    from scripts import materials_archive as a
    import io
    import subprocess
    import sys
    current = tmp_path/'current'; current.mkdir()
    old_names = ['cccccccc-cccc-4ccc-8ccc-cccccccccccc.blob','dddddddd-dddd-4ddd-8ddd-dddddddddddd.blob']
    new_names = ['aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa.blob','bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb.blob']
    for name in old_names: (current/name).write_bytes(b'old '+name.encode())
    source = tmp_path/'source'; source.mkdir()
    for name in new_names: (source/name).write_bytes(b'new '+name.encode())
    archive = tmp_path/'materials.tar'
    with archive.open('wb') as output: a.create_archive(source, output)
    code = '''import os,signal,sys
from pathlib import Path
from scripts import materials_archive as a
root=Path(sys.argv[1]); stage=a.stage_archive(root,Path(sys.argv[2])); moment=sys.argv[3]
replace=Path.replace
def killed(path,target):
    target=Path(target)
    old=path.parent==root and a.BLOB_NAME.fullmatch(path.name)
    new=path.parent.name==stage and a.BLOB_NAME.fullmatch(path.name)
    certificate=target.name=='.recovery.json'
    retain=path.name.startswith('.previous-') and target.name.startswith('.recovered-')
    if (moment=='before-old' and old) or (moment=='before-certificate' and certificate) or (moment=='before-retain' and retain):
        os.kill(os.getpid(),signal.SIGKILL)
    result=replace(path,target)
    if (moment=='after-old' and old) or (moment=='after-new' and new) or (moment=='after-retain' and retain):
        os.kill(os.getpid(),signal.SIGKILL)
    return result
Path.replace=killed
a.publish_stage(root,stage)
'''
    result = subprocess.run([sys.executable,'-c',code,str(current),str(archive),moment], capture_output=True)
    assert result.returncode == -9, result.stderr.decode()
    a.recover_storage(current)
    assert set(a.create_archive(current, io.BytesIO())) == set(new_names)
    for name in new_names: assert (current/name).read_bytes() == b'new '+name.encode()
    for name in old_names:
        preserved = list(current.glob('.recovered-*/'+name))
        assert len(preserved) == 1 and preserved[0].read_bytes() == b'old '+name.encode()
    assert not list(current.glob('.restore-*')) and not list(current.glob('.previous-*'))


@pytest.mark.parametrize('kind', ['unregistered','symlink','forged'])
def test_material_recovery_preserves_unrecognized_or_forged_directories(tmp_path, kind):
    from scripts import materials_archive as a
    current = tmp_path/'current'; current.mkdir()
    name = '.restore-'+('a'*32)
    outside = tmp_path/'private'; outside.mkdir(); secret = outside/'secret'; secret.write_bytes(b'preserve')
    if kind == 'symlink': (current/name).symlink_to(outside, target_is_directory=True)
    else:
        (current/name).mkdir(mode=0o700); (current/name/'secret').write_bytes(b'preserve')
        if kind == 'forged':
            marker = current/'.materials-restore.json'; marker.write_text(json.dumps({'record':{'format':1,'id':'a'*32,'phase':'receiving','new':{},'old':{}},'sha256':'0'*64})); marker.chmod(0o600)
    with pytest.raises(RuntimeError, match='journal|recovery|material|signature'):
        a.recover_storage(current)
    assert secret.read_bytes() == b'preserve'
    if kind != 'symlink': assert (current/name/'secret').read_bytes() == b'preserve'


@pytest.mark.parametrize('action', ['restore','test_restore'])
def test_production_rejects_a_real_local_manifest_without_snapshots_before_any_mutation(tmp_path, monkeypatch, action):
    from scripts import materials_archive as a
    import tarfile
    folder = fixture_backup(tmp_path/'local'); (folder/'COMPLETE').unlink()
    with tarfile.open(folder/'materials.tar','w'): pass
    a.finish_backup(folder, p.SCHEMAS, 'test-internal-key', {})
    assert p.verify_backup(folder, 'test-internal-key')['schema_versions']['schedule'] == 5
    calls = []
    def forbidden(*args, **kwargs):
        calls.append(args); pytest.fail('Production restore mutated state before snapshot preflight')
    stack = SimpleNamespace(token=lambda:'test-internal-key', dc=forbidden, psql=forbidden, restore_dump=forbidden)
    monkeypatch.setattr(p, 'inspect_images', forbidden); monkeypatch.setattr(p, 'backup', forbidden)
    monkeypatch.setattr(p, 'confirm', forbidden); monkeypatch.setattr(p.tempfile, 'TemporaryDirectory', forbidden)
    with pytest.raises(RuntimeError, match='snapshot'):
        getattr(p, action)(stack, folder)
    assert calls == []


def test_production_snapshot_preflight_preserves_retired_queue_backup_support(tmp_path):
    folder = fixture_backup(tmp_path/'historical')
    manifest = json.loads((folder/'manifest.json').read_text())
    manifest['schema_versions']['queue'] = 2
    queue = folder/'queue.dump'; queue.write_text('historical queue dump')
    manifest['files']['queue.dump'] = {'sha256':p.sha256(queue),'bytes':queue.stat().st_size}
    manifest['snapshots'] = {service:{'schema':version,'tables':{'schema_migrations':{
        'count':version,'digest':'a'*32,'columns':['version','applied_at']}}}
        for service,version in manifest['schema_versions'].items()}
    p.private_json(folder/'manifest.json', manifest)
    verified = p.verify_backup(folder, 'test-internal-key')
    p.validate_restore_snapshots(verified)
    del verified['snapshots']['queue']
    p.validate_restore_snapshots(verified)


@pytest.mark.parametrize('bad', ['missing','wrong_schema','wrong_count','wrong_digest','wrong_columns','extra_service'])
def test_malformed_production_snapshots_are_rejected_before_restore_mutation(tmp_path, monkeypatch, bad):
    folder = fixture_backup(tmp_path/'backup')
    manifest = json.loads((folder/'manifest.json').read_text())
    manifest['snapshots'] = {service:{'schema':version,'tables':{'fixture':{
        'count':1,'digest':'a'*32,'columns':['id']}}}
        for service,version in manifest['schema_versions'].items()}
    if bad == 'missing': del manifest['snapshots']['notifications']
    elif bad == 'wrong_schema': manifest['snapshots']['auth']['schema'] = 2
    elif bad == 'extra_service': manifest['snapshots']['other'] = {}
    else:
        key = {'wrong_count':'count','wrong_digest':'digest','wrong_columns':'columns'}[bad]
        manifest['snapshots']['schedule']['tables']['fixture'][key] = {'count':True,'digest':'corrupt','columns':[]}[key]
    p.private_json(folder/'manifest.json', manifest)
    def forbidden(*args, **kwargs): pytest.fail('Malformed snapshots reached a production mutation')
    stack = SimpleNamespace(token=lambda:'test-internal-key',dc=forbidden,psql=forbidden,restore_dump=forbidden)
    monkeypatch.setattr(p, 'inspect_images', forbidden); monkeypatch.setattr(p, 'backup', forbidden)
    monkeypatch.setattr(p, 'confirm', forbidden)
    with pytest.raises(RuntimeError, match='snapshot'): p.restore(stack, folder)
