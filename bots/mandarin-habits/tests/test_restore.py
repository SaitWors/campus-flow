import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from habitbot.app import instance_lock
from habitbot.config import Config
from habitbot.restore import restore
from habitbot.service import Service


class RestoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = Config('123456:' + 'A' * 35, 123456, self.root / 'data')
        self.s = Service(self.config.data_dir / 'state.sqlite3', 123456,
                         clock=lambda: datetime(2026, 10, 16, 9, tzinfo=timezone.utc))
        self.s.register(123456)
        self.backup_path = self.root / 'backup.sqlite3'

    def tearDown(self):
        if self.s:
            self.s.close()
        self.tmp.cleanup()

    def test_offline_restore_reinstates_progress_and_settings(self):
        self.s.complete(self.s.goals()[0]['id'])
        self.s.set_option('track', 'java')
        expected = self.s.profile()['coins']
        self.backup_path.write_bytes(self.s.backup())
        self.s.complete(self.s.goals()[1]['id'])
        self.assertGreater(self.s.profile()['coins'], expected)
        self.s.close()
        self.s = None
        restore(self.backup_path, self.config)
        self.s = Service(self.config.data_dir / 'state.sqlite3', 123456)
        self.assertEqual(expected, self.s.profile()['coins'])
        self.assertEqual('java', self.s.options()['track'])

    def test_active_process_lock_prevents_restore(self):
        self.backup_path.write_bytes(self.s.backup())
        with instance_lock(self.config.data_dir / 'instance.lock'):
            with self.assertRaises(RuntimeError):
                restore(self.backup_path, self.config)

    def test_wrong_owner_does_not_modify_database(self):
        self.backup_path.write_bytes(self.s.backup())
        wrong = Config(self.config.token, 999999, self.config.data_dir)
        with self.assertRaises(ValueError):
            restore(self.backup_path, wrong)
        self.assertEqual(0, self.s.profile()['coins'])

    def test_newer_backup_version_is_rejected(self):
        self.backup_path.write_bytes(self.s.backup())
        db = sqlite3.connect(self.backup_path)
        try:
            db.execute('PRAGMA user_version=4')
            db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        finally:
            db.close()
        with self.assertRaises(ValueError):
            restore(self.backup_path, self.config)

    def test_backup_cli_exports_consistent_sqlite(self):
        self.s.complete(self.s.goals()[0]['id'])
        result = subprocess.run([sys.executable, '-m', 'habitbot.backup'],
                                env={**os.environ, 'BOT_DATA_DIR': str(self.config.data_dir)},
                                capture_output=True, check=True)
        self.assertTrue(result.stdout.startswith(b'SQLite format 3\x00'))
        self.backup_path.write_bytes(result.stdout)
        with sqlite3.connect(self.backup_path) as db:
            self.assertEqual('ok', db.execute('PRAGMA integrity_check').fetchone()[0])
            self.assertGreater(db.execute('SELECT SUM(coins) FROM ledger').fetchone()[0], 0)

    @unittest.skipUnless(os.name == 'posix', 'Read-only directory permissions need POSIX')
    @unittest.skipIf(getattr(os, 'geteuid', lambda: 1)() == 0,
                     'Run this permission test as non-root; this sandbox has no CAP_SETUID')
    def test_restore_from_snapshot_in_readonly_directory(self):
        readonly = self.root / 'readonly'
        readonly.mkdir()
        backup = readonly / 'offline.sqlite3'
        backup.write_bytes(self.s.backup())
        backup.chmod(0o444)
        readonly.chmod(0o555)
        self.root.chmod(0o755)
        target = self.root / 'restored'
        target.mkdir()
        target.chmod(0o777)
        config = Config(self.config.token, 123456, target)
        # Exercise real filesystem permissions even when the test runner is root.
        reader, writer = os.pipe()
        pid = os.fork()
        if pid == 0:
            os.close(reader)
            try:
                restore(backup, config)
            except BaseException as exc:
                os.write(writer, str(exc).encode())
                os._exit(1)
            os._exit(0)
        os.close(writer)
        details = os.read(reader, 10000).decode()
        os.close(reader)
        _, status = os.waitpid(pid, 0)
        readonly.chmod(0o755)
        self.assertEqual(0, os.waitstatus_to_exitcode(status), details)

    def test_restore_cli_accepts_private_backup_from_stdin(self):
        snapshot = self.s.backup()
        self.s.close()
        self.s = None
        result = subprocess.run([sys.executable, '-m', 'habitbot.restore', '-'], input=snapshot,
                                env={**os.environ, 'BOT_TOKEN': self.config.token,
                                     'OWNER_ID': '123456', 'BOT_DATA_DIR': str(self.config.data_dir)},
                                capture_output=True)
        self.assertEqual(0, result.returncode, result.stderr.decode())

    def test_restore_uses_existing_claimed_owner_when_env_owner_is_zero(self):
        self.backup_path.write_bytes(self.s.backup())
        self.s.close()
        self.s=None
        config=Config(self.config.token,0,self.config.data_dir)
        restore(self.backup_path,config)

    def test_offline_cli_can_restore_fresh_volume_without_bot_token(self):
        snapshot=self.s.backup()
        environment={k:v for k,v in os.environ.items() if k not in ('BOT_TOKEN','TELEGRAM_BOT_TOKEN','OWNER_ID')}
        environment['BOT_DATA_DIR']=str(self.root/'fresh-volume')
        result=subprocess.run([sys.executable,'-m','habitbot.restore','--owner-id','123456','-'],
                              input=snapshot,env=environment,capture_output=True)
        self.assertEqual(0,result.returncode,result.stderr.decode())
        restored=Service(self.root/'fresh-volume'/'state.sqlite3',0)
        try:
            self.assertEqual(123456,restored.owner_id)
        finally:
            restored.close()
