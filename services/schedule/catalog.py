"""Shared subject names and independent bell-time preferences."""
import unicodedata
from sqlalchemy import select
from pydantic import Field, model_validator
from services.common.core import Input, digest
from services.schedule.models import Subject, TimePresets, Rule, Occurrence

DEFAULT_PRESETS = [
    {'label': str(n), 'start': start, 'end': end}
    for n, (start, end) in enumerate([
        ('09:30', '11:00'), ('11:15', '12:45'), ('13:00', '14:30'),
        ('15:10', '16:40'), ('16:55', '18:25'),
    ], 1)
]


def subject_key(title):
    return digest(' '.join(unicodedata.normalize('NFKC', title).casefold().split()))


def remember_subject(db, data):
    if data.get('demo'):
        return
    title = ' '.join(data['title'].split())
    key = subject_key(title)
    item = db.get(Subject, key)
    if not item:
        db.add(Subject(key=key, title=title, title_en=data.get('title_en', '')))
    elif data.get('title_en'):
        item.title_en = data['title_en']
    db.flush()


def migrate_catalog(conn):
    Subject.__table__.create(conn, checkfirst=True)
    TimePresets.__table__.create(conn, checkfirst=True)
    # Existing names are available immediately after upgrade, including exceptions.
    for table in (Rule.__table__, Occurrence.__table__):
        for data in conn.execute(select(table.c.data)).scalars():
            if data.get('demo'):
                continue
            key = subject_key(data['title'])
            if not conn.execute(select(Subject.key).where(Subject.key == key)).first():
                conn.execute(Subject.__table__.insert().values(
                    key=key, title=' '.join(data['title'].split()), title_en=data.get('title_en', '')))
    if not conn.execute(select(TimePresets.id).where(TimePresets.id == 1)).first():
        conn.execute(TimePresets.__table__.insert().values(id=1, data=DEFAULT_PRESETS, revision=1))


class TimeSlot(Input):
    label: str = Field(min_length=1, max_length=30)
    start: str = Field(pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    end: str = Field(pattern=r'^([01]\d|2[0-3]):[0-5]\d$')

    @model_validator(mode='after')
    def valid(self):
        if self.end <= self.start:
            raise ValueError('end_before_start')
        return self


class PresetsInput(Input):
    revision: int = Field(ge=1)
    items: list[TimeSlot] = Field(min_length=1, max_length=12)

    @model_validator(mode='after')
    def valid(self):
        if len({s.label.casefold() for s in self.items}) != len(self.items):
            raise ValueError('duplicate_label')
        ordered = sorted(self.items, key=lambda s: s.start)
        if any(a.end > b.start for a, b in zip(ordered, ordered[1:])):
            raise ValueError('overlapping_slots')
        return self
