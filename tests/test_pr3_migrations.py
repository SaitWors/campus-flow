from datetime import timedelta
from types import SimpleNamespace
from sqlalchemy import MetaData, text, select
from services.common.core import database, migrate, now


def test_v2_catalog_upgrade_preserves_lessons_and_backfills(tmp_path,monkeypatch):
    monkeypatch.setenv('DATABASE_URL','sqlite:///'+str(tmp_path/'old-schedule.db'))
    from services.schedule.models import Base, Rule, Occurrence, Subject, TimePresets
    from services.schedule.catalog import migrate_catalog, DEFAULT_PRESETS
    engine,DB=database('schedule');legacy=MetaData()
    for name,table in Base.metadata.tables.items():
        if name not in ('subjects','time_presets'):table.to_metadata(legacy)
    migrate(engine,SimpleNamespace(metadata=legacy),(lambda _:None,))
    with DB.begin() as db:
        db.add(Rule(id='old-rule',data={'title':'Мат. анализ','title_en':'Analysis'},revision=8))
        db.add(Occurrence(id='old-occurrence',rule_id='old-rule',original_date='2026-09-01',date='2026-09-01',data={'title':'МАТ. АНАЛИЗ','title_en':'Analysis','start':'10:13','end':'11:49'},revision=12,overridden=True))
    migrate(engine,Base,(lambda _:None,migrate_catalog));migrate(engine,Base,(lambda _:None,migrate_catalog))
    with DB() as db:
        assert len(db.scalars(select(Subject)).all())==1
        assert db.get(TimePresets,1).data==DEFAULT_PRESETS
        assert db.get(Rule,'old-rule').revision==8
        old=db.get(Occurrence,'old-occurrence')
        assert old.revision==12 and old.overridden and old.data['start']=='10:13'
        assert db.scalar(text('SELECT MAX(version) FROM schema_migrations'))==3
    engine.dispose()


def test_v2_session_upgrade_preserves_logins_and_hashes(tmp_path,monkeypatch):
    monkeypatch.setenv('DATABASE_URL','sqlite:///'+str(tmp_path/'old-auth.db'))
    from services.auth.models import Base, Session, TwoFactor
    from services.auth.security import migrate_security
    engine,DB=database('auth')
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TIMESTAMP NOT NULL)'))
        conn.execute(text('INSERT INTO schema_migrations VALUES (2,:at)'),{'at':now()})
        conn.execute(text('CREATE TABLE sessions(token_hash VARCHAR(64) PRIMARY KEY,user_id VARCHAR(36),csrf VARCHAR(80),expires TIMESTAMP)'))
        for i in range(2):conn.execute(text('INSERT INTO sessions VALUES (:token,:user,:csrf,:expires)'),{'token':str(i)*64,'user':'existing-user','csrf':'keep-csrf','expires':now()+timedelta(days=2)})
    migrate(engine,Base,(lambda _:None,migrate_security));migrate(engine,Base,(lambda _:None,migrate_security))
    with DB() as db:
        sessions=db.scalars(select(Session)).all()
        assert len(sessions)==2 and len({s.public_id for s in sessions})==2
        assert all(s.csrf=='keep-csrf' and s.created_at and s.expires>now() for s in sessions)
        assert not db.scalars(select(TwoFactor)).all()
        assert db.scalar(text('SELECT MAX(version) FROM schema_migrations'))==3
    engine.dispose()
