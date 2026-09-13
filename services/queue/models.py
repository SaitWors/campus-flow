from datetime import datetime
from sqlalchemy import String, DateTime, Integer, JSON, UniqueConstraint, Index, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from services.common.core import uid, now

class Base(DeclarativeBase):
    pass

class Queue(Base):
    __tablename__='queues'
    id:Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    occurrence_id:Mapped[str]=mapped_column(String(36),unique=True)
    state:Mapped[str]=mapped_column(String(20),default='draft')
    capacity:Mapped[int]=mapped_column(Integer,default=30)
    minutes_per_student:Mapped[int]=mapped_column(Integer,default=7)
    opens_before_hours:Mapped[int]=mapped_column(Integer,default=24)
    last_ticket:Mapped[int]=mapped_column(Integer,default=0)
    revision:Mapped[int]=mapped_column(Integer,default=1)
    lesson_revision:Mapped[int]=mapped_column(Integer,default=0)
    blocked_reason:Mapped[str]=mapped_column(String(60),default='')
    updated_at:Mapped[datetime]=mapped_column(DateTime,default=now)

class Entry(Base):
    __tablename__='entries'
    id:Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    queue_id:Mapped[str]=mapped_column(String(36),index=True)
    user_id:Mapped[str]=mapped_column(String(36),index=True)
    name:Mapped[str]=mapped_column(String(80))
    ticket:Mapped[int]=mapped_column(Integer)
    task:Mapped[str]=mapped_column(String(120))
    status:Mapped[str]=mapped_column(String(20),default='waiting')
    joined_at:Mapped[datetime]=mapped_column(DateTime,default=now)
    called_at:Mapped[datetime|None]=mapped_column(DateTime,nullable=True)
    ended_at:Mapped[datetime|None]=mapped_column(DateTime,nullable=True)
    __table_args__=(
        UniqueConstraint('queue_id','ticket'),
        Index('one_active_entry', 'queue_id','user_id',unique=True,postgresql_where=text("status IN ('waiting','called')"),sqlite_where=text("status IN ('waiting','called')")),
        Index('one_called_per_queue','queue_id',unique=True,postgresql_where=text("status = 'called'"),sqlite_where=text("status = 'called'")),
        Index('one_called_per_student','user_id',unique=True,postgresql_where=text("status = 'called'"),sqlite_where=text("status = 'called'")),
    )

class Command(Base):
    __tablename__='commands'
    key:Mapped[str]=mapped_column(String(200),primary_key=True)
    fingerprint:Mapped[str]=mapped_column(String(64))
    at:Mapped[datetime]=mapped_column(DateTime,default=now)

class Audit(Base):
    __tablename__='audit'
    id:Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    queue_id:Mapped[str]=mapped_column(String(36),index=True)
    actor:Mapped[str]=mapped_column(String(80))
    action:Mapped[str]=mapped_column(String(80))
    target:Mapped[str]=mapped_column(String(80))
    at:Mapped[datetime]=mapped_column(DateTime,default=now)
    data:Mapped[dict]=mapped_column(JSON,default=dict)

class Cursor(Base):
    __tablename__='cursors'
    id:Mapped[int]=mapped_column(Integer,primary_key=True,default=1)
    seq:Mapped[int]=mapped_column(Integer,default=0)
