"""Member-only subject details and private personal assignment progress."""
from datetime import timezone
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import AwareDatetime, Field, field_validator
from sqlalchemy import inspect, select, text

from services.common.core import Input
from services.schedule.models import Assignment, AssignmentProgress, Occurrence, Rule
from services.schedule.translation import with_translation


def lesson_data(data):
    # Also covers reads during rolling upgrades before schema4 has run.
    return {key: value for key, value in data.items() if key != 'queue_enabled'}


def migrate_academics(conn):
    columns = {column['name'] for column in inspect(conn).get_columns('subjects')}
    additions = {
        'teacher': "VARCHAR(100) NOT NULL DEFAULT ''",
        'requirements': "TEXT NOT NULL DEFAULT ''",
        'links': "JSON NOT NULL DEFAULT '[]'",
        'revision': 'INTEGER NOT NULL DEFAULT 1',
    }
    for name, definition in additions.items():
        if name not in columns:
            conn.execute(text(f'ALTER TABLE subjects ADD COLUMN {name} {definition}'))
    Assignment.__table__.create(conn, checkfirst=True)
    AssignmentProgress.__table__.create(conn, checkfirst=True)
    for table in (Rule.__table__, Occurrence.__table__):
        for row in conn.execute(select(table.c.id, table.c.data)):
            cleaned = lesson_data(row.data)
            if cleaned != row.data:
                conn.execute(table.update().where(table.c.id == row.id).values(data=cleaned))


def validate_text(value, multiline=False):
    allowed = '\n\t' if multiline else ''
    if any((ord(char) < 32 and char not in allowed) or ord(char) == 127 for char in value):
        raise ValueError('invalid_text')
    return value


def https_url(value):
    if not value:
        return value
    if any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError('https_url_required')
    try:
        parsed = urlsplit(value)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('https_url_required')
        parsed.port
    except ValueError:
        raise ValueError('https_url_required') from None
    return value


class SubjectLink(Input):
    label: str = Field(min_length=1, max_length=120)
    url: str = Field(min_length=1, max_length=500)

    @field_validator('label')
    @classmethod
    def label_valid(cls, value):
        return validate_text(value)

    @field_validator('url')
    @classmethod
    def url_valid(cls, value):
        return https_url(value)


class SubjectDetailsInput(Input):
    revision: int = Field(ge=1)
    teacher: str = Field(default='', max_length=100)
    requirements: str = Field(default='', max_length=10000)
    links: list[SubjectLink] = Field(default_factory=list, max_length=20)

    @field_validator('teacher')
    @classmethod
    def teacher_valid(cls, value):
        return validate_text(value)

    @field_validator('requirements')
    @classmethod
    def requirements_valid(cls, value):
        return validate_text(value, multiline=True)


class AssignmentInput(Input):
    subject_key: str = Field(min_length=1, max_length=240)
    title: str = Field(min_length=2, max_length=160)
    description: str = Field(default='', max_length=10000)
    due_at: AwareDatetime | None = None
    subgroup: int = Field(default=0, ge=0, le=2)
    material_url: str = Field(default='', max_length=500)

    @field_validator('subject_key', 'title')
    @classmethod
    def text_valid(cls, value):
        return validate_text(value)

    @field_validator('description')
    @classmethod
    def description_valid(cls, value):
        return validate_text(value, multiline=True)

    @field_validator('material_url')
    @classmethod
    def material_valid(cls, value):
        return https_url(value)


class AssignmentUpdate(AssignmentInput):
    revision: int = Field(ge=1)


class ProgressInput(Input):
    status: Literal['not_started', 'in_progress', 'ready', 'done']
    revision: int = Field(ge=0)


class AssignmentAudienceInput(Input):
    ids: list[Annotated[str, Field(min_length=1, max_length=36)]] = Field(max_length=200)

    @field_validator('ids')
    @classmethod
    def ids_valid(cls, values):
        for value in values:
            validate_text(value)
        return values


def subject_row(item, db):
    translated = with_translation({'title': item.title, 'title_en': item.title_en}, db)
    return {'key': item.key, 'title': item.title, 'title_en': item.title_en,
            'title_en_auto': translated['title_en_auto'], 'teacher': item.teacher,
            'requirements': item.requirements, 'links': item.links, 'revision': item.revision}


def progress_row(item):
    return {'status': item.status, 'revision': item.revision} if item else {
        'status': 'not_started', 'revision': 0}


def utc_string(value):
    return value.isoformat()+'Z' if value is not None else None


def assignment_row(item, subject, progress=None):
    return {'id': item.id, 'subject_key': item.subject_key, 'subject_title': subject.title,
            'title': item.title, 'description': item.description, 'due_at': utc_string(item.due_at),
            'subgroup': item.subgroup, 'material_url': item.material_url,
            'revision': item.revision, 'progress': progress_row(progress)}


def assignment_values(data):
    values = data.model_dump(exclude={'revision'})
    if values['due_at'] is not None:
        values['due_at'] = values['due_at'].astimezone(timezone.utc).replace(tzinfo=None)
    return values


def assignment_event_data(item, subject):
    return {'assignment_id': item.id, 'title': item.title, 'subject_title': subject.title,
            'due_at': utc_string(item.due_at), 'subgroup': item.subgroup, 'revision': item.revision}


def is_manager(user):
    return user['role'] in ('admin', 'head', 'deputy')


def assignment_visible(item, user):
    return is_manager(user) or item.subgroup in (0, user['subgroup'])
