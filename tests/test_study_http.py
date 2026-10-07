"""Academic boundaries through real auth and schedule HTTP services."""
import json
import os
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest


PASSWORD = 'Study-integration-password-2026'
TOKEN = 'study-internal-token-at-least-32-characters'
SETUP_KEY = 'study-setup-key-at-least-32-characters'
SUBJECT_KEY = 'c1b43d238cfc883b3d82e1c8f793a7baf45a5e411ea14a64bde4942284f6cd06'
LESSON_FIELDS = ('title', 'title_en', 'kind', 'teacher', 'room', 'mode',
                 'meeting_url', 'note', 'start', 'end', 'subgroup', 'date',
                 'status', 'revision')
SAFE_LESSON_FIELDS = {'title', 'title_en', 'date', 'start', 'end', 'room',
                      'status', 'subgroup'}


class AcademicClient:
    def __init__(self, urls):
        self.urls = urls
        self.http = httpx.Client(timeout=15, trust_env=False)
        self.csrf = ''
        self.user = None

    def response(self, method, path, data=None, headers=None):
        service = 'auth' if path.startswith('/api/auth') else 'schedule'
        return self.http.request(method, self.urls[service]+path, json=data,
                                 headers={'X-CSRF-Token': self.csrf, **(headers or {})})

    def request(self, method, path, data=None, expected=200, headers=None):
        response = self.response(method, path, data, headers)
        assert response.status_code == expected, (method, path, response.status_code, response.text)
        return response.json()


@pytest.fixture
def academic_cluster(tmp_path):
    root = Path(__file__).resolve().parents[1]
    ports = {}
    for service in ('auth', 'schedule'):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            ports[service] = sock.getsockname()[1]
    urls = {name: 'http://127.0.0.1:'+str(port) for name, port in ports.items()}
    env = {**os.environ, 'INTERNAL_TOKEN': TOKEN, 'SETUP_KEY': SETUP_KEY,
           'AUTH_URL': urls['auth'], 'TRANSLATION_WORKER': 'false',
           'COOKIE_SECURE': 'false', 'MATERIALS_DIR': str(tmp_path/'materials')}
    processes, logs, clients = [], [], []

    def client():
        item = AcademicClient(urls)
        clients.append(item)
        return item

    try:
        for service, port in ports.items():
            log = open(tmp_path/(service+'.log'), 'w')
            logs.append(log)
            process = subprocess.Popen([
                sys.executable, '-m', 'uvicorn', 'services.'+service+'.main:app',
                '--host', '127.0.0.1', '--port', str(port),
                *(['--h11-max-incomplete-event-size','65536'] if service=='schedule' else [])], cwd=root,
                env={**env, 'DATABASE_URL': 'sqlite:///'+str(tmp_path/(service+'.db'))},
                stdout=log, stderr=subprocess.STDOUT)
            processes.append(process)
            deadline = time.monotonic()+20
            while time.monotonic() < deadline:
                assert process.poll() is None, (tmp_path/(service+'.log')).read_text()
                try:
                    if httpx.get(urls[service]+'/health', timeout=1, trust_env=False).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(.05)
            else:
                pytest.fail(service+' startup timed out')

        admin, guest = client(), client()
        session = admin.request('POST', '/api/auth/setup', {
            'email': 'admin@study.test', 'password': PASSWORD,
            'name': 'Study Administrator', 'setup_key': SETUP_KEY}, expected=201)
        admin.csrf, admin.user = session['csrf'], session['user']
        invite = admin.request('POST', '/api/auth/invitations',
                               {'max_uses': 3, 'days': 1}, expected=201)['token']
        students = []
        for number, subgroup in enumerate((1, 1, 2)):
            student = client()
            email = 'student'+str(number)+'@study.test'
            student.request('POST', '/api/auth/register', {
                'email': email, 'password': PASSWORD, 'name': 'Study Student '+str(number),
                'subgroup': subgroup, 'invite': invite}, expected=201)
            user = next(u for u in admin.request('GET', '/api/auth/users') if u['email'] == email)
            admin.request('PATCH', '/api/auth/users/'+user['id'],
                          {'role': 'student', 'status': 'active', 'subgroup': subgroup})
            session = student.request('POST', '/api/auth/login', {'email': email, 'password': PASSWORD})
            student.csrf, student.user = session['csrf'], session['user']
            students.append(student)

        today = datetime.now(timezone.utc).date()
        config = admin.request('GET', '/api/schedule/settings')
        config.update(semester_start=(today-timedelta(days=7)).isoformat(),
                      semester_end=(today+timedelta(days=21)).isoformat(),
                      anchor_monday=(today-timedelta(days=today.weekday())).isoformat(),
                      timezone='UTC', configured=True)
        admin.request('PUT', '/api/schedule/settings', config)
        rule_data = {'title': 'Базы данных Study', 'title_en': 'Study databases',
                     'kind': 'lab', 'teacher': 'PRIVATE TEACHER', 'room': '101',
                     'mode': 'remote', 'meeting_url': 'https://example.test/private-class',
                     'note': 'PRIVATE NOTE', 'start': '10:00', 'end': '11:00',
                     'subgroup': 1, 'weekday': today.weekday(), 'parity': 'all', 'revision': 0}
        rule = admin.request('POST', '/api/schedule/rules', rule_data, expected=201)
        lessons = admin.request('GET', '/api/schedule/occurrences?start='+today.isoformat()+
                                '&end='+(today+timedelta(days=21)).isoformat())
        lesson = next(item for item in lessons if item['date'] == today.isoformat())
        yield {'admin': admin, 'guest': guest, 'students': students, 'urls': urls,
               'lesson': lesson, 'rule': rule, 'rule_data': rule_data, 'today': today}
    finally:
        for item in clients:
            item.http.close()
        for process in reversed(processes):
            process.terminate()
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        for log in logs:
            log.close()


