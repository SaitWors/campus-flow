"""Build concise bilingual updates from safe internal event metadata."""
from services.notifications.questions import STAFF

LESSON_FIELDS = ('title', 'title_en', 'date', 'start', 'end', 'room', 'status', 'subgroup')


def lesson(value):
    if not isinstance(value, dict):
        return None
    result = {}
    for key in LESSON_FIELDS:
        if key not in value:
            continue
        if key == 'subgroup':
            result[key] = value[key] if value[key] in (0, 1, 2) else 0
        else:
            result[key] = ''.join(c for c in str(value[key] or '') if ord(c) >= 32)[:160]
    return result


def audience(subgroup):
    return 'subgroup' + str(subgroup) if subgroup in (1, 2) else 'all'


def relevant(subgroup, user):
    return subgroup in (0, user['subgroup']) or user['role'] in STAFF


def lesson_text(value, english=False):
    name = (value.get('title_en') or value.get('title', '')) if english else value.get('title', '')
    when = ' '.join(v for v in (value.get('date'), value.get('start', '') + ('–' + value['end'] if value.get('end') else '')) if v)
    room = (('room ' if english else 'ауд. ') + value['room']) if value.get('room') else ''
    status = {'cancelled': 'cancelled' if english else 'отменено',
              'online': 'online' if english else 'онлайн'}.get(value.get('status'), '')
    return ', '.join(v for v in (name, when, room, status) if v)


def change_text(change, english=False):
    before, after = change['before'], change['after']
    if not after or after.get('status') == 'cancelled':
        prefix = 'Cancelled' if english else 'Отменено'
        return prefix + ': ' + lesson_text(after or before or {}, english)
    if not before:
        return ('New class: ' if english else 'Новая пара: ') + lesson_text(after, english)
    moved = any(before.get(k) != after.get(k) for k in ('date', 'start', 'end'))
    label = ('Moved' if moved else 'Changed') if english else ('Перенесено' if moved else 'Изменено')
    return label + ': ' + lesson_text(before, english) + ' → ' + lesson_text(after, english)


def schedule_data(events, user):
    grouped = {}
    restricted = False
    for event in events:
        data = event['data']
        before, after = lesson(data.get('before')), lesson(data.get('after'))
        # Older stream records have no safe snapshot: keep a generic notice only.
        if before is None and after is None:
            if not relevant(data.get('subgroup', 0), user):
                continue
            restricted = restricted or data.get('subgroup', 0) != 0
            grouped[event['id']] = {'before': None, 'after': None}
            continue
        original = (before, after)
        before = before if before and relevant(before.get('subgroup', 0), user) else None
        after = after if after and relevant(after.get('subgroup', 0), user) else None
        if before is None and after is None:
            continue
        restricted = restricted or any(v and v.get('subgroup', 0) != 0 for v in original)
        key = data.get('occurrence_id') or event['id']
        if key in grouped:
            grouped[key]['after'] = after
        else:
            grouped[key] = {'before': before, 'after': after}
    if not grouped:
        return None
    changes = list(grouped.values())
    safe_changes = [c for c in changes if c['before'] or c['after']]
    generic_ru = 'Проверьте время, аудиторию и статус занятий.'
    generic_en = 'Check class times, rooms and status.'
    body = '\n'.join(change_text(c) for c in safe_changes[:12]) or generic_ru
    body_en = '\n'.join(change_text(c, True) for c in safe_changes[:12]) or generic_en
    if len(changes) > 12:
        body += '\nЕщё изменений: ' + str(len(changes) - 12) + '. Откройте расписание.'
        body_en += '\nMore changes: ' + str(len(changes) - 12) + '. Open the timetable.'
    scope = ('managers' if user['role'] in STAFF else audience(user['subgroup'])) if restricted else 'all'
    return {'title': 'Изменения расписания' + (' (' + str(len(changes)) + ')' if len(changes) > 1 else ''),
            'title_en': 'Timetable changes' + (' (' + str(len(changes)) + ')' if len(changes) > 1 else ''),
            'body': body, 'body_en': body_en, 'route': '#schedule', 'important': False,
            'audience': scope, 'changes': safe_changes[:12], 'change_count': len(changes)}


def assignment_data(data, kind='updated', reminder=False):
    title = str(data.get('title', ''))[:160]
    subject = str(data.get('subject_title', ''))[:160]
    due_at = data.get('due_at')
    names = ', '.join(v for v in (subject, title) if v)
    ru, en = {'created': ('Новая работа', 'New assignment'),
              'updated': ('Работа обновлена', 'Assignment updated'),
              'archived': ('Работа снята', 'Assignment archived')}.get(kind, ('Работа обновлена', 'Assignment updated'))
    if reminder:
        ru, en = 'Скоро срок сдачи', 'Assignment due soon'
    return {'title': ru, 'title_en': en,
            'body': names + ('. Срок: ' + due_at if due_at else ''),
            'body_en': names + ('. Due: ' + due_at if due_at else ''),
            'route': '#assignments', 'important': False, 'audience': audience(data.get('subgroup', 0)),
            'assignment_id': data.get('assignment_id') or data.get('id'),
            'revision': data.get('revision', 0), 'due_at': due_at, 'reminder': reminder, 'assignment_archived':kind == 'archived'}
