"""Access control, durable delivery and privacy checks using a real service DB."""
import base64
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, func

from services.common.core import database, fail, now
from services.notifications.push import DEFAULTS, b64, generate_keys, quiet, validate_endpoint
from services.notifications.models import Announcement, Cursor, Delivery, Notification, Preference, Subscription

ADMIN = {'id':'00000000-0000-0000-0000-000000000001','name':'Admin','role':'admin','subgroup':1}
STUDENT = {'id':'00000000-0000-0000-0000-000000000002','name':'Student','role':'student','subgroup':1}
OTHER = {'id':'00000000-0000-0000-0000-000000000003','name':'Other','role':'student','subgroup':2}
PUBLISH = {'title':'Изменения завтра','body':'Лабораторная пройдёт дистанционно.','hours':24,'important':True}
AUTH = {'X-User':ADMIN['id'],'X-CSRF-Token':'valid'}
KEY = {'Idempotency-Key':'test-request-key-0000001'}


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setenv('DATABASE_URL','sqlite:///'+str(tmp_path/'notifications.db'))
    monkeypatch.setenv('INTERNAL_TOKEN','test-internal-token-at-least-32-characters')
    monkeypatch.setenv('APP_ORIGIN','https://campus.example')
    monkeypatch.setenv('VAPID_SUBJECT','mailto:operator@example.test')
    monkeypatch.setenv('PUSH_ENABLED','true')
    monkeypatch.setenv('NOTIFICATION_WORKER','false')
    from services.notifications import main as m
    engine, db = database('notifications')
    monkeypatch.setattr(m,'engine',engine)
    monkeypatch.setattr(m,'DB',db)
    members = {u['id']:dict(u) for u in (ADMIN,STUDENT,OTHER)}
    status = {'active':True}
    def identity(request):
        user = members.get(request.headers.get('X-User',''))
        if not user: fail('unauthorized',401)
        if request.method not in ('GET','HEAD') and request.headers.get('X-CSRF-Token')!='valid':fail('csrf_invalid',403)
        return user
    def auth(path, **kwargs):
        if path=='/internal/notification-recipients':return list(members.values())
        if path=='/internal/push-check':return {**members[kwargs['json']['user_id']],**status}
        raise AssertionError(path)
    monkeypatch.setattr(m,'identity',identity)
    monkeypatch.setattr(m,'auth',auth)
    with TestClient(m.app) as client:
        yield m,client,members,status
    engine.dispose()


def headers(user=STUDENT):
    return {'X-User':user['id'],'X-CSRF-Token':'valid'}


def subscribe(client, user=STUDENT, suffix='test'):
    _, public = generate_keys()
    data={'endpoint':'https://fcm.googleapis.com/fcm/send/'+suffix,
          'keys':{'auth':b64(b'0123456789abcdef'),'p256dh':public},'label':'Phone'}
    result=client.post('/api/notifications/subscriptions',json=data,headers=headers(user))
    assert result.status_code==201,result.text
    return result.json()['id'],data


def publish(client, data=None, key=KEY):
    response=client.post('/api/notifications/announcements',json=data or PUBLISH,headers={**AUTH,**key})
    assert response.status_code==201,response.text
    return response.json()['id']


def test_permissions_audience_idempotency_and_withdraw(service):
    m,client,members,_=service
    assert client.get('/api/notifications/inbox').status_code==401
    assert client.post('/api/notifications/announcements',json=PUBLISH,headers={**headers(),**KEY}).status_code==403
    assert client.post('/api/notifications/announcements',json=PUBLISH,headers={'X-User':ADMIN['id'],**KEY}).status_code==403
    assert client.post('/api/notifications/announcements',json=PUBLISH,headers={**AUTH,**KEY,'Origin':'https://evil.example'}).status_code==403
    aid=publish(client,{**PUBLISH,'audience':'managers'})
    assert publish(client,{**PUBLISH,'audience':'managers'})==aid
    assert not client.get('/api/notifications/inbox',headers=headers()).json()['items']
    data=client.get('/api/notifications/inbox',headers=AUTH).json()
    assert data['unread']==1 and len(data['popups'])==1
    assert client.post('/api/notifications/announcements',json={**PUBLISH,'body':'Changed'},headers={**AUTH,**KEY}).status_code==409
    # Losing a role immediately removes access, without trusting stored audience.
    members[ADMIN['id']]['role']='student'
    assert not client.get('/api/notifications/inbox',headers=AUTH).json()['items']
    members[ADMIN['id']]['role']='admin'
    assert client.delete('/api/notifications/announcements/'+aid,headers=AUTH).status_code==200
    assert not client.get('/api/notifications/inbox',headers=AUTH).json()['items']
    assert len(client.get('/api/notifications/audit',headers=AUTH).json())==2


