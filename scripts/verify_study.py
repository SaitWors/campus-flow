"""Study hub scenarios against real HTTP services; disposable smoke fixtures only."""
from datetime import datetime, timedelta, timezone
from scripts.smoke import Client, PASSWORD


def run(urls):
    admin = Client(urls).login('admin@example.test')
    student = Client(urls).login('student0@example.test', PASSWORD+'new')
    peer = Client(urls).login('student1@example.test')
    other_group = Client(urls).login('student3@example.test')
    guest = Client(urls)
    try:
        guest.request('GET', '/api/schedule/subjects', expected=401)
        guest.request('GET', '/api/schedule/assignments', expected=401)
        subjects = student.request('GET', '/api/schedule/subjects')
        assert subjects
        subject = subjects[0]
        path = '/api/schedule/subjects/'+subject['key']
        details = {'revision':subject['revision'], 'teacher':'Study integration teacher',
            'requirements':'Complete the exercises before the seminar.',
            'links':[{'label':'Study integration materials', 'url':'https://example.test/course'}]}
        student.request('PATCH', path, details, expected=403)
        updated = admin.request('PATCH', path, details)
        admin.request('PATCH', path, details, expected=409)
        assert student.request('GET', path)['links'] == details['links']
        body = {'subject_key':subject['key'], 'title':'Study integration assignment',
            'description':'Only the owner can see their personal progress.',
            'due_at':(datetime.now(timezone.utc)+timedelta(hours=12)).isoformat(),
            'subgroup':1, 'material_url':'https://example.test/assignment'}
        student.request('POST', '/api/schedule/assignments', body, expected=403)
        assignment = admin.request('POST', '/api/schedule/assignments', body, expected=201)
        assignment_path = '/api/schedule/assignments/'+assignment['id']
        assert assignment['progress'] == {'status':'not_started', 'revision':0}
        assert assignment['id'] in [a['id'] for a in student.request('GET', '/api/schedule/assignments')]
        assert assignment['id'] not in [a['id'] for a in other_group.request('GET', '/api/schedule/assignments')]
        other_group.request('PUT', assignment_path+'/progress', {'status':'done','revision':0}, expected=404)
        changed = student.request('PUT', assignment_path+'/progress', {'status':'ready','revision':0})
        assert changed == {'status':'ready','revision':1}
        student.request('PUT', assignment_path+'/progress', {'status':'done','revision':0}, expected=409)
        for viewer in (admin, peer):
            item = next(a for a in viewer.request('GET', '/api/schedule/assignments') if a['id']==assignment['id'])
            assert item['progress'] == {'status':'not_started','revision':0}
            assert not {'user_id','completed_user_ids','student_progress'}.intersection(item)
        moved = admin.request('PUT', assignment_path, {**body, 'subgroup':2, 'revision':assignment['revision']})
        assert assignment['id'] not in [a['id'] for a in student.request('GET', '/api/schedule/assignments')]
        student.request('PUT', assignment_path+'/progress', {'status':'done','revision':changed['revision']}, expected=404)
        admin.request('PUT', assignment_path, {**body,'subgroup':1,'revision':assignment['revision']}, expected=409)
        assert assignment['id'] in [a['id'] for a in other_group.request('GET', '/api/schedule/assignments')]
        admin.request('DELETE', assignment_path+'?revision='+str(moved['revision']))
        assert assignment['id'] not in [a['id'] for a in admin.request('GET', '/api/schedule/assignments')]
        # Leave one common assignment for browser and restore verification.
        common = admin.request('POST', '/api/schedule/assignments', {**body,'title':'Study hub browser assignment','subgroup':0}, expected=201)
        assert common['id'] in [a['id'] for a in other_group.request('GET', '/api/schedule/assignments')]
        student.request('GET', '/api/schedule/assignments?subgroup=2', expected=403)
        print('PASS: member subject details, HTTPS materials, assignment roles, private progress, stale-write conflicts, subgroup revocation and archival', flush=True)
    finally:
        for client in (admin, student, peer, other_group, guest): client.close()


if __name__ == '__main__':
    run({service:'http://localhost:8080' for service in ('auth','schedule','notifications')})