def subject(cluster):
    subjects = cluster['admin'].request('GET', '/api/schedule/subjects')
    assert len(subjects) == 1
    return subjects[0]


def assignment_body(subject_key, **changes):
    return {'subject_key': subject_key, 'title': 'Лабораторная 1',
            'description': 'Постройте схему базы данных.', 'due_at': None,
            'subgroup': 0, 'material_url': 'https://example.test/materials', **changes}


def create_assignment(cluster, **changes):
    return cluster['admin'].request('POST', '/api/schedule/assignments',
                                    assignment_body(SUBJECT_KEY, **changes), expected=201)


def test_subjects_are_member_readable_revision_guarded_and_not_public(academic_cluster):
    c = academic_cluster
    student = c['students'][0]
    item = subject(c)
    assert set(item) == {'key', 'title', 'title_en', 'title_en_auto', 'teacher',
                         'requirements', 'links', 'revision'}
    assert student.request('GET', '/api/schedule/subjects') == [item]
    assert student.request('GET', '/api/schedule/subjects/'+item['key']) == item
    c['guest'].request('GET', '/api/schedule/subjects', expected=401)
    c['guest'].request('GET', '/api/schedule/subjects/'+item['key'], expected=401)
    body = {'revision': item['revision'], 'teacher': 'Преподаватель курса',
            'requirements': 'Защитить две лабораторные.',
            'links': [{'label': 'Курс', 'url': 'https://example.test/course'}]}
    path = '/api/schedule/subjects/'+item['key']
    student.request('PATCH', path, body, expected=403)
    c['admin'].request('PATCH', path, body, expected=403, headers={'X-CSRF-Token': ''})
    c['admin'].request('PATCH', path, {**body, 'links': [{'label': 'Курс', 'url': 'http://example.test/course'}]}, expected=422)
    updated = c['admin'].request('PATCH', path, body)
    assert updated['requirements'] == body['requirements']
    assert updated['teacher'] == body['teacher']
    assert updated['links'] == body['links']
    assert updated['revision'] == item['revision']+1
    c['admin'].request('PATCH', path, body, expected=409)
    assert student.request('GET', path) == updated
    assert c['lesson']['subject_key'] == item['key']
    guest = c['guest'].request('GET', '/api/schedule/guest/occurrences?start='+c['today'].isoformat()+
                               '&end='+c['today'].isoformat())
    assert all('teacher' not in row and 'requirements' not in row for row in guest)


