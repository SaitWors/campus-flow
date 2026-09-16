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
import uuid
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
SERVICES = ('auth', 'schedule', 'queue', 'notifications')
APPS = ('web', 'notifications', 'queue', 'schedule', 'auth')
SCHEMAS = {'auth': 3, 'schedule': 3, 'queue': 2, 'notifications': 2}


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


def run(args, *, what, cwd=ROOT, env=None, input=None, output=None, source=None):
    # Never echo a Docker config, SQL error or command environment to public logs.
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
        raise RuntimeError('Expected four APIs, web and one PostgreSQL; no translator')
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
    if len(passwords) != 5 or len(set(passwords)) != 5 or any(not re.fullmatch(r'[A-Za-z0-9_-]{32,}', p) for p in passwords):
        raise RuntimeError('Use five different URL-safe database passwords of at least 32 characters')
    for name in SERVICES:
        env = services[name]['environment']
        size, overflow = int(env['DB_POOL_SIZE']), int(env['DB_MAX_OVERFLOW'])
        if size < 1 or overflow < 0 or size+overflow > 4 or str(env['DB_POOL_PRE_PING']).lower() != 'true':
            raise RuntimeError('Small-server profile permits at most four pooled connections per API')
    if services['schedule']['environment'].get('TRANSLATION_WORKER') != 'false':
        raise RuntimeError('The local translator must be disabled in this profile')
    tags = {services[s]['image'].rsplit(':', 1)[-1] for s in (*SERVICES, 'web')}
    if len(tags) != 1 or not re.fullmatch(r'[a-f0-9]{40}', next(iter(tags))):
        raise RuntimeError('All application images must use the same full commit SHA')
    return next(iter(tags))


class Stack:
    def __init__(self, env_file, compose_file, project=None, *, legacy=False, disposable=False, image_tag=None):
        self.env_file = Path(env_file).resolve(); self.compose_file = Path(compose_file).resolve()
        if not self.env_file.is_file() or self.env_file.stat().st_mode & 0o077:
            raise RuntimeError('The environment file must exist and have mode 600')
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
                if c['State']['Running'] and c['Config']['Labels']['com.docker.compose.service'] in APPS]

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

    def restore_dump(self, service, path, *, database=None):
        if self.legacy: raise RuntimeError('Use the legacy restore script for a four-instance stack')
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
    # All four databases must be reachable before stopping the application.
    required = sum(int(stack.psql(s, 'SELECT pg_database_size(current_database());')) for s in SERVICES)
    if shutil.disk_usage(folder).free < required*1.25+512*1024**2:
        raise RuntimeError('Insufficient free space for a complete backup')
    stack.dc('stop', *APPS, what='Pause writers for a consistent backup')
    complete = False
    try:
        shutil.copyfile(stack.env_file, folder/'config.env'); os.chmod(folder/'config.env', 0o600)
        private_json(folder/'effective-config.json', stack.config)
        for service in SERVICES:
            snapshots[service] = stack.snapshot(service)
            path = folder/(service+'.dump'); stack.dump(service, path)
            if not path.stat().st_size: raise RuntimeError('Empty database backup: '+service)
        for path in folder.iterdir():
            files[path.name] = {'sha256':sha256(path), 'bytes':path.stat().st_size}
        manifest = {'format':1, 'created_at':stamp(), 'git_sha':stack.live_tag() or 'unknown',
                    'schema_versions':{s:snapshots[s]['schema'] for s in SERVICES}, 'snapshots':snapshots,
                    'files':files, 'running_apps':active,
                    'internal_token_sha256':hashlib.sha256(stack.token().encode()).hexdigest()}
        private_json(folder/'manifest.json', manifest)
        (folder/'COMPLETE').touch(mode=0o600)
        complete = True
        print('Backup complete:', folder)
        return folder
    finally:
        if active and (resume or not complete): stack.dc('start', *active, what='Resume original containers')


def verify_backup(folder, token, *, supported=SCHEMAS):
    folder = Path(folder).resolve()
    if not (folder/'COMPLETE').is_file(): raise RuntimeError('Backup is incomplete')
    manifest = json.loads((folder/'manifest.json').read_text())
    if manifest.get('format') != 1 or set(manifest.get('schema_versions', {})) != set(SERVICES):
        raise RuntimeError('Unknown backup format')
    if manifest.get('internal_token_sha256') != hashlib.sha256(token.encode()).hexdigest():
        raise RuntimeError('Backup requires its original INTERNAL_TOKEN; no databases were changed')
    expected_files = {s+'.dump' for s in SERVICES} | {'config.env', 'effective-config.json'}
    if set(manifest.get('files', {})) != expected_files: raise RuntimeError('Backup file list is invalid')
    for name, expected in manifest['files'].items():
        path = folder/name
        if path.is_symlink() or not path.is_file() or sha256(path) != expected['sha256'] or path.stat().st_size != expected['bytes']:
            raise RuntimeError('Backup checksum mismatch: '+name)
    for service, schema in manifest['schema_versions'].items():
        if not 2 <= schema <= supported[service]:
            raise RuntimeError('Backup schema is incompatible with this application: '+service)
    return manifest


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
    print('PASS: four service logins, no elevated roles, no cross-database CONNECT')


