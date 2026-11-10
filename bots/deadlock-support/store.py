import json
import sqlite3


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA foreign_keys = ON;
            PRAGMA journal_mode = WAL;
            CREATE TABLE IF NOT EXISTS settings (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                bot_id INTEGER NOT NULL,
                owner_id INTEGER
            );
            CREATE TABLE IF NOT EXISTS players (
                chat_id INTEGER PRIMARY KEY,
                language TEXT NOT NULL,
                draft TEXT
            );
            CREATE TABLE IF NOT EXISTS tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL REFERENCES players(chat_id),
                category TEXT NOT NULL,
                closed_at INTEGER
            );
            CREATE UNIQUE INDEX IF NOT EXISTS one_open_ticket
                ON tickets(chat_id) WHERE closed_at IS NULL;
            CREATE TABLE IF NOT EXISTS telegram_updates (
                id INTEGER PRIMARY KEY,
                processed_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS outbox (
                id INTEGER PRIMARY KEY,
                chat_id INTEGER NOT NULL,
                ticket_id INTEGER REFERENCES tickets(id),
                method TEXT NOT NULL,
                payload TEXT,
                next_attempt INTEGER NOT NULL,
                message_id INTEGER
            );
            CREATE INDEX IF NOT EXISTS pending_by_chat
                ON outbox(chat_id, id) WHERE payload IS NOT NULL;
            CREATE UNIQUE INDEX IF NOT EXISTS telegram_notice
                ON outbox(chat_id, message_id) WHERE message_id IS NOT NULL;
        """)

    def close(self):
        self.db.close()

    def bind_bot(self, bot_id):
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO settings VALUES (1, ?, NULL)", (bot_id,))
            actual_id = self.db.execute("SELECT bot_id FROM settings WHERE singleton = 1").fetchone()[0]
            if actual_id != bot_id:
                raise ValueError("This volume is associated with a different Telegram bot")

    def owner_id(self):
        return self.db.execute("SELECT owner_id FROM settings WHERE singleton = 1").fetchone()[0]

    def claim(self, chat_id):
        return self.db.execute(
            "UPDATE settings SET owner_id = ? WHERE singleton = 1 AND owner_id IS NULL",
            (chat_id,),
        ).rowcount == 1

    def first_delivery(self, update_id, now):
        return self.db.execute(
            "INSERT OR IGNORE INTO telegram_updates VALUES (?, ?)", (update_id, now)
        ).rowcount == 1

    def prune_update_ids(self, now):
        with self.db:
            self.db.execute("DELETE FROM telegram_updates WHERE processed_at < ?", (now - 172800,))

    def player(self, chat_id):
        return self.db.execute("SELECT * FROM players WHERE chat_id = ?", (chat_id,)).fetchone()

    def set_player(self, chat_id, language, draft=None):
        self.db.execute("""
            INSERT INTO players VALUES (?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET language = excluded.language, draft = excluded.draft
        """, (chat_id, language, draft))

    def open_ticket(self, chat_id):
        return self.db.execute(
            "SELECT * FROM tickets WHERE chat_id = ? AND closed_at IS NULL", (chat_id,)
        ).fetchone()

    def create_ticket(self, chat_id, category):
        return self.db.execute(
            "INSERT INTO tickets (chat_id, category) VALUES (?, ?)", (chat_id, category)
        ).lastrowid

    def list_open(self, limit):
        return self.db.execute(
            "SELECT id, category FROM tickets WHERE closed_at IS NULL ORDER BY id LIMIT ?", (limit,)
        ).fetchall()

    def ticket(self, ticket_id):
        return self.db.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()

    def close_ticket(self, ticket_id, now):
        self.db.execute("UPDATE tickets SET closed_at = ? WHERE id = ?", (now, ticket_id))

    def ticket_for_notice(self, chat_id, message_id):
        row = self.db.execute(
            "SELECT ticket_id FROM outbox WHERE chat_id = ? AND message_id = ?",
            (chat_id, message_id),
        ).fetchone()
        return row["ticket_id"] if row else None

    def queue(self, chat_id, method, payload, ticket_id=None):
        self.db.execute(
            "INSERT INTO outbox (chat_id, ticket_id, method, payload, next_attempt) VALUES (?, ?, ?, ?, 0)",
            (chat_id, ticket_id, method, json.dumps(payload, ensure_ascii=False)),
        )

    def next_message(self, now):
        return self.db.execute("""
            SELECT * FROM outbox AS candidate
            WHERE candidate.payload IS NOT NULL AND candidate.next_attempt <= ?
              AND NOT EXISTS (
                SELECT 1 FROM outbox AS earlier
                WHERE earlier.chat_id = candidate.chat_id AND earlier.payload IS NOT NULL
                  AND earlier.id < candidate.id
              )
            ORDER BY candidate.id LIMIT 1
        """, (now,)).fetchone()

    def complete(self, item, message_id=None):
        if item["ticket_id"] is not None and message_id is not None:
            self.db.execute(
                "UPDATE outbox SET payload = NULL, message_id = ? WHERE id = ?",
                (message_id, item["id"]),
            )
        else:
            self.db.execute("DELETE FROM outbox WHERE id = ?", (item["id"],))

    def defer(self, item, next_attempt):
        self.db.execute(
            "UPDATE outbox SET next_attempt = ? WHERE id = ?", (next_attempt, item["id"])
        )

    def replace_with_text(self, item, text):
        self.db.execute(
            "UPDATE outbox SET method = 'sendMessage', payload = ? WHERE id = ?",
            (json.dumps({"text": text, "link_preview_options": {"is_disabled": True}}, ensure_ascii=False),
             item["id"]),
        )

    def has_pending(self):
        return self.db.execute("SELECT 1 FROM outbox WHERE payload IS NOT NULL LIMIT 1").fetchone() is not None
