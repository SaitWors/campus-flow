import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from habitbot.service import Service, UserError


class MembersTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.now = datetime(2026, 10, 19, 7, tzinfo=timezone.utc)

    def tearDown(self):
        self.tmp.cleanup()

    def hub(self):
        from habitbot.members import Hub
        return Hub(self.root, 123, clock=lambda: self.now)

    def test_preserves_owner_and_isolates_guest_settings_and_rewards(self):
        old = Service(self.root / 'state.sqlite3', 123, clock=lambda: self.now)
        old.register(123)
        old.complete(old.goals()[0]['id'])
        coins = old.profile()['coins']
        old.close()
        hub = self.hub()
        try:
            code = hub.invite(123)
            guest = hub.join(456, code)
            guest.set_option('timezone', 'Europe/Riga')
            self.assertEqual(hub.member(123).profile()['coins'], coins)
            self.assertEqual(guest.profile()['coins'], 0)
            self.assertEqual(guest.options()['code_enabled'], '0')
            self.assertEqual(hub.member(123).options()['timezone'], 'Europe/Moscow')
            self.assertNotEqual(guest.db, hub.owner.db)
        finally:
            hub.close()
        hub = self.hub()
        try:
            self.assertEqual(hub.member(456).options()['timezone'], 'Europe/Riga')
        finally:
            hub.close()

    def test_invites_owner_only_single_use_and_expire(self):
        hub = self.hub()
        try:
            hub.owner.register(123)
            with self.assertRaises(UserError):
                hub.invite(456)
            code = hub.invite(123)
            hub.join(456, code)
            with self.assertRaises(UserError):
                hub.join(789, code)
            code = hub.invite(123)
            self.now += timedelta(hours=25)
            with self.assertRaises(UserError):
                hub.join(789, code)
            with self.assertRaises(UserError):
                hub.member(789)
        finally:
            hub.close()

    def test_custom_habit_days_and_removal_cancel_pending_reminders(self):
        s = Service(self.root / 'state.sqlite3', 123, clock=lambda: self.now)
        try:
            s.register(123)
            hid = s.add_habit('Читать', '10 страниц', 15, '10:00', [0, 2, 4])
            goal = next(g for g in s.goals() if g['kind'] == f'habit:{hid}')
            self.assertEqual(goal['title'], 'Читать')
            self.assertTrue(any(j['goal_id'] == goal['id'] for j in s.due()))
            s.remove_habit(hid)
            self.assertFalse(any(j['goal_id'] == goal['id'] for j in s.due()))
            self.now += timedelta(days=1)
            self.assertFalse(any(g['kind'].startswith('habit:') for g in s.goals()))
        finally:
            s.close()

    def test_disabling_java_preserves_course_and_stops_notifications(self):
        s = Service(self.root / 'state.sqlite3', 123, clock=lambda: self.now)
        try:
            s.register(123)
            s.start_course(2)
            s.set_option('code_enabled', '0')
            self.assertFalse(any(g['kind'] == 'code' for g in s.goals()))
            self.assertEqual(s.course()['current'], 2)
            s.set_option('code_enabled', '1')
            self.assertTrue(any(g['kind'] == 'code' for g in s.goals()))
        finally:
            s.close()

    def test_bad_habit_input_rejected(self):
        s = Service(self.root / 'state.sqlite3', 123, clock=lambda: self.now)
        try:
            s.register(123)
            for args in [('', '', 15, '10:00', [0]), ('x', '', 0, '10:00', [0]),
                         ('x', '', 10, '03:00', [0]), ('x', '', 10, '10:00', [7]),
                         ('x', '', 10, '10:00', [])]:
                with self.assertRaises(UserError):
                    s.add_habit(*args)
        finally:
            s.close()

    def test_reenabling_direction_restores_unsent_reminder_at_new_time(self):
        s = Service(self.root / 'state.sqlite3', 123, clock=lambda: self.now)
        try:
            s.register(123)
            s.set_option('code_time', '10:00')
            s.due()
            s.set_option('code_enabled', '0')
            s.set_option('code_time', '11:00')
            s.set_option('code_enabled', '1')
            self.now += timedelta(hours=1)
            self.assertTrue(any(s.goal(j['goal_id'])['kind'] == 'code' for j in s.due() if j['goal_id']))
        finally:
            s.close()
