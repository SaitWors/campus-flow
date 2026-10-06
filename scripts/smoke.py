"""Destructive integration check for a NEW, EMPTY test instance only.

Uses real HTTP and parallel requests. Never run against a group's live data.
"""
import argparse
import os
import uuid
from datetime import datetime,timedelta,timezone
import httpx

PASSWORD='Integration-test-password-2026'

class Client:
    def __init__(self,urls):
        self.urls=urls;self.http=httpx.Client(timeout=20,trust_env=False)
        self.csrf='';self.user=None
    def request(self,method,path,data=None,key=None,expected=200):
        service='auth' if path.startswith('/api/auth') else 'schedule' if path.startswith('/api/schedule') else 'notifications' if path.startswith('/api/notifications') else 'notifications'
        headers={'X-CSRF-Token':self.csrf}
        if key:headers['Idempotency-Key']=key
        r=self.http.request(method,self.urls[service]+path,json=data,headers=headers)
        assert r.status_code==expected,(method,path,r.status_code,r.text[:400])
        return r.json()
    def login(self,email,password=PASSWORD):
        r=self.request('POST','/api/auth/login',{'email':email,'password':password})
        self.csrf=r['csrf'];self.user=r['user'];return self
    def close(self):self.http.close()

def key():return str(uuid.uuid4())

