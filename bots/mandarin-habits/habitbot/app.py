import argparse
import asyncio
import contextlib
import io
import logging
import os
import re
import sqlite3
import sys
import time
from datetime import timedelta

from telegram import BotCommand, InlineKeyboardButton as Button, InlineKeyboardMarkup as Markup, InputFile
from telegram.error import Conflict, Forbidden, NetworkError, RetryAfter, TelegramError
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from .config import Config
from .service import Service, UserError
from .ui import HELP, goal_buttons, goal_text, menu, profile_text

LOG = logging.getLogger('habitbot')


class SecretFormatter(logging.Formatter):
    def __init__(self, fmt, token):
        super().__init__(fmt)
        self.token = token

    def format(self, record):
        value = super().format(record)
        if self.token:
            value = value.replace(self.token, '[REDACTED]')
        return re.sub(r'\b\d{5,15}:[A-Za-z0-9_-]{30,}', '[REDACTED]', value)


class Handlers:
    def __init__(self, config, service):
        self.config = config
        self.s = service

    async def authorized(self, update):
        if not update.effective_chat or update.effective_chat.type != 'private':
            return False
        if not update.effective_user or not self.s.owner_id or update.effective_user.id != self.s.owner_id:
            text = ('Бот ещё не активирован. Получи отдельный код активации по инструкции Campus Flow и отправь /claim КОД. Сам токен сюда не отправляй.'
                    if not self.s.owner_id else 'Это личный бот. Доступ есть только у владельца.')
            if update.callback_query:
                await update.callback_query.answer(text, show_alert=True)
            else:
                await update.effective_message.reply_text(text)
            return False
        return True

    async def show_today(self, message):
        goals = self.s.goals()
        extra = ('За обе цели — дополнительно 20 XP и 10 монет.' if len(goals)==2
                 else 'Курс Patchboard завершён. Можно открыть прошлые заметки через /lesson НОМЕР.')
        await message.reply_text(f'Цели на {self.s.day()}\n'+extra, reply_markup=menu())
        for g in goals:
            await message.reply_text(goal_text(g), reply_markup=goal_buttons(g))

    async def show_course(self, message):
        course=self.s.course()
        next_text=(f"Следующее занятие: {course['current']}/30 — {self.s.lesson()['title']}"
                   if course['current'] is not None else 'Все занятия твоего маршрута завершены.')
        await message.reply_text(f"Patchboard — Java за 30 занятий\n\nЗачтено в боте: {len(course['completed'])}/30\n"
                                 f"Маршрут начат с занятия {course['start']}.\n{next_text}\n\n"
                                 'Пропуски не переключают тему. Следующее занятие выдаётся после выполнения текущего, со следующего дня.\n'
                                 'Если день 1 уже сделал до запуска бота: /course start 2\n'
                                 'Открыть заметку: /lesson или /lesson 2', reply_markup=menu())

    async def show_lesson(self, message, day=None):
        lesson=self.s.lesson(day)
        await message.reply_text(lesson['title']+'\n\n'+lesson['objective']+'\n\n'+
                                 '\n'.join('• '+value for value in lesson['criteria']))
        await message.reply_document(InputFile(io.BytesIO(lesson['markdown'].encode('utf-8')),filename=lesson['filename']),
                                     caption='Исходная заметка занятия из твоего Obsidian-плана. Открой в Obsidian или VS Code.')

    async def show_shop(self, message):
        rewards = self.s.shop()
        lines = [f"#{r['id']} · {r['title']} — {r['cost']} монет" for r in rewards]
        buttons = [[Button(f"{r['title']} · {r['cost']}", callback_data=f"buy:{r['id']}")] for r in rewards]
        await message.reply_text(f"Магазин наград\nБаланс: {self.s.profile()['coins']} монет\n\n" +
                                 ('\n'.join(lines) or 'Пока пусто.') +
                                 '\n\nСвоя награда: /reward Название | 100\nРеальных платежей нет.',
                                 reply_markup=Markup(buttons) if buttons else menu())

    async def show_settings(self, message):
        o = self.s.options()
        pause = o['paused_until'] if self.s.day() < o['paused_until'] else 'нет'
        await message.reply_text(
            f"Настройки\n\nЧасовой пояс: {o['timezone']}\nДвижение: {o['sport_time']}\n"
            f"Код: {o['code_time']}\nИтог: {o['summary_time']}\n"
            f"Язык: {o['track']}\nСложность: {o['difficulty']}\nПауза до: {pause}\n\n"
            '/time sport 10:00\n/time code 19:00\n/time summary 21:30\n'
            '/tz Europe/Moscow\n/course\n/difficulty light\n/pause 3\n/resume\n\n'
            'Тихие часы: 23:00–08:00. Сложность движения меняется со следующего дня; урок — по плану Patchboard.',
            reply_markup=menu())

    async def command(self, update, context):
        if not update.effective_chat or update.effective_chat.type != 'private':
            return
        message = update.effective_message
        command = message.text.split()[0].split('@')[0].lstrip('/').lower()
        if command == 'id':
            await message.reply_text(f'Твой Telegram ID: {update.effective_user.id}\nЭто число укажи в OWNER_ID.')
            return
        if command == 'claim':
            if self.s.owner_id:
                if await self.authorized(update):
                    await message.reply_text('Бот уже активирован для тебя. Цели: /today')
                return
            try:
                if len(context.args or []) != 1:
                    raise UserError('Отправь /claim КОД. Код получи локальной командой из инструкции; сам токен в чат не отправляй.')
                self.s.activate(update.effective_user.id,context.args[0],self.config.token)
                await message.reply_text('Бот активирован для тебя. Прогресс и владелец сохраняются. Твой курс — Patchboard, 30 занятий Java.')
                await self.show_today(message)
            except UserError as exc:
                await message.reply_text(str(exc))
            return
        if not await self.authorized(update):
            return
        if command == 'start':
            self.s.register(update.effective_user.id)
            await message.reply_text('Mandarin Habit Bot\nЦели на день: движение и твой урок Java по Patchboard.\n'
                                     'Выполняй, получай XP и монеты, выбирай свои награды.\n'
                                     'Все команды: /help. Выполнение подтверждаешь сам.')
            await self.show_today(message)
            return
        if not self.s.registered():
            await message.reply_text('Сначала нажми /start.')
            return
        args = context.args or []
        try:
            if command in ('today', 'goals'):
                await self.show_today(message)
            elif command == 'profile':
                await message.reply_text(profile_text(self.s.profile()), reply_markup=menu())
            elif command == 'shop':
                await self.show_shop(message)
            elif command == 'settings':
                await self.show_settings(message)
            elif command == 'course':
                if args:
                    if len(args)!=2 or args[0]!='start':
                        raise UserError('Прогресс: /course. Начать с уже изученного дня: /course start 2')
                    self.s.start_course(int(args[1]))
                await self.show_course(message)
            elif command == 'lesson':
                if len(args)>1:
                    raise UserError('Пример: /lesson 2')
                await self.show_lesson(message,int(args[0]) if args else None)
            elif command == 'help':
                await message.reply_text(HELP, reply_markup=menu())
            elif command == 'time':
                if len(args) != 2 or args[0] not in ('sport', 'code', 'summary'):
                    raise UserError('Пример: /time code 19:00. Виды: sport, code, summary.')
                self.s.set_option(args[0] + '_time', args[1])
                await self.show_settings(message)
            elif command in ('tz', 'track', 'difficulty'):
                if len(args) != 1:
                    raise UserError('Нужно одно значение. Примеры: /tz Europe/Moscow, /track java, /difficulty light')
                self.s.set_option({'tz': 'timezone'}.get(command, command), args[0])
                await self.show_settings(message)
            elif command == 'pause':
                if len(args) > 1:
                    raise UserError('Пример: /pause 3')
                self.s.pause(int(args[0]) if args else 1)
                await message.reply_text(f"Напоминания на паузе до {self.s.options()['paused_until']}. Вернуть: /resume")
            elif command == 'resume':
                self.s.resume()
                await message.reply_text('Напоминания снова включены.', reply_markup=menu())
            elif command == 'reward':
                text = ' '.join(args)
                if '|' not in text:
                    raise UserError('Пример: /reward 30 минут игры | 80')
                title, price = text.rsplit('|', 1)
                rid = self.s.add_reward(title.strip(), int(price.strip()))
                await message.reply_text(f'Награда #{rid} добавлена. Открыть: /shop')
            elif command == 'rewarddel':
                if len(args) != 1:
                    raise UserError('Нужен ID из /shop: /rewarddel 1')
                self.s.remove_reward(int(args[0]))
                await message.reply_text('Награда убрана из магазина.')
            elif command == 'focus':
                g = next((g for g in self.s.goals() if g['kind'] == 'code'),None)
                if not g:
                    raise UserError('Курс завершён. Прошлые заметки: /lesson НОМЕР')
                end = self.s.focus(g['id'])
                await message.reply_text(f"Таймер на {g['minutes']} мин. Напомню в {end:%H:%M}.\n{g['title']}", reply_markup=goal_buttons(g))
            elif command == 'history':
                rows = self.s.history()
                await message.reply_text('Последние операции\n\n' + ('\n'.join(
                    f"{r['note']}: {r['coins']:+d} монет, {r['xp']:+d} XP" for r in rows) or 'Пока нет операций.'))
            elif command == 'backup':
                await message.reply_document(InputFile(io.BytesIO(self.s.backup()), filename=f'mandarin-{self.s.day()}.sqlite3'),
                                             caption='Копия прогресса, настроек и магазина. Токена в ней нет. Восстановление описано в README.')
            else:
                await message.reply_text('Команды и примеры: /help', reply_markup=menu())
        except UserError as exc:
            await message.reply_text(str(exc))
        except (ValueError, OverflowError):
            await message.reply_text('Не получилось разобрать число. Проверь пример команды в /help.')

    async def callback(self, update, context):
        if not await self.authorized(update):
            return
        query, message = update.callback_query, update.effective_message
        await query.answer()
        if not self.s.registered():
            await message.reply_text('Сначала нажми /start.')
            return
        try:
            action, value = (query.data or '').split(':', 1)
            if action == 'menu':
                if value == 'today':
                    await self.show_today(message)
                elif value == 'shop':
                    await self.show_shop(message)
                elif value == 'profile':
                    await message.reply_text(profile_text(self.s.profile()), reply_markup=menu())
                elif value == 'settings':
                    await self.show_settings(message)
                elif value == 'course':
                    await self.show_course(message)
                else:
                    raise UserError('Открой /today: эта кнопка неизвестна.')
            elif action == 'lesson':
                await self.show_lesson(message,int(value))
            elif action == 'done':
                r = self.s.complete(int(value))
                if r['changed']:
                    g, p = r['goal'], r['profile']
                    text = f"Цель выполнена! +{g['xp']} XP и +{g['coins']} монет.\nБаланс: {p['coins']} монет."
                    if r['achievements']:
                        text += '\nНовое достижение: ' + ', '.join(r['achievements'])
                else:
                    text = 'Эта цель уже выполнена. Повторно награда не начисляется.'
                await message.reply_text(text, reply_markup=menu())
            elif action in ('snooze', 'focus'):
                end = getattr(self.s, action)(int(value))
                await message.reply_text(f'Напомню в {end:%H:%M} по твоему часовому поясу.')
            elif action == 'skip':
                self.s._current_goal(int(value))
                await message.reply_text('Пропустить цель без награды? Вернуть её сегодня будет нельзя.',
                                         reply_markup=Markup([[Button('Да, пропустить', callback_data=f'skipok:{value}'),
                                                               Button('Назад', callback_data='menu:today')]]))
            elif action == 'skipok':
                self.s.skip(int(value))
                await message.reply_text('Цель пропущена без штрафа и награды. Новый план будет завтра.', reply_markup=menu())
            elif action == 'buy':
                q = self.s.quote(int(value))
                await message.reply_text(f"Подтвердить награду «{q['title']}» за {q['cost']} монет?\n"
                                         'После подтверждения монеты спишутся. Награду выполняешь сам.',
                                         reply_markup=Markup([[Button('Подтвердить', callback_data='confirm:' + q['id'])],
                                                               [Button('Назад в магазин', callback_data='menu:shop')]]))
            elif action == 'confirm':
                r = self.s.redeem(value)
                await message.reply_text((f"Награда получена: {r['title']}. Списано {r['cost']} монет."
                                          if r['changed'] else 'Эта награда уже получена; повторного списания нет.'), reply_markup=menu())
            else:
                raise UserError('Открой /today: эта кнопка неизвестна.')
        except UserError as exc:
            await message.reply_text(str(exc))
        except (ValueError, OverflowError):
            await message.reply_text('Эта кнопка устарела или повреждена. Открой /today.')

    async def text(self, update, context):
        if await self.authorized(update):
            await update.effective_message.reply_text('Открыть цели: /today. Список команд: /help', reply_markup=menu())


