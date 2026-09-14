"""Local HTTP development without Docker; production uses compose.yaml.

Run from the repository using .venv/bin/python scripts/dev.py.
Uses isolated SQLite files in .dev-data, never production databases.
"""
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

root=Path(__file__).resolve().parents[1]
data=root/'.dev-data'
data.mkdir(exist_ok=True)
env={**os.environ,'INTERNAL_TOKEN':'local-development-internal-token-32-characters',
     'SETUP_KEY':'local-development-setup-key', 'APP_ORIGIN':'http://localhost:5173,http://127.0.0.1:5173',
     'COOKIE_SECURE':'false','AUTH_URL':'http://127.0.0.1:8101','SCHEDULE_URL':'http://127.0.0.1:8102','QUEUE_URL':'http://127.0.0.1:8103','TRANSLATION_WORKER':'false'}
processes=[]
def stop(*_):
    for p in processes:p.terminate()
    raise SystemExit(0)
signal.signal(signal.SIGINT,stop)
signal.signal(signal.SIGTERM,stop)
try:
    for service,port in [('auth',8101),('schedule',8102),('queue',8103),('notifications',8104)]:
        processes.append(subprocess.Popen([sys.executable,'-m','uvicorn','services.'+service+'.main:app','--port',str(port),'--host','127.0.0.1'],cwd=root,env={**env,'DATABASE_URL':'sqlite:///'+str(data/(service+'.db'))}))
    print('Development setup key: local-development-setup-key',flush=True)
    while True:
        if any(p.poll() is not None for p in processes):raise RuntimeError('A development service stopped')
        time.sleep(1)
finally:
    for p in processes:
        p.terminate()
    for p in processes:
        try:p.wait(timeout=5)
        except subprocess.TimeoutExpired:p.kill()