def run(urls,setup_key):
    clients=[];groups=[]
    def client():
        c=Client(urls);clients.append(c);return c
    def passed(name):
        groups.append(name);print('PASS:',name,flush=True)
    admin=client()
    try:
        assert admin.request('GET','/api/auth/status')['needs_setup'], 'Refusing to modify a configured instance'
        session=admin.request('POST','/api/auth/setup',{'email':'admin@example.test','password':PASSWORD,'name':'Test Administrator','setup_key':setup_key},expected=201)
        admin.csrf=session['csrf'];admin.user=session['user']
        admin.request('POST','/api/auth/setup',{'email':'other@example.test','password':PASSWORD,'name':'Other Admin','setup_key':setup_key},expected=409)
        bad=client();bad.request('GET','/api/auth/users',expected=401)
        old_csrf=admin.csrf;admin.csrf='invalid'
        admin.request('POST','/api/auth/invitations',{},expected=403);admin.csrf=old_csrf
        r=admin.http.post(urls['auth']+'/api/auth/invitations',json={},headers={'Origin':'https://untrusted.example','X-CSRF-Token':admin.csrf})
        assert r.status_code==403
        invite=admin.request('POST','/api/auth/invitations',{'max_uses':5,'days':1},expected=201)['token']
        students=[]
        for i in range(5):
            c=client();email='student'+str(i)+'@example.test';subgroup=2 if i==3 else 1
            c.request('POST','/api/auth/register',{'email':email,'password':PASSWORD,'name':'Student '+str(i),'subgroup':subgroup,'invite':invite},expected=201)
            c.request('POST','/api/auth/login',{'email':email,'password':PASSWORD},expected=403)
            members=admin.request('GET','/api/auth/users');u=next(u for u in members if u['email']==email)
            admin.request('PATCH','/api/auth/users/'+u['id'],{'role':'student','status':'active','subgroup':subgroup})
            students.append(c.login(email))
        bad.request('POST','/api/auth/register',{'email':'sixth@example.test','password':PASSWORD,'name':'Sixth Student','subgroup':1,'invite':invite},expected=403)
        students[0].request('GET','/api/auth/users',expected=403)
        deputy=students[4]
        admin.request('PATCH','/api/auth/users/'+deputy.user['id'],{'role':'deputy','status':'active','subgroup':1})
        deputy.request('PATCH','/api/auth/users/'+students[0].user['id'],{'role':'admin','status':'active','subgroup':1},expected=403)
        deputy.request('GET','/api/auth/users')
        admin.request('PATCH','/api/auth/users/'+admin.user['id'],{'role':'student','status':'active','subgroup':1},expected=409)

        bad.request('POST','/api/schedule/title-preview',{'title':'Базы данных'},expected=401)
        students[0].request('POST','/api/schedule/title-preview',{'title':'Базы данных'},expected=403)
        old_csrf=admin.csrf;admin.csrf='invalid'
        admin.request('POST','/api/schedule/title-preview',{'title':'Базы данных'},expected=403)
        admin.csrf=old_csrf
        admin.request('POST','/api/schedule/title-preview',{'title':'Базы данных','url':'http://169.254.169.254/'},expected=422)
        bad.request('POST','/api/auth/login',{'email':'x'*230+'@example.test','password':PASSWORD},expected=401)
        passed('Registration, approval, roles, invite limits, CSRF and origin checks')

        today=datetime.now(timezone.utc).date();start=today-timedelta(days=7);end=today+timedelta(days=21)
        config=admin.request('GET','/api/schedule/settings')
        config.update(semester_start=start.isoformat(),semester_end=end.isoformat(),anchor_monday=(today-timedelta(days=today.weekday())).isoformat(),anchor_parity='odd',configured=True,timezone='UTC')
        configured=deputy.request('PUT','/api/schedule/settings',config)
        admin.request('PUT','/api/schedule/settings',config,expected=409)
        assert configured['revision']==config['revision']+1
        rule={'title':'Проверка расписания','title_en':'Schedule verification','kind':'lab','teacher':'Test Teacher','room':'Test 101','mode':'onsite','meeting_url':'','note':'Integration test only','start':'00:00','end':'23:59','subgroup':1,'weekday':today.weekday(),'parity':'all','revision':0}
        students[0].request('POST','/api/schedule/rules',rule,expected=403)
        created=deputy.request('POST','/api/schedule/rules',rule,expected=201)
        admin.request('POST','/api/schedule/rules',rule,expected=409)
        assert len(admin.request('GET','/api/schedule/rules'))==1
        lessons=admin.request('GET','/api/schedule/occurrences?start='+today.isoformat()+'&end='+end.isoformat())
        item=next(l for l in lessons if l['date']==today.isoformat())
        assert item['parity']=='odd'
        assert next(l for l in lessons if l['date']==(today+timedelta(days=7)).isoformat())['parity']=='even'
        assert item['rule_id']==created['id']
        passed('Semester parity, conflicts, rollback and stale schedule edits')

        def patch(l,**changes):
            fields=('title','title_en','kind','teacher','room','mode','meeting_url','note','start','end','subgroup','date','status','revision')
            return admin.request('PATCH','/api/schedule/occurrences/'+l['id'],{**{k:l[k] for k in fields},**changes})
        item=patch(item,date=(today+timedelta(days=1)).isoformat(),room='Test 202',mode='remote',meeting_url='https://example.test/class')
        item=patch(item,status='cancelled')
        old=admin.request('GET','/api/schedule/occurrences?start='+today.isoformat()+'&end='+today.isoformat())
        assert all(l['id']!=item['id'] for l in old)
        ics=admin.http.get(urls['schedule']+'/api/schedule/calendar.ics',params={'start':start.isoformat(),'end':end.isoformat(),'lang':'en'})
        assert ics.status_code==200 and 'STATUS:CANCELLED' in ics.text and 'Schedule verification' in ics.text
        assert all(len(line.encode())<=75 for line in ics.text.splitlines())
        assert admin.request('GET','/api/schedule/events?after=0')
        passed('Individual move, cancellation, ICS and durable schedule audit')
        loser=students[2]

        reset=admin.request('POST','/api/auth/users/'+students[0].user['id']+'/reset',{})['token']
        bad.request('POST','/api/auth/reset',{'token':reset,'password':PASSWORD+'new'})
        students[0].request('GET','/api/auth/me',expected=401)
        bad.request('POST','/api/auth/reset',{'token':reset,'password':PASSWORD+'new'},expected=400)
        students[0].login(students[0].user['email'],PASSWORD+'new')
        admin.request('PATCH','/api/auth/users/'+loser.user['id'],{'role':'student','status':'blocked','subgroup':1})
        loser.request('GET','/api/auth/me',expected=401)
        assert admin.request('GET','/api/auth/audit')
        passed('Single-use password reset and session revocation on reset/block')
        return groups
    finally:
        for c in clients:c.close()

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url',required=True)
    parser.add_argument('--allow-empty-instance',action='store_true',required=True)
    args=parser.parse_args()
    setup_key=os.environ.get('SETUP_KEY','')
    if not setup_key:
        from pathlib import Path
        env=Path('.env')
        if env.exists():
            setup_key=next((line.split('=',1)[1] for line in env.read_text().splitlines() if line.startswith('SETUP_KEY=')),'')
    if not setup_key:raise SystemExit('SETUP_KEY is required')
    run({k:args.base_url.rstrip('/') for k in ('auth','schedule','notifications')},setup_key)
