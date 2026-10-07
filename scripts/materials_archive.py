"""Stream private material archives; never trust archive paths or use extractall.

Commands run as schedule's unprivileged user in one-shot containers. Staging
lives on the material volume, not the 32 MiB production /tmp filesystem.
"""
import argparse
from contextlib import contextmanager
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import tarfile
import uuid

CHUNK_BYTES = 1024 * 1024
BLOB_NAME = re.compile(r'[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}\.blob')
PART_NAME = re.compile(r'[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}\.part')
LOCK_NAME = re.compile(r'[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}\.lock')
STAGE_NAME = re.compile(r'\.restore-[a-f0-9]{32}')
RECOVERED_NAME = re.compile(r'\.recovered-[a-f0-9]{32}')
OPS_TEMP_NAME = re.compile(r'\.materials-journal-[a-f0-9]{32}\.tmp')
OPS_LOCK = '.materials-ops.lock'
OPS_KEY = '.materials-ops.key'
JOURNAL = '.materials-restore.json'


def _directory(path):
    path = Path(path)
    if path.is_symlink() or not path.is_dir():
        raise RuntimeError('Invalid material directory')
    return path


def _private_file(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise RuntimeError('Invalid material recovery control: '+path.name)
    return info


def _sync(directory):
    fd = os.open(directory, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try: os.fsync(fd)
    finally: os.close(fd)


def _atomic_private(path, data):
    temporary = path.parent/('.materials-journal-'+uuid.uuid4().hex+'.tmp')
    with open(temporary, 'xb', opener=lambda p, f: os.open(p, f, 0o600)) as output:
        output.write(data); output.flush(); os.fsync(output.fileno())
    temporary.replace(path); _sync(path.parent)


def _signed_bytes(record, key):
    payload = json.dumps(record, sort_keys=True, separators=(',', ':')).encode()
    return json.dumps({'record':record, 'sha256':hmac.new(key, payload, hashlib.sha256).hexdigest()}).encode()


def _read_signed(path, key):
    _private_file(path)
    try:
        envelope = json.loads(path.read_bytes())
        if not isinstance(envelope, dict) or set(envelope) != {'record','sha256'}:
            raise ValueError()
        payload = json.dumps(envelope['record'], sort_keys=True, separators=(',', ':')).encode()
        if not isinstance(envelope['sha256'], str) or not hmac.compare_digest(envelope['sha256'], hmac.new(key, payload, hashlib.sha256).hexdigest()):
            raise ValueError()
        return envelope['record']
    except (ValueError, TypeError) as error:
        raise RuntimeError('Invalid material recovery journal signature') from error


def _file_name(name):
    return BLOB_NAME.fullmatch(name) or PART_NAME.fullmatch(name) or LOCK_NAME.fullmatch(name)


def _old_inventory(value):
    if not isinstance(value, dict): raise RuntimeError('Invalid material recovery inventory')
    for name, item in value.items():
        if not isinstance(name, str) or not _file_name(name) or not isinstance(item, dict) or set(item) != {'sha256','bytes'} or type(item['bytes']) is not int or item['bytes'] < 0 or not isinstance(item['sha256'], str) or not re.fullmatch(r'[a-f0-9]{64}', item['sha256']):
            raise RuntimeError('Invalid material recovery inventory')
    return value


def _read_journal(directory, key):
    path = directory/JOURNAL
    if not path.exists() and not path.is_symlink(): return None
    record = _read_signed(path, key)
    if not isinstance(record, dict) or set(record) != {'format','id','phase','new','old'} or record['format'] != 1 or not isinstance(record['id'], str) or not re.fullmatch(r'[a-f0-9]{32}', record['id']) or record['phase'] not in ('receiving','ready','publishing','committed'):
        raise RuntimeError('Invalid material recovery journal')
    _expected_inventory(record['new']); _old_inventory(record['old'])
    if record['phase'] in ('receiving','ready') and record['old']:
        raise RuntimeError('Invalid material recovery journal')
    return record


def _write_journal(directory, key, record):
    _atomic_private(directory/JOURNAL, _signed_bytes(record, key))


def _clear_journal(directory):
    (directory/JOURNAL).unlink(); _sync(directory)


@contextmanager
def _operation(directory):
    # Persistent inode: unlinking a flock file would let another process acquire
    # a different inode while the original operation still holds its lock.
    import fcntl
    directory = _directory(directory)
    fd = os.open(directory/OPS_LOCK, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(fd, 'rb+') as lock:
        _private_file(directory/OPS_LOCK)
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Another material operation is running; storage is locked') from None
        try:
            path = directory/OPS_KEY
            if not path.exists() and not path.is_symlink(): _atomic_private(path, os.urandom(32))
            _private_file(path); key = path.read_bytes()
            if len(key) != 32: raise RuntimeError('Invalid material recovery key')
            yield directory, key
        finally: fcntl.flock(lock, fcntl.LOCK_UN)


def _owned_directory(path):
    path = _directory(path); info = path.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise RuntimeError('Invalid material recovery directory: '+path.name)
    return path


def _checksum(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid():
        raise RuntimeError('Invalid material recovery file: '+path.name)
    digest = hashlib.sha256()
    with open(path, 'rb', opener=lambda p, f: os.open(p, f | getattr(os, 'O_NOFOLLOW', 0))) as stream:
        for block in iter(lambda: stream.read(CHUNK_BYTES), b''): digest.update(block)
    return {'sha256':digest.hexdigest(), 'bytes':info.st_size}


def _store_files(directory, inventory, *, partial=False, certificate=False):
    """Validate ownership and every child before deleting or moving anything."""
    _owned_directory(directory); found = {}
    for path in directory.iterdir():
        if certificate and (path.name == '.recovery.json' or OPS_TEMP_NAME.fullmatch(path.name)):
            _private_file(path); continue
        if path.name not in inventory: raise RuntimeError('Unrecognized material recovery file: '+path.name)
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid():
            raise RuntimeError('Invalid material recovery file: '+path.name)
        if partial:
            if info.st_mode & 0o077 or info.st_size > inventory[path.name]['bytes']:
                raise RuntimeError('Invalid incomplete material recovery file: '+path.name)
        elif _checksum(path) != inventory[path.name]:
            raise RuntimeError('Material recovery checksum mismatch: '+path.name)
        found[path.name] = path
    return found


def _retained(directory, path, key):
    _owned_directory(path)
    record = _read_signed(path/'.recovery.json', key)
    if not isinstance(record, dict) or set(record) != {'format','id','old'} or record['format'] != 1 or '.recovered-'+str(record['id']) != path.name:
        raise RuntimeError('Invalid retained material recovery journal')
    expected = _old_inventory(record['old'])
    if set(_store_files(path, expected, certificate=True)) != set(expected):
        raise RuntimeError('Missing retained material recovery files')


def _sources(directory, *, key=None, record=None):
    result = []
    for path in sorted(_directory(directory).iterdir()):
        if key is not None:
            if path.name in (OPS_LOCK, OPS_KEY, JOURNAL) or OPS_TEMP_NAME.fullmatch(path.name):
                _private_file(path); continue
            if RECOVERED_NAME.fullmatch(path.name):
                _retained(directory, path, key); continue
            if record and path.name == '.restore-'+record['id']:
                _owned_directory(path); continue
            if record and record['phase'] in ('publishing','committed') and path.name == '.previous-'+record['id']:
                _owned_directory(path); continue
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or not (BLOB_NAME.fullmatch(path.name) or PART_NAME.fullmatch(path.name) or LOCK_NAME.fullmatch(path.name)):
            raise RuntimeError('Invalid material file: '+path.name)
        if BLOB_NAME.fullmatch(path.name):
            result.append((path, info))
    return result


class _HashReader:
    def __init__(self, stream):
        self.stream = stream
        self.digest = hashlib.sha256()
        self.bytes = 0

    def read(self, size):
        block = self.stream.read(size)
        self.digest.update(block)
        self.bytes += len(block)
        return block


def create_archive(directory, output):
    """Write a flat tar to an output stream without buffering file contents."""
    with _operation(directory) as (directory, key):
        _prepare(directory, key)
        return _create_archive(directory, output, key)


def _create_archive(directory, output, key):
    files = _sources(directory, key=key)
    inventory = {}
    with tarfile.open(fileobj=output, mode='w|') as bundle:
        for path, before in files:
            if before.st_size <= 0:
                raise RuntimeError('Empty material file: '+path.name)
            fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
            with os.fdopen(fd, 'rb') as source:
                actual = os.fstat(source.fileno())
                if not stat.S_ISREG(actual.st_mode) or (actual.st_dev, actual.st_ino, actual.st_size) != (before.st_dev, before.st_ino, before.st_size):
                    raise RuntimeError('Material changed during backup: '+path.name)
                entry = tarfile.TarInfo(path.name)
                entry.size = before.st_size; entry.mode = 0o600
                entry.uid = entry.gid = 0; entry.mtime = 0
                reader = _HashReader(source)
                bundle.addfile(entry, reader)
                after = os.fstat(source.fileno())
                if reader.bytes != before.st_size or (after.st_size, after.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
                    raise RuntimeError('Material changed during backup: '+path.name)
                inventory[path.name] = {'sha256':reader.digest.hexdigest(), 'bytes':reader.bytes}
    return inventory


def _expected_inventory(expected):
    if expected is None:
        return None
    if not isinstance(expected, dict):
        raise RuntimeError('Invalid material inventory')
    for name, item in expected.items():
        if not isinstance(name, str) or not BLOB_NAME.fullmatch(name):
            raise RuntimeError('Invalid material member filename: '+name)
        if not isinstance(item, dict) or set(item) != {'sha256', 'bytes'} or not isinstance(item['sha256'], str) or not re.fullmatch(r'[a-f0-9]{64}', item['sha256']) or type(item['bytes']) is not int or item['bytes'] <= 0:
            raise RuntimeError('Invalid material checksum record: '+name)
    return expected


def _read_archive(source, expected=None, *, destination=None):
    expected = _expected_inventory(expected)
    inventory = {}
    is_path = isinstance(source, (str, Path))
    if is_path:
        source = Path(source)
        if source.is_symlink() or not source.is_file():
            raise RuntimeError('Invalid material archive file')
    try:
        with tarfile.open(name=str(source) if is_path else None,
                          fileobj=None if is_path else source, mode='r|') as bundle:
            for member in bundle:
                name = member.name
                if not BLOB_NAME.fullmatch(name) or not member.isreg() or member.linkname or member.size <= 0 or name in inventory:
                    raise RuntimeError('Invalid material archive member: '+name)
                if expected is not None and (name not in expected or member.size != expected[name]['bytes']):
                    raise RuntimeError('Material checksum mismatch: '+name)
                entry = bundle.extractfile(member)
                if entry is None:
                    raise RuntimeError('Invalid material archive member: '+name)
                output = None
                try:
                    if destination is not None:
                        output = open(destination/name, 'xb', opener=lambda p, f: os.open(p, f, 0o600))
                    digest = hashlib.sha256(); size = 0
                    for block in iter(lambda: entry.read(CHUNK_BYTES), b''):
                        digest.update(block); size += len(block)
                        if output is not None: output.write(block)
                    if output is not None: output.flush(); os.fsync(output.fileno())
                finally:
                    entry.close()
                    if output is not None: output.close()
                item = {'sha256':digest.hexdigest(), 'bytes':size}
                if size != member.size or (expected is not None and item != expected[name]):
                    raise RuntimeError('Material checksum mismatch: '+name)
                inventory[name] = item
    except (tarfile.TarError, EOFError, ValueError) as error:
        raise RuntimeError('Invalid material archive') from error
    if expected is not None and inventory != expected:
        raise RuntimeError('Material archive file list differs from its inventory')
    return inventory


def validate_archive(source, expected=None):
    return _read_archive(source, expected)


def stage_archive(directory, archive, expected=None):
    """Validate first, then populate an isolated directory on the disk volume."""
    directory = _directory(directory)
    inventory = validate_archive(archive, expected) if archive is not None else _expected_inventory(expected or {})
    if archive is None and inventory:
        raise RuntimeError('Missing material archive')
    with _operation(directory) as (directory, key):
        return _receive_stage(directory, key, inventory, archive)


def stage_stream(directory, source):
    """Receive an already-verified host archive over stdin without a bind mount.

    The private host backup can remain mode 700; UID 10001 never needs access
    to it. We still verify every streamed file before publishing the stage.
    """
    try:
        header = json.loads(source.readline())
    except (ValueError, UnicodeError) as error:
        raise RuntimeError('Invalid material restore header') from error
    if not isinstance(header, dict) or set(header) != {'has_archive','materials'} or type(header['has_archive']) is not bool:
        raise RuntimeError('Invalid material restore header')
    inventory = _expected_inventory(header['materials'])
    if not header['has_archive'] and inventory:
        raise RuntimeError('Missing material archive')
    with _operation(directory) as (directory, key):
        return _receive_stage(directory, key, inventory, source if header['has_archive'] else None)


def _receive_stage(directory, key, inventory, archive):
    _prepare(directory, key)
    if shutil.disk_usage(directory).free < sum(item['bytes'] for item in inventory.values()) + 1024*1024:
        raise RuntimeError('Insufficient disk space for material restore stage')
    record = {'format':1, 'id':uuid.uuid4().hex, 'phase':'receiving', 'new':inventory, 'old':{}}
    _write_journal(directory, key, record)
    name = '.restore-'+record['id']; stage = directory/name
    stage.mkdir(mode=0o700); _sync(directory)
    try:
        if archive is not None: _read_archive(archive, inventory, destination=stage)
        _sync(stage)
        record['phase'] = 'ready'; _write_journal(directory, key, record)
        return name
    except BaseException:
        _discard_receiving(directory, record)
        raise


def metadata_inventory(rows):
    if not isinstance(rows, list): raise RuntimeError('Invalid material metadata')
    result = {}
    for item in rows:
        try:
            name = item['id']+'.blob'
            if name in result: raise RuntimeError('Duplicate material metadata')
            result[name] = {'sha256':item['sha256'], 'bytes':item['bytes']}
        except (KeyError, TypeError) as error:
            raise RuntimeError('Invalid material metadata') from error
    _expected_inventory(result)
    return result


def _stage_path(directory, name):
    if not isinstance(name, str) or not STAGE_NAME.fullmatch(name):
        raise RuntimeError('Invalid material restore stage')
    return _directory(_directory(directory)/name)


def discard_stage(directory, name):
    with _operation(directory) as (directory, key):
        record = _read_journal(directory, key)
        if not record or name != '.restore-'+record['id'] or record['phase'] not in ('receiving','ready'):
            raise RuntimeError('Material recovery required before discarding this stage')
        _sources(directory, key=key, record=record)
        _discard_receiving(directory, record)


def _discard_receiving(directory, record):
    stage = directory/('.restore-'+record['id'])
    if stage.exists() or stage.is_symlink():
        _store_files(stage, record['new'], partial=True)
        shutil.rmtree(stage); _sync(directory)
    _clear_journal(directory)


def _prepare(directory, key):
    record = _read_journal(directory, key)
    _sources(directory, key=key, record=record)
    if record:
        if record['phase'] == 'receiving':
            # Holding the same persistent flock proves the receiver is gone.
            _discard_receiving(directory, record)
        elif record['phase'] == 'committed':
            _finish_publication(directory, key, record)
        else:
            raise RuntimeError('Pending material restore; run materials_archive recover with the application stopped')
    _sources(directory, key=key)


def storage_inventory(directory):
    with _operation(directory) as (directory, key):
        _prepare(directory, key)
        files = _sources(directory, key=key)
        return {'bytes':sum(info.st_size for _,info in files), 'files':len(files)}


def publish_stage(directory, name):
    """Move a complete staged snapshot into place while all writers stay stopped.

    The mount point itself cannot be renamed. Each file is atomically renamed;
    previous files are retained until publication succeeds and rolled back on an
    error. The application is never restarted with a partially restored snapshot.
    """
    with _operation(directory) as (directory, key):
        record = _read_journal(directory, key)
        if not record or name != '.restore-'+record['id'] or record['phase'] != 'ready':
            raise RuntimeError('Invalid material restore stage; recovery required')
        _sources(directory, key=key, record=record)
        staged = _store_files(_stage_path(directory, name), record['new'])
        if set(staged) != set(record['new']): raise RuntimeError('Missing material restore stage files')
        record['old'] = {path.name:_checksum(path) for path in directory.iterdir() if _file_name(path.name)}
        record['phase'] = 'publishing'; _write_journal(directory, key, record)
        try:
            _complete_publication(directory, key, record)
        except BaseException:
            # A normal error rolls back; SIGKILL leaves the signed plan intact.
            if record['phase'] == 'publishing': _rollback_publication(directory, key, record)
            raise


def _publication_files(directory, key, record):
    _sources(directory, key=key, record=record)
    stage = directory/('.restore-'+record['id']); previous = directory/('.previous-'+record['id'])
    staged = _store_files(stage, record['new'])
    prior = _store_files(previous, record['old']) if previous.exists() or previous.is_symlink() else {}
    current = {path.name:_checksum(path) for path in directory.iterdir() if _file_name(path.name)}
    for name, item in current.items():
        if item not in (record['new'].get(name), record['old'].get(name)):
            raise RuntimeError('Material recovery checksum mismatch: '+name)
    for name, item in record['old'].items():
        if name not in prior and current.get(name) != item:
            raise RuntimeError('Missing previous material recovery file: '+name)
    for name, item in record['new'].items():
        if name not in staged and current.get(name) != item:
            raise RuntimeError('Missing new material recovery file: '+name)
        if name in staged and name in current and (name not in record['old'] or name in prior):
            raise RuntimeError('Duplicate new material recovery file: '+name)
    return stage, previous, staged, prior


def _complete_publication(directory, key, record):
    stage, previous, staged, prior = _publication_files(directory, key, record)
    if not previous.exists(): previous.mkdir(mode=0o700); _sync(directory)
    for name in record['old']:
        if name not in prior: (directory/name).replace(previous/name)
    _sync(previous); _sync(directory)
    for name in staged: (stage/name).replace(directory/name)
    _sync(stage); _sync(directory)
    current = {path.name:_checksum(path) for path in directory.iterdir() if _file_name(path.name)}
    if current != record['new']: raise RuntimeError('Published material recovery snapshot differs')
    record['phase'] = 'committed'; _write_journal(directory, key, record)
    _finish_publication(directory, key, record)


def _rollback_publication(directory, key, record):
    stage = directory/('.restore-'+record['id']); previous = directory/('.previous-'+record['id'])
    _owned_directory(stage)
    for name in record['new']:
        if not (stage/name).exists() and (directory/name).exists():
            if _checksum(directory/name) != record['new'][name]: raise RuntimeError('Cannot roll back material publication; recovery required')
            (directory/name).replace(stage/name)
    if previous.exists():
        for name in _store_files(previous, record['old']): (previous/name).replace(directory/name)
        previous.rmdir()
    _sync(stage); _sync(directory)
    record['phase'] = 'ready'; record['old'] = {}; _write_journal(directory, key, record)


def _finish_publication(directory, key, record):
    current = {path.name:_checksum(path) for path in directory.iterdir() if _file_name(path.name)}
    if current != record['new']: raise RuntimeError('Committed material recovery snapshot differs')
    stage = directory/('.restore-'+record['id']); previous = directory/('.previous-'+record['id'])
    retained = directory/('.recovered-'+record['id'])
    if previous.exists() or previous.is_symlink():
        if set(_store_files(previous, record['old'], certificate=True)) != set(record['old']):
            raise RuntimeError('Missing previous material recovery files')
        if record['old']:
            _atomic_private(previous/'.recovery.json', _signed_bytes({'format':1,'id':record['id'],'old':record['old']}, key))
            if retained.exists() or retained.is_symlink(): raise RuntimeError('Duplicate retained material recovery directory')
            previous.replace(retained); _sync(directory)
        else: previous.rmdir(); _sync(directory)
    elif record['old']:
        if not retained.exists() and not retained.is_symlink(): raise RuntimeError('Missing retained material recovery directory')
        _retained(directory, retained, key)
    if stage.exists() or stage.is_symlink():
        if _store_files(stage, {}): raise RuntimeError('Unexpected committed material stage')
        stage.rmdir(); _sync(directory)
    _clear_journal(directory)


def recover_storage(directory):
    """Recover file publication only; database state is never changed here."""
    with _operation(directory) as (directory, key):
        record = _read_journal(directory, key)
        _sources(directory, key=key, record=record)
        if not record: return
        if record['phase'] in ('receiving','ready'): _discard_receiving(directory, record)
        elif record['phase'] == 'publishing': _complete_publication(directory, key, record)
        else: _finish_publication(directory, key, record)


def _schemas(value):
    try:
        result = {key:int(version) for key, version in (part.split(':', 1) for part in value.split(','))}
    except (ValueError, TypeError):
        raise RuntimeError('Invalid backup schema versions') from None
    if set(result) != {'auth','schedule','notifications'}:
        raise RuntimeError('Invalid backup schema versions')
    return result


def finish_backup(folder, schemas, token, metadata=None):
    # Imported lazily so production can reuse the archive functions above.
    from scripts import production as p
    folder = Path(folder)
    names = [*(s+'.dump' for s in p.SERVICES), 'config.env', 'effective-config.json', 'materials.tar']
    inventory = validate_archive(folder/'materials.tar')
    if metadata is None and schemas['schedule'] >= 5:
        raise RuntimeError('Missing database material metadata')
    if inventory != (metadata or {}):
        raise RuntimeError('Material files differ from database metadata; backup is incomplete')
    files = {}
    for name in names:
        path = folder/name
        if path.is_symlink() or not path.is_file() or not path.stat().st_size:
            raise RuntimeError('Incomplete backup file: '+name)
        files[name] = {'sha256':p.sha256(path), 'bytes':path.stat().st_size}
    p.private_json(folder/'manifest.json', {'format':1, 'created_at':p.stamp(),
        'schema_versions':schemas, 'files':files, 'materials':inventory,
        'internal_token_sha256':hashlib.sha256(token.encode()).hexdigest()})
    (folder/'COMPLETE').touch(mode=0o600)
    try: p.verify_backup(folder, token)
    except BaseException:
        (folder/'COMPLETE').unlink(missing_ok=True)
        raise


def parse_schema_dump(lines):
    """Read pg_restore's schema_migrations COPY data without executing SQL."""
    header = re.compile(
        r'\s*COPY\s+(?:(?:public|"public")\.)?(?:schema_migrations|"schema_migrations")'
        r'\s*\(\s*(?:version|"version")(?P<timestamp>\s*,\s*(?:applied_at|"applied_at"))?'
        r'\s*\)\s+FROM\s+stdin;\s*')
    seen = False; in_copy = False; complete = False; maximum = 0; column_count = 0
    for raw in lines:
        line = raw.rstrip('\r\n')
        if in_copy:
            if line == r'\.':
                in_copy = False; complete = True
                continue
            fields = line.split('\t')
            if len(fields) != column_count or not re.fullmatch(r'[0-9]+', fields[0]):
                raise RuntimeError('Invalid historical schema COPY row')
            version = int(fields[0])
            if version < 1: raise RuntimeError('Invalid historical schema version')
            maximum = max(maximum, version)
        else:
            match = header.fullmatch(line)
            if match:
                if seen: raise RuntimeError('Multiple historical schema COPY blocks')
                seen = True; in_copy = True
                column_count = 2 if match.group('timestamp') else 1
    if not seen or not complete or in_copy or not maximum:
        raise RuntimeError('Missing or incomplete historical schema COPY data')
    return maximum


def verify_local_backup(folder, token, legacy_schemas=None):
    from scripts import production as p
    folder = Path(folder)
    if (folder/'manifest.json').exists():
        return p.verify_backup(folder, token)
    # Historical local backups had no manifest. Read their schema versions from
    # pg_restore's data output without loading them into any database.
    if legacy_schemas is None or not 2 <= legacy_schemas.get('schedule', 0) < 5:
        raise RuntimeError('Missing material archive or backup manifest for schema 5')
    if folder.is_symlink() or not folder.is_dir() or (folder/'COMPLETE').is_symlink() or not (folder/'COMPLETE').is_file():
        raise RuntimeError('Backup is incomplete')
    for service in p.SERVICES:
        if not 2 <= legacy_schemas.get(service, 0) <= p.SCHEMAS[service]:
            raise RuntimeError('Backup schema is incompatible: '+service)
    config = folder/'config.env'
    if config.is_symlink() or not config.is_file(): raise RuntimeError('Missing original backup configuration')
    import shlex
    original = None
    for line in config.read_text().splitlines():
        match = re.match(r'^\s*(?:export\s+)?INTERNAL_TOKEN\s*=\s*(.*?)\s*$', line)
        if match:
            values = shlex.split(match[1]); original = values[0] if len(values) == 1 else None
    if original != token: raise RuntimeError('Backup requires its original INTERNAL_TOKEN')
    if (folder/'materials.tar').exists(): raise RuntimeError('Material archive requires a backup manifest')
    for service in p.SERVICES:
        path = folder/(service+'.dump')
        if path.is_symlink() or not path.is_file() or not path.stat().st_size:
            raise RuntimeError('Incomplete backup file: '+service+'.dump')
    return {'format':0, 'schema_versions':legacy_schemas, 'materials':{}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    for action in ('create','inventory','stage-stream','recover'):
        sub.add_parser(action).add_argument('directory')
    for action in ('finish-backup','verify-backup','backup-header','verify-metadata'):
        item = sub.add_parser(action); item.add_argument('folder'); item.add_argument('--schemas')
        if action == 'finish-backup': item.add_argument('--metadata-stdin', action='store_true')
    for action in ('publish','discard'):
        item = sub.add_parser(action); item.add_argument('directory'); item.add_argument('stage')
    sub.add_parser('schema-version')
    args = parser.parse_args(); os.umask(0o077)
    if args.action == 'schema-version': print(parse_schema_dump(sys.stdin))
    elif args.action == 'create': create_archive(args.directory, sys.stdout.buffer)
    elif args.action == 'inventory':
        print(json.dumps(storage_inventory(args.directory)))
    elif args.action == 'stage-stream': print(stage_stream(args.directory, sys.stdin.buffer))
    elif args.action == 'recover': recover_storage(args.directory)
    elif args.action == 'finish-backup':
        metadata = metadata_inventory(json.load(sys.stdin)) if args.metadata_stdin else None
        finish_backup(args.folder, _schemas(args.schemas), os.environ['INTERNAL_TOKEN'], metadata)
    elif args.action in ('verify-backup','backup-header','verify-metadata'):
        manifest = verify_local_backup(args.folder, os.environ['INTERNAL_TOKEN'], _schemas(args.schemas) if args.schemas else None)
        if args.action == 'backup-header':
            print(json.dumps({'has_archive':'materials.tar' in manifest.get('files', {}), 'materials':manifest.get('materials', {})}))
        elif args.action == 'verify-metadata':
            if metadata_inventory(json.load(sys.stdin)) != manifest.get('materials', {}):
                raise RuntimeError('Restored material metadata differs from files')
    elif args.action == 'publish': publish_stage(args.directory, args.stage)
    else: discard_stage(args.directory, args.stage)


if __name__ == '__main__': main()
