"""Telegram-authenticated JSON API. Shares the bot event loop and its Services."""
import hashlib
import hmac
import json
import re
import time
from collections import defaultdict, deque
from urllib.parse import parse_qsl

from aiohttp import web

from .service import Service, UserError

SERVICE = web.RequestKey('service', Service)
UID = web.RequestKey('uid', int)


def telegram_user(raw, token, now=None):
    """Validate raw initData, never initDataUnsafe or a user ID from the body."""
    now = time.time() if now is None else now
    try:
        if not raw or len(raw) > 16384:
            raise ValueError
        pairs = parse_qsl(raw, keep_blank_values=True, strict_parsing=True, max_num_fields=30)
        data = dict(pairs)
        if len(data) != len(pairs):
            raise ValueError
        supplied = data.pop('hash')
        if not re.fullmatch(r'[a-f0-9]{64}', supplied):
            raise ValueError
        check = '\n'.join(f'{k}={v}' for k, v in sorted(data.items()))
        secret = hmac.new(b'WebAppData', token.encode(), hashlib.sha256).digest()
        expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(supplied, expected):
            raise ValueError
        age = now - int(data['auth_date'])
        if not -30 <= age <= 3600:
            raise ValueError
        user = json.loads(data['user'])
        uid = user['id']
        if type(uid) is not int or not 0 < uid < 2**52 or user.get('is_bot'):
            raise ValueError
        return uid
    except (KeyError, ValueError, TypeError, OverflowError):
        raise web.HTTPUnauthorized(text='Открой приложение заново через меню бота.') from None


def state(service, role):
    goals = service.goals()
    focuses = [dict(goal_id=r['goal_id'], due_at=r['due_at']) for r in service.db.execute(
        "SELECT goal_id,due_at FROM notifications WHERE kind='focus' AND state='pending' AND day=? ORDER BY due_at", (service.day(),))
        if any(g['id'] == r['goal_id'] and g['status'] == 'pending' for g in goals)]
    return dict(date=service.day(), role=role, profile=service.profile(), goals=goals,
                options=service.options(), habits=service.habits(), course=service.course(),
                rewards=service.shop(), history=service.history(), week=service.week(), focuses=focuses)


def integer(value):
    if type(value) is not int or not 0 < value < 2**52:
        raise UserError('Нужен положительный числовой ID.')
    return value


def apply_action(service, hub, user_id, data, username=''):
    fields = {
        'done': {'id'}, 'skip': {'id'}, 'focus': {'id'}, 'snooze': {'id'},
        'setting': {'key', 'value'}, 'pause': {'days'}, 'resume': set(),
        'habit_add': {'title', 'details', 'minutes', 'time', 'weekdays'}, 'habit_remove': {'id'},
        'reward_add': {'title', 'cost'}, 'reward_remove': {'id'},
        'quote': {'id'}, 'redeem': {'purchase'}, 'course_start': {'day'},
        'invite': set(), 'revoke': {'id'},
    }
    action = data.get('action')
    if not isinstance(action, str) or action not in fields or set(data) != fields[action] | {'action'}:
        raise UserError('Неизвестное действие или лишние поля.')
    result = {}
    if action in ('done', 'skip', 'focus', 'snooze'):
        goal_id = integer(data['id'])
        if action == 'done':
            result = service.complete(goal_id)
        elif action == 'skip':
            service.skip(goal_id)
        else:
            end = getattr(service, action)(goal_id)
            result = {'message': f'Напомню в {end:%H:%M}.', 'due_at': end.isoformat()}
    elif action == 'setting':
        service.set_option(data['key'], data['value'])
    elif action == 'pause':
        service.pause(data['days'])
    elif action == 'resume':
        service.resume()
    elif action == 'habit_add':
        result['id'] = service.add_habit(data['title'], data['details'], data['minutes'], data['time'], data['weekdays'])
    elif action == 'habit_remove':
        service.remove_habit(integer(data['id']))
    elif action == 'reward_add':
        if not isinstance(data['title'], str):
            raise UserError('Нужно название награды.')
        result['id'] = service.add_reward(data['title'], data['cost'])
    elif action == 'reward_remove':
        service.remove_reward(integer(data['id']))
    elif action == 'quote':
        result['quote'] = service.quote(integer(data['id']))
    elif action == 'redeem':
        if not isinstance(data['purchase'], str) or not re.fullmatch(r'[a-f0-9]{32}', data['purchase']):
            raise UserError('Подтверждение покупки не найдено.')
        result = service.redeem(data['purchase'])
    elif action == 'course_start':
        service.start_course(integer(data['day']))
    elif action == 'invite':
        code = hub.invite(user_id)
        result['invite'] = f'https://t.me/{username}?start=join_{code}' if username else '/join ' + code
    elif action == 'revoke':
        hub.revoke(user_id, integer(data['id']))
    return result


def create_web_app(hub, token, username=''):
    buckets = defaultdict(deque)

    @web.middleware
    async def secure(request, handler):
        try:
            uid = telegram_user(request.headers.get('X-Telegram-Init-Data', ''), token)
            try:
                service = hub.member(uid)
            except UserError as exc:
                raise web.HTTPForbidden(text=str(exc)) from None
            queue = buckets[uid]
            current = time.monotonic()
            while queue and queue[0] < current - 60:
                queue.popleft()
            if len(queue) >= 120:
                raise web.HTTPTooManyRequests(text='Слишком много действий. Подожди минуту.')
            queue.append(current)
            request[SERVICE], request[UID] = service, uid
            response = await handler(request)
        except web.HTTPException as exc:
            response = web.json_response({'error': exc.text}, status=exc.status)
        except (UserError, ValueError, TypeError, KeyError, AttributeError):
            response = web.json_response({'error': 'Проверь данные действия и попробуй ещё раз.'}, status=400)
        response.headers.update({'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})
        return response

    app = web.Application(middlewares=[secure], client_max_size=32768)

    async def get_state(request):
        return web.json_response(state(request[SERVICE], 'owner' if request[UID] == hub.owner.owner_id else 'member'))

    async def action(request):
        if request.content_type != 'application/json':
            raise web.HTTPBadRequest(text='Нужен JSON.')
        data = await request.json()
        if not isinstance(data, dict):
            raise web.HTTPBadRequest(text='Нужен объект JSON.')
        try:
            result = apply_action(request[SERVICE], hub, request[UID], data, username)
        except UserError as exc:
            raise web.HTTPBadRequest(text=str(exc)) from None
        role = 'owner' if request[UID] == hub.owner.owner_id else 'member'
        return web.json_response(dict(result=result, state=state(request[SERVICE], role)))

    async def lesson(request):
        value = request[SERVICE].lesson(int(request.match_info['day']))
        return web.json_response({key: value[key] for key in ('day', 'title', 'markdown', 'criteria', 'objective')})

    app.router.add_get('/api/state', get_state)
    app.router.add_post('/api/action', action)
    app.router.add_get('/api/lesson/{day:\\d+}', lesson)
    return app
