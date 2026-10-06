"""Telegram uses a real notification DB and a local HTTP transport; never live sends."""
import json
import logging
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from sqlalchemy import select, func

from services.common.core import now, digest, database
from services.notifications.models import Notification, Preference
from test_notifications import service, headers, ADMIN, STUDENT, OTHER, PUBLISH, publish

TOKEN='123456789:abcdefghijklmnopqrstuvwxyz_0123456789'


def configured(monkeypatch):
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN',TOKEN)
    monkeypatch.setenv('TELEGRAM_BOT_USERNAME','CampusFlowTestBot')


def issue(client,user=STUDENT):
    r=client.post('/api/notifications/telegram/link',json={},headers=headers(user))
    assert r.status_code==200,r.text
    value=r.json()
    assert 570 < ( datetime.fromisoformat(value['expires_at'].replace('Z','+00:00')).replace(tzinfo=None)-now()).total_seconds() <=600
    return parse_qs(urlsplit(value['url']).query)['start'][0]


def update(token,update_id=1,chat_id=555,**extra):
    return {'update_id':update_id,'message':{'text':'/start '+token,'chat':{'type':'private','id':chat_id},'from':{'id':chat_id,'is_bot':False,'first_name':'Student Telegram'},**extra}}


def transport(m,monkeypatch,updates=None,responses=None):
    calls=[];pending=updates if updates is not None else []
    outcomes=responses if responses is not None else []
    def handler(request):
        assert request.url.host=='api.telegram.org' and request.method=='POST'
        data=json.loads(request.content)
        method=request.url.path.rsplit('/',1)[-1]
        calls.append((method,data))
        if method=='getUpdates':
            result=[u for u in pending if u['update_id']>=data['offset']]
            return httpx.Response(200,json={'ok':True,'result':result})
        assert method=='sendMessage' and data['chat_id']>0
        assert 'parse_mode' not in data and data['link_preview_options']=={'is_disabled':True}
        result=outcomes.pop(0) if outcomes else {'ok':True,'result':{'message_id':100,'chat':{'id':data['chat_id'],'type':'private'}}}
        return httpx.Response(result.get('error_code',200),json=result)
    client=httpx.Client(transport=httpx.MockTransport(handler),trust_env=False,follow_redirects=False)
    monkeypatch.setattr(m,'telegram_transport',m.tg.Transport(client=client))
    return calls,pending,outcomes


def connect(m,client,monkeypatch,user=STUDENT,chat=555):
    token=issue(client,user)
    calls,updates,outcomes=transport(m,monkeypatch,[update(token,chat_id=chat)])
    assert m.poll_telegram()
    assert client.get('/api/notifications/telegram',headers=headers(user)).json()['connected']
    return calls,updates,outcomes


def due_now(m):
    with m.DB.begin() as db:
        for item in db.scalars(select(m.tg.TelegramDelivery)):item.due_at=now()-timedelta(seconds=1)
        state=db.get(m.tg.TelegramState,1)
        if state:state.send_after=now()-timedelta(seconds=1);state.due_at=now()-timedelta(seconds=1)


def test_missing_configuration_is_explicit_and_csrf_is_required(service,monkeypatch):
    m,client,_,_=service
    status=client.get('/api/notifications/telegram',headers=headers())
    assert status.status_code==200 and status.json()=={'enabled':False,'bot_username':'','connected':False}
    assert client.post('/api/notifications/telegram/link',json={},headers=headers()).status_code==503
    assert client.post('/api/notifications/telegram/test',json={},headers=headers()).status_code==503
    configured(monkeypatch)
    assert client.post('/api/notifications/telegram/link',json={},headers={'X-User':STUDENT['id']}).status_code==403
    assert TOKEN not in json.dumps(client.get('/api/notifications/telegram',headers=headers()).json())


