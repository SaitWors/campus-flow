"""Upgrade existing rows rather than testing fresh schemas only."""
from sqlalchemy import text, inspect, select
from services.common.core import database, migrate, now


def test_auth_role_migration_preserves_existing_user(tmp_path, monkeypatch):
    monkeypatch.setenv('DATABASE_URL','sqlite:///'+str(tmp_path/'old-auth.db'))
    from services.auth.main import add_group_role, user_json
    from services.auth.models import Base, User
    engine, DB=database('auth')
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TIMESTAMP NOT NULL)'))
        conn.execute(text('INSERT INTO schema_migrations VALUES (1, :at)'),{'at':now()})
        conn.execute(text('CREATE TABLE users (id VARCHAR(36) PRIMARY KEY, email VARCHAR(254), name VARCHAR(80), password_hash VARCHAR(255), role VARCHAR(20), status VARCHAR(20), subgroup INTEGER, created_at TIMESTAMP)'))
        conn.execute(text("INSERT INTO users VALUES ('existing', 'old@example.test', 'Existing head', 'unchanged-password-hash', 'head', 'active', 2, :at)"),{'at':now()})
    migrate(engine,Base,(add_group_role,))
    migrate(engine,Base,(add_group_role,))
    with DB() as db:
        u=db.get(User,'existing')
        assert u.password_hash=='unchanged-password-hash'
        assert u.role=='head' and u.group_role=='none' and u.subgroup==2
        assert user_json(u)['group_role']=='head'
        assert db.scalar(text('SELECT MAX(version) FROM schema_migrations'))==2
    engine.dispose()


def test_question_schema_upgrade_preserves_notifications(tmp_path, monkeypatch):
    monkeypatch.setenv('DATABASE_URL','sqlite:///'+str(tmp_path/'old-notifications.db'))
    from services.notifications.models import Base, Notification, Question, QuestionMessage
    from services.notifications.questions import migrate_questions
    engine, DB=database('notifications')
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TIMESTAMP NOT NULL)'))
        conn.execute(text('INSERT INTO schema_migrations VALUES (1, :at)'),{'at':now()})
        for table in Base.metadata.sorted_tables:
            if table.name not in ('questions','question_messages'):
                table.create(conn)
        conn.execute(Notification.__table__.insert().values(
            id='existing',user_id='owner',source_key='before-upgrade',category='schedule',
            data={'title':'Keep this'},announcement_id='',created_at=now(),expires_at=now(),
            popup_dismissed=False))
    migrate(engine,Base,(migrate_questions,))
    with DB() as db:
        assert db.get(Notification,'existing').data=={'title':'Keep this'}
        assert db.scalars(select(Question)).all()==[]
        assert db.scalars(select(QuestionMessage)).all()==[]
    assert {'questions','question_messages'}.issubset(inspect(engine).get_table_names())
    engine.dispose()
