import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

import faq
from bot import SupportBot
from messages import CATEGORIES
from store import Store
from telegram_api import TelegramError


OWNER = 101
PLAYER = 202
OTHER = 303


class TelegramStub:
    def __init__(self):
        self.sent = []
        self.reject = None
        self.message_id = 500

    def call(self, method, **params):
        if method == "answerCallbackQuery":
            return True
        if self.reject and params["chat_id"] == self.reject[0]:
            raise self.reject[1]
        self.message_id += 1
        result = {"message_id": self.message_id}
        self.sent.append((method, params, result["message_id"]))
        return result

    def to(self, chat_id, method="sendMessage"):
        return [
            (params, message_id)
            for action, params, message_id in self.sent
            if action == method and params["chat_id"] == chat_id
        ]


class SupportTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "support.sqlite3"
        self.api = TelegramStub()
        self.now = 100_000
        self.seq = 0
        clock = patch("bot.time.time", side_effect=lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)
        self.restart()
        self.addCleanup(lambda: self.store.close())

    def restart(self):
        if hasattr(self, "store"):
            self.store.close()
        self.store = Store(self.path)
        self.store.bind_bot(23456)
        self.bot = SupportBot(self.store, self.api)

    def update(self, chat_id, *, text=None, category=None, reply_to=None, photo=None, chat_type="private"):
        self.seq += 1
        sender = {"id": chat_id, "is_bot": False, "first_name": "Test", "language_code": "en"}
        msg = {"chat": {"id": chat_id, "type": chat_type}, "from": sender,
               "message_id": self.seq, "date": self.now}
        if category:
            return {"update_id": self.seq, "callback_query": {
                "id": str(self.seq), "from": sender, "message": msg, "data": category,
            }}
        if text is not None:
            msg["text"] = text
        if reply_to is not None:
            msg["reply_to_message"] = {"message_id": reply_to}
        if photo:
            msg["photo"] = [{"file_id": photo, "file_unique_id": "unique", "width": 400, "height": 400}]
            msg["caption"] = "Synthetic screenshot"
        return {"update_id": self.seq, "message": msg}

    def deliver(self, chat_id, **kwargs):
        self.bot.handle(self.update(chat_id, **kwargs))
        self.bot.flush()

    def claim(self):
        self.deliver(OWNER, text="/claim " + self.bot.claim_code)

    def open_ticket(self, player=PLAYER, text="The contract still shows 0/3"):
        self.deliver(player, category="category:contracts")
        self.deliver(player, text=text)

    def test_owner_requires_the_code_and_survives_restart(self):
        code = self.bot.claim_code
        self.deliver(OTHER, text="/claim wrong-code")
        self.assertIsNone(self.store.owner_id())
        self.claim()
        self.deliver(OTHER, text="/claim " + code)
        self.restart()
        self.assertEqual(self.store.owner_id(), OWNER)
        self.assertIsNone(self.bot.claim_code)

    def test_expired_owner_code_is_rejected(self):
        code = self.bot.claim_code
        self.now += 1_201
        self.deliver(OWNER, text="/claim " + code)
        self.assertIsNone(self.store.owner_id())

    def test_client_and_ban_categories_link_out_until_player_continues(self):
        self.claim()
        for category in ("crash", "ban"):
            with self.subTest(category=category):
                self.deliver(PLAYER, category=f"category:{category}")
                notice, _ = self.api.to(PLAYER)[-1]
                buttons = [button for row in notice["reply_markup"]["inline_keyboard"] for button in row]
                self.assertTrue(any(button.get("url", "").startswith("https://help.steampowered.com/") for button in buttons))
                self.assertTrue(any(button.get("callback_data") == f"submit:{category}" for button in buttons))
                self.assertEqual(len(self.api.to(OWNER)), 1)
        self.deliver(PLAYER, category="submit:ban")
        self.deliver(PLAYER, text="Matchmaking restriction shown in game")
        self.assertIn("Matchmaking restriction", self.api.to(OWNER)[-1][0]["text"])

    def test_owner_replies_to_correct_player_and_can_close(self):
        self.claim()
        self.open_ticket()
        _, player_notice = self.api.to(OWNER)[-1]
        self.open_ticket(OTHER, "The Companion stopped")
        self.deliver(OWNER, text="Please send the match ID.", reply_to=player_notice)
        self.assertIn("Please send the match ID.", self.api.to(PLAYER)[-1][0]["text"])
        self.assertNotIn("Please send", self.api.to(OTHER)[-1][0]["text"])
        self.deliver(OTHER, text="/close 1")
        self.deliver(OWNER, text="/inbox")
        self.assertIn("#1", self.api.to(OWNER)[-1][0]["text"])
        self.deliver(OWNER, text="/close 1")
        self.assertIn("closed", self.api.to(PLAYER)[-1][0]["text"])
        self.deliver(OWNER, text="/inbox")
        self.assertNotIn("#1", self.api.to(OWNER)[-1][0]["text"])
        self.assertIn("#2", self.api.to(OWNER)[-1][0]["text"])

    def test_numbered_reply_reaches_the_player(self):
        self.claim()
        self.open_ticket()
        self.deliver(OWNER, text="/reply 1 Please send the Companion version.")
        self.assertIn("Please send the Companion version.", self.api.to(PLAYER)[-1][0]["text"])

    def test_photo_is_delivered_and_supports_reply_after_restart(self):
        self.claim()
        self.deliver(PLAYER, category="category:companion")
        self.deliver(PLAYER, photo="photo-file-id")
        notice, reply_id = self.api.to(OWNER, "sendPhoto")[-1]
        self.assertEqual(notice["photo"], "photo-file-id")
        self.assertIn("#1", notice["caption"])
        self.restart()
        self.deliver(OWNER, text="I see it.", reply_to=reply_id)
        self.assertIn("I see it.", self.api.to(PLAYER)[-1][0]["text"])

    def test_pending_report_survives_restarts_and_redelivery(self):
        self.claim()
        self.deliver(PLAYER, category="category:contracts")
        report = self.update(PLAYER, text="A report that must only appear once")
        self.api.reject = (OWNER, TelegramError(503))
        self.bot.handle(report)
        self.bot.flush()
        self.assertEqual(len(self.api.to(OWNER)), 1)
        self.restart()
        self.bot.handle(report)
        self.api.reject = None
        self.now += 301
        self.bot.flush()
        self.assertEqual(len(self.api.to(OWNER)), 2)
        self.assertIn("only appear once", self.api.to(OWNER)[-1][0]["text"])

    def test_revoked_token_does_not_drop_a_pending_report(self):
        self.claim()
        self.deliver(PLAYER, category="category:contracts")
        self.bot.handle(self.update(PLAYER, text="Wait for token rotation"))
        self.api.reject = (OWNER, TelegramError(401))
        with self.assertRaises(TelegramError):
            self.bot.flush()
        self.restart()
        self.api.reject = None
        self.bot.flush()
        self.assertIn("Wait for token rotation", self.api.to(OWNER)[-1][0]["text"])

    def test_long_report_is_delivered_without_losing_unicode(self):
        self.claim()
        body = "🎮" * 2048
        self.open_ticket(text=body)
        notices = [payload["text"] for payload, _ in self.api.to(OWNER)[1:]]
        self.assertGreater(len(notices), 1)
        self.assertTrue(all(len(text.encode("utf-16-le")) <= 8192 for text in notices))
        self.assertEqual("".join(notices).count("🎮"), 2048)

    def test_blocked_player_is_reported_to_owner(self):
        self.claim()
        self.open_ticket()
        self.api.reject = (PLAYER, TelegramError(403))
        self.deliver(OWNER, text="/reply 1 Are you still there?")
        self.assertIn("не доставлено", self.api.to(OWNER)[-1][0]["text"])

    def test_group_updates_are_ignored(self):
        self.claim()
        self.deliver(PLAYER, text="/start", chat_type="group")
        self.assertEqual(self.api.to(PLAYER), [])

    def test_faq_is_in_the_start_menu_and_game_answers_link_to_official_support(self):
        self.claim()
        self.deliver(PLAYER, text="/start")
        menu = self.api.to(PLAYER)[-1][0]["reply_markup"]["inline_keyboard"]
        self.assertIn("faq", [button.get("callback_data") for row in menu for button in row])
        self.deliver(PLAYER, category="faq")
        self.deliver(PLAYER, category="faq-group:game")
        self.deliver(PLAYER, category="faq-read:game_crash")
        answer = self.api.to(PLAYER)[-1][0]
        self.assertIn("Verify integrity", answer["text"])
        buttons = [button for row in answer["reply_markup"]["inline_keyboard"] for button in row]
        self.assertTrue(any(button.get("url", "").startswith("https://help.steampowered.com/") for button in buttons))
        self.assertEqual(len(self.api.to(OWNER)), 1)

    def test_russian_free_text_finds_the_abandon_penalty_without_opening_a_ticket(self):
        self.claim()
        self.deliver(PLAYER, category="language:ru")
        self.deliver(PLAYER, text="Меня забанили после того, как я вышел из матча")
        answer = self.api.to(PLAYER)[-1][0]
        self.assertIn("Штраф после выхода или AFK", answer["text"])
        self.assertIsNone(self.store.open_ticket(PLAYER))
        self.assertEqual(len(self.api.to(OWNER)), 1)

    def test_a_faq_ticket_keeps_its_topic_after_restart(self):
        self.claim()
        self.deliver(PLAYER, category="faq-read:contract_count")
        self.deliver(PLAYER, category="faq-ticket:contract_count")
        self.assertIsNone(self.store.open_ticket(PLAYER))
        self.restart()
        self.deliver(PLAYER, text="Only this match did not count")
        notice, reply_id = self.api.to(OWNER)[-1]
        self.assertIn("Контракт не засчитался", notice["text"])
        self.assertIn("Only this match", notice["text"])
        self.deliver(OWNER, text="/inbox")
        self.assertIn("Контракт не засчитался", self.api.to(OWNER)[-1][0]["text"])
        self.deliver(OWNER, text="Send the match ID, please.", reply_to=reply_id)
        self.assertIn("Send the match ID", self.api.to(PLAYER)[-1][0]["text"])

    def test_faq_search_with_an_open_ticket_does_not_send_the_search_to_the_owner(self):
        self.claim()
        self.open_ticket()
        self.deliver(PLAYER, text="/faq pairing code expired")
        self.assertIn("Pairing code", self.api.to(PLAYER)[-1][0]["text"])
        self.assertEqual(len(self.api.to(OWNER)), 2)
        self.assertEqual(self.store.open_ticket(PLAYER)["id"], 1)

    def test_opening_faq_after_a_draft_does_not_send_free_text_as_a_report(self):
        self.claim()
        self.deliver(PLAYER, category="category:contracts")
        self.deliver(PLAYER, category="faq-search")
        self.deliver(PLAYER, text="game crashes mid match")
        self.assertIsNone(self.store.open_ticket(PLAYER))
        self.assertEqual(len(self.api.to(OWNER)), 1)

    def test_unknown_question_offers_the_catalog_and_ticket_choice(self):
        self.claim()
        self.deliver(PLAYER, text="/faq qzxqw unmapped-phrase")
        buttons = self.api.to(PLAYER)[-1][0]["reply_markup"]["inline_keyboard"]
        actions = {button.get("callback_data") for row in buttons for button in row}
        self.assertTrue({"faq", "tickets"} <= actions)
        self.assertIsNone(self.store.open_ticket(PLAYER))

    def test_upgrading_the_original_database_preserves_owner_and_pending_reports(self):
        self.claim()
        self.deliver(PLAYER, category="category:contracts")
        report = self.update(PLAYER, text="Queued on the original schema")
        self.bot.handle(report)
        with self.store.db:
            self.store.db.execute("DROP TABLE faq_tickets")
            self.store.db.execute("DROP TABLE faq_drafts")

        self.restart()
        self.bot.handle(report)
        self.bot.flush()
        self.assertEqual(self.store.owner_id(), OWNER)
        self.assertIsNone(self.bot.claim_code)
        self.assertEqual(self.store.open_ticket(PLAYER)["id"], 1)
        self.assertEqual(len(self.api.to(OWNER)), 2)
        self.assertIn("Queued on the original schema", self.api.to(OWNER)[-1][0]["text"])

    def test_data_volume_cannot_be_rebound_to_another_telegram_bot(self):
        with self.assertRaises(ValueError):
            self.store.bind_bot(99999)