def test_hash_only_expiring_link_single_use_private_identity_and_ownership(service,monkeypatch):
    m,client,_,_=service;configured(monkeypatch)
    token=issue(client)
    with m.DB() as db:
        links=list(db.scalars(select(m.tg.TelegramLink)))
        assert len(links)==1 and links[0].token_hash==digest(token)
        assert token not in str(links[0].__dict__)
    invalid=[update(token,1,chat={'type':'supergroup','id':-55}),update(token,2,**{'from':{'id':999,'is_bot':False}}),update(token,3,forward_origin={'type':'user'})]
    calls,updates,_=transport(m,monkeypatch,invalid)
    m.poll_telegram()
    assert not client.get('/api/notifications/telegram',headers=headers()).json()['connected']
    due_now(m);updates.append(update(token,4))
    m.poll_telegram()
    info=client.get('/api/notifications/telegram',headers=headers()).json()
    assert info['connected'] and info['chat_label']=='Student Telegram'
    assert 'chat_id' not in info and 'user_id' not in info
    # A replay cannot rebind to a different Telegram account.
    due_now(m);updates.append(update(token,5,chat_id=777));m.poll_telegram()
    with m.DB() as db:assert db.get(m.tg.TelegramBinding,STUDENT['id']).chat_id==555
    # One Telegram account cannot be claimed by another Campus account.
    other=issue(client,OTHER);due_now(m);updates.append(update(other,6));m.poll_telegram()
    assert not client.get('/api/notifications/telegram',headers=headers(OTHER)).json()['connected']
    # Existing binding cannot silently be replaced with another chat.
    own=issue(client);due_now(m);updates.append(update(own,7,chat_id=888));m.poll_telegram()
    with m.DB() as db:assert db.get(m.tg.TelegramBinding,STUDENT['id']).chat_id==555
    assert client.delete('/api/notifications/telegram',headers=headers()).json()=={'ok':True}
    assert not client.get('/api/notifications/telegram',headers=headers()).json()['connected']
    # Disconnect invalidates outstanding links, including the rejected own link.
    due_now(m);updates.append(update(own,8,chat_id=888));m.poll_telegram()
    assert not client.get('/api/notifications/telegram',headers=headers()).json()['connected']
    expired=issue(client)
    with m.DB.begin() as db:db.get(m.tg.TelegramLink,digest(expired)).expires_at=now()-timedelta(seconds=1)
    due_now(m);updates.append(update(expired,9));m.poll_telegram()
    assert not client.get('/api/notifications/telegram',headers=headers()).json()['connected']


def test_link_supersession_collision_retry_and_rate_limit(service,monkeypatch):
    m,client,_,_=service;configured(monkeypatch)
    first=issue(client);second=issue(client)
    calls,updates,_=transport(m,monkeypatch,[update(first)])
    m.poll_telegram()
    assert not client.get('/api/notifications/telegram',headers=headers()).json()['connected']
    # Random token collisions must never return a token belonging to someone else.
    values=iter([first,'x'*43])
    with monkeypatch.context() as local:
        local.setattr(m.tg.secrets,'token_urlsafe',lambda _:next(values))
        assert issue(client,OTHER)=='x'*43
    issue(client);issue(client);issue(client)
    assert client.post('/api/notifications/telegram/link',json={},headers=headers()).status_code==429


