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
    for service in ('auth','schedule','notifications'):
        with socket.socket() as s:
            s.bind(('127.0.0.1',0));ports[service]=s.getsockname()[1]
    urls={k:'http://127.0.0.1:'+str(v) for k,v in ports.items()}
    env={**os.environ,'INTERNAL_TOKEN':'integration-internal-token-at-least-32-characters','SETUP_KEY':'integration-setup-key-long-enough','AUTH_URL':urls['auth'],'SCHEDULE_URL':urls['schedule'],'EVENT_WORKER':'true','TRANSLATION_WORKER':'false','COOKIE_SECURE':'false','MATERIALS_DIR':str(tmp_path/'materials')}
    processes=[];logs=[]
    try:
        for service in ports:
            log=open(tmp_path/(service+'.log'),'w');logs.append(log)
            p=subprocess.Popen([sys.executable,'-m','uvicorn','services.'+service+'.main:app','--host','127.0.0.1','--port',str(ports[service]),*(['--h11-max-incomplete-event-size','65536'] if service=='schedule' else [])],cwd=root,env={**env,'DATABASE_URL':'sqlite:///'+str(tmp_path/(service+'.db'))},stdout=log,stderr=subprocess.STDOUT)
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
    assert len(run(urls,setup_key))==4
    from scripts.verify_notifications import run as notifications
    notifications(urls)

    from scripts.verify_community import run as community
    community(urls)

    from scripts.verify_pr3 import run as pr3
    pr3(urls)

    from scripts.verify_study import run as study
    study(urls)

    from scripts.verify_materials import run as materials
    materials(urls)


@pytest.mark.parametrize('kind', ['credit', 'exam'])
def test_standalone_assessment(cluster,kind):
    from datetime import date,timedelta
    from scripts.smoke import Client,PASSWORD
    urls,setup_key=cluster
    admin=Client(urls)
    try:
        session=admin.request('POST','/api/auth/setup',{'email':'admin@example.test','password':PASSWORD,'name':'Test Administrator','setup_key':setup_key},expected=201)
        admin.csrf=session['csrf']
        config=admin.request('GET','/api/schedule/settings')
        day=date.fromisoformat(config['semester_start'])+timedelta(days=1)
        end=day+timedelta(days=7)
        body={'title':'Assessment test','kind':kind,'start':'10:00','end':'11:00','teacher':'Teacher','room':'101','subgroup':0}
        admin.request('POST','/api/schedule/rules',{**body,'weekday':day.weekday()},expected=422)
        item=admin.request('POST','/api/schedule/occurrences',{**body,'date':day.isoformat()},expected=201)
        assert item['kind']==kind and item['is_one_off'] and item['rule_id']==''
        path=f'/api/schedule/occurrences?start={day}&end={end}'
        assert [x['id'] for x in admin.request('GET',path)]==[item['id']]
        updated=admin.request('PATCH','/api/schedule/occurrences/'+item['id'],{**body,'date':end.isoformat(),'status':'confirmed','revision':item['revision']})
        assert updated['date']==end.isoformat() and updated['kind']==kind
        assert any(e['data'].get('after',{}).get('kind')==kind for e in admin.request('GET','/api/schedule/events?after=0'))
        admin.request('DELETE',f"/api/schedule/occurrences/{item['id']}?revision={updated['revision']}")
        assert not admin.request('GET',path)
    finally:
        admin.close()
