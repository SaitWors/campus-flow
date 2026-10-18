"""Private profiles; the original owner's database is never moved or replaced."""
import hashlib
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .service import Service, UserError


class Hub:
    def __init__(self, root, owner_id=0, clock=None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.owner = Service(self.root / 'state.sqlite3', owner_id, self.clock)
        self.cache = {}
        self.db = sqlite3.connect(self.root / 'members.sqlite3', isolation_level=None)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS members (user_id INTEGER PRIMARY KEY, active INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE IF NOT EXISTS invites (hash TEXT PRIMARY KEY, expires_at REAL NOT NULL, used_by INTEGER);
        ''')

    @staticmethod
    def valid_id(user_id):
        if type(user_id) is not int or not 0 < user_id < 2**52:
            raise UserError('Нужен числовой Telegram ID.')

    def member(self, user_id):
        self.valid_id(user_id)
        if user_id == self.owner.owner_id and self.owner.registered():
            return self.owner
        if not self.db.execute('SELECT 1 FROM members WHERE user_id=? AND active=1', (user_id,)).fetchone():
            raise UserError('Доступ по приглашению. Попроси владельца отправить /invite.')
        if user_id not in self.cache:
            self.cache[user_id] = Service(self.root / 'users' / str(user_id) / 'state.sqlite3', user_id, self.clock)
        return self.cache[user_id]

    def users(self):
        result = [self.owner.owner_id] if self.owner.registered() else []
        return result + [r[0] for r in self.db.execute('SELECT user_id FROM members WHERE active=1 ORDER BY user_id')]

    def invite(self, actor):
        if actor != self.owner.owner_id or not self.owner.registered():
            raise UserError('Приглашения выдаёт только владелец бота.')
        if len(self.users()) >= 10:
            raise UserError('Достигнут лимит: 10 участников.')
        code = secrets.token_urlsafe(24)
        self.db.execute('DELETE FROM invites WHERE expires_at<? OR used_by IS NOT NULL', (self.clock().timestamp(),))
        self.db.execute('INSERT INTO invites(hash,expires_at) VALUES (?,?)',
                        (hashlib.sha256(code.encode()).hexdigest(), self.clock().timestamp() + 86400))
        return code

    def join(self, user_id, code):
        self.valid_id(user_id)
        if user_id == self.owner.owner_id:
            raise UserError('Ты уже владелец.')
        if not isinstance(code, str) or not 20 <= len(code) <= 64:
            raise UserError('Приглашение недействительно или истекло.')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            if len(self.users()) >= 10:
                raise UserError('Достигнут лимит: 10 участников.')
            row = self.db.execute('SELECT expires_at,used_by FROM invites WHERE hash=?',
                                  (hashlib.sha256(code.encode()).hexdigest(),)).fetchone()
            if not row or row[1] is not None or row[0] <= self.clock().timestamp():
                raise UserError('Приглашение недействительно или истекло.')
            if self.db.execute('SELECT 1 FROM members WHERE user_id=? AND active=1', (user_id,)).fetchone():
                raise UserError('Ты уже подключён. Открой /today.')
            service = self.cache.get(user_id)
            if service is None:
                service = Service(self.root / 'users' / str(user_id) / 'state.sqlite3', user_id, self.clock)
                self.cache[user_id] = service
            if not service.registered():
                service.register(user_id)
            if service._get('guest_initialized') != '1':
                service.set_option('code_enabled', '0')
                service._put('guest_initialized', '1')
            self.db.execute('INSERT INTO members(user_id) VALUES (?) ON CONFLICT(user_id) DO UPDATE SET active=1', (user_id,))
            self.db.execute('UPDATE invites SET used_by=? WHERE hash=?', (user_id, hashlib.sha256(code.encode()).hexdigest()))
            self.db.execute('COMMIT')
            return service
        except BaseException:
            self.db.execute('ROLLBACK')
            raise

    def revoke(self, actor, user_id):
        self.valid_id(user_id)
        if actor != self.owner.owner_id or user_id == actor:
            raise UserError('Только владелец может отключить приглашённого участника.')
        if not self.db.execute('UPDATE members SET active=0 WHERE user_id=? AND active=1', (user_id,)).rowcount:
            raise UserError('Участник не найден.')

    def close(self):
        for service in [self.owner, *self.cache.values()]:
            service.close()
        self.db.close()
