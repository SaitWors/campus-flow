import hashlib
import hmac
import json
import tempfile
import time
import unittest
from pathlib import Path
from urllib.parse import urlencode

from aiohttp.test_utils import TestClient, TestServer

from habitbot.members import Hub

TOKEN = '123456:' + 'A' * 35


def signed(user_id, token=TOKEN, age=0, extra=None):
    values = dict(auth_date=str(int(time.time()) - age), user=json.dumps({'id': user_id, 'first_name': 'Тест'}))
    values.update(extra or {})
    secret = hmac.new(b'WebAppData', token.encode(), hashlib.sha256).digest()
    values['hash'] = hmac.new(secret, '\n'.join(f'{k}={v}' for k, v in sorted(values.items())).encode(), hashlib.sha256).hexdigest()
    return urlencode(values)


class MiniAppTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.hub = Hub(Path(self.tmp.name), 123)
        self.hub.owner.register(123)
        self.hub.join(456, self.hub.invite(123))
        from habitbot.miniapp import create_web_app
        self.client = TestClient(TestServer(create_web_app(self.hub, TOKEN)))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.hub.close()
        self.tmp.cleanup()

    async def action(self, user, data):
        return await self.client.post('/api/action', headers={'X-Telegram-Init-Data': signed(user)}, json=data)

    async def test_signature_membership_expiry_and_duplicate_fields(self):
        for value in ['', signed(123, token='wrong'), signed(789), signed(123, age=3601),
                      signed(123, age=-100), signed(123) + '&auth_date=1', signed(True)]:
            r = await self.client.get('/api/state', headers={'X-Telegram-Init-Data': value})
            self.assertIn(r.status, (401, 403))
        r = await self.client.get('/api/state', headers={'X-Telegram-Init-Data': signed(123)})
        self.assertEqual(r.status, 200)
        self.assertEqual((await r.json())['role'], 'owner')
        self.assertEqual(r.headers['Cache-Control'], 'no-store')

    async def test_guest_goal_completion_and_settings_do_not_modify_owner(self):
        goal = self.hub.member(456).goals()[0]
        for _ in range(2):
            r = await self.action(456, dict(action='done', id=goal['id']))
            self.assertEqual(r.status, 200)
        self.assertEqual(self.hub.owner.profile()['coins'], 0)
        self.assertGreater(self.hub.member(456).profile()['coins'], 0)
        before = self.hub.member(456).profile()['coins']
        r = await self.action(456, dict(action='done', id=goal['id']))
        self.assertEqual(r.status, 200)
        self.assertEqual(self.hub.member(456).profile()['coins'], before)
        r = await self.action(456, dict(action='setting', key='display_name', value='Мандаринка'))
        self.assertEqual(r.status, 200)
        self.assertEqual(self.hub.owner.options()['display_name'], '')

    async def test_body_cannot_select_profile_and_guest_cannot_invite(self):
        r = await self.action(456, dict(action='done', id=1, user_id=123))
        self.assertEqual(r.status, 400)
        r = await self.action(456, dict(action='invite'))
        self.assertEqual(r.status, 400)
        r = await self.action(456, dict(action='done', id=999999))
        self.assertEqual(r.status, 400)

    async def test_habit_creation_and_disabled_goal_reminders(self):
        r = await self.action(456, dict(action='habit_add', title='Читать', details='10 страниц', minutes=15, time='18:00', weekdays=[0,1,2,3,4,5,6]))
        self.assertEqual(r.status, 200)
        state = (await r.json())['state']
        self.assertTrue(any(h['title'] == 'Читать' for h in state['habits']))
        r = await self.action(456, dict(action='done', id=True))
        self.assertEqual(r.status, 400)

    async def test_revoked_member_loses_web_access(self):
        self.hub.revoke(123, 456)
        r = await self.client.get('/api/state', headers={'X-Telegram-Init-Data': signed(456)})
        self.assertEqual(r.status, 403)
