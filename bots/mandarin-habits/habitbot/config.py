import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Config:
    token: str = field(repr=False)
    owner_id: int = 0
    data_dir: Path = Path('/data')

    @classmethod
    def load(cls, env=None, owner_file=None, *, require_token=True):
        env = os.environ if env is None else env
        path = Path(owner_file) if owner_file else Path(__file__).resolve().parents[1] / 'owner.json'
        baked = json.loads(path.read_text('utf-8')) if path.exists() else {}
        token = (env.get('BOT_TOKEN') or env.get('TELEGRAM_BOT_TOKEN') or '').strip()
        if require_token and not re.fullmatch(r'\d{5,15}:[A-Za-z0-9_-]{30,}', token):
            raise ValueError('Задай BOT_TOKEN в .env: нужен токен бота от @BotFather.')
        try:
            owner = int(env.get('OWNER_ID', baked.get('owner_id', 0)))
        except (ValueError, TypeError):
            raise ValueError('OWNER_ID должен быть числовым Telegram ID.') from None
        if owner < 0:
            raise ValueError('OWNER_ID должен быть положительным, либо 0 для получения /id.')
        return cls(token, owner, Path(env.get('BOT_DATA_DIR', '/data')))
