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


def test_teaching_end_preserves_assessments_and_restores_regular_classes(cluster):
    from datetime import datetime,timedelta,timezone
    from scripts.smoke import Client,PASSWORD
    urls,setup_key=cluster
    admin=Client(urls)
    try:
        session=admin.request('POST','/api/auth/setup',{'email':'admin@example.test','password':PASSWORD,'name':'Test Administrator','setup_key':setup_key},expected=201)
        admin.csrf=session['csrf']
        today=datetime.now(timezone.utc).date()
        first=today-timedelta(days=7)
        cutoff=today+timedelta(days=1)
        last=today+timedelta(days=21)
        config=admin.request('GET','/api/schedule/settings')
        config.update(semester_start=first.isoformat(),semester_end=last.isoformat(),anchor_monday=(today-timedelta(days=today.weekday())).isoformat(),timezone='UTC',configured=True)
        config=admin.request('PUT','/api/schedule/settings',config)
        body={'title':'Regular class','kind':'lecture','start':'09:00','end':'10:00'}
        rule=admin.request('POST','/api/schedule/rules',{**body,'weekday':today.weekday()},expected=201)
        path=f'/api/schedule/occurrences?start={first}&end={last}'
        regular=admin.request('GET',path)
        past=next(x for x in regular if x['date']==first.isoformat())
        future=next(x for x in regular if x['date']==(today+timedelta(days=7)).isoformat())
        moved_date=today+timedelta(days=8)
        admin.request('PATCH','/api/schedule/occurrences/'+future['id'],{**body,'date':moved_date.isoformat(),'room':'Changed room','status':'pending','revision':future['revision']})
        oneoffs=[]
        for kind,hour in [('lecture','11'),('credit','13'),('exam','15')]:
            oneoffs.append(admin.request('POST','/api/schedule/occurrences',{'title':'Standalone '+kind,'kind':kind,'start':hour+':00','end':hour+':30','date':moved_date.isoformat()},expected=201))
        config=admin.request('PUT','/api/schedule/settings',{**config,'teaching_end':cutoff.isoformat()})
        items=admin.request('GET',path)
        assert past['id'] in {x['id'] for x in items}
        assert all(x['date']<=cutoff.isoformat() for x in items if x['rule_id'])
        assert {x['id'] for x in oneoffs}<={x['id'] for x in items}
        guest=admin.request('GET',path.replace('/occurrences','/guest/occurrences'))
        assert {x['id'] for x in items}=={x['id'] for x in guest}
        admin.request('PUT','/api/schedule/settings',{**config,'teaching_end':(last+timedelta(days=1)).isoformat()},expected=422)
        extra=admin.request('POST','/api/schedule/rules',{**body,'title':'Second rule','start':'17:00','end':'18:00','weekday':today.weekday()},expected=201)
        assert all(x['date']<=cutoff.isoformat() for x in admin.request('GET',path) if x['rule_id']==extra['id'])
        config=admin.request('PUT','/api/schedule/settings',{**config,'teaching_end':None})
        restored=admin.request('GET',path)
        assert {x['id'] for x in regular}<={x['id'] for x in restored}
        moved=next(x for x in restored if x['id']==future['id'])
        assert moved['date']==moved_date.isoformat() and moved['room']=='Changed room' and moved['status']=='pending'
        assert next(x for x in restored if x['id']==past['id'])['revision']==past['revision']
    finally:
        admin.close()
