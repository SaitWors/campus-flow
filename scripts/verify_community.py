"""Guest and private conversations against real HTTP services and databases."""
from datetime import date, timedelta
from scripts.smoke import Client, PASSWORD, key


def run(urls):
    admin=Client(urls).login('admin@example.test')
    student=Client(urls).login('student0@example.test',PASSWORD+'new')
    deputy=Client(urls).login('student4@example.test')
    guest=Client(urls)
    try:
        a=admin.request('GET','/api/auth/me')['user']
        patch={'role':'admin','status':'active','subgroup':a['subgroup'],'group_role':'head'}
        dual=admin.request('PATCH','/api/auth/users/'+a['id'],patch)
        assert dual['role']=='admin' and dual['group_role']=='head'
        assert admin.request('GET','/api/auth/me')['user']['group_role']=='head'
        deputy.request('PATCH','/api/auth/users/'+a['id'],patch,expected=403)
        s=student.request('GET','/api/auth/me')['user']
        deputy.request('PATCH','/api/auth/users/'+s['id'],
            {'role':'student','status':'active','subgroup':s['subgroup'],'group_role':'head'},expected=403)
        config=guest.request('GET','/api/schedule/guest/settings')
        rules=admin.request('GET','/api/schedule/rules')
        start=config['semester_start']; end=(date.fromisoformat(start)+timedelta(days=41)).isoformat()
        path='/api/schedule/guest/occurrences?start='+start+'&end='+end
        public=guest.request('GET',path)
        assert public
        forbidden={'teacher','note','meeting_url','rule_id','queue_enabled','revision'}
        assert all(not forbidden.intersection(item) for item in public)
        guest.request('GET','/api/schedule/occurrences?start='+start+'&end='+end,expected=401)
        guest.request('GET','/api/schedule/calendar.ics?start='+start+'&end='+end,expected=401)
        guest.request('GET','/api/auth/users',expected=401)
        guest.request('GET','/api/queues',expected=401)
        guest.request('POST','/api/schedule/rules',
            {k:v for k,v in rules[0].items() if k not in ('id','title_en_auto','translation_pending','translation_enabled')},
            expected=401)
        guest.request('GET','/api/notifications/questions',expected=401)
        recipients=student.request('GET','/api/notifications/questions/recipients')
        assert next(u for u in recipients if u['id']==a['id'])['group_role']=='head'
        assert all(set(u)=={'id','name','role','group_role'} for u in recipients)
        body={'recipient_id':a['id'],'title':'CI private question','body':'CI private content, visible only to the selected recipient.'}
        request_key=key()
        q=student.request('POST','/api/notifications/questions',body,key=request_key,expected=201)
        assert student.request('POST','/api/notifications/questions',body,key=request_key,expected=201)['id']==q['id']
        path='/api/notifications/questions/'+q['id']
        deputy.request('GET',path,expected=404)
        deputy.request('POST',path+'/messages',{'body':'Unauthorized reply'},key=key(),expected=404)
        old=student.csrf;student.csrf=''
        student.request('POST',path+'/messages',{'body':'No csrf'},key=key(),expected=403)
        student.csrf=old
        admin.request('POST',path+'/messages',{'body':'CI private answer'},key=key(),expected=201)
        detail=student.request('GET',path)
        assert detail['unread'] and len(detail['messages'])==2
        student.request('POST',path+'/read',{'revision':detail['revision']})
        assert not student.request('GET',path)['unread']
        inbox=student.request('GET','/api/notifications/inbox')
        assert any(i['category']=='questions' for i in inbox['items'])
        assert 'CI private content' not in str(inbox) and 'CI private answer' not in str(inbox)
        student.request('PATCH',path,{'revision':detail['revision'],'closed':True})
        student.request('POST',path+'/messages',{'body':'Closed question'},key=key(),expected=409)
        student.request('PATCH',path,{'revision':detail['revision'],'closed':False},expected=409)
        student.request('PATCH',path,{'revision':detail['revision']+1,'closed':False})
        print('PASS: guest field allowlist and mutation denial, admin+head role, private questions, CSRF, ownership, replies, idempotency, read state and closure',flush=True)
    finally:
        for c in (admin,student,deputy,guest):c.close()


if __name__=='__main__':
    run({k:'http://localhost:8080' for k in ('auth','schedule','queue','notifications')})