def test_assignment_audience_and_personal_progress_are_private(academic_cluster):
    c = academic_cluster
    student, peer, other = c['students']
    common = create_assignment(c)
    own = create_assignment(c, subgroup=1, title='Первая подгруппа')
    foreign = create_assignment(c, subgroup=2, title='Вторая подгруппа')
    assert set(common) == {'id', 'subject_key', 'subject_title', 'title', 'description',
                           'due_at', 'subgroup', 'material_url', 'revision', 'progress'}
    assert common['progress'] == {'status': 'not_started', 'revision': 0}
    path = '/api/schedule/assignments'
    assert {item['id'] for item in student.request('GET', path)} == {common['id'], own['id']}
    assert {item['id'] for item in other.request('GET', path+'?subgroup=0')} == {common['id'], foreign['id']}
    assert len(c['admin'].request('GET', path)) == 3
    assert {item['id'] for item in c['admin'].request('GET', path+'?subgroup=1')} == {common['id'], own['id']}
    student.request('GET', path+'?subgroup=2', expected=403)
    c['guest'].request('GET', path, expected=401)
    student.request('POST', path, assignment_body(common['subject_key']), expected=403)
    progress_path = path+'/'+own['id']+'/progress'
    student.request('PUT', progress_path, {'status': 'ready', 'revision': 0, 'user_id': peer.user['id']}, expected=422)
    student.request('PUT', progress_path, {'status': 'ready', 'revision': 0}, expected=403, headers={'X-CSRF-Token': ''})
    assert student.request('PUT', progress_path, {'status': 'ready', 'revision': 0}) == {'status': 'ready', 'revision': 1}
    student.request('PUT', progress_path, {'status': 'done', 'revision': 0}, expected=409)
    student.request('PUT', progress_path, {'status': 'submitted_by_teacher', 'revision': 1}, expected=422)
    other.request('PUT', progress_path, {'status': 'done', 'revision': 0}, expected=404)
    own_results = student.request('GET', path+'?subject_key='+own['subject_key'])
    assert next(item for item in own_results if item['id'] == own['id'])['progress'] == {'status': 'ready', 'revision': 1}
    for reader in (peer, c['admin']):
        assert next(item for item in reader.request('GET', path) if item['id'] == own['id'])['progress'] == {'status': 'not_started', 'revision': 0}
        assert 'user_id' not in json.dumps(reader.request('GET', path))
    # The sequential public stream must obey the same assignment audience.
    visible = [event['data']['assignment_id'] for event in student.request('GET', '/api/schedule/events')
               if event['type'].startswith('assignment.')]
    assert own['id'] in visible and foreign['id'] not in visible


def test_assignment_updates_archives_validation_and_events(academic_cluster):
    c = academic_cluster
    key = SUBJECT_KEY
    path = '/api/schedule/assignments'
    for extra in ({'due_at': '2026-10-10T10:00:00'}, {'material_url': 'http://example.test/file'},
                  {'material_url': 'https://'}, {'title': 'bad\x00title'}):
        c['admin'].request('POST', path, assignment_body(key, **extra), expected=422)
    c['admin'].request('POST', path, assignment_body('unknown-subject'), expected=404)
    created = create_assignment(c, due_at='2026-10-10T13:00:00+03:00')
    assert created['due_at'] == '2026-10-10T10:00:00Z'
    detail = path+'/'+created['id']
    body = assignment_body(key, title='Исправленная работа', due_at='2026-10-11T10:00:00Z', revision=created['revision'])
    c['students'][0].request('PUT', detail, body, expected=403)
    updated = c['admin'].request('PUT', detail, body)
    assert updated['revision'] == 2 and updated['title'] == 'Исправленная работа'
    c['admin'].request('PUT', detail, body, expected=409)
    c['admin'].request('DELETE', detail+'?revision=1', expected=409)
    assert c['admin'].request('DELETE', detail+'?revision=2') == {'ok': True}
    assert c['students'][0].request('GET', path) == []
    c['students'][0].request('PUT', detail+'/progress', {'status': 'done', 'revision': 0}, expected=404)
    c['admin'].request('PUT', detail, {**body, 'revision': 3}, expected=404)
    events = [event for event in c['admin'].request('GET', '/api/schedule/events')
              if event['type'].startswith('assignment.')]
    assert [event['type'] for event in events] == ['assignment.created', 'assignment.updated', 'assignment.archived']
    assert [event['data']['revision'] for event in events] == [1, 2, 3]
    assert all(set(event['data']) == {'assignment_id', 'title', 'subject_title', 'due_at',
                                     'subgroup', 'revision'} for event in events)
    assert 'Постройте' not in json.dumps(events, ensure_ascii=False)
    assert all(events[i]['seq'] < events[i+1]['seq'] for i in range(len(events)-1))


