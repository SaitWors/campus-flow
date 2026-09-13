import os
import socket
import subprocess
import sys
import time
from pathlib import Path
import httpx
import pytest
from scripts.smoke import run

@pytest.fixture
def cluster(tmp_path):
    root=Path(__file__).resolve().parents[1]
    ports={}
    for service in ('auth','schedule','queue'):
        with socket.socket() as s:
            s.bind(('127.0.0.1',0));ports[service]=s.getsockname()[1]
    urls={k:'http://127.0.0.1:'+str(v) for k,v in ports.items()}
    env={**os.environ,'INTERNAL_TOKEN':'integration-internal-token-at-least-32-characters','SETUP_KEY':'integration-setup-key-long-enough','AUTH_URL':urls['auth'],'SCHEDULE_URL':urls['schedule'],'EVENT_WORKER':'true','COOKIE_SECURE':'false'}
    processes=[];logs=[]
    try:
        for service in ports:
            log=open(tmp_path/(service+'.log'),'w');logs.append(log)
            p=subprocess.Popen([sys.executable,'-m','uvicorn','services.'+service+'.main:app','--host','127.0.0.1','--port',str(ports[service])],cwd=root,env={**env,'DATABASE_URL':'sqlite:///'+str(tmp_path/(service+'.db'))},stdout=log,stderr=subprocess.STDOUT)
            processes.append(p)
            deadline=time.monotonic()+30
            while time.monotonic()<deadline:
                if p.poll() is not None:raise AssertionError((tmp_path/(service+'.log')).read_text())
                try:
                    if httpx.get(urls[service]+'/health',timeout=1,trust_env=False).status_code==200:break
                except httpx.HTTPError:pass
                time.sleep(.1)
            else:raise AssertionError(service+' startup timed out')
        yield urls,env['SETUP_KEY']
    finally:
        for p in reversed(processes):p.terminate()
        for p in processes:
            try:p.wait(timeout=5)
            except subprocess.TimeoutExpired:p.kill()
        for log in logs:log.close()

def test_real_http_workflow(cluster):
    urls,setup_key=cluster
    assert len(run(urls,setup_key))==6