async def scheduler(application, service, data_dir):
    while True:
        try:
            (data_dir / 'heartbeat').write_text(str(time.time()), encoding='ascii')
            for job in service.due():
                job = service.ready(job['id'])
                if job is None:
                    continue
                try:
                    if job['kind'] == 'summary':
                        goals = service.goals()
                        done = sum(g['status'] == 'done' for g in goals)
                        text = f"Итог дня: {done} из {len(goals)} целей.\nБаланс: {service.profile()['coins']} монет.\n"
                        text += 'Все цели закрыты. Хорошая работа!' if done == len(goals) else 'Если осталось немного сил — выбери одну небольшую цель.'
                        markup = menu()
                    else:
                        g = service.goal(job['goal_id'])
                        prefix = 'Таймер завершён. Выполнил цель? Нажми «Готово».\n\n' if job['kind'] == 'focus' else 'Время для небольшой цели.\n\n'
                        text, markup = prefix + goal_text(g), goal_buttons(g)
                    await application.bot.send_message(service.owner_id, text, reply_markup=markup)
                    service.mark_sent(job['id'])
                except RetryAfter as exc:
                    delay = exc.retry_after.total_seconds() if isinstance(exc.retry_after, timedelta) else exc.retry_after
                    service.retry(job['id'], delay + 1)
                except Forbidden:
                    service.retry(job['id'], 86400)
                    LOG.warning('Владелец заблокировал бота или чат недоступен. Возобнови чат через /start.')
                except TelegramError as exc:
                    service.retry(job['id'], min(300, 20 * 2 ** min(job['attempts'], 4)))
                    LOG.warning('Уведомление будет повторено: %s', type(exc).__name__)
        except Exception:
            LOG.exception('Ошибка планировщика; следующая попытка через 20 секунд')
        await asyncio.sleep(20)


