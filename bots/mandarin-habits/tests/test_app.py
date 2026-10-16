import json
import logging
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from telegram import Update
from telegram.request import BaseRequest

from habitbot.app import SecretFormatter, build_application
from habitbot.config import Config
from habitbot.service import Service
from habitbot.activation import activation_code

TOKEN = '123456:' + 'A' * 35
OWNER = 123456


class FakeRequest(BaseRequest):
    """Exercise real PTB serialization without a Telegram account or network."""
    def __init__(self):
        self.calls = []

    @property
    def read_timeout(self):
        return 5

    async def initialize(self):
        pass

    async def shutdown(self):
        pass

    async def do_request(self, url, method, request_data=None, **kwargs):
        endpoint = url.rsplit('/', 1)[-1]
        params = request_data.parameters if request_data else {}
        self.calls.append((endpoint, params))
        if endpoint == 'getMe':
            result = dict(id=777777, is_bot=True, first_name='Mandarin', username='mandarin_test_bot')
        elif endpoint in ('sendMessage', 'editMessageText', 'sendDocument'):
            result = dict(message_id=len(self.calls), date=1792130400,
                          chat=dict(id=OWNER, type='private'), text=params.get('text', ''))
        else:
            result = True
        return 200, json.dumps(dict(ok=True, result=result)).encode()


class AppTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = Config(TOKEN, OWNER, Path(self.tmp.name))
        self.service = Service(self.cfg.data_dir / 'state.sqlite3', OWNER,
                               clock=lambda: datetime(2026, 10, 16, 6, tzinfo=timezone.utc))
        self.req = FakeRequest()
        self.app = build_application(self.cfg, service=self.service, request=self.req)
        self.errors = []
        async def capture(update, context):
            self.errors.append(context.error)
        self.app.add_error_handler(capture)
        await self.app.initialize()
        self.counter = 10

    async def asyncTearDown(self):
        await self.app.shutdown()
        self.service.close()
        self.tmp.cleanup()
        self.assertEqual([], self.errors)

    async def command(self, text, user=OWNER, chat_type='private'):
        self.counter += 1
        data = dict(update_id=self.counter, message=dict(
            message_id=self.counter, date=1792130400, text=text,
            chat=dict(id=user if chat_type == 'private' else -123, type=chat_type),
            **{'from': dict(id=user, first_name='Tester', is_bot=False)},
            entities=[dict(type='bot_command', offset=0, length=len(text.split()[0]))]))
        await self.app.process_update(Update.de_json(data, self.app.bot))

    async def callback(self, payload, user=OWNER):
        self.counter += 1
        data = dict(update_id=self.counter, callback_query=dict(
            id=str(self.counter), chat_instance='123', data=payload,
            **{'from': dict(id=user, first_name='Tester', is_bot=False)},
            message=dict(message_id=4, date=1792130400,
                         chat=dict(id=user, type='private'), text='Goal')))
        await self.app.process_update(Update.de_json(data, self.app.bot))

    def messages(self):
        return [params.get('text', '') for method, params in self.req.calls if method == 'sendMessage']

    async def test_start_shows_goals_and_buttons(self):
        await self.command('/start')
        self.assertTrue(self.service.registered())
        self.assertTrue(any('Прогулка' in text for text in self.messages()))
        calls = [p for method, p in self.req.calls if method == 'sendMessage']
        self.assertTrue(any('reply_markup' in p for p in calls))

    async def test_other_user_cannot_register_or_read_profile(self):
        await self.command('/profile', user=987654)
        await self.command('/start', user=987654)
        self.assertFalse(self.service.registered())
        self.assertFalse(any('Баланс' in text for text in self.messages()))

    async def test_group_does_not_show_even_own_id(self):
        await self.command('/id', chat_type='group')
        await self.command('/start', chat_type='group')
        self.assertEqual([], self.messages())

    async def test_id_is_available_for_setup(self):
        await self.command('/id', user=987654)
        self.assertIn('987654', self.messages()[-1])
        self.assertFalse(self.service.registered())

    async def test_callback_completion_double_click(self):
        await self.command('/start')
        goal = self.service.goals()[0]
        await self.callback(f"done:{goal['id']}")
        balance = self.service.profile()['coins']
        await self.callback(f"done:{goal['id']}")
        self.assertEqual(balance, self.service.profile()['coins'])
        self.assertTrue(any('уже' in text.lower() for text in self.messages()))

    async def test_foreign_callback_cannot_complete_goal(self):
        await self.command('/start')
        goal = self.service.goals()[0]
        await self.callback(f"done:{goal['id']}", user=987654)
        self.assertEqual(0, self.service.profile()['coins'])

    async def test_bad_callback_is_handled(self):
        await self.command('/start')
        await self.callback('done:not-an-integer')
        await self.callback('garbage')
        self.assertEqual(0, self.service.profile()['coins'])

    async def test_settings_and_invalid_command_args(self):
        await self.command('/start')
        await self.command('/time sport 11:30')
        await self.command('/track java')
        self.assertEqual('11:30', self.service.options()['sport_time'])
        self.assertEqual('java', self.service.options()['track'])
        for text in ('/time sport rubbish', '/pause abc', '/reward abc', '/rewarddel nope', '/tz Fake/Zone'):
            await self.command(text)
        self.assertEqual('Europe/Moscow', self.service.options()['timezone'])

    async def test_shop_requires_confirmation_then_charges_once(self):
        await self.command('/start')
        await self.callback(f"done:{self.service.goals()[0]['id']}")
        await self.command('/reward Чай | 5')
        reward = next(r for r in self.service.shop() if r['title'] == 'Чай')
        before = self.service.profile()['coins']
        await self.callback(f"buy:{reward['id']}")
        self.assertEqual(before, self.service.profile()['coins'])
        candidates = [p for m, p in self.req.calls if m == 'sendMessage' and 'Подтвердить' in p.get('text', '')]
        markup = candidates[-1]['reply_markup']
        payload = markup['inline_keyboard'][0][0]['callback_data']
        await self.callback(payload)
        await self.callback(payload)
        self.assertEqual(before - 5, self.service.profile()['coins'])

    async def test_backup_uses_send_document(self):
        await self.command('/start')
        await self.command('/backup')
        self.assertTrue(any(m == 'sendDocument' for m, p in self.req.calls))

    async def test_course_commands_use_uploaded_lessons(self):
        await self.command('/start')
        await self.command('/course start 2')
        await self.command('/course')
        self.assertEqual(2,self.service.course()['current'])
        await self.command('/lesson 2')
        self.assertTrue(any('Типы' in text for text in self.messages()))
        self.assertTrue(any(m=='sendDocument' for m,p in self.req.calls))
        await self.callback('lesson:3')
        self.assertTrue(any('Классы' in text for text in self.messages()))

    async def test_secure_claim_without_baked_owner(self):
        await self.app.shutdown()
        self.service.close()
        self.cfg=Config(TOKEN,0,Path(self.tmp.name))
        self.service=Service(self.cfg.data_dir/'bootstrap.sqlite3',0)
        self.app=build_application(self.cfg,service=self.service,request=self.req)
        await self.app.initialize()
        await self.command('/start',user=987654)
        self.assertEqual(0,self.service.owner_id)
        await self.command('/claim wrong',user=987654)
        self.assertEqual(0,self.service.owner_id)
        await self.command('/claim '+activation_code(TOKEN))
        self.assertEqual(OWNER,self.service.owner_id)
        await self.command('/profile')
        self.assertIn('Баланс',self.messages()[-1])
        await self.command('/claim '+activation_code(TOKEN),user=987654)
        self.assertEqual(OWNER,self.service.owner_id)


