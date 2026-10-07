"""Production operations. All database writes are explicit; no volume pruning."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from contextlib import contextmanager, ExitStack
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
SERVICES = ('auth', 'schedule', 'notifications')
APPS = ('web', 'notifications', 'schedule', 'auth')
SCHEMAS = {'auth': 3, 'schedule': 5, 'notifications': 3}

sys.path.insert(0, str(ROOT))
from scripts import materials_archive as materials


def writer_apps(stack):
    # Only an explicitly selected old source Compose can contain this service.
    retired = ('queue',) if getattr(stack, 'legacy', False) and 'queue' in stack.config.get('services', {}) else ()
    return (*APPS, *retired)


def stamp():
    return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')


def sha256(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            result.update(block)
    return result.hexdigest()


def private_json(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix('.tmp')
    with open(temporary, 'w', opener=lambda p, flags: os.open(p, flags, 0o600)) as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush(); os.fsync(stream.fileno())
    temporary.replace(path)


@contextmanager
def operation_lock(stack):
    import fcntl
    with open(stack.state/'operation.lock', 'a', opener=lambda p, flags: os.open(p, flags, 0o600)) as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Another production operation is already running: '+stack.project) from None
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def persist_release(stack):
    # Keep a later manual Compose command on the successfully deployed release.
    if sha256(stack.env_file) != stack.env_digest:
        raise RuntimeError('Environment file changed during deployment; review IMAGE_TAG before continuing')
    text = stack.env_file.read_text()
    text, count = re.subn(r'(?m)^(?:export\s+)?IMAGE_TAG\s*=.*$', 'IMAGE_TAG='+stack.tag, text)
    if not count: text = text.rstrip('\n')+'\nIMAGE_TAG='+stack.tag+'\n'
    temporary = stack.env_file.with_name(stack.env_file.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with open(temporary, 'x', opener=lambda p, flags: os.open(p, flags, 0o600)) as stream:
            stream.write(text); stream.flush(); os.fsync(stream.fileno())
        temporary.replace(stack.env_file)
    finally:
        temporary.unlink(missing_ok=True)
    stack.env_digest = sha256(stack.env_file)


def run(args, *, what, cwd=ROOT, env=None, input=None, output=None, source=None, prefix_input=None):
    # Never echo a Docker config, SQL error or command environment to public logs.
    if prefix_input is not None:
        # Only the small JSON inventory is in RAM; archive bytes stream through
        # stdin onto the disk volume, even when /tmp is a 32 MiB tmpfs.
        with tempfile.TemporaryFile() as errors:
            process = subprocess.Popen(args, cwd=cwd, env=env, stdin=subprocess.PIPE,
                stdout=output or subprocess.PIPE, stderr=errors)
            try:
                process.stdin.write(prefix_input)
                if source is not None: shutil.copyfileobj(source, process.stdin, 1024*1024)
                process.stdin.close()
                data = process.stdout.read() if output is None else None
                code = process.wait()
            except BaseException:
                process.kill(); process.wait()
                raise
            errors.seek(0)
            result = subprocess.CompletedProcess(args, code, data, errors.read())
    else:
        result = subprocess.run(args, cwd=cwd, env=env, input=input,
                                stdin=source, stdout=output or subprocess.PIPE,
                                stderr=subprocess.PIPE, text=source is None and output is None)
    if result.returncode:
        error = result.stderr
        if isinstance(error, bytes): error = error.decode(errors='replace')
        folder = ROOT/'.production'; folder.mkdir(exist_ok=True, mode=0o700)
        path = folder/'last-error.log'
        with open(path, 'w', opener=lambda p, flags: os.open(p, flags, 0o600)) as stream:
            stream.write(what+'\n'+error)
        raise RuntimeError(f'{what} failed (exit {result.returncode}); private details: {path}')
    return result.stdout


def validate_config(config, *, disposable=False):
    services = config['services']
    if set(services) != {*SERVICES, 'web', 'postgres'}:
        raise RuntimeError('Expected three APIs, web and one PostgreSQL; no translator')
    for name, service in services.items():
        if 'build' in service:
            raise RuntimeError('Production may not build images: '+name)
        if name != 'web' and service.get('ports'):
            raise RuntimeError('Database/API ports must remain private')
        if not service.get('mem_limit') or not service.get('cpus') or not service.get('pids_limit'):
            raise RuntimeError('Missing resource limits: '+name)
        log = service.get('logging', {})
        if log.get('driver') != 'json-file' or log.get('options') != {'max-size':'10m', 'max-file':'3'}:
            raise RuntimeError('Missing bounded container logs: '+name)
    ports = services['web'].get('ports', [])
    if len(ports) != 1 or ports[0].get('host_ip') != '127.0.0.1' or int(ports[0]['target']) != 8080:
        raise RuntimeError('Web must listen only on loopback')
    environment = services['auth']['environment']
    origin = environment['APP_ORIGIN']
    local_test = disposable and re.fullmatch(r'http://(localhost|127\.0\.0\.1):\d+', origin)
    if not local_test and (not origin.startswith('https://') or str(environment['COOKIE_SECURE']).lower() != 'true'):
        raise RuntimeError('Production requires HTTPS and Secure cookies')
    if len(environment.get('INTERNAL_TOKEN', '')) < 32 or len(environment.get('SETUP_KEY', '')) < 24:
        raise RuntimeError('Use strong INTERNAL_TOKEN and SETUP_KEY values')
    passwords = [v for k, v in services['postgres']['environment'].items() if k.endswith('PASSWORD')]
    if len(passwords) != 4 or len(set(passwords)) != 4 or any(not re.fullmatch(r'[A-Za-z0-9_-]{32,}', p) for p in passwords):
        raise RuntimeError('Use four different URL-safe database passwords of at least 32 characters')
    for name in SERVICES:
        env = services[name]['environment']
        size, overflow = int(env['DB_POOL_SIZE']), int(env['DB_MAX_OVERFLOW'])
        if size < 1 or overflow < 0 or size+overflow > 4 or str(env['DB_POOL_PRE_PING']).lower() != 'true':
            raise RuntimeError('Small-server profile permits at most four pooled connections per API')
    if services['schedule']['environment'].get('TRANSLATION_WORKER') != 'false':
        raise RuntimeError('The local translator must be disabled in this profile')
    schedule = services['schedule']
    material_path = '/var/lib/campus/materials'
    mounts = [v for v in schedule.get('volumes', []) if v.get('target') == material_path]
    if schedule['environment'].get('MATERIALS_DIR') != material_path or len(mounts) != 1 or mounts[0].get('type') != 'volume' or mounts[0].get('read_only', False):
        raise RuntimeError('Schedule requires a private persistent material volume')
    if str(schedule.get('user')) != '10001:10001' or schedule.get('read_only') is not True:
        raise RuntimeError('The material writer must be unprivileged with a read-only container')
    source = mounts[0].get('source')
    if not source or any(v.get('source') == source for name, service in services.items() if name != 'schedule' for v in service.get('volumes', [])):
        raise RuntimeError('Only schedule may mount the material volume')
    tags = {services[s]['image'].rsplit(':', 1)[-1] for s in (*SERVICES, 'web')}
    if len(tags) != 1 or not re.fullmatch(r'[a-f0-9]{40}', next(iter(tags))):
        raise RuntimeError('All application images must use the same full commit SHA')
    return next(iter(tags))


class Stack:
    def __init__(self, env_file, compose_file, project=None, *, legacy=False, disposable=False, image_tag=None):
        self.env_file = Path(env_file).resolve(); self.compose_file = Path(compose_file).resolve()
        if not self.env_file.is_file() or self.env_file.stat().st_mode & 0o077:
            raise RuntimeError('The environment file must exist and have mode 600')
        self.env_digest = sha256(self.env_file)
        self.environment = dict(os.environ)
        if image_tag: self.environment['IMAGE_TAG'] = image_tag
        self.prefix = ['docker', 'compose', '--env-file', str(self.env_file), '-f', str(self.compose_file)]
        if project: self.prefix += ['--project-name', project]
        self.legacy = legacy
        self.disposable = disposable
        self.config = json.loads(self.dc('config', '--format', 'json', what='Compose validation'))
        self.project = self.config['name']
        if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,60}', self.project):
            raise RuntimeError('Invalid Compose project name')
        if disposable and (os.getenv('CAMPUS_DISPOSABLE') != '1' or not self.project.startswith('campus-ci-')):
            raise RuntimeError('Disposable mode requires CAMPUS_DISPOSABLE=1 and a campus-ci- project')
        self.tag = None if legacy else validate_config(self.config, disposable=disposable)
        if self.tag: self.environment['IMAGE_TAG'] = self.tag
        self.state = ROOT/'.production'/self.project
        self.state.mkdir(parents=True, exist_ok=True, mode=0o700)

    def dc(self, *args, what='Compose operation', **kwargs):
        return run([*self.prefix, *args], what=what, cwd=self.compose_file.parent, env=self.environment, **kwargs)

    def pull(self):
        # CI builds local images with real commit labels; live deployments pull GHCR.
        if not self.disposable: self.dc('pull', what='Download prebuilt images')

    def containers(self):
        ids = self.dc('ps', '--all', '--quiet').split()
        if not ids: return []
        return json.loads(run(['docker', 'inspect', *ids], what='Container inspection'))

    def running_apps(self):
        return [c['Config']['Labels']['com.docker.compose.service'] for c in self.containers()
                if c['State']['Running'] and c['Config']['Labels']['com.docker.compose.service'] in writer_apps(self)]

    def live_tag(self):
        tags = {c['Config']['Labels'].get('org.opencontainers.image.revision') for c in self.containers()
                if c['Config']['Labels']['com.docker.compose.service'] in APPS}
        tags.discard(None)
        if len(tags) > 1: raise RuntimeError('Running application images have different versions')
        return next(iter(tags), None)

    def token(self):
        return self.config['services']['auth']['environment']['INTERNAL_TOKEN']

    def check_live_secret(self):
        for container in self.containers():
            if container['Config']['Labels']['com.docker.compose.service'] not in SERVICES: continue
            env = dict(v.split('=', 1) for v in container['Config']['Env'] if '=' in v)
            if env.get('INTERNAL_TOKEN') != self.token():
                raise RuntimeError('INTERNAL_TOKEN differs from the installed application; preserve the existing key')

    def psql(self, service, sql, *, database=None):
        db_container, user = (service+'-db', service) if self.legacy else ('postgres', 'postgres')
        return self.dc('exec', '-T', db_container, 'psql', '-X', '-At', '-v', 'ON_ERROR_STOP=1',
                       '-U', user, '-d', database or service, input=sql, what='Database inspection: '+service).strip()

    def snapshot(self, service, *, database=None, columns=None):
        version = int(self.psql(service, 'SELECT COALESCE(MAX(version),0) FROM schema_migrations;', database=database))
        if columns is None:
            raw = self.psql(service, "SELECT COALESCE(json_agg(row_to_json(t)), '[]') FROM (SELECT table_name, column_name FROM information_schema.columns WHERE table_schema='public' AND table_name <> 'schema_migrations' ORDER BY table_name, ordinal_position) t;", database=database)
            columns = {}
            for row in json.loads(raw): columns.setdefault(row['table_name'], []).append(row['column_name'])
        tables = {}
        quote = lambda name: '"'+name.replace('"', '""')+'"'
        for table, names in columns.items():
            sql = "SELECT json_build_object('count', count(*), 'digest', md5(COALESCE(string_agg(md5(row_to_json(r)::text), '' ORDER BY md5(row_to_json(r)::text)), ''))) FROM (SELECT "+', '.join(map(quote,names))+' FROM public.'+quote(table)+') r;'
            tables[table] = {**json.loads(self.psql(service, sql, database=database)), 'columns':names}
        return {'schema':version, 'tables':tables}

    def dump(self, service, path):
        db_container, user = (service+'-db', service) if self.legacy else ('postgres', 'postgres')
        with open(path, 'wb', opener=lambda p, flags: os.open(p, flags, 0o600)) as output:
            self.dc('exec', '-T', db_container, 'pg_dump', '-U', user, '-d', service, '-Fc', '--no-owner', '--no-acl',
                    output=output, what='Backup '+service)

    def has_materials(self):
        service = self.config['services']['schedule']
        directory = service.get('environment', {}).get('MATERIALS_DIR')
        return directory == '/var/lib/campus/materials' and any(
            item.get('target') == directory and not item.get('read_only', False)
            for item in service.get('volumes', []))

    def material_state(self):
        if not self.has_materials(): return {'bytes':0, 'files':0}
        result = self.dc('run', '--rm', '--no-deps', '-T', 'schedule',
            'python', '-m', 'scripts.materials_archive', 'inventory', '/var/lib/campus/materials',
            what='Inspect private material storage')
        return json.loads(result)

    def material_metadata(self, schema):
        if schema < 5: return {}
        raw = self.psql('schedule', "SELECT COALESCE(json_agg(json_build_object('id', id, 'bytes', size_bytes, 'sha256', sha256)), '[]') FROM materials;")
        return materials.metadata_inventory(json.loads(raw))

    def archive_materials(self, path):
        if not self.has_materials(): return None
        with open(path, 'xb', opener=lambda p, f: os.open(p, f, 0o600)) as output:
            self.dc('run', '--rm', '--no-deps', '-T', 'schedule', 'python', '-m',
                'scripts.materials_archive', 'create', '/var/lib/campus/materials',
                output=output, what='Stream private material backup')
        return materials.validate_archive(path)

    def stage_materials(self, folder, manifest):
        if not self.has_materials(): raise RuntimeError('Private material volume is not configured')
        header = json.dumps({'has_archive':'materials.tar' in manifest['files'],
            'materials':manifest.get('materials', {})}).encode()+b'\n'
        command = ('run', '--rm', '--no-deps', '-T', 'schedule', 'python', '-m',
            'scripts.materials_archive', 'stage-stream', '/var/lib/campus/materials')
        if 'materials.tar' in manifest['files']:
            with (folder/'materials.tar').open('rb') as source:
                result = self.dc(*command, prefix_input=header, source=source,
                    what='Stream verified material snapshot to disk stage')
        else:
            result = self.dc(*command, prefix_input=header, what='Stage empty legacy material snapshot')
        stage = (result.decode() if isinstance(result, bytes) else result).strip()
        if not materials.STAGE_NAME.fullmatch(stage): raise RuntimeError('Invalid material restore stage')
        return stage

    def publish_materials(self, stage):
        self.dc('run', '--rm', '--no-deps', '-T', 'schedule', 'python', '-m',
            'scripts.materials_archive', 'publish', '/var/lib/campus/materials', stage,
            what='Publish complete private material snapshot')

    def discard_materials(self, stage):
        self.dc('run', '--rm', '--no-deps', '-T', 'schedule', 'python', '-m',
            'scripts.materials_archive', 'discard', '/var/lib/campus/materials', stage,
            what='Discard unpublished material restore stage')

    def restore_dump(self, service, path, *, database=None):
        if self.legacy: raise RuntimeError('Use the legacy restore script for a multi-instance stack')
        db = database or service
        if service not in SERVICES or not re.fullmatch(r'[a-z0-9_]+', db): raise RuntimeError('Invalid restore destination')
        command = 'export PGPASSWORD="$'+service.upper()+'_DB_PASSWORD"; exec pg_restore -h 127.0.0.1 -U '+service+' -d '+db+' --no-owner --no-acl --exit-on-error'
        with path.open('rb') as source:
            self.dc('exec', '-T', 'postgres', 'sh', '-c', command, source=source, what='Restore '+service)


def backup(stack, *, resume=True):
    stack.check_live_secret()
    folder = ROOT/'backups'/('production-'+stamp())
    folder.mkdir(parents=True, mode=0o700)
    active = stack.running_apps()
    snapshots = {}; files = {}
    # All active databases must be reachable before stopping the application.
    required = sum(int(stack.psql(s, 'SELECT pg_database_size(current_database());')) for s in SERVICES)
    required += getattr(stack, 'material_state', lambda:{'bytes':0})()['bytes']
    if shutil.disk_usage(folder).free < required*1.25+512*1024**2:
        raise RuntimeError('Insufficient free space for a complete backup')
    stack.dc('stop', *writer_apps(stack), what='Pause writers for a consistent backup')
    complete = False
    try:
        shutil.copyfile(stack.env_file, folder/'config.env'); os.chmod(folder/'config.env', 0o600)
        private_json(folder/'effective-config.json', stack.config)
        for service in SERVICES:
            snapshots[service] = stack.snapshot(service)
            path = folder/(service+'.dump'); stack.dump(service, path)
            if not path.stat().st_size: raise RuntimeError('Empty database backup: '+service)
        material_files = getattr(stack, 'archive_materials', lambda path:None)(folder/'materials.tar')
        if snapshots['schedule']['schema'] >= 5 and material_files is None:
            raise RuntimeError('Schema 5 requires its private materials volume and archive')
        if material_files is not None:
            expected = stack.material_metadata(snapshots['schedule']['schema'])
            if material_files != expected:
                raise RuntimeError('Material files differ from database metadata; backup is incomplete')
        for path in folder.iterdir():
            files[path.name] = {'sha256':sha256(path), 'bytes':path.stat().st_size}
        manifest = {'format':1, 'created_at':stamp(), 'git_sha':stack.live_tag() or 'unknown',
                    'schema_versions':{s:snapshots[s]['schema'] for s in SERVICES}, 'snapshots':snapshots,
                    'files':files, 'running_apps':active,
                    'internal_token_sha256':hashlib.sha256(stack.token().encode()).hexdigest()}
        if material_files is not None: manifest['materials'] = material_files
        private_json(folder/'manifest.json', manifest)
        (folder/'COMPLETE').touch(mode=0o600)
        complete = True
        print('Backup complete:', folder)
        return folder
    finally:
        if active and (resume or not complete): stack.dc('start', *active, what='Resume original containers')


def verify_backup(folder, token, *, supported=SCHEMAS):
    folder = Path(folder)
    if folder.is_symlink() or not folder.is_dir(): raise RuntimeError('Invalid backup directory')
    folder = folder.resolve()
    for name in ('COMPLETE', 'manifest.json'):
        if (folder/name).is_symlink() or not (folder/name).is_file(): raise RuntimeError('Backup is incomplete')
    try:
        manifest = json.loads((folder/'manifest.json').read_text())
    except (ValueError, OSError) as error:
        raise RuntimeError('Invalid backup manifest') from error
    if not isinstance(manifest, dict): raise RuntimeError('Unknown backup format')
    versions = manifest.get('schema_versions', {})
    if not isinstance(versions, dict): raise RuntimeError('Unknown backup format')
    recorded_services = set(versions)
    if manifest.get('format') != 1 or recorded_services not in (set(SERVICES), set(SERVICES) | {'queue'}):
        raise RuntimeError('Unknown backup format')
    if manifest.get('internal_token_sha256') != hashlib.sha256(token.encode()).hexdigest():
        raise RuntimeError('Backup requires its original INTERNAL_TOKEN; no databases were changed')
    for service, schema in versions.items():
        maximum = 2 if service == 'queue' else supported[service]
        if type(schema) is not int or not 2 <= schema <= maximum:
            raise RuntimeError('Backup schema is incompatible with this application: '+service)
    expected_files = {s+'.dump' for s in recorded_services} | {'config.env', 'effective-config.json'}
    files = manifest.get('files', {})
    if not isinstance(files, dict): raise RuntimeError('Backup file list is invalid')
    has_materials = 'materials.tar' in files
    if versions['schedule'] >= 5 and not has_materials:
        raise RuntimeError('Schema 5 backup requires materials.tar')
    if has_materials: expected_files.add('materials.tar')
    if set(files) != expected_files: raise RuntimeError('Backup file list is invalid')
    for name, expected in files.items():
        path = folder/name
        valid = isinstance(expected, dict) and set(expected) == {'sha256','bytes'} and isinstance(expected['sha256'], str) and re.fullmatch(r'[a-f0-9]{64}', expected['sha256']) and type(expected['bytes']) is int and expected['bytes'] > 0
        if not valid or path.is_symlink() or not path.is_file() or sha256(path) != expected['sha256'] or path.stat().st_size != expected['bytes']:
            raise RuntimeError('Backup checksum mismatch: '+name)
    if has_materials:
        if 'materials' not in manifest: raise RuntimeError('Missing material checksum inventory')
        materials.validate_archive(folder/'materials.tar', manifest['materials'])
    elif manifest.get('materials'):
        raise RuntimeError('Material inventory requires materials.tar')
    return manifest


def validate_restore_snapshots(manifest):
    """Production comparison requires snapshots; local backup verification does not."""
    snapshots = manifest.get('snapshots')
    if not isinstance(snapshots, dict) or not set(SERVICES) <= set(snapshots) or set(snapshots) - set(SERVICES) - {'queue'}:
        raise RuntimeError('Production restore requires valid database snapshots; use the local restore script for local backups')
    for service in SERVICES:
        snapshot = snapshots[service]
        if not isinstance(snapshot, dict) or set(snapshot) != {'schema','tables'} or type(snapshot['schema']) is not int or snapshot['schema'] != manifest['schema_versions'][service] or not isinstance(snapshot['tables'], dict):
            raise RuntimeError('Invalid restore snapshot: '+service)
        for name, table in snapshot['tables'].items():
            valid = isinstance(name, str) and bool(name) and isinstance(table, dict) and set(table) == {'count','digest','columns'}
            if valid:
                valid = type(table['count']) is int and table['count'] >= 0 and isinstance(table['digest'], str) and re.fullmatch(r'[a-f0-9]{32}', table['digest']) and isinstance(table['columns'], list) and bool(table['columns']) and all(isinstance(column, str) and column for column in table['columns'])
            if not valid: raise RuntimeError('Invalid restore snapshot table: '+service)


def compare_snapshots(before, after, *, upgraded=False):
    if before['tables'] != after['tables']:
        changed = [name for name in before['tables'] if before['tables'][name] != after['tables'].get(name)]
        raise RuntimeError('Restored data differs: '+', '.join(changed))
    if not upgraded and before['schema'] != after['schema']:
        raise RuntimeError('Restored schema version differs')


def check_role_isolation(stack):
    if stack.legacy: raise RuntimeError('Role isolation check requires the production stack')
    for service in SERVICES:
        # Password stays in the container environment, never in command arguments.
        variable = service.upper()+'_DB_PASSWORD'
        command = 'export PGPASSWORD="$'+variable+'"; exec psql -X -At -h 127.0.0.1 -U '+service+' -d '+service+' -c "SELECT current_user"'
        actual = stack.dc('exec', '-T', 'postgres', 'sh', '-c', command, what='Service database login').strip()
        if actual != service: raise RuntimeError('Wrong database role')
        sql = "SELECT rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls FROM pg_roles WHERE rolname='"+service+"';"
        if stack.psql(service, sql) != 'f': raise RuntimeError('Excessive database privileges')
        for other in (*SERVICES, 'postgres', 'template1'):
            if other == service: continue
            if stack.psql(service, "SELECT has_database_privilege('"+service+"', '"+other+"', 'CONNECT');") != 'f':
                raise RuntimeError('Cross-database access is allowed')
    print('PASS: three service logins, no elevated roles, no cross-database CONNECT')


def test_restore(stack, folder):
    folder = Path(folder); manifest = verify_backup(folder, stack.token()); folder = folder.resolve()
    validate_restore_snapshots(manifest)
    with tempfile.TemporaryDirectory(prefix='cf-restore-materials-', dir=folder.parent) as temporary:
        directory = Path(temporary)
        archive = folder/'materials.tar' if 'materials.tar' in manifest['files'] else None
        stage = materials.stage_archive(directory, archive, manifest.get('materials', {}))
        materials.publish_stage(directory, stage)
    prefix = 'cf_restore_'+uuid.uuid4().hex[:12]
    for service in SERVICES:
        name = prefix+'_'+service
        stack.psql(service, f'CREATE DATABASE {name} OWNER {service};', database='postgres')
        try:
            stack.psql(service, f'REVOKE ALL ON DATABASE {name} FROM PUBLIC;', database='postgres')
            stack.restore_dump(service, folder/(service+'.dump'), database=name)
            compare_snapshots(manifest['snapshots'][service], stack.snapshot(service, database=name))
        finally:
            stack.psql(service, f'DROP DATABASE {name} WITH (FORCE);', database='postgres')
    print('PASS: backup restored and compared in isolated temporary databases and material directory')


def confirm(word):
    if input('Type '+word+' to continue: ').strip() != word:
        raise RuntimeError('Operation cancelled')


def restore(stack, folder):
    folder = Path(folder); manifest = verify_backup(folder, stack.token()); folder = folder.resolve()
    validate_restore_snapshots(manifest)
    inspect_images(stack)
    print('Restore replaces all current accounts, timetables, notifications and material files.')
    confirm('RESTORE')
    before = backup(stack, resume=False)
    print('Pre-restore backup:', before, '\nApplication stays stopped if any restore step fails.')
    stage = stack.stage_materials(folder, manifest)
    try:
        for service in SERVICES:
            stack.psql(service, f'DROP SCHEMA public CASCADE; CREATE SCHEMA public AUTHORIZATION {service};')
            stack.restore_dump(service, folder/(service+'.dump'))
            compare_snapshots(manifest['snapshots'][service], stack.snapshot(service))
        if stack.material_metadata(manifest['schema_versions']['schedule']) != manifest.get('materials', {}):
            raise RuntimeError('Restored material metadata differs from files')
        stack.publish_materials(stage); stage = None
    finally:
        if stage is not None: stack.discard_materials(stage)
    stack.dc('up', '-d', '--wait', '--wait-timeout', '300', what='Start restored application')
    smoke(stack)
    print('PASS: all three databases and material files restored and application verified')


def smoke(stack):
    from scripts.prod_smoke import check
    port = stack.config['services']['web']['ports'][0]['published']
    return check('http://127.0.0.1:'+str(port), stack.tag)


def inspect_images(stack, *, schemas=None):
    for name in ('auth', 'web'):
        image = stack.config['services'][name]['image']
        info = json.loads(run(['docker', 'image', 'inspect', image], what='Image metadata check'))[0]
        labels = info['Config'].get('Labels') or {}
        if labels.get('org.opencontainers.image.revision') != stack.tag:
            raise RuntimeError('Image commit label differs from IMAGE_TAG')
        if name == 'auth':
            supported = json.loads(labels.get('io.campus-flow.schemas', '{}'))
            if set(supported) != set(SERVICES): raise RuntimeError('Image schema metadata is missing')
            if any((schemas or SCHEMAS)[s] != supported[s] for s in SERVICES):
                raise RuntimeError('Image rollback is incompatible with live schemas; restore a matching backup instead')


def disk_check():
    if shutil.disk_usage(ROOT).free < 3*1024**3:
        raise RuntimeError('Keep at least 3 GiB free before downloading images and backing up')


def deploy(stack):
    disk_check(); stack.check_live_secret()
    previous = stack.live_tag()
    # Pull and verify before maintenance so a registry failure leaves the site up.
    stack.pull()
    inspect_images(stack)
    containers = stack.containers()
    db_exists = any(c['Config']['Labels']['com.docker.compose.service'] == 'postgres' for c in containers)
    presence = [stack.psql(s, "SELECT to_regclass('public.schema_migrations') IS NOT NULL;") == 't' for s in SERVICES] if db_exists else [False]*len(SERVICES)
    if any(presence) and not all(presence): raise RuntimeError('Partial database initialization; inspect the migration before deployment')
    folder = backup(stack, resume=False) if all(presence) else None
    stack.dc('up', '-d', '--wait', '--wait-timeout', '300', '--remove-orphans', what='Deploy verified images')
    smoke(stack)
    persist_release(stack)
    private_json(stack.state/'deployment.json', {'current':stack.tag, 'previous':previous,
        'backup':str(folder) if folder else None, 'at':stamp()})
    diagnose(stack, include_logs=False)


def rollback(stack, tag, disposable=False):
    history_file = stack.state/'deployment.json'
    if not tag:
        history = json.loads(history_file.read_text()) if history_file.exists() else {}
        tag = history.get('previous')
    if not tag or not re.fullmatch(r'[a-f0-9]{40}', tag): raise RuntimeError('Specify the previous full image SHA')
    target = Stack(stack.env_file, stack.compose_file, stack.project, disposable=disposable, image_tag=tag)
    target.pull()
    versions = {s:int(stack.psql(s, 'SELECT MAX(version) FROM schema_migrations;')) for s in SERVICES}
    inspect_images(target, schemas=versions)
    confirm('ROLLBACK')
    deploy(target)
    print('Rollback verified; IMAGE_TAG saved in the environment file:', tag)


def migrate_databases(source, target):
    if source.project == target.project: raise RuntimeError('Source and destination projects must differ')
    if source.token() != target.token(): raise RuntimeError('Copy the existing INTERNAL_TOKEN into production first')
    disk_check()
    for service in SERVICES: source.psql(service, 'SELECT 1;')
    if target.running_apps(): raise RuntimeError('Destination applications must be stopped')
    target.pull(); inspect_images(target)
    print('Migration pauses the source app, preserves all old volumes, and prepares the destination without exposing web.')
    confirm('MIGRATE')
    source_active = source.running_apps()
    retired_source_active = [c['Config']['Labels']['com.docker.compose.service'] for c in source.containers()
        if c['State']['Running'] and c['Config']['Labels']['com.docker.compose.service'] == 'queue-db']
    folder = backup(source, resume=False)
    complete = False; stage = None; target_touched = False
    try:
        manifest = verify_backup(folder, target.token())
        # Even creating new destination volumes follows archive validation.
        target_touched = True
        if target.material_state()['files']:
            raise RuntimeError('Destination material volume is not empty; no source data was changed')
        target.dc('up', '-d', '--wait', '--wait-timeout', '180', 'postgres', what='Start destination database')
        for service in SERVICES:
            count = target.psql(service, "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';")
            if count != '0': raise RuntimeError('Destination is not empty; no source data was changed')
        check_role_isolation(target)
        stage = target.stage_materials(folder, manifest)
        for service in SERVICES:
            target.restore_dump(service, folder/(service+'.dump'))
            compare_snapshots(manifest['snapshots'][service], target.snapshot(service))
        if target.material_metadata(manifest['schema_versions']['schedule']) != manifest.get('materials', {}):
            raise RuntimeError('Migrated material metadata differs from files')
        target.publish_materials(stage); stage = None
        # The public gateway is deliberately not started during the migration.
        target.dc('up', '-d', '--wait', '--wait-timeout', '300', *SERVICES, what='Apply application migrations')
        versions = {s:int(target.psql(s, 'SELECT MAX(version) FROM schema_migrations;')) for s in SERVICES}
        if versions != SCHEMAS: raise RuntimeError('Unexpected migrated schema versions')
        private_json(target.state/'migration.json', {'source_project':source.project, 'backup':str(folder),
            'schema_versions':versions, 'at':stamp()})
        retired_databases = [name for name in ('queue-db',) if name in source.config['services']]
        if retired_databases:
            source.dc('stop', *retired_databases, what='Stop retired source database; preserve its volume')
        complete = True
        print('PASS: source dumps and destination data matched before upgrades; schemas:', json.dumps(versions))
        print('Original volumes remain intact. Source app is paused. Run deploy_production.sh to start the destination web gateway.')
    finally:
        try:
            if stage is not None: target.discard_materials(stage)
        finally:
            if not complete:
                if target_touched: target.dc('stop', *APPS, what='Keep partial destination unexposed')
                if retired_source_active: source.dc('start', *retired_source_active, what='Resume original source database')
                if source_active: source.dc('start', *source_active, what='Resume unchanged source')


def diagnose(stack, *, include_logs=True):
    print(stack.dc('ps', what='Container status'))
    containers = stack.containers(); ids = [c['Id'] for c in containers]
    if ids: print(run(['docker','stats','--no-stream',*ids], what='Container resource sample'))
    print(run(['free','-h'], what='Host memory sample'))
    print(run(['df','-h',str(ROOT)], what='Host disk sample'))
    print(run(['docker','system','df'], what='Docker disk sample'))
    summary = [{'service':c['Config']['Labels']['com.docker.compose.service'],
                'restarts':c['RestartCount'], 'oom':c['State']['OOMKilled'],
                'health':c['State'].get('Health',{}).get('Status',c['State']['Status']) if c['State']['Running'] else c['State']['Status']} for c in containers]
    print(json.dumps(summary, indent=2))
    if include_logs:
        # Whitelist structured fields. Never reproduce arbitrary exception/SQL text.
        lines = stack.dc('logs', '--no-color', '--no-log-prefix', '--tail', '40').splitlines()
        keys = {'time','service','request_id','method','route','status','duration_ms','duration_s'}
        for line in lines:
            try: item = json.loads(line[line.index('{'):])
            except (ValueError, json.JSONDecodeError): continue
            if isinstance(item, dict) and 'request_id' in item:
                print(json.dumps({k:v for k,v in item.items() if k in keys}, ensure_ascii=False))
    return summary


def budget(stack):
    containers = stack.containers()
    expected = {*SERVICES, 'web', 'postgres'}
    if {c['Config']['Labels']['com.docker.compose.service'] for c in containers} != expected:
        raise RuntimeError('Expected the complete production stack')
    total_limits = 0
    for c in containers:
        if c['State'].get('Health',{}).get('Status') != 'healthy' or c['State']['OOMKilled'] or c['RestartCount']:
            raise RuntimeError('Unhealthy container, OOM or restart detected')
        host = c['HostConfig']; total_limits += host['Memory']
        if not host['Memory'] or not host['NanoCpus'] or not host['PidsLimit']:
            raise RuntimeError('Resource limits were not applied')
        if host['LogConfig'] != {'Type':'json-file','Config':{'max-file':'3','max-size':'10m'}}:
            raise RuntimeError('Log rotation was not applied')
    if total_limits > 1280*1024**2: raise RuntimeError('Container limits exceed the small-host budget')
    check_role_isolation(stack); smoke(stack); diagnose(stack, include_logs=False)
    print('Configured container memory ceiling MiB:', total_limits//1024**2)
    print('This host sample is not proof of acceptance on a real Timeweb 2 GB VM.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['validate','backup','test-restore','restore','deploy','rollback','migrate','diagnose','budget'])
    parser.add_argument('--env-file', default=str(ROOT/'.env.production'))
    parser.add_argument('--compose-file', default=str(ROOT/'compose.production.yaml'))
    parser.add_argument('--project')
    parser.add_argument('--disposable', action='store_true')
    parser.add_argument('--backup')
    parser.add_argument('--image-tag')
    parser.add_argument('--source-env', default=str(ROOT/'.env'))
    parser.add_argument('--source-compose', default=str(ROOT/'compose.yaml'))
    parser.add_argument('--source-project')
    args = parser.parse_args()
    os.umask(0o077)
    stack = Stack(args.env_file, args.compose_file, args.project, disposable=args.disposable, image_tag=args.image_tag)
    source = None
    if args.action == 'migrate':
        source = Stack(args.source_env, args.source_compose, args.source_project, legacy=True, disposable=args.disposable)
    with ExitStack() as locks:
        if args.action not in ('validate','diagnose','budget'):
            for item in sorted([stack]+([source] if source else []), key=lambda s:s.project):
                locks.enter_context(operation_lock(item))
        if args.action == 'validate': print('PASS: image-only production config, private ports, bounded resources and credentials')
        elif args.action == 'backup': backup(stack)
        elif args.action in ('test-restore','restore'):
            if not args.backup: parser.error('--backup is required')
            (test_restore if args.action == 'test-restore' else restore)(stack, args.backup)
        elif args.action == 'deploy': deploy(stack)
        elif args.action == 'rollback': rollback(stack, args.image_tag, args.disposable)
        elif args.action == 'migrate': migrate_databases(source, stack)
        elif args.action == 'diagnose': diagnose(stack)
        elif args.action == 'budget': budget(stack)


if __name__ == '__main__':
    sys.path.insert(0, str(ROOT))
    try: main()
    except (RuntimeError, OSError, ValueError, KeyError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