def test_restore(stack, folder):
    folder = Path(folder).resolve(); manifest = verify_backup(folder, stack.token())
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
    print('PASS: backup restored and compared in isolated temporary databases')


def confirm(word):
    if input('Type '+word+' to continue: ').strip() != word:
        raise RuntimeError('Operation cancelled')


def restore(stack, folder):
    folder = Path(folder).resolve(); manifest = verify_backup(folder, stack.token())
    inspect_images(stack)
    print('Restore replaces all current accounts, timetables, queues and notifications.')
    confirm('RESTORE')
    before = backup(stack, resume=False)
    print('Pre-restore backup:', before, '\nApplication stays stopped if any restore step fails.')
    for service in SERVICES:
        stack.psql(service, f'DROP SCHEMA public CASCADE; CREATE SCHEMA public AUTHORIZATION {service};')
        stack.restore_dump(service, folder/(service+'.dump'))
        compare_snapshots(manifest['snapshots'][service], stack.snapshot(service))
    stack.dc('up', '-d', '--wait', '--wait-timeout', '300', what='Start restored application')
    smoke(stack)
    print('PASS: all four databases restored and application verified')


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
    presence = [stack.psql(s, "SELECT to_regclass('public.schema_migrations') IS NOT NULL;") == 't' for s in SERVICES] if db_exists else [False]*4
    if any(presence) and not all(presence): raise RuntimeError('Partial database initialization; inspect the migration before deployment')
    folder = backup(stack, resume=False) if all(presence) else None
    stack.dc('up', '-d', '--wait', '--wait-timeout', '300', what='Deploy verified images')
    smoke(stack)
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
    print('Set IMAGE_TAG='+tag+' in your environment file before the next manual Compose command.')


def migrate_four(source, target):
    if source.project == target.project: raise RuntimeError('Source and destination projects must differ')
    if source.token() != target.token(): raise RuntimeError('Copy the existing INTERNAL_TOKEN into production first')
    disk_check()
    for service in SERVICES: source.psql(service, 'SELECT 1;')
    if target.running_apps(): raise RuntimeError('Destination applications must be stopped')
    target.pull(); inspect_images(target)
    target.dc('up', '-d', '--wait', '--wait-timeout', '180', 'postgres', what='Start destination database')
    for service in SERVICES:
        count = target.psql(service, "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';")
        if count != '0': raise RuntimeError('Destination is not empty; no source data was changed')
    check_role_isolation(target)
    print('Migration pauses the source app, preserves all old volumes, and prepares the destination without exposing web.')
    confirm('MIGRATE')
    source_active = source.running_apps()
    folder = backup(source, resume=False)
    complete = False
    try:
        manifest = verify_backup(folder, target.token())
        for service in SERVICES:
            target.restore_dump(service, folder/(service+'.dump'))
            compare_snapshots(manifest['snapshots'][service], target.snapshot(service))
        # The public gateway is deliberately not started during the migration.
        target.dc('up', '-d', '--wait', '--wait-timeout', '300', *SERVICES, what='Apply application migrations')
        versions = {s:int(target.psql(s, 'SELECT MAX(version) FROM schema_migrations;')) for s in SERVICES}
        if versions != SCHEMAS: raise RuntimeError('Unexpected migrated schema versions')
        private_json(target.state/'migration.json', {'source_project':source.project, 'backup':str(folder),
            'schema_versions':versions, 'at':stamp()})
        complete = True
        print('PASS: source dumps and destination data matched before upgrades; schemas:', json.dumps(versions))
        print('Original volumes remain intact. Source app is paused. Run deploy_production.sh to start the destination web gateway.')
    finally:
        if not complete:
            target.dc('stop', *APPS, what='Keep partial destination unexposed')
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
                'health':c['State'].get('Health',{}).get('Status',c['State']['Status'])} for c in containers]
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
    if args.action == 'validate': print('PASS: image-only production config, private ports, bounded resources and credentials')
    elif args.action == 'backup': backup(stack)
    elif args.action in ('test-restore','restore'):
        if not args.backup: parser.error('--backup is required')
        (test_restore if args.action == 'test-restore' else restore)(stack, args.backup)
    elif args.action == 'deploy': deploy(stack)
    elif args.action == 'rollback': rollback(stack, args.image_tag, args.disposable)
    elif args.action == 'migrate':
        source = Stack(args.source_env, args.source_compose, args.source_project, legacy=True, disposable=args.disposable)
        migrate_four(source, stack)
    elif args.action == 'diagnose': diagnose(stack)
    elif args.action == 'budget': budget(stack)


if __name__ == '__main__':
    sys.path.insert(0, str(ROOT))
    try: main()
    except (RuntimeError, OSError, ValueError, KeyError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
