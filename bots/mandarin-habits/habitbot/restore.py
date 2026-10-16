"""Offline restore from a snapshot path or bytes streamed through stdin."""
import argparse
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

from .app import instance_lock
from .config import Config


def restore(source_path, config):
    config.data_dir.mkdir(parents=True, exist_ok=True)
    with instance_lock(config.data_dir / 'instance.lock'):
        # This is a complete OFFLINE snapshot produced by SQLite backup(), never
        # the live WAL database. immutable avoids sidecar creation on RO mounts.
        source = sqlite3.connect(source_path.resolve().as_uri() + '?mode=ro&immutable=1', uri=True)
        staging = config.data_dir / 'restore-staging.sqlite3'
        try:
            if source.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('Копия повреждена.')
            if source.execute('PRAGMA user_version').fetchone()[0] not in (1, 2):
                raise ValueError('Несовместимая версия копии.')
            owner = source.execute("SELECT value FROM settings WHERE key='owner_id'").fetchone()
            expected_owner = config.owner_id
            target = config.data_dir / 'state.sqlite3'
            if not expected_owner and target.exists():
                current = sqlite3.connect(target.resolve().as_uri() + '?mode=ro', uri=True)
                try:
                    existing = current.execute("SELECT value FROM settings WHERE key='owner_id'").fetchone()
                    expected_owner = int(existing[0]) if existing else 0
                finally:
                    current.close()
            if not owner or not expected_owner or int(owner[0]) != expected_owner:
                raise ValueError('OWNER_ID в настройках запуска и копии должен совпадать.')
            if staging.exists():
                staging.unlink()
            destination = sqlite3.connect(staging)
            try:
                source.backup(destination)
            finally:
                destination.close()
            # No live connection: instance.lock rejects a running main process.
            for suffix in ('-wal', '-shm'):
                sidecar = config.data_dir / ('state.sqlite3' + suffix)
                if sidecar.exists():
                    sidecar.unlink()
            os.replace(staging, target)
        finally:
            source.close()


def main():
    parser = argparse.ArgumentParser(description='Восстановить прогресс при остановленном боте')
    parser.add_argument('backup', help='Путь к копии либо - для чтения из stdin')
    parser.add_argument('--owner-id', type=int, help='Ожидаемый владелец копии; обязателен для пустого тома')
    args = parser.parse_args()
    os.umask(0o077)
    config = Config.load(require_token=False)
    if args.owner_id is not None:
        config = Config(config.token,args.owner_id,config.data_dir)
    if args.backup == '-':
        # The host can keep a root-owned 0600 backup. Docker passes its bytes
        # through stdin; the non-root process creates its own private snapshot.
        config.data_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=config.data_dir, prefix='incoming-') as directory:
            snapshot = Path(directory) / 'snapshot.sqlite3'
            with snapshot.open('wb') as incoming:
                shutil.copyfileobj(sys.stdin.buffer, incoming)
            restore(snapshot, config)
    else:
        restore(Path(args.backup), config)
    print('Прогресс восстановлен. Запусти обычный контейнер бота.')


if __name__ == '__main__':
    main()