def build_application(config, *, service=None, request=None):
    owns_service = service is None
    config.data_dir.mkdir(parents=True, exist_ok=True)
    service = service or Service(config.data_dir / 'state.sqlite3', config.owner_id)
    handlers = Handlers(config, service)

    async def start(app):
        try:
            await app.bot.set_my_commands([BotCommand(c, desc) for c, desc in [
                ('today', 'Цели на сегодня'), ('profile', 'Опыт и награды'), ('shop', 'Магазин наград'),
                ('course','Мой план Patchboard'),('lesson','Исходная заметка урока'),
                ('focus', 'Таймер программирования'), ('settings', 'Расписание'), ('help', 'Все команды'), ('id', 'Мой Telegram ID')]])
        except TelegramError as exc:
            LOG.warning('Меню команд пока не обновлено: %s', type(exc).__name__)
        app.bot_data['scheduler'] = asyncio.create_task(scheduler(app, service, config.data_dir))
        LOG.info('Бот запущен. Владелец %s. Часовой пояс %s.', service.owner_id or 'не задан; требуется секретная активация /claim', service.options()['timezone'])
        if str(config.data_dir).startswith('/tmp'):
            LOG.warning('Временное хранилище: прогресс не переживёт перезапуск контейнера. Для постоянной работы нужен том /data.')

    async def stop(app):
        task = app.bot_data.get('scheduler')
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def shutdown(app):
        if owns_service:
            service.close()

    async def error(update, context):
        LOG.error('Ошибка Telegram: %s', type(context.error).__name__)
        if isinstance(context.error, Conflict):
            LOG.error('Один токен запущен в нескольких местах. Останови вторую копию бота.')
            if context.application.running:
                context.application.stop_running()
        elif update and update.effective_message and isinstance(context.error, NetworkError) is False:
            with contextlib.suppress(TelegramError):
                await update.effective_message.reply_text('Действие не завершено. Попробуй /today; детали есть в журнале сервера.')

    builder = Application.builder().token(config.token).post_init(start).post_stop(stop).post_shutdown(shutdown)
    if request:
        builder = builder.request(request).get_updates_request(request)
    application = builder.build()
    commands = ['start', 'claim', 'id', 'today', 'goals', 'profile', 'shop', 'settings', 'help', 'time', 'course','lesson',
                'tz', 'track', 'difficulty', 'pause', 'resume', 'reward', 'rewarddel', 'focus', 'history', 'backup']
    application.add_handler(CommandHandler(commands, handlers.command))
    application.add_handler(CallbackQueryHandler(handlers.callback))
    application.add_handler(MessageHandler(filters.TEXT, handlers.text))
    application.add_error_handler(error)
    return application


