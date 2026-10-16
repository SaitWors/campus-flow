"""Print a consistent SQLite backup to stdout; redirect to a private file."""
import os
import sqlite3
import sys
from pathlib import Path


def main():
    path = Path(os.environ.get('BOT_DATA_DIR', '/data')) / 'state.sqlite3'
    source = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
    copy = sqlite3.connect(':memory:')
    try:
        source.backup(copy)
        sys.stdout.buffer.write(copy.serialize())
    finally:
        source.close()
        copy.close()


if __name__ == '__main__':
    main()
