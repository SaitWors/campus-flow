"""Validate a restored CI fixture; not for the live group database."""
import httpx
with httpx.Client(base_url='http://localhost:8080',trust_env=False) as c:
    login=c.post('/api/auth/login',json={'email':'admin@example.test','password':'Integration-test-password-2026'})
    assert login.status_code==200,login.status_code
    assert len(c.get('/api/auth/users').json())==6
    assert len(c.get('/api/schedule/rules').json())==1
    assert c.get('/api/queues/activity/log').json()
    print('Restored accounts, schedule and queue history verified.')
    inbox=c.get('/api/notifications/inbox').json()
    assert any(n['title']=='CI important update' for n in inbox['items'])
    assert c.get('/api/notifications/audit').json()
    print('Restored notification inbox and announcement audit verified.')
