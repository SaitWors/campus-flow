import hashlib
import json
import tarfile

import pytest
from scripts import production as p
from scripts import external_backup as external


def backup_fixture(folder):
    folder.mkdir()
    files = {}
    for name in [*(s+'.dump' for s in p.SERVICES), 'config.env', 'effective-config.json']:
        path = folder/name
        path.write_text('Private backup fixture: '+name)
        files[name] = {'sha256': p.sha256(path), 'bytes': path.stat().st_size}
    p.private_json(folder/'manifest.json', {'format': 1, 'schema_versions': p.SCHEMAS,
        'files': files, 'internal_token_sha256': hashlib.sha256(b'fixture-token').hexdigest()})
    (folder/'COMPLETE').touch()
    return folder


def test_archive_contains_only_verified_backup_and_is_private(tmp_path):
    folder = backup_fixture(tmp_path/'production-fixture')
    (folder/'unrelated-secret').write_text('Do not export this file')
    archive = external.archive_backup(folder, 'fixture-token')
    assert archive.stat().st_mode & 0o777 == 0o600
    with tarfile.open(archive) as stream:
        names = set(stream.getnames())
        assert names == {*(s+'.dump' for s in p.SERVICES), 'config.env', 'effective-config.json', 'manifest.json', 'COMPLETE'}
        assert all(member.isfile() and member.mode == 0o600 for member in stream.getmembers())
        assert stream.extractfile('auth.dump').read() == (folder/'auth.dump').read_bytes()
    (folder/'schedule.dump').write_text('corrupted')
    with pytest.raises(RuntimeError, match='checksum mismatch'):
        external.archive_backup(folder, 'fixture-token')


def test_failed_upload_preserves_complete_local_copy(tmp_path):
    archive = external.archive_backup(backup_fixture(tmp_path/'production-fixture'), 'fixture-token')
    calls = []
    def broken(args, **kwargs):
        calls.append(args)
        if args[0] == 'scp': raise RuntimeError('Remote server unavailable')
        return ''
    with pytest.raises(RuntimeError, match='unavailable'):
        external.upload_verified(archive, 'backup@example.test', '/srv/campus-backups', runner=broken)
    assert archive.is_file() and (tmp_path/'production-fixture'/'COMPLETE').is_file()
    assert '-o' in calls[0] and 'StrictHostKeyChecking=yes' in calls[0]
    assert not any('mv ' in str(call) for call in calls)


def test_remote_checksum_required_before_atomic_publish(tmp_path):
    archive = external.archive_backup(backup_fixture(tmp_path/'production-fixture'), 'fixture-token')
    calls = []
    def remote(args, **kwargs):
        calls.append(args)
        return ('0'*64+'  remote.part\n') if 'sha256sum' in args[-1] else ''
    with pytest.raises(RuntimeError, match='checksum'):
        external.upload_verified(archive, 'backup@example.test', '/srv/campus-backups', runner=remote)
    assert not any('mv ' in str(call) for call in calls)
    calls.clear()
    def verified(args, **kwargs):
        calls.append(args)
        return (p.sha256(archive)+'  remote.part\n') if 'sha256sum' in args[-1] else ''
    external.upload_verified(archive, 'backup@example.test', '/srv/campus-backups', runner=verified)
    assert 'mv ' in calls[-1][-1]
    assert archive.is_file()


@pytest.mark.parametrize('host,path', [('-oProxyCommand=evil', '/backup'), ('ok.example', '/a/../b'), ('ok.example', '/backup;evil')])
def test_invalid_remote_destination_is_rejected_before_transport(tmp_path, host, path):
    def forbidden(*args, **kwargs): pytest.fail('Invalid destination reached transport')
    with pytest.raises(ValueError):
        external.upload_verified(tmp_path/'copy.tar.gz', host, path, runner=forbidden)
