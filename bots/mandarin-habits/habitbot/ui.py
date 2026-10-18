from telegram import InlineKeyboardButton as Button, InlineKeyboardMarkup as Markup, WebAppInfo


def menu(app_url=''):
    rows = [[Button('Сегодня', callback_data='menu:today'),
                    Button('Прогресс', callback_data='menu:profile')],
                   [Button('Магазин наград', callback_data='menu:shop'),
                    Button('Настройки', callback_data='menu:settings')],
                   [Button('Курс Java',callback_data='menu:course')]]
    if app_url:
        rows.insert(0, [Button('OPEN APP · Мандарин', web_app=WebAppInfo(app_url))])
    return Markup(rows)


def goal_text(goal):
    label = {'sport': 'Движение', 'code': 'Java · Patchboard'}.get(goal['kind'], 'Личная привычка')
    status = {'pending': 'В планах', 'done': 'Выполнено', 'skipped': 'Пропущено'}[goal['status']]
    return (f"{label} · {goal['day']} · {goal['minutes']} мин\n\n"
            f"{goal['title']}\n{goal['details']}\n\n"
            f"Награда: +{goal['xp']} XP, +{goal['coins']} монет\nСтатус: {status}")


def goal_buttons(goal):
    lesson_row = ([Button('Полная заметка занятия',callback_data=f"lesson:{goal['lesson']}")]
                  if goal.get('lesson') else None)
    if goal['status'] != 'pending':
        return Markup([lesson_row]) if lesson_row else menu()
    gid = goal['id']
    rows=[[Button('Готово', callback_data=f'done:{gid}'),
                    Button('Начать таймер', callback_data=f'focus:{gid}')],
                   [Button('Напомнить через 30 мин', callback_data=f'snooze:{gid}')],
                   [Button('Пропустить', callback_data=f'skip:{gid}')]]
    if lesson_row:
        rows.append(lesson_row)
    return Markup(rows)


def profile_text(profile):
    p = profile
    achievements = '\n'.join('• ' + s for s in p['achievements']) or 'Пока нет. Первый шаг — первая цель.'
    return (f"Твой прогресс\n\nУровень {p['level']} · {p['xp']} XP\n"
            f"До следующего уровня: {p['next_level']} XP\nБаланс: {p['coins']} монет\n"
            f"Выполнено целей: {p['completed']}\nСерия: {p['streak']} дн.\n\n"
            f"Достижения\n{achievements}\n\n"
            'Серия растёт за дни подряд хотя бы с одной выполненной целью.')


HELP = '''Мандарин — твои привычки

/app — открыть приложение
/invite — одноразовое приглашение (владелец)
/join КОД — принять приглашение
/members — участники (владелец)
/revoke ID — отключить участника (владелец)
/habit Читать | 15 | 18:00 — своя ежедневная привычка
/habitdel ID — убрать привычку
/enable code off — отключить Java; on — включить
/enable sport off — отключить стандартное движение
Дни недели личных привычек выбираются в приложении.

/today — свои цели на сегодня
/profile — опыт, монеты, серия и достижения
/focus — таймер программирования
/course — прогресс 30 занятий Java по Patchboard
/course start 2 — начать с занятия 2, если первое уже сделал до запуска бота
/lesson — полная исходная заметка текущего занятия
/lesson 5 — посмотреть любую заметку без начисления наград
/shop — свои награды за монеты
/history — последние начисления и покупки
/settings — расписание и настройки

/time sport 10:00 — время движения
/time code 19:00 — время кода
/time summary 21:30 — вечерний итог
/tz Europe/Moscow — часовой пояс
/difficulty light, normal или hard — нагрузка движения
/pause 3 — остановить напоминания на 3 календарных дня
/resume — возобновить напоминания

/reward 30 минут игры | 80 — добавить награду
/rewarddel 1 — убрать награду по ID из /shop
/backup — копия базы с прогрессом
/id — узнать свой Telegram ID

Тишина: 23:00–08:00 по твоему часовому поясу.
Невыполненное занятие Java переносится. Сложность движения меняется со следующего дня.
Пауза останавливает уведомления; серию она не замораживает.
Кнопка «Пропустить» закрывает цель без награды до завтра.
Таймер не подтверждает выполнение: после занятия нажми «Готово».
Монеты виртуальные, награды выполняешь сам — платежей здесь нет.
'''
