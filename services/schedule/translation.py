"""Local RU -> EN translation. Manual titles and schedule revisions stay untouched."""
import json
import logging
import os
import re
from collections import OrderedDict
from datetime import timedelta
from threading import Event as StopEvent, Lock, Thread
from time import monotonic

import httpx
from sqlalchemy import select
from services.common.core import fail, now
from services.schedule.models import Occurrence, Rule, TitleTranslation

# No caller-controlled URLs, credentials or external provider configuration.
TRANSLATE_URL = 'http://translator:5000/translate'
MAX_RESPONSE_BYTES = 4096
RETRY_SECONDS = 60


def request_translation(title):
    with httpx.Client(timeout=httpx.Timeout(8, connect=2), trust_env=False, follow_redirects=False) as client:
        with client.stream('POST', TRANSLATE_URL, json={
            'q': title, 'source': 'ru', 'target': 'en', 'format': 'text'
        }) as response:
            response.raise_for_status()
            payload = bytearray()
            for chunk in response.iter_bytes():
                payload.extend(chunk)
                if len(payload) > MAX_RESPONSE_BYTES:
                    raise ValueError('translation_response_too_large')
    result = json.loads(payload)
    value = result.get('translatedText') if isinstance(result, dict) else None
    if not isinstance(value, str):
        raise ValueError('invalid_translation')
    value = ' '.join(value.split())
    if not value or len(value) > 120 or any(ord(ch) < 32 for ch in value):
        raise ValueError('invalid_translation')
    if value == title and re.search('[а-яА-ЯёЁ]', title):
        raise ValueError('untranslated_title')
    return value


def with_translation(data, db):
    # Keep the manual field separate so API consumers never persist a generated override.
    cached = None if data.get('title_en') else db.get(TitleTranslation, data['title'])
    auto = cached.translated if cached else ''
    return {**data, 'title_en_auto': auto,
            'translation_pending': not bool(data.get('title_en') or auto)}


class TitleTranslator:
    def __init__(self, db_factory, translate=request_translation):
        self.db_factory = db_factory
        self.translate = translate
        self.lock = Lock()
        self.rate_lock = Lock()
        self.rates = OrderedDict()
        self.stop_event = StopEvent()
        self.thread = None

    def limit_preview(self, user_id):
        with self.rate_lock:
            stamp = monotonic()
            start, count = self.rates.get(user_id, (stamp, 0))
            if stamp - start >= 60:
                start, count = stamp, 0
            if count >= 20:
                fail('too_many_attempts', 429)
            self.rates[user_id] = (start, count + 1)
            self.rates.move_to_end(user_id)
            while len(self.rates) > 256:
                self.rates.popitem(last=False)

    def resolve(self, title):
        title = title.strip()
        if not 2 <= len(title) <= 120:
            return ''
        # At most one inference, including previews. Do not queue HTTP threads.
        if not self.lock.acquire(blocking=False):
            return ''
        try:
            with self.db_factory() as db:
                cached = db.get(TitleTranslation, title)
                if cached and (cached.translated or cached.updated_at > now() - timedelta(seconds=RETRY_SECONDS)):
                    return cached.translated
            try:
                value = self.translate(title)
            except (httpx.HTTPError, ValueError, TypeError):
                value = ''
            # No network/inference inside a database transaction.
            with self.db_factory.begin() as db:
                cached = db.get(TitleTranslation, title)
                if cached is None:
                    cached = TitleTranslation(title=title)
                    db.add(cached)
                cached.translated, cached.updated_at = value, now()
            return value
        finally:
            self.lock.release()

    def fill_missing(self):
        with self.db_factory() as db:
            titles = set()
            for data in db.scalars(select(Rule.data).where(Rule.archived == False)):
                if not data.get('title_en'):
                    titles.add(data['title'])
            for data in db.scalars(select(Occurrence.data)):
                if not data.get('title_en'):
                    titles.add(data['title'])
            cached = {c.title: c for c in db.scalars(select(TitleTranslation))}
            due = [title for title in sorted(titles) if title not in cached or (
                not cached[title].translated and cached[title].updated_at <= now() - timedelta(seconds=RETRY_SECONDS))]
        for title in due[:10]:
            if self.stop_event.is_set():
                break
            self.resolve(title)

    def start(self):
        if os.getenv('TRANSLATION_WORKER', 'true').lower() != 'true':
            return
        def work():
            while not self.stop_event.wait(2):
                try:
                    self.fill_missing()
                except Exception:
                    # Do not log input text, SQL parameters, cookies or provider response.
                    logging.getLogger(__name__).warning('Title translation worker will retry')
        self.thread = Thread(target=work, name='title-translator', daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=10)
