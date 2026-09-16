"""Destructive integration tests, ONLY for newly created disposable CI projects."""
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
from unittest.mock import patch

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import production as p
from scripts.smoke import run as smoke
from scripts.verify_notifications import run as notifications
from scripts.verify_community import run as community
from scripts.verify_pr3 import run as pr3

PR2 = '3d3b650ac6ea2ac3b9953b829b43a658a4e97270'
# A real earlier PR3 revision with the same schema and version metadata.
PREVIOUS = '8074ee12fdd4b2b4ab6c1fb0b87c709813ace617'


def command(*args, cwd=ROOT, **kwargs):
    subprocess.run(args, cwd=cwd, check=True, **kwargs)


def environment(path, port, shared=None, **extra):
    values = {'APP_ORIGIN':f'http://localhost:{port}', 'HTTP_PORT':str(port), 'COOKIE_SECURE':'false',
              'IMAGE_REPOSITORY':'campus-flow-ci', 'IMAGE_TAG':os.environ['GITHUB_SHA'],
              'TRANSLATION_WORKER':'false', 'COMPOSE_PROFILES':'', 'PUSH_ENABLED':'true',
              'INTERNAL_TOKEN':secrets.token_hex(32), 'SETUP_KEY':secrets.token_hex(32)}
    values.update({s.upper()+'_DB_PASSWORD':secrets.token_hex(32) for s in p.SERVICES})
    values['POSTGRES_PASSWORD'] = secrets.token_hex(32)
    values.update(shared or {}); values.update(extra)
    path.write_text(''.join(k+'='+v+'\n' for k,v in values.items())); path.chmod(0o600)
    return values


def ensure_empty_project(stack):
    if stack.containers(): raise RuntimeError('Refusing to reuse existing containers: '+stack.project)
    volumes = p.run(['docker','volume','ls','--quiet','--filter','label=com.docker.compose.project='+stack.project], what='Disposable project check')
    if volumes.strip(): raise RuntimeError('Refusing to reuse existing volumes: '+stack.project)


def api_urls(port):
    return {s:f'http://localhost:{port}' for s in p.SERVICES}


def assert_pool_and_postgres(stack):
    expected = {'shared_buffers':'192MB','work_mem':'2MB','maintenance_work_mem':'64MB',
                'max_connections':'30','jit':'off','autovacuum_work_mem':'16MB'}
    for name, value in expected.items():
        assert stack.psql('auth', 'SHOW '+name+';') == value
    for service in p.SERVICES:
        code = "from services.common.core import database; e,_=database('probe'); assert e.pool.size()==2 and e.pool._max_overflow==2 and e.pool.timeout()==10 and e.pool._recycle==1800 and e.pool._pre_ping; e.dispose()"
        stack.dc('exec','-T',service,'python','-c',code,what='Runtime connection pool check')
    print('PASS: actual PostgreSQL memory settings and actual SQLAlchemy pools in all four containers')