class FaqTest(unittest.TestCase):
    def test_common_bilingual_searches_include_the_relevant_guide(self):
        for query, expected in (
            ("Deadlock won’t start", "game_launch"),
            ("игра вылетает посреди матча", "game_crash"),
            ("разбаньте пж", "penalty_abandon"),
            ("How many Low Priority games remain?", "penalty_low"),
            ("не засчитался контракт", "contract_count"),
            ("у меня не пришёл XP", "contract_xp"),
            ("pairing code expired", "companion_pair"),
            ("Companion not detecting match after patch", "companion_events"),
            ("my pack animation froze", "pack_replay"),
            ("как удалить аккаунт", "account_data"),
        ):
            with self.subTest(query=query):
                self.assertIn(expected, {article["id"] for article in faq.find_articles(query)})

    def test_every_answer_is_bilingual_and_uses_valid_telegram_buttons(self):
        official_domains = {
            "deadlock-contracts.online", "help.steampowered.com", "store.steampowered.com",
            "forums.playdeadlock.com", "support.overwolf.com",
        }
        self.assertEqual({article["group"] for article in faq.ARTICLES.values()}, set(faq.GROUPS))
        for key, article in faq.ARTICLES.items():
            with self.subTest(article=key):
                self.assertIn(article["category"], CATEGORIES)
                self.assertLessEqual(len(f"faq-ticket:{key}".encode()), 64)
                for language in ("ru", "en"):
                    self.assertTrue(article[language]["report"])
                    self.assertGreaterEqual(len(article[language]["steps"]), 2)
                    self.assertLessEqual(len(faq.answer(article, language).encode("utf-16-le")), 8192)
                    sources = list(faq.links(article, language))
                    self.assertTrue(sources)
                    for link in sources:
                        url = urlsplit(link["url"])
                        self.assertEqual(url.scheme, "https")
                        self.assertIn(url.hostname, official_domains)


if __name__ == "__main__":
    unittest.main()
