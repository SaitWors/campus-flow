import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from telegram import Update
from habitbot.app import build_application
from habitbot.config import Config
from habitbot.members import Hub
from tests import test_app as helpers
from tests.test_app import FakeRequest, TOKEN, OWNER


class MultiTests(unittest.IsolatedAsyncioTestCase):
    command = helpers.AppTests.command
    callback = helpers.AppTests.callback
    messages = helpers.AppTests.messages
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = Config(TOKEN, OWNER, Path(self.tmp.name))
        self.hub = Hub(self.cfg.data_dir, OWNER, clock=lambda: datetime(2026, 10, 19, 7, tzinfo=timezone.utc))
        self.service = self.hub.owner
        self.service.register(OWNER)
        self.guest = 987654
        self.hub.join(self.guest, self.hub.invite(OWNER))
        self.req = FakeRequest()
        self.app = build_application(self.cfg, hub=self.hub, request=self.req)
        self.errors = []
        async def capture(update, context):
            self.errors.append(context.error)
        self.app.add_error_handler(capture)
        await self.app.initialize()
        self.counter = 10

    async def asyncTearDown(self):
        await self.app.shutdown()
        self.hub.close()
        self.tmp.cleanup()
        self.assertEqual([], self.errors)

    # These test only the new multi-profile dispatcher; inherited single-owner
    # tests run in test_app against the unchanged compatibility injection.
    async def test_guest_settings_and_rewards_are_personal(self):
        await self.command('/tz Europe/Riga', user=self.guest)
        await self.command('/reward Кофе | 10', user=self.guest)
        self.assertEqual(self.service.options()['timezone'], 'Europe/Moscow')
        self.assertEqual(self.hub.member(self.guest).options()['timezone'], 'Europe/Riga')
        self.assertFalse(any(r['title'] == 'Кофе' for r in self.service.shop()))

    async def test_guest_cannot_invite_and_unknown_cannot_read(self):
        await self.command('/invite', user=self.guest)
        self.assertIn('владелец', self.messages()[-1])
        await self.command('/today', user=111111)
        self.assertIn('приглашению', self.messages()[-1])

    async def test_join_link_and_duplicate_claim_do_not_change_owner(self):
        code = self.hub.invite(OWNER)
        await self.command('/start join_' + code, user=555555)
        self.assertTrue(self.hub.member(555555).registered())
        self.assertEqual(self.hub.owner.owner_id, OWNER)
        await self.command('/claim wrong', user=self.guest)
        self.assertEqual(self.hub.owner.owner_id, OWNER)

    async def test_guest_can_configure_habits_from_chat(self):
        await self.command('/habit Читать | 15 | 18:00', user=self.guest)
        self.assertEqual(self.hub.member(self.guest).habits()[0]['title'], 'Читать')
        self.assertEqual(self.service.habits(), [])
