import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from habitbot.service import Service, UserError
from habitbot.activation import activation_code


class CourseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'state.sqlite3'
        self.now = datetime(2026, 10, 16, 6, tzinfo=timezone.utc)
        self.s = Service(self.path, 123456, clock=lambda: self.now)
        self.s.register(123456)

    def tearDown(self):
        self.s.close()
        self.tmp.cleanup()

    def test_exact_uploaded_curriculum_has_30_original_lessons(self):
        self.assertEqual(30, self.s.course()['total'])
        first=self.s.lesson(1)
        self.assertIn('Первый запуск',first['title'])
        self.assertEqual(60,first['minutes'])
        self.assertIn('Maven',self.s.lesson(5)['title'])
        self.assertIn('транзакция',self.s.lesson(19)['title'])
        self.assertIn('следующий шаг',self.s.lesson(30)['title'])
        self.assertIn('public class Main',first['markdown'])

    def test_pending_lesson_is_carried_to_next_day(self):
        goal=self.s.goals()[1]
        self.assertEqual(1,goal['lesson'])
        self.assertNotIn('учёта расходов',goal['details'])
        self.now+=timedelta(days=3)
        self.assertEqual(1,self.s.goals()[1]['lesson'])

    def test_completion_advances_course_tomorrow(self):
        goal=self.s.goals()[1]
        self.s.complete(goal['id'])
        self.assertEqual([1],self.s.course()['completed'])
        self.assertEqual(2,self.s.course()['current'])
        self.assertEqual('done',self.s.goals()[1]['status'])
        self.now+=timedelta(days=1)
        self.assertEqual(2,self.s.goals()[1]['lesson'])
        self.assertIn('Типы',self.s.goals()[1]['title'])

    def test_skip_does_not_skip_lesson_in_curriculum(self):
        self.s.skip(self.s.goals()[1]['id'])
        self.now+=timedelta(days=1)
        self.assertEqual(1,self.s.goals()[1]['lesson'])
        self.assertEqual([],self.s.course()['completed'])

    def test_course_progress_survives_restart(self):
        self.s.complete(self.s.goals()[1]['id'])
        self.s.close()
        self.s=Service(self.path,0,clock=lambda:self.now)
        self.assertEqual(123456,self.s.owner_id)
        self.assertEqual(2,self.s.course()['current'])

    def test_start_from_day_2_without_fake_rewards_for_day_1(self):
        self.s.start_course(2)
        self.assertEqual(2,self.s.goals()[1]['lesson'])
        self.assertEqual([],self.s.course()['completed'])
        self.assertEqual(0,self.s.profile()['coins'])
        self.s.complete(self.s.goals()[1]['id'])
        with self.assertRaises(UserError):
            self.s.start_course(1)

    def test_completed_lesson_cannot_be_farmed_on_another_day(self):
        self.s.complete(self.s.goals()[1]['id'])
        balance=self.s.profile()['coins']
        self.now+=timedelta(days=1)
        goal=self.s.goals()[1]
        self.s.db.execute('UPDATE goals SET lesson=1 WHERE id=?',(goal['id'],))
        with self.assertRaises(UserError):
            self.s.complete(goal['id'])
        self.assertEqual(balance,self.s.profile()['coins'])

    def test_after_lesson_30_no_repeated_code_reward(self):
        self.s.start_course(30)
        self.s.complete(self.s.goals()[1]['id'])
        self.assertIsNone(self.s.course()['current'])
        self.now+=timedelta(days=1)
        self.assertEqual(['sport'],[g['kind'] for g in self.s.goals()])

    def test_unconfigured_owner_uses_secret_activation_and_persists(self):
        self.s.close()
        self.s=Service(Path(self.tmp.name)/'new.sqlite3',0,clock=lambda:self.now)
        token='123456:'+'X'*35
        with self.assertRaises(UserError):
            self.s.activate(999999,'wrong',token)
        with self.assertRaises(UserError):
            self.s.activate(999999,'неверный код 🍊',token)
        self.assertEqual(0,self.s.owner_id)
        self.s.activate(123456,activation_code(token),token)
        self.assertEqual(123456,self.s.owner_id)
        self.assertTrue(self.s.registered())
        with self.assertRaises(UserError):
            self.s.activate(999999,activation_code(token),token)

    def test_activation_code_does_not_contain_bot_token(self):
        token='123456:'+'X'*35
        code=activation_code(token)
        self.assertEqual(32,len(code))
        self.assertNotIn(token,code)
        self.assertNotEqual(code,activation_code('999999:'+'Y'*35))

    def test_v1_database_migrates_without_losing_owner_or_balance(self):
        self.s.complete(self.s.goals()[0]['id'])
        coins=self.s.profile()['coins']
        self.s.db.execute('DROP TABLE course_completions')
        self.s.db.execute('ALTER TABLE goals DROP COLUMN lesson')
        self.s.db.execute('PRAGMA user_version=1')
        self.s.close()
        self.s=Service(self.path,0,clock=lambda:self.now)
        self.assertEqual(123456,self.s.owner_id)
        self.assertEqual(coins,self.s.profile()['coins'])
        self.assertEqual(2,self.s.db.execute('PRAGMA user_version').fetchone()[0])
        self.assertEqual(1,self.s.goals()[1]['lesson'])