class ConfigTests(unittest.TestCase):
    def test_alias_and_no_token_in_repr(self):
        cfg = Config.load({'TELEGRAM_BOT_TOKEN': TOKEN, 'OWNER_ID': str(OWNER)}, '/no/such/file')
        self.assertEqual(TOKEN, cfg.token)
        self.assertNotIn(TOKEN, repr(cfg))

    def test_zero_owner_is_safe_bootstrap(self):
        self.assertEqual(0, Config.load({'BOT_TOKEN': TOKEN}, '/no/such/file').owner_id)

    def test_invalid_configuration_fails_without_echoing_secret(self):
        for env in ({'BOT_TOKEN': 'secret'}, {'BOT_TOKEN': TOKEN, 'OWNER_ID': '-1'}):
            with self.assertRaises(ValueError) as caught:
                Config.load(env, '/no/such/file')
            self.assertNotIn(TOKEN, str(caught.exception))

    def test_logging_redacts_token_in_message(self):
        record = logging.LogRecord('test', logging.ERROR, __file__, 1, 'URL: %s',
                                   ('https://api.telegram.org/bot' + TOKEN + '/getMe',), None)
        text = SecretFormatter('%(message)s', TOKEN).format(record)
        self.assertNotIn(TOKEN, text)
        self.assertIn('[REDACTED]', text)
