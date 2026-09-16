"""PR3 integration scenarios. Run only after smoke.py on a disposable instance."""
import base64
import hashlib
import hmac
import struct
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from scripts.smoke import Client, PASSWORD


def code(secret):
    # Independent RFC 6238 client, as used by an authenticator app.
    value=hmac.new(base64.b32decode(secret),struct.pack('>Q',int(time.time())//30),hashlib.sha1).digest()
    offset=value[-1]&15
    return str((int.from_bytes(value[offset:offset+4],'big')&0x7fffffff)%1000000).zfill(6)


def run(urls):
    clients=[]
    def client():
        c=Client(urls);clients.append(c);return c
    admin=client().login('admin@example.test')
    guest=client()
    try:
        # Subject catalogue, independent preset revision and role/CSRF boundaries.
        invitation=admin.request('POST','/api/auth/invitations',{'max_uses':2,'days':1},expected=201)['token']
        for email in ('pr3-student@example.test','pr3-other@example.test'):
            guest.request('POST','/api/auth/register',{'email':email,'password':PASSWORD,'name':email,'invite':invitation,'subgroup':1},expected=201)
            u=next(u for u in admin.request('GET','/api/auth/users') if u['email']==email)
            admin.request('PATCH','/api/auth/users/'+u['id'],{'role':'student','status':'active','subgroup':1})
        student=client().login('pr3-student@example.test');other=client().login('pr3-other@example.test')
        for path in ('subjects','time-presets'):
            guest.request('GET','/api/schedule/'+path,expected=401)
            student.request('GET','/api/schedule/'+path,expected=403)
        presets=admin.request('GET','/api/schedule/time-presets')
        assert [(s['start'],s['end']) for s in presets['items']]==[('09:30','11:00'),('11:15','12:45'),('13:00','14:30'),('15:10','16:40'),('16:55','18:25')]
        config=admin.request('GET','/api/schedule/settings')
        today=date.today();weekday=(today.weekday()+3)%7
        body={'title':'Математический анализ PR3','title_en':'PR3 Mathematical analysis','kind':'lab','teacher':'PRIVATE TEACHER PR3','room':'PR3 421','mode':'remote','meeting_url':'https://example.test/private-meeting-pr3','note':'PRIVATE NOTE PR3','start':'18:30','end':'19:00','subgroup':1,'queue_enabled':True,'weekday':weekday,'parity':'all','revision':0}
        rule=admin.request('POST','/api/schedule/rules',body,expected=201)
        second=admin.request('POST','/api/schedule/rules',{**body,'title':'  МАТЕМАТИЧЕСКИЙ  АНАЛИЗ PR3 ','subgroup':2},expected=201)
        admin.request('POST','/api/schedule/rules',{**body,'subgroup':0},expected=409)
        found=[s for s in admin.request('GET','/api/schedule/subjects') if s['title'].casefold()=='математический анализ pr3']
        assert len(found)==1 and found[0]['title_en']=='PR3 Mathematical analysis'
        path='/api/schedule/occurrences?start='+config['semester_start']+'&end='+config['semester_end']
        all_before=admin.request('GET',path)
        changed={**presets,'items':[{**s,'start':'09:35'} if i==0 else s for i,s in enumerate(presets['items'])]}
        student.request('PUT','/api/schedule/time-presets',changed,expected=403)
        old=admin.csrf;admin.csrf='';admin.request('PUT','/api/schedule/time-presets',changed,expected=403);admin.csrf=old
        updated=admin.request('PUT','/api/schedule/time-presets',changed)
        admin.request('PUT','/api/schedule/time-presets',changed,expected=409)
        admin.request('PUT','/api/schedule/time-presets',{'revision':updated['revision'],'items':[{'label':'1','start':'12:00','end':'11:00'}]},expected=422)
        assert admin.request('GET',path)==all_before
        assert admin.request('GET','/api/schedule/settings')==config
        admin.request('PUT','/api/schedule/time-presets',{**presets,'revision':updated['revision']})
        # Protect manual exceptions and historical rows; preview has no writes.
        matching=[i for i in all_before if i['rule_id']==rule['id']]
        future=[i for i in matching if i['date']>=today.isoformat()]
        assert len(future)>=2
        fields=('title','title_en','kind','teacher','room','mode','meeting_url','note','start','end','subgroup','queue_enabled','date','status','revision')
        exception=admin.request('PATCH','/api/schedule/occurrences/'+future[0]['id'],{**{k:future[0][k] for k in fields},'room':'Individual room'})
        edit={**body,'revision':rule['revision'],'room':'Changed room'}
        admin.request('PUT','/api/schedule/rules/'+rule['id'],edit,expected=409)
        events_before=admin.request('GET','/api/schedule/events')
        before=admin.request('GET',path)
        preview=admin.request('POST','/api/schedule/rules/'+rule['id']+'/preview',edit)
        assert preview['preserved_exceptions']==1 and preview['changes']
        assert all(c['after']['id']!=exception['id'] and c['after']['date']>=today.isoformat() for c in preview['changes'])
        assert admin.request('GET',path)==before
        assert admin.request('GET','/api/schedule/events')==events_before
        # Another manager changes a different occurrence after the preview.
        foreign=next(i for i in before if i['rule_id']==second['id'] and i['date']>=today.isoformat())
        admin.request('PATCH','/api/schedule/occurrences/'+foreign['id'],{**{k:foreign[k] for k in fields},'room':'Concurrent room'})
        admin.request('PUT','/api/schedule/rules/'+rule['id'],{**edit,'preview_token':preview['preview_token']},expected=409)
        preview=admin.request('POST','/api/schedule/rules/'+rule['id']+'/preview',edit)
        final=admin.request('PUT','/api/schedule/rules/'+rule['id'],{**edit,'preview_token':preview['preview_token']})
        after={i['id']:i for i in admin.request('GET',path)}
        assert after[exception['id']]==exception
        for old in before:
            if old['rule_id']==rule['id'] and old['date']<today.isoformat():assert after[old['id']]==old
        changed_ids={c['after']['id'] for c in preview['changes']}
        assert all(after[i]['room']=='Changed room' for i in changed_ids)
        # Public subscriptions include cancellation tombstones, not private fields.
        feed=guest.http.get(urls['schedule']+'/api/schedule/guest/calendar.ics?lang=en&subgroup=1')
        assert feed.status_code==200 and 'text/calendar' in feed.headers['content-type']
        assert 'PR3 Mathematical analysis' in feed.text and '\r\n' in feed.text
        assert all(private not in feed.text for private in ('PRIVATE TEACHER PR3','PRIVATE NOTE PR3','private-meeting-pr3','DESCRIPTION:'))
        assert all(len(line.encode())<=75 for line in feed.text.split('\r\n'))
        admin.request('DELETE','/api/schedule/rules/'+rule['id']+'?revision='+str(final['revision']))
        tombstones=guest.http.get(urls['schedule']+'/api/schedule/guest/calendar.ics').text
        assert 'STATUS:CANCELLED' in tombstones and any(i+'@campus-flow' in tombstones for i in changed_ids)
        admin.request('DELETE','/api/schedule/rules/'+second['id']+'?revision='+str(second['revision']))
        print('PASS: subject deduplication, presets, permissions, preview rollback and concurrency, past/exception preservation, public calendar cancellation',flush=True)
        # Enrollment requires both a password and a code; all other sessions end.
        parallel=client().login('pr3-student@example.test')
        devices=student.request('GET','/api/auth/sessions')
        assert len(devices)==2 and not any('token_hash' in d or 'csrf' in d for d in devices)
        other.request('DELETE','/api/auth/sessions/'+devices[0]['id'],expected=404)
        student.request('POST','/api/auth/security/setup',{'password':'wrong'},expected=401)
        old=student.csrf;student.csrf='';student.request('POST','/api/auth/security/setup',{'password':PASSWORD},expected=403);student.csrf=old
        setup=student.request('POST','/api/auth/security/setup',{'password':PASSWORD})
        otp=code(setup['secret'])
        codes=student.request('POST','/api/auth/security/enable',{'code':otp})['recovery_codes']
        assert len(set(codes))==10
        parallel.request('GET','/api/auth/me',expected=401)
        stranger=client()
        challenge=stranger.request('POST','/api/auth/login',{'email':'pr3-student@example.test','password':PASSWORD})
        assert challenge['mfa_required'] and 'csrf' not in challenge and not stranger.http.cookies
        stranger.request('GET','/api/auth/me',expected=401)
        stranger.request('POST','/api/auth/login/verify',{'challenge':challenge['challenge'],'code':otp},expected=401)
        verified=stranger.request('POST','/api/auth/login/verify',{'challenge':challenge['challenge'],'code':codes[0]})
        stranger.csrf=verified['csrf'];stranger.user=verified['user']
        stranger.request('POST','/api/auth/login/verify',{'challenge':challenge['challenge'],'code':codes[1]},expected=401)
        # Concurrent consumption: one recovery code cannot create two sessions.
        pair=[client(),client()]
        challenges=[c.request('POST','/api/auth/login',{'email':'pr3-student@example.test','password':PASSWORD})['challenge'] for c in pair]
        def race(i):return pair[i].http.post(urls['auth']+'/api/auth/login/verify',json={'challenge':challenges[i],'code':codes[1]}).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:assert sorted(pool.map(race,range(2)))==[200,401]
        exhausted=client();challenge=exhausted.request('POST','/api/auth/login',{'email':'pr3-student@example.test','password':PASSWORD})['challenge']
        for _ in range(5):exhausted.request('POST','/api/auth/login/verify',{'challenge':challenge,'code':'WRONG-CODE'},expected=401)
        exhausted.request('POST','/api/auth/login/verify',{'challenge':challenge,'code':codes[2]},expected=401)
        assert student.request('GET','/api/auth/security')['recovery_remaining']==8
        # Regeneration invalidates old codes. Password reset preserves MFA.
        new_codes=student.request('POST','/api/auth/security/recovery',{'password':PASSWORD,'code':codes[2]})['recovery_codes']
        stranger.request('GET','/api/auth/me',expected=401)
        reset=admin.request('POST','/api/auth/users/'+student.user['id']+'/reset',{})['token']
        exhausted.request('POST','/api/auth/reset',{'token':reset,'password':PASSWORD+'new'})
        student.request('GET','/api/auth/me',expected=401)
        login=client();challenge=login.request('POST','/api/auth/login',{'email':'pr3-student@example.test','password':PASSWORD+'new'})['challenge']
        login.request('POST','/api/auth/login/verify',{'challenge':challenge,'code':codes[3]},expected=401)
        verified=login.request('POST','/api/auth/login/verify',{'challenge':challenge,'code':new_codes[0]});login.csrf=verified['csrf'];login.user=verified['user']
        login.request('POST','/api/auth/password',{'current_password':PASSWORD+'new','new_password':PASSWORD+'again'},expected=400)
        login.request('POST','/api/auth/security/disable',{'password':PASSWORD+'new','code':new_codes[1]})
        assert not login.request('GET','/api/auth/security')['enabled']
        extra=client().login('pr3-student@example.test',PASSWORD+'new')
        login.request('DELETE','/api/auth/sessions/others');extra.request('GET','/api/auth/me',expected=401)
        audit=admin.request('GET','/api/auth/audit')
        assert all(secret not in str(audit) for secret in [setup['secret'],*codes,*new_codes])
        print('PASS: TOTP enrollment, session ownership/revocation, replay rejection, concurrent single-use recovery, attempt limit, rotation and password-reset MFA preservation',flush=True)
    finally:
        for c in clients:c.close()


if __name__=='__main__':
    run({k:'http://localhost:8080' for k in ('auth','schedule','queue','notifications')})