def test_reads_preferences_and_private_devices(service):
    m,client,_,_=service
    sid, data=subscribe(client)
    assert client.post('/api/notifications/subscriptions',json=data,headers=headers(OTHER)).status_code==409
    assert client.delete('/api/notifications/subscriptions/'+sid,headers=headers(OTHER)).status_code==404
    devices=client.get('/api/notifications/subscriptions',headers=headers()).json()
    assert set(devices[0])=={'id','label','updated_at'}
    assert 'private' not in str(client.get('/api/notifications/config',headers=headers()).json())
    pref=client.get('/api/notifications/preferences',headers=headers()).json()
    updated={**pref,'important_popups':False,'schedule':False}
    assert client.put('/api/notifications/preferences',json=updated,headers=headers()).json()['revision']==1
    assert client.put('/api/notifications/preferences',json=updated,headers=headers()).status_code==409
    assert client.put('/api/notifications/preferences',json={**updated,'revision':1,'timezone':'../../etc/passwd'},headers=headers()).status_code==422
    publish(client)
    own=client.get('/api/notifications/inbox',headers=headers()).json()
    other=client.get('/api/notifications/inbox',headers=headers(OTHER)).json()
    assert own['unread']==1 and not own['popups']
    client.post('/api/notifications/read',json={'ids':[other['items'][0]['id']]},headers=headers())
    assert client.get('/api/notifications/inbox',headers=headers(OTHER)).json()['unread']==1
    client.post('/api/notifications/read',json={'before':own['as_of']},headers=headers())
    assert client.get('/api/notifications/inbox?unread_only=true',headers=headers()).json()['total']==0
    assert client.delete('/api/notifications/subscriptions/'+sid,headers=headers()).status_code==200
    with m.DB() as db:assert db.scalar(select(func.count()).select_from(Delivery))==0


@pytest.mark.parametrize('endpoint',[
 'http://fcm.googleapis.com/fcm/send/test','https://127.0.0.1/push','https://169.254.169.254/latest/meta-data',
 'https://fcm.googleapis.com.evil.test/fcm/send/test','https://evil.test/fcm/send/test',
 'https://fcm.googleapis.com:444/fcm/send/test','https://secret@fcm.googleapis.com/fcm/send/test',
 'https://updates.push.services.mozilla.com/wpush/v2/test#fragment',
 'https://x.notify.windows.com.evil.test/w/?token=test',
])
def test_reject_ssrf_endpoints(endpoint):
    with pytest.raises(ValueError):validate_endpoint(endpoint)


def test_provider_validation_and_quiet_hours():
    for endpoint in ['https://fcm.googleapis.com/fcm/send/test','https://updates.push.services.mozilla.com/wpush/v2/test','https://web.push.apple.com/Qtest','https://wns2-am2p.notify.windows.com/w/?token=test']:
        validate_endpoint(endpoint)
    pref={**DEFAULTS,'quiet_enabled':True}
    assert quiet(pref,datetime(2026,9,14,20,0,tzinfo=timezone.utc))
    assert not quiet(pref,datetime(2026,9,14,9,0,tzinfo=timezone.utc))
    assert not quiet(DEFAULTS,datetime(2026,9,14,20,0,tzinfo=timezone.utc))