def test_assignment_and_first_progress_updates_serialize_stale_races(academic_cluster):
    c = academic_cluster
    created = create_assignment(c)
    path = '/api/schedule/assignments/'+created['id']
    body = assignment_body(created['subject_key'], revision=1)
    with ThreadPoolExecutor(max_workers=2) as pool:
        updates = list(pool.map(lambda number: c['admin'].response('PUT', path, {**body, 'title': 'Версия '+str(number)}), range(2)))
    assert sorted(response.status_code for response in updates) == [200, 409]
    with ThreadPoolExecutor(max_workers=2) as pool:
        progress = list(pool.map(lambda status: c['students'][0].response(
            'PUT', path+'/progress', {'status': status, 'revision': 0}), ('in_progress', 'ready')))
    assert sorted(response.status_code for response in progress) == [200, 409]
    stored = c['students'][0].request('GET', '/api/schedule/assignments')[0]['progress']
    assert stored['revision'] == 1 and stored['status'] in ('in_progress', 'ready')


def test_assignment_event_access_follows_current_audience_after_reassignment(academic_cluster):
    c = academic_cluster
    item = create_assignment(c, subgroup=1)
    c['admin'].request('PUT', '/api/schedule/assignments/'+item['id'],
                       assignment_body(SUBJECT_KEY, subgroup=2, revision=1))
    assert c['students'][0].request('GET', '/api/schedule/assignments') == []
    assert not any(event['type'].startswith('assignment.') for event in
                   c['students'][0].request('GET', '/api/schedule/events'))
    visible = [event for event in c['students'][2].request('GET', '/api/schedule/events')
               if event['type'].startswith('assignment.')]
    assert [event['type'] for event in visible] == ['assignment.updated']


def test_hidden_assignment_events_do_not_block_later_visible_events(academic_cluster):
    c = academic_cluster
    for number in range(201):
        create_assignment(c, subgroup=2, title='Вторая подгруппа '+str(number))
    visible = create_assignment(c, subgroup=1, title='Для первой подгруппы')
    events = c['students'][0].request('GET', '/api/schedule/events')
    assert [event['data']['assignment_id'] for event in events
            if event['type'].startswith('assignment.')] == [visible['id']]


def test_internal_reminders_only_include_due_active_assignments_and_completed_ids(academic_cluster):
    c = academic_cluster
    stamp = datetime.now(timezone.utc)
    due = create_assignment(c, due_at=(stamp+timedelta(hours=3)).isoformat())
    create_assignment(c, title='Просроченная', due_at=(stamp-timedelta(hours=1)).isoformat())
    create_assignment(c, title='Позже', due_at=(stamp+timedelta(hours=25)).isoformat())
    create_assignment(c, title='Без срока')
    archived = create_assignment(c, title='Скрытая', due_at=(stamp+timedelta(hours=2)).isoformat())
    c['admin'].request('DELETE', '/api/schedule/assignments/'+archived['id']+'?revision=1')
    for student, status in zip(c['students'][:2], ('done', 'ready')):
        student.request('PUT', '/api/schedule/assignments/'+due['id']+'/progress', {'status': status, 'revision': 0})
    path = '/internal/assignments/reminders'
    c['admin'].request('GET', path, expected=403)
    c['guest'].request('GET', path, expected=403, headers={'X-Internal-Token': 'wrong'})
    result = c['guest'].request('GET', path, headers={'X-Internal-Token': TOKEN})
    assert len(result) == 1 and result[0]['id'] == due['id']
    assert set(result[0]) == {'id', 'title', 'subject_title', 'due_at', 'subgroup',
                             'revision', 'completed_user_ids'}
    assert result[0]['completed_user_ids'] == [c['students'][0].user['id']]
    public = c['students'][0].request('GET', '/api/schedule/assignments')
    assert 'completed_user_ids' not in json.dumps(public)


