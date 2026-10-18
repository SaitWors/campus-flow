"""Route each update to a private Service; never mutate a shared current user."""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

from .service import UserError


class MultiHandlers:
    def __init__(self, config, hub):
        self.config, self.hub = config, hub

    async def dispatch(self, kind, update, context):
        if not update.effective_chat or update.effective_chat.type != 'private' or not update.effective_user:
            return
        from .app import Handlers
        message, uid = update.effective_message, update.effective_user.id
        command = message.text.split()[0].split('@')[0].lstrip('/').lower() if kind == 'command' else ''
        args = context.args or []
        try:
            if command == 'claim':
                return await Handlers(self.config, self.hub.owner).command(update, context)
            if command == 'id':
                await message.reply_text(f'Твой Telegram ID: {uid}')
                return
            if command == 'start' and not self.hub.owner.registered():
                return await Handlers(self.config, self.hub.owner).command(update, context)
            if command == 'join' or (command == 'start' and args and args[0].startswith('join_')):
                code = args[0].removeprefix('join_') if args else ''
                self.hub.join(uid, code)
                await message.reply_text('Добро пожаловать в Мандарин! У тебя отдельные цели, расписание и награды. Java выключена: выбери свои привычки в /app или добавь через /habit Читать | 15 | 18:00.')
            service = self.hub.member(uid)
            if command == 'invite':
                code = self.hub.invite(uid)
                username = context.bot.username
                await message.reply_text(f'Одноразовое приглашение на 24 часа:\nhttps://t.me/{username}?start=join_{code}\n\nУ участника будет отдельный профиль. Код владельца /claim передавать не нужно.')
                return
            if command == 'members':
                if uid != self.hub.owner.owner_id:
                    raise UserError('Список участников доступен только владельцу.')
                await message.reply_text('Участники (личный прогресс не показывается):\n' + '\n'.join(map(str, self.hub.users())) + '\nОтключить приглашённого: /revoke ID')
                return
            if command == 'revoke':
                self.hub.revoke(uid, int(args[0]) if len(args) == 1 else 0)
                await message.reply_text('Доступ отключён. Личный прогресс сохранён для повторного приглашения.')
                return
            if command == 'app':
                if not self.config.app_url:
                    raise UserError('Приложение ещё не подключено. Обнови web/API Campus Flow и Runner по инструкции Mini App. Все чатовые команды доступны.')
                await message.reply_text('Мандарин · привычки, прогресс и награды', reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton('OPEN APP', web_app=WebAppInfo(self.config.app_url))]]))
                return
            if command == 'habit':
                parts = ' '.join(args).split('|')
                if len(parts) != 3:
                    raise UserError('Пример: /habit Читать | 15 | 18:00')
                hid = service.add_habit(parts[0].strip(), '', int(parts[1].strip()), parts[2].strip(), list(range(7)))
                await message.reply_text(f'Привычка #{hid} добавлена. Дни недели можно выбрать при создании в /app.')
                return
            if command == 'habitdel':
                service.remove_habit(int(args[0]) if len(args) == 1 else 0)
                await message.reply_text('Привычка убрана, напоминания отменены.')
                return
            if command == 'enable':
                if len(args) != 2 or args[0] not in ('sport', 'code') or args[1] not in ('on', 'off'):
                    raise UserError('Пример: /enable code off или /enable sport on')
                service.set_option(args[0] + '_enabled', '1' if args[1] == 'on' else '0')
                await message.reply_text('Направление включено.' if args[1] == 'on' else 'Направление отключено, прогресс сохранён.')
                return
            if command == 'join':
                await Handlers(self.config, service).show_today(message)
                return
            await getattr(Handlers(self.config, service), kind)(update, context)
        except (UserError, ValueError, IndexError) as exc:
            if update.callback_query:
                await update.callback_query.answer()
            await message.reply_text(str(exc) if isinstance(exc, UserError) else 'Проверь пример команды в /help.')

    async def command(self, update, context):
        await self.dispatch('command', update, context)

    async def callback(self, update, context):
        await self.dispatch('callback', update, context)

    async def text(self, update, context):
        await self.dispatch('text', update, context)
