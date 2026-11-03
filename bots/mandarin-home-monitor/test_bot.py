import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


class HostSnapshotTests(unittest.TestCase):
    def setUp(self):
        source = Path(__file__).with_name("bot.py")
        self.assertTrue(source.exists(), "Runner entrypoint is not implemented")
        spec = importlib.util.spec_from_file_location("home_monitor", source)
        self.bot = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.bot)
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.data = Path(scratch.name)

    def test_rejects_old_host_snapshot(self):
        self.bot.write_json(self.data / "host.json", {"owner": 101, "timestamp": 100, "metrics": {"cpu_pct": 27}})
        self.assertIsNone(self.bot.fetch_metrics(self.data, owner=101, now=221))

    def test_rejects_a_snapshot_for_another_owner(self):
        self.bot.write_json(self.data / "host.json", {"owner": 102, "timestamp": 100, "metrics": {"cpu_pct": 27}})
        self.assertIsNone(self.bot.fetch_metrics(self.data, owner=101, now=110))

    def test_reads_current_snapshot(self):
        self.bot.write_json(self.data / "host.json", {"owner": 101, "timestamp": 100, "metrics": {"cpu_pct": 27}})
        self.assertEqual(self.bot.fetch_metrics(self.data, owner=101, now=110), {"cpu_pct": 27})

    def test_accepts_only_the_bound_owners_private_chat(self):
        def message(owner, chat_type):
            return {"from": {"id": owner}, "chat": {"id": owner, "type": chat_type}, "text": "/status"}
        self.assertEqual(self.bot.owner_command(message(101, "private"), 101), "/status")
        self.assertIsNone(self.bot.owner_command(message(102, "private"), 101))
        self.assertIsNone(self.bot.owner_command(message(101, "group"), 101))


if __name__ == "__main__":
    unittest.main()
