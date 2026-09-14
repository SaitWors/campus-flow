"""Notification checks on the disposable CI fixture created by scripts/smoke.py."""
import time
from scripts.smoke import Client, PASSWORD, key


def run(urls):
    admin=Client(urls).login('admin@example.test')
    student=Client(urls)
    try:
        # This student is always active after the base workflow (its password was reset).
        student.login('student0@example.test',PASSWORD+'new')
        assert student.request('GET','/api/notifications/config')['public_key']
        pref=student.request('GET','/api/notifications/preferences')
        student.request('PUT','/api/notifications/preferences',{**pref,'quiet_enabled':True,'quiet_start':'22:00','quiet_end':'08:00'})
        student.request('PUT','/api/notifications/preferences',pref,expected=409)
        anonymous=Client(urls)
        try:anonymous.request('GET','/api/notifications/inbox',expected=401)
        finally:anonymous.close()
        message={'title':'CI important update','body':'Notification integration check','important':True,'audience':'all'}
        student.request('POST','/api/notifications/announcements',message,key=key(),expected=403)
        old=admin.csrf;admin.csrf=''
        admin.request('POST','/api/notifications/announcements',message,key=key(),expected=403)
        admin.csrf=old
        response=admin.http.post(urls['notifications']+'/api/notifications/announcements',json=message,
            headers={'Origin':'https://evil.example','X-CSRF-Token':admin.csrf,'Idempotency-Key':key()})
        assert response.status_code==403
        request_key=key()
        aid=admin.request('POST','/api/notifications/announcements',message,key=request_key,expected=201)['id']
        assert admin.request('POST','/api/notifications/announcements',message,key=request_key,expected=201)['id']==aid
        inbox=student.request('GET','/api/notifications/inbox')
        item=next(i for i in inbox['items'] if i['title']==message['title'])
        assert any(p['id']==item['id'] for p in inbox['popups'])
        student.request('POST','/api/notifications/read',{'ids':[item['id']]})
        assert not any(p['id']==item['id'] for p in student.request('GET','/api/notifications/inbox')['popups'])
        manager_id=admin.request('POST','/api/notifications/announcements',{**message,'title':'Managers only','audience':'managers'},key=key(),expected=201)['id']
        assert not any(i['title']=='Managers only' for i in student.request('GET','/api/notifications/inbox')['items'])
        admin.request('DELETE','/api/notifications/announcements/'+manager_id)
        # Keep the first announcement in DB for the backup/restore verification.
        assert aid and admin.request('GET','/api/notifications/audit')
        print('PASS: notification HTTP authentication, roles, CSRF, audience, preferences, idempotency, read receipts and withdrawal',flush=True)
    finally:
        admin.close();student.close()


if __name__=='__main__':
    run({k:'http://localhost:8080' for k in ('auth','schedule','queue','notifications')})