def test_internal_assignment_audiences_are_bounded_current_and_token_protected(academic_cluster):
    c = academic_cluster
    active = create_assignment(c, subgroup=1)
    changed = create_assignment(c, subgroup=1, title='Перенесённая работа')
    c['admin'].request('PUT', '/api/schedule/assignments/'+changed['id'],
                       assignment_body(SUBJECT_KEY, subgroup=2, revision=1))
    archived = create_assignment(c, subgroup=2, title='Архивная работа')
    c['admin'].request('DELETE', '/api/schedule/assignments/'+archived['id']+'?revision=1')
    path = '/internal/assignments/audiences'
    body = {'ids': [active['id'], changed['id'], archived['id'],
                    '00000000-0000-0000-0000-000000000000', active['id']]}
    c['admin'].request('POST', path, body, expected=403)
    c['guest'].request('POST', path, body, expected=403, headers={'X-Internal-Token': 'wrong'})
    auth = {'X-Internal-Token': TOKEN}
    result = c['guest'].request('POST', path, body, headers=auth)
    assert set(result) == {'items'}
    assert {item['id']: item for item in result['items']} == {
        active['id']: {'id': active['id'], 'subgroup': 1, 'archived': False, 'revision': 1},
        changed['id']: {'id': changed['id'], 'subgroup': 2, 'archived': False, 'revision': 2},
        archived['id']: {'id': archived['id'], 'subgroup': 2, 'archived': True, 'revision': 2}}
    assert c['guest'].request('POST', path, {'ids': []}, headers=auth) == {'items': []}
    assert len(c['guest'].request('POST', path, {'ids': [active['id']]*200}, headers=auth)['items']) == 1
    for invalid in ({'ids': [active['id']]*201}, {'ids': ['x'*37]}, {'ids': ['']},
                    {'ids': ['bad\x00id']}, {'ids': [1]}, {}, {'ids': body['ids'], 'subgroup': 1}):
        c['guest'].request('POST', path, invalid, expected=422, headers=auth)


def test_lesson_events_have_safe_before_after_on_move_rule_edit_and_removal(academic_cluster):
    c = academic_cluster
    created = next(event for event in c['admin'].request('GET', '/api/schedule/events')
                   if event['data']['occurrence_id'] == c['lesson']['id'])
    assert created['data']['before'] is None
    assert set(created['data']['after']) == SAFE_LESSON_FIELDS
    old = c['lesson']
    changed = c['admin'].request('PATCH', '/api/schedule/occurrences/'+old['id'],
                                 {**{key: old[key] for key in LESSON_FIELDS},
                                  'date': (c['today']+timedelta(days=1)).isoformat(), 'room': '202'})
    event = c['admin'].request('GET', '/api/schedule/events')[-1]
    assert event['data']['before']['date'] == c['today'].isoformat()
    assert event['data']['before']['room'] == '101'
    assert event['data']['after']['date'] == changed['date'] and event['data']['after']['room'] == '202'
    edit = {**c['rule_data'], 'revision': c['rule']['revision'], 'room': '303'}
    preview = c['admin'].request('POST', '/api/schedule/rules/'+c['rule']['id']+'/preview', edit)
    events_before = c['admin'].request('GET', '/api/schedule/events')
    c['admin'].request('PUT', '/api/schedule/rules/'+c['rule']['id'], {**edit, 'preview_token': preview['preview_token']})
    rule_events = c['admin'].request('GET', '/api/schedule/events?after='+str(events_before[-1]['seq']))
    assert rule_events and all(event['data']['before']['room'] == '101' and
                               event['data']['after']['room'] == '303' for event in rule_events)
    c['admin'].request('DELETE', '/api/schedule/occurrences/'+changed['id']+'?revision='+str(changed['revision']))
    removal = c['admin'].request('GET', '/api/schedule/events')[-1]
    assert removal['type'] == 'occurrence.cancelled'
    assert removal['data']['before']['status'] == 'confirmed'
    assert removal['data']['after']['status'] == 'cancelled'
    for event in c['admin'].request('GET', '/api/schedule/events'):
        encoded = json.dumps(event)
        assert 'PRIVATE TEACHER' not in encoded and 'PRIVATE NOTE' not in encoded and 'private-class' not in encoded
        assert set(event['data']['after']) == SAFE_LESSON_FIELDS


def test_obsolete_lesson_option_is_rejected_and_never_returned(academic_cluster):
    c = academic_cluster
    c['admin'].request('POST', '/api/schedule/rules',
                       {**c['rule_data'], 'queue_enabled': True, 'subgroup': 2}, expected=422)
    assert 'queue_enabled' not in c['lesson'] and 'queue_enabled' not in c['rule']
