import os
import sys
import time
from pathlib import Path


def main():
    path = Path(os.environ.get('BOT_DATA_DIR', '/data')) / 'heartbeat'
    try:
        age = time.time() - float(path.read_text('ascii'))
        return 0 if 0 <= age <= 180 else 1
    except (OSError, ValueError):
        return 1


if __name__ == '__main__':
    sys.exit(main())
