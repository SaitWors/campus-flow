import fcntl
import json
import logging
import os
import re
import secrets
import time
from pathlib import Path

from messages import CATEGORIES, FORUM, GAME_ISSUES, GAME_SUPPORT, category_name, tr
from store import Store
from telegram_api import Telegram, TelegramError


log = logging.getLogger("deadlock-support")
CLAIM_SECONDS = 20 * 60
INBOX_LIMIT = 30
FATAL_API_ERRORS = (401, 404, 409)


def text_part(text, limit):
    return text.encode("utf-16-le")[:limit * 2].decode("utf-16-le", errors="ignore")


def keyboard(rows):
    return {"inline_keyboard": rows}


def button(text, action):
    return {"text": text, "callback_data": action}


def ticket_number(value):
    return int(value.lstrip("#")) if re.fullmatch(r"#?[0-9]{1,12}", value) else None


class SupportBot:
    def __init__(self, store, api):
        self.store = store
        self.api = api
        self.claim_code = None if store.owner_id() else secrets.token_urlsafe(24)
        self.claim_expires = time.time() + CLAIM_SECONDS
        self.pause_delivery_until = 0

    def say(self, chat, text, *, markup=None, ticket_id=None):
        while text:
            part = text_part(text, 4096)
            payload = {"text": part, "link_preview_options": {"is_disabled": True}}
            if markup:
                payload["reply_markup"] = markup
                markup = None
            self.store.queue(chat, "sendMessage", payload, ticket_id)
            text = text[len(part):]

    def handle(self, update):
        callback = update.get("callback_query")
        message = callback.get("message") if callback else update.get("message")
        if not message:
            return
        chat = message.get("chat", {})
        sender = (callback or message).get("from", {})
        chat_id = chat.get("id")
        if (chat.get("type") != "private" or not isinstance(chat_id, int)
                or chat_id != sender.get("id") or sender.get("is_bot")):
            return
        telegram_language = sender.get("language_code") or ""
        language = "ru" if telegram_language.split("-", 1)[0] == "ru" else "en"
        now = int(time.time())
        with self.store.db:
            if self.store.first_delivery(update["update_id"], now):
                if callback:
                    self.on_button(chat_id, callback.get("data", ""), language)
                else:
                    self.on_message(chat_id, message, language, now)
        if self.claim_code and self.store.owner_id() is not None:
            self.claim_code = None
        if callback:
            try:
                self.api.call("answerCallbackQuery", callback_query_id=callback["id"])
            except TelegramError:
                # The stored action is valid even if Telegram's button spinner expires.
                pass

    def on_message(self, chat, message, language, now):
        owner = self.store.owner_id()
        parts = message.get("text", "").split(maxsplit=1)
        command = parts[0].split("@", 1)[0].lower() if parts else ""
        argument = parts[1].strip() if len(parts) == 2 else ""

        if command == "/claim":
            self.claim(chat, argument, language, now)
        elif owner == chat:
            self.on_owner_message(chat, message, command, argument, now)
        elif owner is None:
            self.say(chat, tr("setup", language))
        else:
            self.on_player_message(chat, message, command, language, owner)

    def claim(self, chat, code, language, now):
        if (self.claim_code and now < self.claim_expires
                and secrets.compare_digest(code.encode(), self.claim_code.encode())
                and self.store.claim(chat)):
            self.say(chat, "Аккаунт владельца привязан.\n\n"
                           "/inbox — открытые обращения\n"
                           "/reply 12 текст — ответить по номеру\n"
                           "/close 12 — закрыть обращение\n\n"
                           "Можно также ответить прямо на уведомление бота.")
            return
        self.say(chat, "Код недействителен или уже использован." if language == "ru"
                 else "The code is invalid or has already been used.")

    def menu(self, chat, language):
        rows = [[button(category_name(key, language), "category:" + key)] for key in CATEGORIES]
        rows.append([button("Русский", "language:ru"), button("English", "language:en")])
        self.say(chat, tr("start", language), markup=keyboard(rows))

    def on_button(self, chat, action, language):
        owner = self.store.owner_id()
        if owner is None or chat == owner:
            return
        player = self.store.player(chat)
        if player:
            language = player["language"]
        kind, _, value = action.partition(":")
        active = self.store.open_ticket(chat)
        if kind == "language" and value in ("ru", "en"):
            self.store.set_player(chat, value)
            if active:
                self.say(chat, tr("open", value, ticket=active["id"]))
            else:
                self.menu(chat, value)
            return
        if active:
            self.say(chat, tr("open", language, ticket=active["id"]))
            return
        if action == "menu":
            self.store.set_player(chat, language)
            self.menu(chat, language)
            return
        if kind == "category" and value in ("crash", "ban"):
            self.store.set_player(chat, language)
            steam_url = GAME_ISSUES if value == "crash" else GAME_SUPPORT
            rows = [[{"text": tr("steam", language), "url": steam_url.format(language=language)}]]
            if value == "crash":
                rows.append([{"text": tr("forum", language), "url": FORUM}])
            rows.append([button(tr("continue", language), "submit:" + value)])
            rows.append([button(tr("back", language), "menu")])
            self.say(chat, tr(value + "_help", language), markup=keyboard(rows))
            return
        if (kind == "category" and value in ("contracts", "companion", "other")
                or kind == "submit" and value in ("crash", "ban")):
            self.store.set_player(chat, language, value)
            prompt = tr("prompt", language)
            if value in ("contracts", "companion"):
                prompt += "\n\n" + tr(value + "_hint", language)
            self.say(chat, prompt, markup=keyboard([[button(tr("back", language), "menu")]]))

    def on_player_message(self, chat, message, command, language, owner):
        player = self.store.player(chat)
        if player:
            language = player["language"]
        active = self.store.open_ticket(chat)
        if command in ("/start", "/new"):
            if active:
                self.say(chat, tr("open", language, ticket=active["id"]))
            else:
                self.store.set_player(chat, language)
                self.menu(chat, language)
            return
        if command == "/status":
            status = tr("open", language, ticket=active["id"]) if active else tr("empty", language)
            self.say(chat, status)
            return
        if command == "/cancel":
            if active:
                self.say(chat, tr("open", language, ticket=active["id"]))
            else:
                self.store.set_player(chat, language)
                self.say(chat, tr("cancelled", language))
                self.menu(chat, language)
            return
        if command == "/language":
            self.say(chat, tr("language", language), markup=keyboard(
                [[button("Русский", "language:ru"), button("English", "language:en")]]
            ))
            return
        if command.startswith("/"):
            self.say(chat, tr("help", language))
            return

        draft = player["draft"] if player else None
        if not active and not draft:
            self.menu(chat, language)
            return
        photos = message.get("photo", [])
        body = (message.get("caption", "") if photos else message.get("text", "")).strip()
        if not photos and not body:
            self.say(chat, tr("supported", language))
            return

        category = active["category"] if active else draft
        ticket_id = active["id"] if active else self.store.create_ticket(chat, category)
        if not active:
            self.store.set_player(chat, language)
        heading = f"Обращение #{ticket_id} · {category_name(category, 'ru')}"
        notice = f"{heading}\n\n{body}" if body else heading
        if photos:
            caption = text_part(notice, 1024)
            self.store.queue(owner, "sendPhoto", {"photo": photos[-1]["file_id"], "caption": caption}, ticket_id)
            if len(caption) < len(notice):
                self.say(owner, f"Обращение #{ticket_id} · продолжение\n\n{notice[len(caption):]}", ticket_id=ticket_id)
        else:
            self.say(owner, notice, ticket_id=ticket_id)
        self.say(chat, tr("added" if active else "created", language, ticket=ticket_id))

    def on_owner_message(self, chat, message, command, argument, now):
        if command in ("/start", "/help"):
            self.say(chat, "/inbox — открытые обращения\n/reply 12 текст — ответить\n"
                           "/close 12 — закрыть\n\nМожно ответить прямо на уведомление бота.")
            return
        if command == "/inbox":
            rows = self.store.list_open(INBOX_LIMIT + 1)
            if not rows:
                self.say(chat, "Открытых обращений нет.")
                return
            entries = [f"#{row['id']} · {category_name(row['category'], 'ru')}" for row in rows[:INBOX_LIMIT]]
            if len(rows) > INBOX_LIMIT:
                entries.append(f"Показаны первые {INBOX_LIMIT} открытых обращений.")
            self.say(chat, "Открытые обращения\n\n" + "\n".join(entries))
            return

        reply = message.get("reply_to_message", {})
        text = message.get("text", "").strip()
        if command in ("/reply", "/close"):
            args = argument.split(maxsplit=1)
            ticket_id = ticket_number(args[0]) if args else None
            text = args[1] if len(args) == 2 else ""
            if ticket_id is None or (command == "/reply" and not text):
                self.say(chat, "Формат команды /reply 12 текст" if command == "/reply" else "Формат команды /close 12")
                return
        elif text and reply:
            ticket_id = self.store.ticket_for_notice(chat, reply["message_id"])
        else:
            ticket_id = None
        ticket = self.store.ticket(ticket_id) if ticket_id is not None else None
        if not ticket:
            self.say(chat, "Ответь на уведомление бота или укажи номер из /inbox.")
            return
        if ticket["closed_at"] is not None:
            self.say(chat, f"Обращение #{ticket_id} уже закрыто.")
            return

        recipient = ticket["chat_id"]
        language = self.store.player(recipient)["language"]
        if command == "/close":
            self.store.close_ticket(ticket_id, now)
            self.say(recipient, tr("closed", language, ticket=ticket_id), ticket_id=ticket_id)
            self.say(chat, f"Обращение #{ticket_id} закрыто.")
            return
        self.say(recipient, tr("reply", language, ticket=ticket_id) + text, ticket_id=ticket_id)
        self.say(chat, f"Ответ по #{ticket_id} поставлен в очередь.")

    def flush(self):
        for _ in range(30):
            now = int(time.time())
            if now < self.pause_delivery_until:
                return
            item = self.store.next_message(now)
            if item is None:
                return
            payload = json.loads(item["payload"])
            try:
                result = self.api.call(item["method"], chat_id=item["chat_id"], **payload)
            except TelegramError as exc:
                if exc.code in FATAL_API_ERRORS:
                    raise
                if self.delivery_failed(item, payload, exc, now):
                    return
            else:
                with self.store.db:
                    self.store.complete(item, result["message_id"])

    def delivery_failed(self, item, payload, error, now):
        owner = self.store.owner_id()
        ticket_id = item["ticket_id"]
        log.warning("Telegram %s returned %s", item["method"], error.code)
        with self.store.db:
            if error.code == 429 or error.code >= 500:
                delay = max(2, error.retry_after) if error.code == 429 else 5
                self.pause_delivery_until = now + delay
                self.store.defer(item, now + delay)
                return True
            if error.code == 400 and item["method"] == "sendPhoto":
                self.store.replace_with_text(item, payload["caption"] + "\n\nTelegram не принял фото.")
                return False
            if error.code in (400, 403) and owner == item["chat_id"] and ticket_id is not None:
                self.store.defer(item, now + 60)
                return False
            self.store.complete(item)
            if ticket_id is not None and owner != item["chat_id"]:
                self.say(owner, f"Сообщение по #{ticket_id} не доставлено. Telegram вернул ошибку {error.code}.")
        return False

    def poll(self):
        offset = None
        next_prune = 0
        while True:
            self.flush()
            now = int(time.time())
            if now >= next_prune:
                self.store.prune_update_ids(now)
                next_prune = now + 3600
            timeout = 2 if self.store.has_pending() else 25
            params = {"timeout": timeout, "allowed_updates": ["message", "callback_query"]}
            if offset is not None:
                params["offset"] = offset
            try:
                updates = self.api.call("getUpdates", **params)
            except TelegramError as exc:
                if exc.code in FATAL_API_ERRORS:
                    raise
                log.warning("Telegram polling returned %s", exc.code)
                time.sleep(max(5, exc.retry_after))
                continue

            # An empty response means the previous offset is confirmed. Leaving it unset also
            # handles Telegram choosing a fresh update ID after a week without new updates.
            offset = None
            for update in updates:
                self.handle(update)
                offset = update["update_id"] + 1


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    os.umask(0o077)
    token = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("BOT_TOKEN", "")
    if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token):
        raise SystemExit("Set a valid Telegram bot token in the Campus Flow bot card.")
    data_dir = os.environ.get("BOT_DATA_DIR")
    if not data_dir:
        raise SystemExit("BOT_DATA_DIR is required. Check Campus Flow Bot Runner data-v1 support.")
    try:
        with (Path(data_dir) / ".polling.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            api = Telegram(token)
            identity = api.call("getMe")
            if api.call("getWebhookInfo").get("url"):
                raise SystemExit("The Telegram bot has an active webhook. Use a separate Telegram token for this bot.")
            store = Store(Path(data_dir) / "support.sqlite3")
            try:
                store.bind_bot(identity["id"])
                bot = SupportBot(store, api)
                log.info("Telegram polling started for @%s", identity["username"])
                if bot.claim_code:
                    log.info("Owner setup, valid for 20 minutes: /claim %s", bot.claim_code)
                bot.poll()
            finally:
                store.close()
    except TelegramError as exc:
        message = "Another poller or webhook is active for this bot." if exc.code == 409 else str(exc)
        raise SystemExit(message) from None
    except BlockingIOError:
        raise SystemExit("This data volume is already in use by another worker.") from None
    except ValueError as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