def test_delivery_survives_session_expiry_and_obeys_privacy_quiet_audience_and_active_user(service,monkeypatch):
    m,client,members,status=service;configured(monkeypatch)
    calls,_,_=connect(m,client,monkeypatch)
    status['active']=False # browser session expiry does not affect Telegram
    publish(client)
    assert m.deliver_telegram()
    messages=[data for method,data in calls if method=='sendMessage']
    assert len(messages)==1 and PUBLISH['body'] not in messages[0]['text']
    assert messages[0]['text'].endswith('https://campus.example/#notifications')
    p=client.get('/api/notifications/preferences',headers=headers()).json()
    p.update(show_details=True,quiet_enabled=True)
    assert client.put('/api/notifications/preferences',json=p,headers=headers()).status_code==200
    publish(client,{**PUBLISH,'title':'Second'},key={'Idempotency-Key':'telegram-second-000000000'})
    monkeypatch.setattr(m.tg,'quiet',lambda _:True)
    assert m.deliver_telegram() and len([1 for method,_ in calls if method=='sendMessage'])==1
    monkeypatch.setattr(m.tg,'quiet',lambda _:False);due_now(m)
    members[STUDENT['id']]['active']=False
    assert m.deliver_telegram() and len([1 for method,_ in calls if method=='sendMessage'])==1
    info=client.get('/api/notifications/telegram',headers=headers()).json()
    assert not info['connected'] and info['last_error']=='account_inactive'


def test_delivery_retries_429_persists_restart_cursor_and_disconnects_on_403(service,monkeypatch):
    m,client,_,_=service;configured(monkeypatch)
    calls,updates,outcomes=connect(m,client,monkeypatch)
    with m.DB() as db:assert db.get(m.tg.TelegramState,1).offset==2
    due_now(m);m.poll_telegram()
    assert [data['offset'] for method,data in calls if method=='getUpdates']==[0,2]
    publish(client)
    outcomes.append({'ok':False,'error_code':429,'description':'Do not log '+TOKEN,'parameters':{'retry_after':120}})
    assert m.deliver_telegram()
    with m.DB() as db:
        d=db.scalar(select(m.tg.TelegramDelivery));assert d.state=='pending' and d.attempts==1
        assert (d.due_at-now()).total_seconds()>110
    # Simulate a restart by replacing transport; durable delivery resumes.
    outcomes.append({'ok':False,'error_code':503,'description':'Transient'})
    due_now(m);assert m.deliver_telegram()
    with m.DB() as db:assert db.scalar(select(m.tg.TelegramDelivery)).attempts==2
    outcomes.append({'ok':False,'error_code':403,'description':'Blocked '+TOKEN})
    due_now(m);assert m.deliver_telegram()
    info=client.get('/api/notifications/telegram',headers=headers()).json()
    assert not info['connected'] and info['last_error']=='bot_blocked' and TOKEN not in json.dumps(info)
    assert not m.deliver_telegram()


def test_test_limits_disconnect_cancels_delivery_and_private_question_body_stays_private(service,monkeypatch):
    m,client,_,_=service;configured(monkeypatch)
    calls,_,_=connect(m,client,monkeypatch)
    assert client.post('/api/notifications/telegram/test',json={},headers=headers()).json()=={'ok':True}
    assert client.post('/api/notifications/telegram/test',json={},headers=headers()).status_code==429
    assert client.post('/api/notifications/telegram/test',json={},headers=headers(OTHER)).status_code==409
    client.delete('/api/notifications/telegram',headers=headers())
    assert not m.deliver_telegram()
    assert not any(method=='sendMessage' for method,_ in calls)
    assert client.post('/api/notifications/telegram/test',json={},headers=headers()).status_code==409


def test_delivery_rechecks_changed_audience_and_completed_assignment(service,monkeypatch):
    m,client,members,_=service;configured(monkeypatch)
    calls,_,_=connect(m,client,monkeypatch)
    publish(client,{**PUBLISH,'audience':'subgroup1'})
    members[STUDENT['id']]['subgroup']=2
    assert m.deliver_telegram() and not any(method=='sendMessage' for method,_ in calls)
    members[STUDENT['id']]['subgroup']=1
    due=(now()+timedelta(hours=2)).isoformat()+'Z'
    with m.DB.begin() as db:
        m.notify(db,STUDENT,'reminder:one','assignments',{'title':'Lab','body':'Deadline','route':'#assignments','audience':'subgroup1','reminder':True,'assignment_id':'lab1','revision':1,'due_at':due},now()+timedelta(hours=2))
    def remote(base,path,**kwargs):
        if path=='/internal/assignments/audiences':return {'items':[{'id':'lab1','subgroup':1,'archived':False,'revision':1}]}
        assert path=='/internal/assignments/reminders'
        return [{'id':'lab1','title':'Lab','subject_title':'Physics','revision':1,'due_at':due,'subgroup':1,'completed_user_ids':[STUDENT['id']]}]
    monkeypatch.setattr(m,'remote',remote)
    assert m.deliver_telegram() and not any(method=='sendMessage' for method,_ in calls)


