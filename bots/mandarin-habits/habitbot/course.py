import json
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def lessons():
    data = json.loads(Path(__file__).with_name('lessons.json').read_text('utf-8'))
    if [lesson['day'] for lesson in data] != list(range(1, 31)):
        raise RuntimeError('Курс Patchboard должен содержать ровно 30 занятий по порядку.')
    return data


def lesson_goal(lesson, day):
    steps = '\n'.join(lesson['steps'])
    criteria = '\n'.join('• ' + value for value in lesson['criteria'])
    details = (f"Patchboard · Java · занятие {lesson['day']}/30\n\n"
               f"Цель: {lesson['objective']}\n\nПрактика\n{steps}\n\n"
               f"Готово, когда:\n{criteria}\n\nЗаметка: {lesson['filename']}\n"
               f"План: {lesson['minutes']}–{lesson['minutes_max']} мин. Полная заметка — по кнопке ниже.")
    return dict(day=day, kind='code', title=lesson['title'], details=details,
                minutes=lesson['minutes'], xp=50, coins=30, lesson=lesson['day'])