@contextlib.contextmanager
def instance_lock(path):
    handle = path.open('a+b')
    try:
        try:
            if os.name == 'nt':
                import msvcrt
                handle.seek(0)
                handle.write(b'0')
                handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError('С этой базой уже работает бот. Останови предыдущий процесс.') from None
        yield
    finally:
        handle.close()


def main():
    parser = argparse.ArgumentParser(description='Mandarin Habit Bot')
    parser.add_argument('--check', action='store_true', help='Проверить конфиг без соединения с Telegram')
    args = parser.parse_args()
    os.umask(0o077)
    try:
        config = Config.load()
        if args.check:
            print(f'Конфиг корректен. OWNER_ID={config.owner_id}; каталог данных: {config.data_dir}')
            return 0
        handler = logging.StreamHandler()
        handler.setFormatter(SecretFormatter('%(asctime)s %(levelname)s %(name)s: %(message)s', config.token))
        logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
        logging.getLogger('httpx').setLevel(logging.WARNING)
        logging.getLogger('httpcore').setLevel(logging.WARNING)
        config.data_dir.mkdir(parents=True, exist_ok=True)
        with instance_lock(config.data_dir / 'instance.lock'):
            app = build_application(config)
            app.run_polling(allowed_updates=['message', 'callback_query'], drop_pending_updates=False,
                            timeout=timedelta(seconds=30), bootstrap_retries=5)
    except (ValueError, RuntimeError, OSError) as exc:
        print(f'Не удалось запустить: {exc}', file=sys.stderr)
        return 1
    except sqlite3.Error:
        LOG.error('Нет доступа к базе в /data. Для запуска через Campus Flow обнови Runner и проверь постоянный том.')
        return 1
    except TelegramError as exc:
        LOG.error('Не удалось подключиться к Telegram: %s. Проверь токен и сеть.', type(exc).__name__)
        return 1
    return 0
