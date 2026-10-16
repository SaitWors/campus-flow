import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram.error import NetworkError, RetryAfter

from habitbot.app import instance_lock, scheduler
from habitbot.healthcheck import main as healthcheck
from habitbot.service import Service


class SchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)
        self.s = Service(self.path / 'db.sqlite3', 123456,
                         clock=lambda: datetime(2026, 10, 16, 7, tzinfo=timezone.utc))
        self.s.register(123456)
        self.bot = AsyncMock()
        self.app = SimpleNamespace(bot=self.bot)

    async def asyncTearDown(self):
        self.s.close()
        self.tmp.cleanup()

    async def one_tick(self):
        with patch('habitbot.app.asyncio.sleep', side_effect=asyncio.CancelledError):
            with self.assertRaises(asyncio.CancelledError):
                await scheduler(self.app, self.s, self.path)

    async def test_successful_send_is_persisted(self):
        await self.one_tick()
        self.bot.send_message.assert_awaited_once()
        self.assertEqual([], self.s.due())
        with patch.dict('os.environ', {'BOT_DATA_DIR': str(self.path)}):
            self.assertEqual(0, healthcheck())

    async def test_network_failure_does_not_mark_sent(self):
        job = self.s.due()[0]
        self.bot.send_message.side_effect = NetworkError('offline')
        await self.one_tick()
        row = self.s.db.execute('SELECT state,attempts FROM notifications WHERE id=?', (job['id'],)).fetchone()
        self.assertEqual(('pending', 1), tuple(row))

    async def test_retry_after_is_respected(self):
        self.bot.send_message.side_effect = RetryAfter(123)
        await self.one_tick()
        row = self.s.db.execute("SELECT due_at FROM notifications WHERE kind='reminder' AND goal_id=?",
                                (self.s.goals()[0]['id'],)).fetchone()
        self.assertGreaterEqual(row[0], self.s.clock().timestamp() + 123)

    async def test_unconfigured_owner_never_gets_reminder(self):
        self.s.close()
        self.s = Service(self.path / 'other.sqlite3', 0)
        await self.one_tick()
        self.bot.send_message.assert_not_awaited()

    async def test_batch_stops_when_quiet_hours_begin(self):
        for key in ('sport_time', 'code_time', 'summary_time'):
            self.s.set_option(key, '22:59')
        now = [datetime(2026, 10, 16, 19, 59, 50, tzinfo=timezone.utc)]
        self.s.clock = lambda: now[0]
        async def delayed_send(*args, **kwargs):
            now[0] += timedelta(seconds=15)
        self.bot.send_message.side_effect = delayed_send
        await self.one_tick()
        self.assertEqual(1, self.bot.send_message.await_count)

    async def test_pause_during_first_send_cancels_remaining_batch(self):
        self.s.set_option('code_time', '10:00')
        self.s.set_option('summary_time', '10:00')
        async def pause_during_send(*args, **kwargs):
            self.s.pause(1)
        self.bot.send_message.side_effect = pause_during_send
        await self.one_tick()
        self.assertEqual(1, self.bot.send_message.await_count)

    async def test_completion_during_first_send_cancels_next_goal(self):
        self.s.set_option('code_time', '10:00')
        code = self.s.goals()[1]
        # ORDER BY n.id puts code first; complete sport while code is in flight.
        sport = self.s.goals()[0]
        async def complete_during_send(*args, **kwargs):
            self.s.complete(sport['id'])
        self.bot.send_message.side_effect = complete_during_send
        await self.one_tick()
        self.assertEqual(1, self.bot.send_message.await_count)

    async def test_reschedule_during_first_send_cancels_old_time(self):
        self.s.set_option('code_time', '10:00')
        async def change_during_send(*args, **kwargs):
            self.s.set_option('sport_time', '12:00')
        self.bot.send_message.side_effect = change_during_send
        await self.one_tick()
        self.assertEqual(1, self.bot.send_message.await_count)
        self.assertEqual([], self.s.due(), 'In-flight delivery must retain its sent marker')


class RuntimeTests(unittest.TestCase):
    def test_same_database_cannot_be_started_twice(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'lock'
            with instance_lock(path):
                with self.assertRaises(RuntimeError):
                    with instance_lock(path):
                        self.fail('Second instance acquired the lock')
            with instance_lock(path):
                pass

    def test_healthcheck_rejects_missing_and_stale_heartbeat(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict('os.environ', {'BOT_DATA_DIR': directory}):
            self.assertEqual(1, healthcheck())
            (Path(directory) / 'heartbeat').write_text('1', encoding='ascii')
            self.assertEqual(1, healthcheck())
