#!/usr/bin/env python3
"""Opt-in host-side reconciler for owner-managed, digest-pinned GHCR bot images.

This runs as a local systemd service, not inside the Campus Flow web/API
containers. Only this host process is allowed to speak to Docker.
"""
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

API = os.getenv('BOT_RUNNER_URL', 'http://127.0.0.1:8080').rstrip('/')
SECRET = os.getenv('BOT_RUNNER_TOKEN', '')
POLL = 20
IMAGE = re.compile(r'ghcr\.io/saitwors/[a-z0-9][a-z0-9._/-]*@sha256:[a-f0-9]{64}\Z')
BOT_ID = re.compile(r'[0-9a-f-]{36}\Z')
NAME = re.compile(r'cfbot-[0-9a-f-]{36}\Z')
LOG = logging.getLogger('campus.bot-runner')


def request(path, payload=None):
    content = json.dumps(payload).encode() if payload is not None else None
    headers = {'X-Bot-Runner-Token': SECRET}
    if payload is not None:
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(API + '/api/auth/bot-runner/' + path,
                                 headers=headers, data=content,
                                 method='POST' if content is not None else 'GET')
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=15) as response:
        return json.loads(response.read(65536))


def docker(*args, timeout=30, check=True):
    result = subprocess.run(['/usr/bin/docker', *args],
                            capture_output=True, text=True, timeout=timeout,
                            check=False)
    if check and result.returncode:
        # Docker stderr can contain bot env or private data; never log verbatim.
        raise RuntimeError('docker operation failed: ' + args[0])
    return result


def details(name):
    res = docker('inspect', name, check=False)
    if res.returncode:
        return None
    try:
        obj = json.loads(res.stdout)[0]
        return {'running': obj['State']['Running'],
                'labels': obj['Config'].get('Labels') or {}}
    except (KeyError, ValueError, IndexError, TypeError):
        raise RuntimeError('unable to parse Docker inspect')


def owned(name, item):
    return (item and item['labels'].get('campus-flow.bot-managed') == 'true'
            and item['labels'].get('campus-flow.bot-id') == name.removeprefix('cfbot-'))


def pull_image(bot):
    if shutil.disk_usage('/').free < 3 * 1024 ** 3:
        raise RuntimeError('less than 3 GiB free disk: refusing image pull')
    docker('pull', bot['image'], timeout=240)


def create(bot, name, *, pre_pulled=False):
    if not pre_pulled:
        pull_image(bot)
    # The temporary env file prevents exposing the secret in the command line.
    # Docker still stores container environment variables: only host root /
    # privileged Docker administrators should access Docker inspect.
    fd, env_file = tempfile.mkstemp(prefix='cfbot-', dir='/run')
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w') as out:
            out.write('BOT_TOKEN=' + bot['token'] + '\n')
            out.write('PYTHONDONTWRITEBYTECODE=1\nPYTHONUNBUFFERED=1\n')
        docker('run', '-d', '--init', '--name', name,
               '--label', 'campus-flow.bot-managed=true',
               '--label', 'campus-flow.bot-id=' + bot['id'],
               '--label', 'campus-flow.bot-revision=' + str(bot['revision']),
               '--restart', 'unless-stopped', '--pull', 'never',
               '--read-only', '--user', '10001:10001',
               '--tmpfs', '/tmp:rw,nosuid,nodev,size=32m',
               '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
               '--memory', '192m', '--memory-swap', '192m',
               '--cpus', '0.35', '--pids-limit', '64',
               '--network', 'bridge',
               '--log-driver', 'json-file', '--log-opt', 'max-size=5m',
               '--log-opt', 'max-file=2',
               '--env-file', env_file, bot['image'], timeout=90)
    finally:
        os.unlink(env_file)


def reconcile(bot):
    if not isinstance(bot, dict) or not BOT_ID.fullmatch(str(bot.get('id', ''))):
        raise ValueError('invalid bot identifier')
    if not IMAGE.fullmatch(str(bot.get('image', ''))):
        raise ValueError('non-allowlisted image')
    if bot.get('desired_state') not in ('running', 'stopped'):
        raise ValueError('invalid desired state')
    if not isinstance(bot.get('revision'), int) or bot['revision'] < 1:
        raise ValueError('invalid revision')
    if not isinstance(bot.get('token'), str) or '\n' in bot['token'] or '\r' in bot['token']:
        raise ValueError('invalid token')
    name = 'cfbot-' + bot['id']
    info = details(name)
    if info and not owned(name, info):
        raise RuntimeError('container-name collision; not touching unmanaged resource')

    try:
        if bot['desired_state'] == 'stopped':
            if info and info['running']:
                docker('stop', '--time', '10', name, timeout=25)
            state = 'stopped'
        else:
            pre_pulled = False
            if info and info['labels'].get('campus-flow.bot-revision') != str(bot['revision']):
                # Keep the old bot running if GHCR is unreachable or pull fails.
                pull_image(bot)
                pre_pulled = True
                docker('rm', '-f', name, timeout=30)
                info = None
            if not info:
                create(bot, name, pre_pulled=pre_pulled)
            elif not info['running']:
                docker('start', name)
            current = details(name)
            state = 'running' if current and current['running'] else 'error'
        # docker logs separates stdout and stderr; retain both, redact the token.
        proc = docker('logs', '--tail', '20', name, check=False, timeout=10)
        logs = (proc.stdout + proc.stderr)[-4000:].replace(bot['token'], '[REDACTED]')
        request('report', {'id': bot['id'], 'observed_revision': bot['revision'],
                           'observed_state': state, 'last_logs': logs})
    except Exception as exc:
        request('report', {'id': bot['id'], 'observed_revision': bot['revision'],
                           'observed_state': 'error', 'last_error': str(exc)[:200]})
        raise


def purge_orphans(active):
    res = docker('ps', '-a', '--filter', 'label=campus-flow.bot-managed=true',
                 '--format', '{{.Names}}')
    for name in res.stdout.splitlines():
        if NAME.fullmatch(name) and name.removeprefix('cfbot-') not in active:
            info = details(name)
            if owned(name, info):
                docker('rm', '-f', name)


def main():
    if len(SECRET) < 40:
        raise SystemExit('BOT_RUNNER_TOKEN is missing or too short')
    parsed = urlsplit(API)
    if parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost') or parsed.username or parsed.password or parsed.path not in ('', '/') or parsed.query or parsed.fragment:
        raise SystemExit('BOT_RUNNER_URL must use local loopback only')
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    while True:
        try:
            bots = request('state')
            if not isinstance(bots, list) or len(bots) > 5:
                raise RuntimeError('invalid bot registry response')
            active = set()
            for bot in bots:
                try:
                    active.add(bot['id'])
                    reconcile(bot)
                except Exception:
                    LOG.exception('Bot reconciliation failed (no secrets logged)')
            purge_orphans(active)
        except Exception:
            LOG.exception('Bot runner sync failed')
        time.sleep(POLL)


if __name__ == '__main__':
    main()