def main():
    if os.getenv('CAMPUS_DISPOSABLE') != '1' or os.getenv('GITHUB_ACTIONS') != 'true':
        raise RuntimeError('This destructive workflow runs only in disposable GitHub Actions jobs')
    current = os.environ['GITHUB_SHA']
    old_checkout = ROOT/'.production'/'ci-pr2-source'
    previous_checkout = ROOT/'.production'/'ci-previous-release'
    old_checkout.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    command('git','worktree','add','--detach',str(old_checkout),PR2)
    command('git','worktree','add','--detach',str(previous_checkout),PREVIOUS)
    for checkout, revision in [(ROOT,current),(previous_checkout,PREVIOUS)]:
        for component in ('api','web'):
            command('docker','build','--build-arg','VCS_REF='+revision,
                    '--build-arg','BUILD_DATE=2026-09-16T00:00:00Z',
                    '-f','infra/'+component+'.Dockerfile','-t','campus-flow-ci-'+component+':'+revision,'.',cwd=checkout)
    created = []
    with tempfile.TemporaryDirectory(prefix='campus-ci-config-') as config_dir:
        folder = Path(config_dir)
        original = environment(folder/'source.env',8080)
        source = p.Stack(folder/'source.env',old_checkout/'compose.yaml','campus-ci-source',legacy=True,disposable=True)
        shared = {key:original[key] for key in ('INTERNAL_TOKEN','SETUP_KEY')}
        environment(folder/'migrated.env',8081,shared,NOTIFICATION_WORKER='false')
        migrated = p.Stack(folder/'migrated.env',ROOT/'compose.production.yaml','campus-ci-migrated',disposable=True)
        environment(folder/'fresh.env',8080)
        fresh = p.Stack(folder/'fresh.env',ROOT/'compose.production.yaml','campus-ci-fresh',disposable=True)
        for stack in (source,migrated,fresh): ensure_empty_project(stack)
        try:
            created.append(source)
            source.dc('build',*p.APPS,what='Build genuine PR2 fixture')
            source.dc('up','-d','--wait','--wait-timeout','300',*p.APPS,what='Start genuine PR2 fixture')
            smoke(api_urls(8080),original['SETUP_KEY']); notifications(api_urls(8080)); community(api_urls(8080))
            old_session = httpx.Client(base_url='http://localhost:8080',trust_env=False)
            assert old_session.post('/api/auth/login',json={'email':'admin@example.test','password':'Integration-test-password-2026'}).status_code == 200
            created.append(migrated)
            with patch('builtins.input',return_value='MIGRATE'): p.migrate_four(source,migrated)
            migration = json.loads((migrated.state/'migration.json').read_text())
            manifest = json.loads((Path(migration['backup'])/'manifest.json').read_text())
            for service in p.SERVICES:
                before = manifest['snapshots'][service]
                after = migrated.snapshot(service,columns={table:data['columns'] for table,data in before['tables'].items()})
                p.compare_snapshots(before,after,upgraded=True)
            # Restarting the upgraded code must be idempotent.
            migrated.dc('restart',*p.SERVICES,what='Repeat migration startup')
            migrated.dc('up','-d','--wait','--wait-timeout','300',*p.SERVICES,what='Wait for repeated startup')
            p.deploy(migrated)
            with httpx.Client(base_url='http://localhost:8081',cookies=old_session.cookies,trust_env=False) as client:
                me = client.get('/api/auth/me'); assert me.status_code == 200
                assert me.json()['user']['group_role'] == 'head'
            old_session.close()
            print('PASS: real PR2 four-instance data migrated to PR3 single-instance; every original column matched, old session works, restart is idempotent')
            # Keep volumes, stop old stacks to measure only the fresh production profile.
            source.dc('stop',what='Stop disposable migration source'); migrated.dc('stop',what='Stop disposable migration target')
            created.append(fresh)
            p.deploy(fresh)
            values = fresh.config['services']['auth']['environment']
            smoke(api_urls(8080),values['SETUP_KEY']); notifications(api_urls(8080)); community(api_urls(8080)); pr3(api_urls(8080))
            assert_pool_and_postgres(fresh)
            p.budget(fresh)
            # The same browser feature checks now run within production memory/CPU limits.
            command('node','scripts/check_pr3_ui.cjs',env={**os.environ,'CAMPUS_BASE_URL':'http://localhost:8080'})
            saved = p.backup(fresh); p.test_restore(fresh,saved)
            # Prove that restore repopulates erased disposable schemas, not just a live DB.
            fresh.dc('stop',*p.APPS,what='Pause disposable restore fixture')
            for service in p.SERVICES:
                fresh.psql(service,f'DROP SCHEMA public CASCADE; CREATE SCHEMA public AUTHORIZATION {service};')
                fresh.restore_dump(service,Path(saved)/(service+'.dump'))
                expected = json.loads((Path(saved)/'manifest.json').read_text())['snapshots'][service]
                p.compare_snapshots(expected,fresh.snapshot(service))
            fresh.dc('up','-d','--wait','--wait-timeout','300',what='Start restored disposable fixture')
            command(sys.executable,'scripts/verify_restore.py')
            # Exercise the operator restore path too, including its pre-restore backup.
            with patch('builtins.input',return_value='RESTORE'): p.restore(fresh,saved)
            with patch('builtins.input',return_value='ROLLBACK'): p.rollback(fresh,PREVIOUS,disposable=True)
            command(sys.executable,'scripts/verify_restore.py')
            fresh = p.Stack(folder/'fresh.env',ROOT/'compose.production.yaml','campus-ci-fresh',disposable=True,image_tag=current)
            p.deploy(fresh)
            command(sys.executable,'scripts/verify_restore.py')
            p.budget(fresh)
            print('PASS: production profile, memory limits, browser, erased-database recovery, operator restore, real previous-image rollback and forward update')
        except Exception:
            # Only this job's synthetic fixtures exist here. Still redact every secret
            # before sharing the private command error in CI (never in the live CLI).
            error_file = ROOT/'.production'/'last-error.log'
            if error_file.exists():
                details = error_file.read_text()
                for stack in created:
                    for service in stack.config['services'].values():
                        for key, value in service.get('environment', {}).items():
                            if any(word in key for word in ('PASSWORD','TOKEN','KEY','DATABASE_URL')) and value:
                                details = details.replace(str(value), '[redacted]')
                print('Disposable CI command error:', details[-12000:])
            raise
        finally:
            for stack in reversed(created):
                try:
                    p.diagnose(stack,include_logs=True)
                except Exception as error: print('Diagnostics unavailable:',type(error).__name__)
                # These projects were checked empty before this process created them.
                assert stack.project.startswith('campus-ci-') and os.getenv('CAMPUS_DISPOSABLE') == '1'
                stack.dc('down','--volumes',what='Remove only this job\'s disposable fixtures')


if __name__ == '__main__': main()
