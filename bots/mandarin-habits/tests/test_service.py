import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from habitbot.service import Service, UserError

UTC = timezone.utc


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'state.sqlite3'
        self.now = datetime(2026, 10, 16, 6, 0, tzinfo=UTC)  # 09:00 Moscow
        self.s = Service(self.path, 123456, clock=lambda: self.now)
        self.s.register(123456)

    def tearDown(self):
        self.s.close()
        self.tmp.cleanup()

    def test_daily_plan_does_not_duplicate(self):
        one = self.s.goals()
        self.assertEqual(['sport', 'code'], [g['kind'] for g in one])
        self.assertEqual(one, self.s.goals())

    def test_completion_is_idempotent_even_after_restart(self):
        goal = self.s.goals()[0]
        result = self.s.complete(goal['id'])
        self.assertTrue(result['changed'])
        coins = self.s.profile()['coins']
        self.assertFalse(self.s.complete(goal['id'])['changed'])
        self.s.close()
        self.s = Service(self.path, 123456, clock=lambda: self.now)
        self.assertFalse(self.s.complete(goal['id'])['changed'])
        self.assertEqual(coins, self.s.profile()['coins'])

    def test_full_day_bonus_is_only_given_once(self):
        a, b = self.s.goals()
        self.s.complete(a['id'])
        before = self.s.profile()['coins']
        self.s.complete(b['id'])
        self.assertEqual(before + b['coins'] + 10, self.s.profile()['coins'])
        self.s.complete(b['id'])
        self.assertEqual(before + b['coins'] + 10, self.s.profile()['coins'])

    def test_old_goal_cannot_be_completed(self):
        goal = self.s.goals()[0]
        self.now += timedelta(days=1)
        with self.assertRaises(UserError):
            self.s.complete(goal['id'])

    def test_skipped_goal_cannot_give_reward(self):
        g = self.s.goals()[0]
        self.s.skip(g['id'])
        with self.assertRaises(UserError):
            self.s.complete(g['id'])
        self.assertEqual(0, self.s.profile()['coins'])

    def test_settings_do_not_regenerate_rewards(self):
        goals = self.s.goals()
        self.s.set_option('difficulty', 'hard')
        self.s.set_option('track', 'java')
        self.assertEqual(goals, self.s.goals())
        self.now += timedelta(days=1)
        self.assertGreater(self.s.goals()[0]['minutes'], goals[0]['minutes'])
        self.assertIn('Java', self.s.goals()[1]['details'])

    def test_sport_rotates_but_unfinished_java_lesson_stays(self):
        title = self.s.goals()[0]['title']
        code = self.s.goals()[1]['title']
        self.now += timedelta(days=1)
        self.assertNotEqual(title, self.s.goals()[0]['title'])
        self.assertEqual(code, self.s.goals()[1]['title'])

    def test_local_midnight_creates_new_day(self):
        old = self.s.goals()[0]
        self.now = datetime(2026, 10, 16, 21, 1, tzinfo=UTC)
        self.assertEqual('2026-10-17', self.s.goals()[0]['day'])
        self.assertNotEqual(old['id'], self.s.goals()[0]['id'])

    def test_streak_and_gap(self):
        for _ in range(3):
            self.s.complete(self.s.goals()[0]['id'])
            self.now += timedelta(days=1)
        self.assertEqual(3, self.s.profile()['streak'])
        self.now += timedelta(days=1)
        self.assertEqual(0, self.s.profile()['streak'])

    def test_owner_cannot_change_under_existing_database(self):
        with self.assertRaises(UserError):
            Service(self.path, 987654, clock=lambda: self.now)

    def test_register_rejects_other_user(self):
        with self.assertRaises(UserError):
            self.s.register(987654)

    def test_invalid_options(self):
        for key, value in [('timezone', 'Fake/Zone'), ('sport_time', '03:00'),
                           ('sport_time', '25:00'), ('difficulty', 'extreme'),
                           ('track', 'go'), ('sql', 'oops')]:
            with self.subTest(key=key, value=value), self.assertRaises(UserError):
                self.s.set_option(key, value)

    def test_reward_cost_must_be_positive_integer(self):
        for cost in (0, -2, 1.5, True, 1000001):
            with self.subTest(cost=cost), self.assertRaises(UserError):
                self.s.add_reward('Награда', cost)

    def test_purchase_is_confirmed_once(self):
        self.s.complete(self.s.goals()[0]['id'])
        reward = self.s.add_reward('Чай', 5)
        quote = self.s.quote(reward)
        before = self.s.profile()['coins']
        self.assertEqual(before, self.s.profile()['coins'])
        self.assertTrue(self.s.redeem(quote['id'])['changed'])
        self.assertFalse(self.s.redeem(quote['id'])['changed'])
        self.assertEqual(before - 5, self.s.profile()['coins'])

    def test_insufficient_balance_preserves_state(self):
        reward = self.s.add_reward('Большая награда', 999)
        quote = self.s.quote(reward)
        with self.assertRaises(UserError):
            self.s.redeem(quote['id'])
        self.assertEqual(0, self.s.profile()['coins'])

    def test_removed_reward_and_expired_quote_cannot_be_bought(self):
        reward = self.s.add_reward('Чай', 1)
        q = self.s.quote(reward)
        self.s.remove_reward(reward)
        with self.assertRaises(UserError):
            self.s.redeem(q['id'])
        q = self.s.quote(self.s.shop()[0]['id'])
        self.now += timedelta(days=2)
        with self.assertRaises(UserError):
            self.s.redeem(q['id'])

    def test_backup_is_a_valid_database_with_progress(self):
        self.s.complete(self.s.goals()[0]['id'])
        target = Path(self.tmp.name) / 'copy.sqlite3'
        target.write_bytes(self.s.backup())
        with sqlite3.connect(target) as db:
            self.assertEqual('ok', db.execute('PRAGMA integrity_check').fetchone()[0])
        copy = Service(target, 123456, clock=lambda: self.now)
        try:
            self.assertEqual(self.s.profile()['coins'], copy.profile()['coins'])
        finally:
            copy.close()

    def test_reminder_is_due_once_and_survives_restart(self):
        self.assertEqual([], self.s.due())
        self.now += timedelta(hours=1)
        jobs = self.s.due()
        self.assertEqual(1, len(jobs))
        self.assertEqual('sport', self.s.goal(jobs[0]['goal_id'])['kind'])
        self.s.mark_sent(jobs[0]['id'])
        self.s.close()
        self.s = Service(self.path, 123456, clock=lambda: self.now)
        self.assertEqual([], self.s.due())

    def test_completed_goal_has_no_reminder(self):
        self.s.complete(self.s.goals()[0]['id'])
        self.now += timedelta(hours=1)
        self.assertEqual([], self.s.due())

    def test_downtime_does_not_cause_old_reminder_spam(self):
        self.s.due()
        self.now += timedelta(hours=5)
        self.assertEqual([], self.s.due())

    def test_retry_only_after_delay(self):
        self.now += timedelta(hours=1)
        job = self.s.due()[0]
        self.s.retry(job['id'], 60)
        self.assertEqual([], self.s.due())
        self.now += timedelta(seconds=61)
        self.assertEqual(job['id'], self.s.due()[0]['id'])

    def test_snooze_persists_and_only_has_one_job(self):
        goal = self.s.goals()[0]
        self.s.snooze(goal['id'])
        self.s.snooze(goal['id'])
        self.now += timedelta(minutes=31)
        self.assertEqual(1, len(self.s.due()))

    def test_snooze_respects_quiet_hours(self):
        goal = self.s.goals()[0]
        self.now = datetime(2026, 10, 16, 19, 45, tzinfo=UTC)  # 22:45
        with self.assertRaises(UserError):
            self.s.snooze(goal['id'])

    def test_focus_finish_survives_restart(self):
        goal = self.s.goals()[1]
        self.s.focus(goal['id'])
        self.s.close()
        self.s = Service(self.path, 123456, clock=lambda: self.now)
        self.now += timedelta(minutes=goal['minutes'] + 1)
        jobs = self.s.due()
        self.assertEqual('focus', jobs[0]['kind'])

    def test_pause_stops_and_resume_enables_delivery(self):
        self.s.pause(3)
        self.now += timedelta(hours=1)
        self.assertEqual([], self.s.due())
        self.s.resume()
        self.assertEqual(1, len(self.s.due()))

    def test_evening_summary_is_once(self):
        self.now = datetime(2026, 10, 16, 18, 30, tzinfo=UTC)
        jobs = self.s.due()
        self.assertEqual(1, len(jobs))
        self.assertEqual('summary', jobs[0]['kind'])
        self.s.mark_sent(jobs[0]['id'])
        self.assertEqual([], self.s.due())

    def test_database_does_not_contain_token(self):
        self.assertNotIn(b'BOT_TOKEN', self.s.backup())


if __name__ == '__main__':
    unittest.main()
