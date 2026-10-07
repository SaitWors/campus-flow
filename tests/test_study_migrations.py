"""Upgrade persisted schema3 content without losing historical revisions."""
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import MetaData, text

from services.common.core import database, migrate, now


def test_schema4_upgrade_preserves_subjects_and_lessons_and_removes_obsolete_json(tmp_path, monkeypatch):
    monkeypatch.setenv('DATABASE_URL', 'sqlite:///'+str(tmp_path/'schedule.db'))
    monkeypatch.setenv('INTERNAL_TOKEN', 'migration-internal-token-at-least-32-characters')
    monkeypatch.setenv('TRANSLATION_WORKER', 'false')
    from services.schedule import main as m
    from services.schedule.models import Base, Rule, Occurrence
    engine, db = database('schedule')
    legacy = MetaData()
    for name, table in Base.metadata.tables.items():
        if name not in ('subjects', 'assignments', 'assignment_progress', 'materials', 'material_storage'):
            table.to_metadata(legacy)
    migrate(engine, SimpleNamespace(metadata=legacy), (lambda _: None, lambda _: None))
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE subjects (key VARCHAR(240) PRIMARY KEY, title VARCHAR(120) NOT NULL, title_en VARCHAR(120) NOT NULL)'))
        conn.execute(text("INSERT INTO subjects VALUES ('old-subject','Старый предмет','Old subject')"))
    content = {'title': 'Старый предмет', 'title_en': 'Old subject', 'kind': 'lab',
               'teacher': 'Existing teacher', 'room': '101', 'mode': 'remote',
               'meeting_url': 'https://example.test/existing', 'note': 'Keep this note',
               'start': '10:13', 'end': '11:49', 'subgroup': 1, 'queue_enabled': True}
    with db.begin() as session:
        session.add(Rule(id='old-rule', data={**content, 'weekday': 1, 'parity': 'all'}, revision=8))
        session.add(Occurrence(id='old-occurrence', rule_id='old-rule', original_date='2026-09-01',
                               date='2026-09-02', data={**content, 'status': 'confirmed'},
                               revision=12, overridden=True, updated_at=now()))
    monkeypatch.setattr(m, 'engine', engine)
    monkeypatch.setattr(m, 'DB', db)
    monkeypatch.setattr(m, 'translator', m.TitleTranslator(db))
    # A freshly started service executes the actual migration chain.
    with TestClient(m.app) as client:
        with db() as session:
            assert session.scalar(text('SELECT MAX(version) FROM schema_migrations')) == 5
        row = client.get('/internal/occurrences/old-occurrence', headers={
            'X-Internal-Token': 'migration-internal-token-at-least-32-characters'})
        assert row.status_code == 200 and 'queue_enabled' not in row.json()
    with TestClient(m.app):
        pass
    with db() as session:
        rule, occurrence = session.get(Rule, 'old-rule'), session.get(Occurrence, 'old-occurrence')
        assert rule.revision == 8 and occurrence.revision == 12 and occurrence.overridden
        assert occurrence.date == '2026-09-02' and occurrence.original_date == '2026-09-01'
        assert occurrence.data['start'] == '10:13' and occurrence.data['note'] == 'Keep this note'
        assert 'queue_enabled' not in rule.data and 'queue_enabled' not in occurrence.data
        subject = session.execute(text("SELECT key,title,title_en,teacher,requirements,links,revision FROM subjects WHERE key='old-subject'")).one()
        assert subject.key == 'old-subject' and subject.title_en == 'Old subject'
        assert subject.requirements == '' and json.loads(subject.links) == [] and subject.revision == 1
        assert session.scalar(text('SELECT MAX(version) FROM schema_migrations')) == 5
        assert session.scalar(text('SELECT COUNT(*) FROM assignments')) == 0
        assert session.scalar(text('SELECT COUNT(*) FROM assignment_progress')) == 0
        assert session.scalar(text('SELECT COUNT(*) FROM materials')) == 0
        assert session.scalar(text('SELECT COUNT(*) FROM material_storage')) == 1
    engine.dispose()


def test_legacy_lesson_output_filters_obsolete_field_even_before_cleanup(tmp_path, monkeypatch):
    monkeypatch.setenv('DATABASE_URL', 'sqlite:///'+str(tmp_path/'immediate-output.db'))
    monkeypatch.setenv('TRANSLATION_WORKER', 'false')
    from services.schedule import main as m
    from services.schedule.models import Base, Occurrence
    engine, db = database('schedule')
    Base.metadata.create_all(engine)
    item = Occurrence(id='legacy', rule_id='rule', original_date='2026-09-01', date='2026-09-01',
                      revision=1, overridden=False, data={'title': 'Legacy lesson', 'title_en': '',
                      'start': '10:00', 'end': '11:00', 'subgroup': 0, 'status': 'confirmed', 'queue_enabled': True})
    with db.begin() as session:
        session.add(item)
    config = {**m.DEFAULT_SETTINGS, 'timezone': 'UTC'}
    with db() as session:
        output = m.row(session.get(Occurrence, 'legacy'), config, session)
        assert 'queue_enabled' not in output
    engine.dispose()
