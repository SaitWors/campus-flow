"""Validate a restored CI fixture; not for the live group database."""
import httpx
with httpx.Client(base_url='http://localhost:8080',trust_env=False) as c:
    login=c.post('/api/auth/login',json={'email':'admin@example.test','password':'Integration-test-password-2026'})
    assert login.status_code==200,login.status_code
    assert len(c.get('/api/auth/users').json())==8
    assert len(c.get('/api/schedule/rules').json())==1
    assert c.get('/api/queues/activity/log').json()
    print('Restored accounts, schedule and queue history verified.')
    inbox=c.get('/api/notifications/inbox').json()
    assert any(n['title']=='CI important update' for n in inbox['items'])
    assert c.get('/api/notifications/audit').json()
    print('Restored notification inbox and announcement audit verified.')

    assert login.json()['user']['group_role']=='head'
    questions=c.get('/api/notifications/questions').json()['items']
    q=next(q for q in questions if q['title']=='CI private question')
    detail=c.get('/api/notifications/questions/'+q['id']).json()
    assert len(detail['messages'])==2 and detail['messages'][1]['body']=='CI private answer'
    print('Restored combined role and private question history verified.')

    subjects=c.get('/api/schedule/subjects').json()
    assert any(s['title']=='Математический анализ PR3' for s in subjects)
    assert len(c.get('/api/schedule/time-presets').json()['items'])==5
    assert all(s['id'] and s['created_at'] for s in c.get('/api/auth/sessions').json())
    print('Restored PR3 subject catalogue, time slots and session metadata verified.')
