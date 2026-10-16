import sqlite3
from contextlib import contextmanager
from pathlib import Path


class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, isolation_level=None, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        version = self.db.execute('PRAGMA user_version').fetchone()[0]
        if version not in (0, 1, 2):
            self.db.close()
            raise RuntimeError('База создана более новой версией бота. Обнови образ.')
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS goals (
                id INTEGER PRIMARY KEY, day TEXT NOT NULL, kind TEXT NOT NULL,
                title TEXT NOT NULL, details TEXT NOT NULL, minutes INTEGER NOT NULL,
                xp INTEGER NOT NULL, coins INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending', done_at REAL,
                UNIQUE(day, kind)
            );
            CREATE TABLE IF NOT EXISTS ledger (
                key TEXT PRIMARY KEY, kind TEXT NOT NULL, xp INTEGER NOT NULL,
                coins INTEGER NOT NULL, note TEXT NOT NULL, created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS rewards (
                id INTEGER PRIMARY KEY, title TEXT NOT NULL,
                cost INTEGER NOT NULL CHECK(cost > 0), active INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS purchases (
                id TEXT PRIMARY KEY, reward_id INTEGER NOT NULL REFERENCES rewards(id),
                title TEXT NOT NULL, cost INTEGER NOT NULL,
                state TEXT NOT NULL DEFAULT 'pending', created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS notifications (
                id TEXT PRIMARY KEY, day TEXT NOT NULL, goal_id INTEGER REFERENCES goals(id),
                kind TEXT NOT NULL, due_at REAL NOT NULL, expires_at REAL NOT NULL,
                state TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS course_completions (
                lesson INTEGER PRIMARY KEY CHECK(lesson BETWEEN 1 AND 30),
                goal_id INTEGER UNIQUE NOT NULL REFERENCES goals(id), completed_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS notifications_due ON notifications(state, due_at);
        ''')
        if 'lesson' not in {row[1] for row in self.db.execute('PRAGMA table_info(goals)')}:
            self.db.execute('ALTER TABLE goals ADD COLUMN lesson INTEGER')
        self.db.execute('PRAGMA user_version=2')

    @contextmanager
    def transaction(self):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            yield self.db
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK')
            raise

    def close(self):
        self.db.close()

    def backup(self):
        copy = sqlite3.connect(':memory:')
        try:
            self.db.backup(copy)
            return copy.serialize()
        finally:
            copy.close()
