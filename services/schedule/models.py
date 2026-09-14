from datetime import datetime
from sqlalchemy import String, DateTime, Integer, Boolean, JSON
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from services.common.core import now, uid

class Base(DeclarativeBase):
    pass

class Settings(Base):
    __tablename__='settings'
    id:Mapped[int]=mapped_column(Integer,primary_key=True,default=1)
    data:Mapped[dict]=mapped_column(JSON)
    revision:Mapped[int]=mapped_column(Integer,default=1)

class Rule(Base):
    __tablename__='rules'
    id:Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    data:Mapped[dict]=mapped_column(JSON)
    revision:Mapped[int]=mapped_column(Integer,default=1)
    archived:Mapped[bool]=mapped_column(Boolean,default=False)

class Occurrence(Base):
    __tablename__='occurrences'
    id:Mapped[str]=mapped_column(String(36),primary_key=True)
    rule_id:Mapped[str]=mapped_column(String(36),index=True)
    original_date:Mapped[str]=mapped_column(String(10),index=True)
    date:Mapped[str]=mapped_column(String(10),index=True)
    data:Mapped[dict]=mapped_column(JSON)
    overridden:Mapped[bool]=mapped_column(Boolean,default=False)
    revision:Mapped[int]=mapped_column(Integer,default=1)
    updated_at:Mapped[datetime]=mapped_column(DateTime,default=now)

class Audit(Base):
    __tablename__='audit'
    id:Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    actor:Mapped[str]=mapped_column(String(80))
    action:Mapped[str]=mapped_column(String(80))
    target:Mapped[str]=mapped_column(String(80))
    at:Mapped[datetime]=mapped_column(DateTime,default=now)
    data:Mapped[dict]=mapped_column(JSON,default=dict)

class Event(Base):
    __tablename__='events'
    seq:Mapped[int]=mapped_column(Integer,primary_key=True,autoincrement=True)
    id:Mapped[str]=mapped_column(String(36),unique=True,default=uid)
    type:Mapped[str]=mapped_column(String(80))
    data:Mapped[dict]=mapped_column(JSON)
    at:Mapped[datetime]=mapped_column(DateTime,default=now)

class TitleTranslation(Base):
    __tablename__='title_translations'
    title:Mapped[str]=mapped_column(String(120),primary_key=True)
    translated:Mapped[str]=mapped_column(String(120),default='')
    updated_at:Mapped[datetime]=mapped_column(DateTime,default=now)
