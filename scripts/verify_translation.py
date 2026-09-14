"""Only for the disposable fixture created by scripts/smoke.py."""
import time
import httpx
from scripts.smoke import PASSWORD

with httpx.Client(base_url='http://localhost:8080',timeout=20,trust_env=False) as client:
    login=client.post('/api/auth/login',json={'email':'admin@example.test','password':PASSWORD})
    login.raise_for_status()
    client.headers['X-CSRF-Token']=login.json()['csrf']
    deadline=time.monotonic()+90
    while True:
        response=client.post('/api/schedule/title-preview',json={'title':'Технологии баз данных'})
        response.raise_for_status()
        result=response.json()
        if result['title_en_auto']:
            assert 'data' in result['title_en_auto'].lower(), result
            print('PASS: real local RU -> EN model, private network and persisted cache')
            break
        if time.monotonic()>deadline:
            raise AssertionError('Local translation did not recover in time')
        time.sleep(6)