def test_delivery_privacy_retries_revocation_and_gone(service,monkeypatch):
    m,client,_,status=service
    sid,_=subscribe(client)
    publish(client)
    sent=[]
    monkeypatch.setattr(m,'send',lambda *args:sent.append(args) or 503)
    assert m.deliver_one()
    assert sent[0][1]['title']=='Campus Flow'
    assert PUBLISH['body'] not in json.dumps(sent[0][1],ensure_ascii=False)
    with m.DB.begin() as db:
        d=db.scalar(select(Delivery));assert d.attempts==1 and d.state=='pending'
        d.due_at=now()-timedelta(seconds=1)
    status['active']=False
    assert m.deliver_one() and len(sent)==1
    with m.DB() as db:assert not db.get(Subscription,sid)
    status['active']=True
    sid,_=subscribe(client,suffix='another')
    publish(client,{**PUBLISH,'title':'Second'},key={'Idempotency-Key':'test-request-key-0000002'})
    monkeypatch.setattr(m,'send',lambda *args:410)
    assert m.deliver_one()
    with m.DB() as db:assert not db.get(Subscription,sid)


def test_muted_expired_and_finished_calls_are_not_pushed(service,monkeypatch):
    m,client,_,_=service
    subscribe(client)
    publish(client)
    sent=[]
    monkeypatch.setattr(m,'send',lambda *args:sent.append(args) or 201)
    with m.DB.begin() as db:
        db.add(Preference(user_id=STUDENT['id'],data={**DEFAULTS,'quiet_enabled':True}))
    monkeypatch.setattr(m,'quiet',lambda _:True)
    m.deliver_one();assert not sent
    with m.DB.begin() as db:
        d=db.scalar(select(Delivery));d.due_at=now()-timedelta(seconds=1)
        db.get(Notification,d.notification_id).expires_at=now()-timedelta(seconds=1)
    m.deliver_one();assert not sent
    monkeypatch.setattr(m,'quiet',lambda _:False)
    with m.DB.begin() as db:
        m.notify(db,STUDENT,'call:test','queue',{'title':'Your turn','body':'Call','entry_id':'entry','route':'#queues'},now()+timedelta(minutes=5))
    monkeypatch.setattr(m,'remote',lambda *args,**kw:{'active':False})
    m.deliver_one();assert not sent


def test_event_cursor_restart_deduplicates_and_rolls_back(service,monkeypatch):
    m,client,_,_=service
    events=[{'seq':1,'id':'event','type':'occurrence.updated','data':{'subgroup':1},'at':now().isoformat()+'Z'}]
    monkeypatch.setattr(m,'remote',lambda base,path,**kw:{'seq':0} if path.endswith('/head') else events)
    m.consume('schedule');m.consume('schedule');m.consume('schedule')
    with m.DB() as db:
        assert db.get(Cursor,'schedule').seq==1
        assert db.scalar(select(func.count()).select_from(Notification))==2
    assert not client.get('/api/notifications/inbox',headers=headers(OTHER)).json()['items']
    events.append({**events[0],'seq':2,'id':'next'})
    original=m.notify
    def crash(*args,**kwargs):
        original(*args,**kwargs)
        raise RuntimeError('simulated failure before commit')
    monkeypatch.setattr(m,'notify',crash)
    with pytest.raises(RuntimeError):m.consume('schedule')
    with m.DB() as db:assert db.get(Cursor,'schedule').seq==1
    monkeypatch.setattr(m,'notify',original)
    m.consume('schedule')
    with m.DB() as db:
        assert db.get(Cursor,'schedule').seq==2
        assert db.scalar(select(func.count()).select_from(Notification))==4


def test_standard_webpush_encryption_without_network(monkeypatch):
    from services.notifications import push
    private,public=generate_keys()
    subscription={'endpoint':'https://fcm.googleapis.com/fcm/send/test','keys':{'auth':b64(b'0123456789abcdef'),'p256dh':public}}
    captured={}
    import requests
    def request(self,method,url,**kwargs):
        captured.update(kwargs)
        assert self.trust_env is False
        response=requests.Response();response.status_code=201;return response
    monkeypatch.setattr(requests.Session,'request',request)
    monkeypatch.setattr(push.socket,'getaddrinfo',lambda *a,**k:[(2,1,6,'',('8.8.8.8',443))])
    assert push.send(subscription,{'body':'Private message'},private,'mailto:operator@example.test',60)==201
    assert captured['allow_redirects'] is False and captured['verify'] is True
    assert b'Private message' not in captured['data']
    assert captured['headers']['content-encoding']=='aes128gcm'