def test_transport_errors_and_http_logging_never_expose_credentials(service,monkeypatch,caplog):
    m,_,_,_=service;configured(monkeypatch)
    def handler(request):raise httpx.ConnectError('Secret '+str(request.url),request=request)
    transport=m.tg.Transport(client=httpx.Client(transport=httpx.MockTransport(handler),trust_env=False))
    with caplog.at_level(logging.DEBUG):
        result=transport.call('sendMessage',{'chat_id':555,'text':'Hello'})
    assert result['status']==503 and TOKEN not in str(result) and TOKEN not in caplog.text
    transport.close()


def test_internal_active_user_check_needs_internal_secret_and_no_browser_session(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from services.auth import main as auth
    from services.auth.models import User, Session
    monkeypatch.setenv('DATABASE_URL','sqlite:///'+str(tmp_path/'auth-telegram.db'))
    monkeypatch.setenv('INTERNAL_TOKEN','test-internal-token-at-least-32-characters')
    engine,DB=database('auth')
    monkeypatch.setattr(auth,'engine',engine);monkeypatch.setattr(auth,'DB',DB)
    secret={'X-Internal-Token':'test-internal-token-at-least-32-characters'}
    with TestClient(auth.app) as client:
        with DB.begin() as db:
            db.add(User(id=STUDENT['id'],email='student@example.test',name='Student',password_hash='unused',status='active',role='student',subgroup=1))
            db.add(Session(token_hash='a'*64,user_id=STUDENT['id'],csrf='csrf',expires=now()-timedelta(days=1)))
        assert client.post('/internal/notification-check',json={'user_id':STUDENT['id']}).status_code==403
        result=client.post('/internal/notification-check',json={'user_id':STUDENT['id']},headers=secret)
        assert result.status_code==200 and result.json()['active'] is True
        assert set(result.json())=={'active','id','role','subgroup','group_role'}
        # An expired browser session cannot keep push active.
        push=client.post('/internal/push-check',json={'user_id':STUDENT['id'],'session_hash':'a'*64},headers=secret).json()
        assert push['active'] is False and push['expires_at']
        with DB.begin() as db:db.get(User,STUDENT['id']).status='disabled'
        assert client.post('/internal/notification-check',json={'user_id':STUDENT['id']},headers=secret).json()=={'active':False}
    engine.dispose()


def test_cursor_link_and_binding_resume_atomically_after_process_failure(service,monkeypatch):
    m,client,_,_=service;configured(monkeypatch)
    token=issue(client)
    calls,_,_=transport(m,monkeypatch,[update(token,42)])
    original=m.tg.set_enabled
    def crash(*args):
        original(*args)
        raise RuntimeError('process stopped before commit')
    monkeypatch.setattr(m.tg,'set_enabled',crash)
    with pytest.raises(RuntimeError,match='before commit'):m.poll_telegram()
    with m.DB() as db:
        assert db.get(m.tg.TelegramState,1).offset==0
        assert db.get(m.tg.TelegramLink,digest(token)).used_at is None
        assert db.get(m.tg.TelegramBinding,STUDENT['id']) is None
    monkeypatch.setattr(m.tg,'set_enabled',original)
    with m.DB.begin() as db:db.get(m.tg.TelegramState,1).lease_until=now()-timedelta(seconds=1)
    # A new transport and new DB connection represent a restarted worker.
    calls,_,_=transport(m,monkeypatch,[update(token,42)])
    assert m.poll_telegram()
    with m.DB() as db:
        assert db.get(m.tg.TelegramState,1).offset==43
        assert db.get(m.tg.TelegramLink,digest(token)).used_at
        assert db.scalar(select(func.count()).select_from(m.tg.TelegramBinding))==1
    due_now(m);assert m.poll_telegram()
    assert [data['offset'] for method,data in calls if method=='getUpdates']==[0,43]


def test_poll_rate_limits_and_webhook_conflicts_are_durable_and_safe(service,monkeypatch):
    m,client,_,_=service;configured(monkeypatch)
    issue(client)
    outcomes=[{'ok':False,'error_code':429,'description':'Secret '+TOKEN,'parameters':{'retry_after':90}},
              {'ok':False,'error_code':409,'description':'Conflict secret '+TOKEN}]
    requests=[]
    def handler(request):
        requests.append(json.loads(request.content))
        data=outcomes.pop(0)
        return httpx.Response(data['error_code'],json=data)
    monkeypatch.setattr(m,'telegram_transport',m.tg.Transport(client=httpx.Client(transport=httpx.MockTransport(handler))))
    assert m.poll_telegram()
    with m.DB() as db:
        state=db.get(m.tg.TelegramState,1)
        assert state.offset==0 and (state.due_at-now()).total_seconds()>80
    assert not m.poll_telegram() and len(requests)==1
    due_now(m);assert m.poll_telegram()
    info=client.get('/api/notifications/telegram',headers=headers()).json()
    assert info['last_error']=='webhook_conflict' and TOKEN not in json.dumps(info)
    assert requests[0]['timeout']>0 and requests[1]['offset']==0


def test_private_question_contents_never_enter_telegram_even_with_details(service,monkeypatch):
    m,client,_,_=service;configured(monkeypatch)
    calls,_,_=connect(m,client,monkeypatch,user=ADMIN,chat=999)
    pref=client.get('/api/notifications/preferences',headers=headers(ADMIN)).json()
    client.put('/api/notifications/preferences',json={**pref,'show_details':True},headers=headers(ADMIN))
    body={'recipient_id':ADMIN['id'],'title':'SECRET QUESTION TITLE','body':'SECRET PRIVATE MEDICAL DETAILS'}
    created=client.post('/api/notifications/questions',json=body,headers={**headers(),'Idempotency-Key':'telegram-private-question-001'})
    assert created.status_code==201,created.text
    assert m.deliver_telegram()
    text_value=[data['text'] for method,data in calls if method=='sendMessage'][0]
    assert 'SECRET' not in text_value and text_value.endswith('/#questions')


def test_telegram_respects_muted_categories_and_channel_then_sends_selected_language(service,monkeypatch):
    m,client,_,_=service;configured(monkeypatch)
    calls,_,_=connect(m,client,monkeypatch)
    pref=client.get('/api/notifications/preferences',headers=headers()).json()
    client.put('/api/notifications/preferences',json={**pref,'announcements':False},headers=headers())
    publish(client)
    assert not m.deliver_telegram()
    pref=client.get('/api/notifications/preferences',headers=headers()).json()
    client.put('/api/notifications/preferences',json={**pref,'announcements':True,'telegram_enabled':False},headers=headers())
    publish(client,{**PUBLISH,'title':'Second'},key={'Idempotency-Key':'telegram-muted-second-0001'})
    assert not m.deliver_telegram()
    pref=client.get('/api/notifications/preferences',headers=headers()).json()
    client.put('/api/notifications/preferences',json={**pref,'telegram_enabled':True,'language':'en','show_details':True},headers=headers())
    publish(client,{**PUBLISH,'title_en':'Lab changed','body_en':'Physics moved to room 305.'},key={'Idempotency-Key':'telegram-language-third-0001'})
    assert m.deliver_telegram()
    text_value=[data['text'] for method,data in calls if method=='sendMessage'][0]
    assert text_value.startswith('Lab changed\nPhysics moved to room 305.')


def test_delivery_stops_after_five_transient_failures(service,monkeypatch):
    m,client,_,_=service;configured(monkeypatch)
    calls,_,outcomes=connect(m,client,monkeypatch)
    publish(client)
    outcomes.extend([{'ok':False,'error_code':503,'description':'Unavailable'} for _ in range(5)])
    for _ in range(5):due_now(m);assert m.deliver_telegram()
    due_now(m);assert not m.deliver_telegram()
    with m.DB() as db:
        delivery=db.scalar(select(m.tg.TelegramDelivery))
        assert delivery.attempts==5 and delivery.state=='failed'
    assert len([1 for method,_ in calls if method=='sendMessage'])==5


def test_manual_telegram_test_bypasses_muted_category_and_quiet_without_pushing(service,monkeypatch):
    from test_notifications import subscribe
    from services.notifications.models import Delivery
    m,client,_,_=service;configured(monkeypatch)
    calls,_,_=connect(m,client,monkeypatch)
    subscribe(client)
    pref=client.get('/api/notifications/preferences',headers=headers()).json()
    client.put('/api/notifications/preferences',json={**pref,'announcements':False,'quiet_enabled':True},headers=headers())
    monkeypatch.setattr(m.tg,'quiet',lambda _:True)
    publish(client)
    assert not m.deliver_telegram()
    result=client.post('/api/notifications/telegram/test',json={},headers=headers())
    assert result.status_code==200,result.text
    assert m.deliver_telegram()
    with m.DB() as db:
        assert db.scalar(select(func.count()).select_from(Delivery))==0
    assert len([1 for method,_ in calls if method=='sendMessage'])==1
    pref=client.get('/api/notifications/preferences',headers=headers()).json()
    client.put('/api/notifications/preferences',json={**pref,'announcements':True},headers=headers())
    publish(client,{**PUBLISH,'title':'Quiet update'},key={'Idempotency-Key':'telegram-quiet-after-test-0001'})
    assert m.deliver_telegram() and len([1 for method,_ in calls if method=='sendMessage'])==1


def test_transport_does_not_treat_a_malformed_success_as_delivery(service,monkeypatch,caplog):
    m,_,_,_=service;configured(monkeypatch)
    outcomes=[{'ok':False,'description':'SECRET '+TOKEN},{'ok':True,'result':None}]
    def handler(request):return httpx.Response(200,json=outcomes.pop(0))
    t=m.tg.Transport(client=httpx.Client(transport=httpx.MockTransport(handler),trust_env=False))
    with caplog.at_level(logging.INFO):
        assert t.call('sendMessage',{'chat_id':555,'text':'Test'})['status']==503
        assert t.call('getUpdates',{'offset':0,'timeout':10})['status']==503
    assert TOKEN not in caplog.text
    t.close()


def test_current_work_audience_revokes_delayed_telegram_without_session_dependency(service,monkeypatch):
    m,client,_,_=service;configured(monkeypatch)
    calls,_,_=connect(m,client,monkeypatch)
    with m.DB.begin() as db:
        m.notify(db,STUDENT,'assignment:before-move','assignments',{'title':'Private assignment','body':'Former subgroup content','assignment_id':'work-moved','audience':'all','route':'#assignments'},now()+timedelta(days=1))
    def remote(base,path,**kwargs):
        assert path=='/internal/assignments/audiences'
        return {'items':[{'id':'work-moved','subgroup':2,'archived':False,'revision':2}]}
    monkeypatch.setattr(m,'remote',remote)
    assert m.deliver_telegram() and not any(method=='sendMessage' for method,_ in calls)
    assert client.get('/api/notifications/inbox',headers=headers()).json()['total']==0
