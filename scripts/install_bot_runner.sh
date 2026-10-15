#!/usr/bin/env bash
# Run as root on the existing Campus Flow production host. No data volumes are removed.
set -Eeuo pipefail
cd "$(dirname "$0")/.."
[[ $(id -u) == 0 ]] || { echo "Run as root" >&2; exit 1; }
[[ "$(pwd -P)" == "/opt/campus-flow" ]] || { echo "Expected /opt/campus-flow checkout" >&2; exit 1; }
mode="$1"
[[ "$mode" == prepare || "$mode" == start ]] || { echo "Usage: bash scripts/install_bot_runner.sh prepare|start"; exit 2; }

if [[ "$mode" == prepare ]]; then
  [[ -f .env.production ]] || { echo "Missing .env.production" >&2; exit 1; }
  python3 - <<'PY'
import os, re, secrets, tempfile
from pathlib import Path
path=Path('.env.production')
if path.is_symlink():
    raise SystemExit('Refusing symlinked environment file')
if path.stat().st_mode & 0o077:
    raise SystemExit('Require chmod 600 .env.production first')
source=path.read_text()
matches=list(re.finditer(r'(?m)^BOT_RUNNER_TOKEN=(.*)$',source))
if len(matches)>1:
    raise SystemExit('Duplicate BOT_RUNNER_TOKEN entries')
token=matches[0].group(1) if matches else ''
if not token:
    token=secrets.token_hex(32)
    if matches:
        source=source[:matches[0].start()]+f'BOT_RUNNER_TOKEN={token}'+source[matches[0].end():]
    else:
        source=source.rstrip('\n')+'\nBOT_RUNNER_TOKEN='+token+'\n'
    fd,tmp=tempfile.mkstemp(dir='.',prefix='.env.production.',suffix='.tmp')
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd,'w') as stream:
            stream.write(source)
            stream.flush();os.fsync(stream.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)
elif not re.fullmatch(r'[0-9a-f]{64}',token):
    raise SystemExit('BOT_RUNNER_TOKEN must be 64 lowercase hex characters')
port=re.search(r'(?m)^HTTP_PORT=(\d+)$',source)
port=port.group(1) if port else '8080'
if not (1<=int(port)<=65535):
    raise SystemExit('Invalid HTTP_PORT')
target=Path('/etc/campus-flow-bot-runner.env')
target.write_text(f'BOT_RUNNER_TOKEN={token}\nBOT_RUNNER_URL=http://127.0.0.1:{port}\n')
target.chmod(0o600)
PY
  install -m 0644 infra/campus-flow-bot-runner.service /etc/systemd/system/campus-flow-bot-runner.service
  systemctl daemon-reload
  echo "Bot Runner prepared. Deploy the new API image before running: bash scripts/install_bot_runner.sh start"
else
  [[ -f /etc/campus-flow-bot-runner.env ]] || { echo "Run prepare first" >&2; exit 1; }
  systemctl enable --now campus-flow-bot-runner.service
  systemctl restart campus-flow-bot-runner.service
  systemctl --no-pager --full status campus-flow-bot-runner.service | head -n 16
fi
