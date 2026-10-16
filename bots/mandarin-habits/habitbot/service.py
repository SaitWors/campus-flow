import re
import uuid
import hmac
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .catalog import make_goals
from .store import Store
from .activation import activation_code
from .course import lessons, lesson_goal

DEFAULTS = dict(timezone='Europe/Moscow', sport_time='10:00', code_time='19:00',
                summary_time='21:30', track='java', difficulty='normal', paused_until='')
ACHIEVEMENTS = {'first': ('Первый шаг', 10), 'ten': ('Десять выполненных целей', 30),
                'streak3': ('Три дня подряд', 25), 'streak7': ('Неделя привычки', 75)}


class UserError(ValueError):
    pass


class Service:
    def __init__(self, path, owner_id, clock=None):
        self.store = Store(path)
        self.db = self.store.db
        self.owner_id = owner_id
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        existing = self._get('owner_id')
        if existing and owner_id and int(existing) != owner_id:
            self.close()
            raise UserError('В этой базе другой владелец. Используй его OWNER_ID или отдельный том.')
        if existing:
            self.owner_id = int(existing)
        if self._get('curriculum') != 'patchboard-v1':
            with self.store.transaction():
                self._put('curriculum', 'patchboard-v1')
                self._put('track', 'java')
                self._put('course_start', '1')

    def close(self):
        self.store.close()

    def _get(self, key, default=None):
        row = self.db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return row[0] if row else default

    def _put(self, key, value):
        self.db.execute('INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                        (key, str(value)))

    def options(self):
        return {k: self._get(k, v) for k, v in DEFAULTS.items()}

    def local_now(self):
        return self.clock().astimezone(ZoneInfo(self._get('timezone', DEFAULTS['timezone'])))

    def day(self):
        return self.local_now().date().isoformat()

    def registered(self):
        return bool(self.owner_id and self._get('owner_id'))

    def register(self, user_id):
        if not self.owner_id or user_id != self.owner_id:
            raise UserError('Бот доступен только настроенному владельцу.')
        with self.store.transaction():
            if not self._get('owner_id'):
                self._put('owner_id', user_id)
                self._put('start_day', self.day())
                for k, v in DEFAULTS.items():
                    self._put(k, v)
                self.db.executemany('INSERT INTO rewards(title,cost) VALUES (?,?)',
                                    [('30 минут игры', 80), ('Серия любимого сериала', 100),
                                     ('Небольшая покупка для себя', 300)])
        return self.goals()

    def activate(self, user_id, code, token):
        if type(user_id) is not int or user_id <= 0:
            raise UserError('Нужен личный Telegram-аккаунт.')
        if self.owner_id or self._get('owner_id'):
            raise UserError('Владелец уже назначен. Повторная активация закрыта.')
        if not hmac.compare_digest(str(code).strip().encode('utf-8'), activation_code(token).encode('ascii')):
            raise UserError('Неверный код активации. Получи его локальной командой из инструкции; сам токен сюда не отправляй.')
        with self.store.transaction():
            if self._get('owner_id'):
                raise UserError('Владелец уже назначен.')
            self._put('owner_id', user_id)
            self._put('start_day', self.day())
            for key, value in DEFAULTS.items():
                self._put(key, value)
            self.db.executemany('INSERT INTO rewards(title,cost) VALUES (?,?)',
                                [('30 минут игры', 80), ('Серия любимого сериала', 100), ('Небольшая покупка для себя', 300)])
        self.owner_id = user_id
        return self.goals()

    def course(self):
        completed = [row[0] for row in self.db.execute('SELECT lesson FROM course_completions ORDER BY lesson')]
        start = int(self._get('course_start', '1'))
        current = next((day for day in range(start, 31) if day not in completed), None)
        return dict(total=30, current=current, completed=completed, start=start)

    def lesson(self, day=None):
        day = self.course()['current'] if day is None else day
        if type(day) is not int or not 1 <= day <= 30:
            raise UserError('Выбери занятие 1–30: /lesson 2. Прогресс — /course.')
        return lessons()[day - 1]

    def start_course(self, day):
        lesson = self.lesson(day)
        if self.course()['completed']:
            raise UserError('Курс уже начат в боте. Завершённые занятия не сбрасываются; заметку можно открыть через /lesson НОМЕР.')
        with self.store.transaction():
            self._put('course_start', day)
            g = self.db.execute("SELECT id,status FROM goals WHERE day=? AND kind='code'", (self.day(),)).fetchone()
            if g and g['status'] == 'pending':
                fresh = lesson_goal(lesson, self.day())
                self.db.execute('UPDATE goals SET title=:title,details=:details,minutes=:minutes,xp=:xp,coins=:coins,lesson=:lesson WHERE id=:id',
                                {**fresh, 'id': g['id']})

    def goals(self):
        if not self.registered():
            return []
        day = self.day()
        with self.store.transaction():
            generated = [make_goals(day, self._get('start_day'), 'java', self._get('difficulty'))[0]]
            generated[0]['lesson'] = None
            if self.course()['current'] is not None:
                generated.append(lesson_goal(self.lesson(), day))
            for g in generated:
                self.db.execute('''INSERT OR IGNORE INTO goals(day,kind,title,details,minutes,xp,coins)
                                   VALUES (:day,:kind,:title,:details,:minutes,:xp,:coins)''', g)
                if g['kind'] == 'code':
                    self.db.execute("UPDATE goals SET title=:title,details=:details,minutes=:minutes,xp=:xp,coins=:coins,lesson=:lesson WHERE day=:day AND kind='code' AND lesson IS NULL AND status='pending'", g)
        return [dict(g) for g in self.db.execute(
            "SELECT * FROM goals WHERE day=? ORDER BY CASE kind WHEN 'sport' THEN 0 ELSE 1 END", (day,))]

    def goal(self, goal_id):
        row = self.db.execute('SELECT * FROM goals WHERE id=?', (goal_id,)).fetchone()
        if not row:
            raise UserError('Цель не найдена. Открой /today.')
        return dict(row)

    def _current_goal(self, goal_id):
        g = self.goal(goal_id)
        if g['day'] != self.day():
            raise UserError('Эта цель уже из прошлого дня. Открой /today.')
        return g

    def _award(self, key, kind, xp, coins, note):
        return bool(self.db.execute('INSERT OR IGNORE INTO ledger VALUES (?,?,?,?,?,?)',
                                   (key, kind, xp, coins, note, self.clock().timestamp())).rowcount)

    def _streak(self):
        days = {r[0] for r in self.db.execute("SELECT DISTINCT day FROM goals WHERE status='done'")}
        cursor = date.fromisoformat(self.day())
        if cursor.isoformat() not in days:
            cursor -= timedelta(days=1)
        count = 0
        while cursor.isoformat() in days:
            count += 1
            cursor -= timedelta(days=1)
        return count

    def complete(self, goal_id):
        g = self._current_goal(goal_id)
        if g['status'] == 'skipped':
            raise UserError('Цель пропущена. Новые задачи будут завтра.')
        achievements = []
        with self.store.transaction():
            changed = bool(self.db.execute("UPDATE goals SET status='done',done_at=? WHERE id=? AND status='pending'",
                                          (self.clock().timestamp(), goal_id)).rowcount)
            if changed:
                if g['kind'] == 'code' and g['lesson'] is not None:
                    if self.db.execute('SELECT 1 FROM course_completions WHERE lesson=?', (g['lesson'],)).fetchone():
                        raise UserError('Это занятие уже зачтено. Награда за один урок выдаётся один раз.')
                    self.db.execute('INSERT INTO course_completions VALUES (?,?,?)', (g['lesson'], goal_id, self.clock().timestamp()))
                    key = f"course:patchboard:{g['lesson']}"
                else:
                    key = f'goal:{goal_id}'
                self._award(key, 'goal', g['xp'], g['coins'], g['title'])
                self.db.execute("UPDATE notifications SET state='cancelled' WHERE goal_id=? AND state='pending'", (goal_id,))
                done = self.db.execute("SELECT COUNT(*) FROM goals WHERE day=? AND status='done'", (g['day'],)).fetchone()[0]
                if done == 2:
                    self._award('day:' + g['day'], 'day', 20, 10, 'Обе цели за день')
                total = self.db.execute("SELECT COUNT(*) FROM goals WHERE status='done'").fetchone()[0]
                checks = {'first': total >= 1, 'ten': total >= 10,
                          'streak3': self._streak() >= 3, 'streak7': self._streak() >= 7}
                for key, condition in checks.items():
                    title, coins = ACHIEVEMENTS[key]
                    if condition and self._award('achievement:' + key, 'achievement', 0, coins, title):
                        achievements.append(title)
        return dict(changed=changed, goal=g, achievements=achievements, profile=self.profile())

    def skip(self, goal_id):
        g = self._current_goal(goal_id)
        if g['status'] != 'pending':
            raise UserError('Цель уже выполнена или пропущена.')
        with self.store.transaction():
            self.db.execute("UPDATE goals SET status='skipped' WHERE id=? AND status='pending'", (goal_id,))
            self.db.execute("UPDATE notifications SET state='cancelled' WHERE goal_id=? AND state='pending'", (goal_id,))

    def profile(self):
        row = self.db.execute('SELECT COALESCE(SUM(xp),0),COALESCE(SUM(coins),0) FROM ledger').fetchone()
        return dict(xp=row[0], coins=row[1], level=1 + row[0] // 200, next_level=200 - row[0] % 200,
                    streak=self._streak(), completed=self.db.execute("SELECT COUNT(*) FROM goals WHERE status='done'").fetchone()[0],
                    achievements=[r[0] for r in self.db.execute("SELECT note FROM ledger WHERE kind='achievement' ORDER BY created_at")])

    def set_option(self, key, value):
        if key not in DEFAULTS or key == 'paused_until':
            raise UserError('Неизвестная настройка.')
        if key == 'timezone':
            try:
                ZoneInfo(value)
            except (ZoneInfoNotFoundError, ValueError):
                raise UserError('Нужен часовой пояс: Europe/Moscow, Europe/Riga или Asia/Yekaterinburg.') from None
        elif key.endswith('_time'):
            if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', value) or not 8 <= int(value[:2]) < 23:
                raise UserError('Нужно время HH:MM между 08:00 и 22:59. Ночью бот молчит.')
        elif key == 'track' and value != 'java':
            raise UserError('Этот бот привязан к твоему Java-курсу Patchboard. Открыть занятие: /lesson')
        elif key == 'difficulty' and value not in ('light', 'normal', 'hard'):
            raise UserError('Выбери light, normal или hard: /difficulty light')
        with self.store.transaction():
            self._put(key, value)
            if key.endswith('_time') or key == 'timezone':
                # Preserve identities, including a request currently in flight.
                # Deleting one would let _seed_jobs recreate an already sent job.
                pending = self.db.execute("SELECT * FROM notifications WHERE state='pending' AND kind IN ('reminder','summary')").fetchall()
                for job in pending:
                    option = 'summary_time' if job['kind'] == 'summary' else self.goal(job['goal_id'])['kind'] + '_time'
                    if key != 'timezone' and key != option:
                        continue
                    due_at = self._wall_time(job['day'], self._get(option))
                    self.db.execute('UPDATE notifications SET due_at=?,expires_at=? WHERE id=?',
                                    (due_at, min(due_at + 7200, self._wall_time(job['day'], '23:00')), job['id']))

    def pause(self, days):
        if type(days) is not int or not 1 <= days <= 30:
            raise UserError('Число дней от 1 до 30: /pause 3')
        self._put('paused_until', (self.local_now().date() + timedelta(days=days)).isoformat())

    def resume(self):
        self._put('paused_until', '')

    def shop(self):
        return [dict(r) for r in self.db.execute('SELECT * FROM rewards WHERE active=1 ORDER BY cost,id')]

    def add_reward(self, title, cost):
        if not 1 <= len(title.strip()) <= 60:
            raise UserError('Название награды должно содержать 1–60 символов.')
        if type(cost) is not int or not 1 <= cost <= 1000000:
            raise UserError('Цена — целое число от 1 до 1000000 монет.')
        if len(self.shop()) >= 20:
            raise UserError('В магазине уже 20 наград. Удали ненужную: /rewarddel ID')
        return self.db.execute('INSERT INTO rewards(title,cost) VALUES (?,?)', (title.strip(), cost)).lastrowid

    def remove_reward(self, reward_id):
        if not self.db.execute('UPDATE rewards SET active=0 WHERE id=? AND active=1', (reward_id,)).rowcount:
            raise UserError('Такой активной награды нет.')

    def quote(self, reward_id):
        row = self.db.execute('SELECT * FROM rewards WHERE id=? AND active=1', (reward_id,)).fetchone()
        if not row:
            raise UserError('Эта награда удалена. Открой /shop.')
        purchase = dict(id=uuid.uuid4().hex, reward_id=reward_id, title=row['title'], cost=row['cost'], created_at=self.clock().timestamp())
        self.db.execute('INSERT INTO purchases(id,reward_id,title,cost,created_at) VALUES (:id,:reward_id,:title,:cost,:created_at)', purchase)
        return purchase

    def redeem(self, purchase_id):
        with self.store.transaction():
            row = self.db.execute('SELECT p.*, r.active FROM purchases p JOIN rewards r ON r.id=p.reward_id WHERE p.id=?',
                                  (purchase_id,)).fetchone()
            if not row:
                raise UserError('Покупка не найдена. Открой /shop.')
            if row['state'] == 'redeemed':
                return dict(changed=False, title=row['title'], cost=row['cost'])
            if not row['active'] or self.clock().timestamp() - row['created_at'] > 86400:
                raise UserError('Подтверждение устарело или награда удалена. Открой /shop.')
            balance = self.db.execute('SELECT COALESCE(SUM(coins),0) FROM ledger').fetchone()[0]
            if balance < row['cost']:
                raise UserError(f"Не хватает {row['cost'] - balance} монет. Их можно заработать за цели.")
            self.db.execute("UPDATE purchases SET state='redeemed' WHERE id=?", (purchase_id,))
            self._award('purchase:' + purchase_id, 'purchase', 0, -row['cost'], row['title'])
        return dict(changed=True, title=row['title'], cost=row['cost'])

    def history(self):
        return [dict(r) for r in self.db.execute('SELECT * FROM ledger ORDER BY created_at DESC,rowid DESC LIMIT 12')]

    def _wall_time(self, day, clock):
        return datetime.combine(date.fromisoformat(day), time.fromisoformat(clock), self.local_now().tzinfo).timestamp()

    def _insert_job(self, key, day, goal_id, kind, due_at, expires_at):
        self.db.execute('''INSERT OR IGNORE INTO notifications(id,day,goal_id,kind,due_at,expires_at)
                           VALUES (?,?,?,?,?,?)''', (key, day, goal_id, kind, due_at, expires_at))

    def _seed_jobs(self):
        opts, day = self.options(), self.day()
        for g in self.goals():
            due_at = self._wall_time(day, opts[g['kind'] + '_time'])
            self._insert_job(f"reminder:{day}:{g['kind']}", day, g['id'], 'reminder',
                             due_at, min(due_at + 7200, self._wall_time(day, '23:00')))
        due_at = self._wall_time(day, opts['summary_time'])
        self._insert_job('summary:' + day, day, None, 'summary', due_at,
                         min(due_at + 7200, self._wall_time(day, '23:00')))

    def due(self):
        if not self.registered():
            return []
        now, day = self.local_now(), self.day()
        if not 8 <= now.hour < 23 or day < self._get('paused_until', ''):
            return []
        self._seed_jobs()
        self.db.execute("UPDATE notifications SET state='expired' WHERE state='pending' AND expires_at<?", (now.timestamp(),))
        return [dict(r) for r in self.db.execute('''
            SELECT n.* FROM notifications n LEFT JOIN goals g ON g.id=n.goal_id
            WHERE n.state='pending' AND n.day=? AND n.due_at<=? AND n.expires_at>?
            AND (n.goal_id IS NULL OR g.status='pending') ORDER BY n.due_at,n.id LIMIT 5
        ''', (day, now.timestamp(), now.timestamp()))]

    def ready(self, job_id):
        """Revalidate just before I/O; earlier sends may await while state changes."""
        if not self.registered():
            return None
        now, day = self.local_now(), self.day()
        if not 8 <= now.hour < 23 or day < self._get('paused_until', ''):
            return None
        row = self.db.execute('''SELECT n.* FROM notifications n LEFT JOIN goals g ON g.id=n.goal_id
            WHERE n.id=? AND n.state='pending' AND n.day=? AND n.due_at<=? AND n.expires_at>?
            AND (n.goal_id IS NULL OR g.status='pending')''',
            (job_id, day, now.timestamp(), now.timestamp())).fetchone()
        return dict(row) if row else None

    def mark_sent(self, job_id):
        self.db.execute("UPDATE notifications SET state='sent' WHERE id=?", (job_id,))

    def retry(self, job_id, seconds=60):
        self.db.execute("UPDATE notifications SET due_at=?,attempts=attempts+1 WHERE id=? AND state='pending'",
                        (self.clock().timestamp() + max(20, seconds), job_id))

    def _manual_job(self, goal_id, kind, minutes):
        g = self._current_goal(goal_id)
        if g['status'] != 'pending':
            raise UserError('Эта цель уже закрыта.')
        if self.day() < self._get('paused_until', ''):
            raise UserError('Напоминания на паузе. Сначала /resume.')
        due = self.local_now() + timedelta(minutes=minutes)
        if due.date().isoformat() != self.day() or not 8 <= due.hour < 23:
            raise UserError('Таймер попадает на тихие часы. Открой новые цели завтра.')
        self._seed_jobs()
        with self.store.transaction():
            self.db.execute("UPDATE notifications SET state='cancelled' WHERE goal_id=? AND state='pending'", (goal_id,))
            self.db.execute('''INSERT INTO notifications(id,day,goal_id,kind,due_at,expires_at)
                VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET due_at=excluded.due_at,
                expires_at=excluded.expires_at,state='pending',attempts=0''',
                (f'{kind}:{goal_id}', self.day(), goal_id, kind, due.timestamp(),
                 min(due.timestamp() + 7200, self._wall_time(self.day(), '23:00'))))
        return due

    def snooze(self, goal_id):
        return self._manual_job(goal_id, 'snooze', 30)

    def focus(self, goal_id):
        g = self._current_goal(goal_id)
        return self._manual_job(goal_id, 'focus', g['minutes'])

    def backup(self):
        return self.store.backup()
