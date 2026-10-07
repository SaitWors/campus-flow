from datetime import datetime
from sqlalchemy import String, DateTime, Integer, Boolean, JSON, Text, ForeignKey
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

class Subject(Base):
    __tablename__='subjects'
    key:Mapped[str]=mapped_column(String(240),primary_key=True)
    title:Mapped[str]=mapped_column(String(120))
    title_en:Mapped[str]=mapped_column(String(120),default='')
    teacher:Mapped[str]=mapped_column(String(100),default='')
    requirements:Mapped[str]=mapped_column(Text,default='')
    links:Mapped[list]=mapped_column(JSON,default=list)
    revision:Mapped[int]=mapped_column(Integer,default=1)

class Assignment(Base):
    __tablename__='assignments'
    id:Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    subject_key:Mapped[str]=mapped_column(ForeignKey('subjects.key'),index=True)
    title:Mapped[str]=mapped_column(String(160))
    description:Mapped[str]=mapped_column(Text,default='')
    due_at:Mapped[datetime|None]=mapped_column(DateTime,nullable=True,index=True)
    subgroup:Mapped[int]=mapped_column(Integer,default=0)
    material_url:Mapped[str]=mapped_column(String(500),default='')
    revision:Mapped[int]=mapped_column(Integer,default=1)
    archived:Mapped[bool]=mapped_column(Boolean,default=False,index=True)
    created_at:Mapped[datetime]=mapped_column(DateTime,default=now)
    updated_at:Mapped[datetime]=mapped_column(DateTime,default=now)

class AssignmentProgress(Base):
    __tablename__='assignment_progress'
    assignment_id:Mapped[str]=mapped_column(ForeignKey('assignments.id'),primary_key=True)
    user_id:Mapped[str]=mapped_column(String(36),primary_key=True)
    status:Mapped[str]=mapped_column(String(20),default='not_started')
    revision:Mapped[int]=mapped_column(Integer,default=1)
    updated_at:Mapped[datetime]=mapped_column(DateTime,default=now)

class Material(Base):
    __tablename__='materials'
    id:Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    subject_key:Mapped[str]=mapped_column(ForeignKey('subjects.key'),index=True)
    title:Mapped[str]=mapped_column(String(160))
    description:Mapped[str]=mapped_column(Text,default='')
    category:Mapped[str]=mapped_column(String(20),default='other')
    original_filename:Mapped[str]=mapped_column(String(240))
    mime_type:Mapped[str]=mapped_column(String(100))
    size_bytes:Mapped[int]=mapped_column(Integer)
    sha256:Mapped[str]=mapped_column(String(64))
    search_text:Mapped[str]=mapped_column(Text)
    uploader_name:Mapped[str]=mapped_column(String(80))
    created_at:Mapped[datetime]=mapped_column(DateTime,default=now,index=True)
    revision:Mapped[int]=mapped_column(Integer,default=1)

class MaterialStorage(Base):
    """A single row serializes final publishes and removals across processes."""
    __tablename__='material_storage'
    id:Mapped[int]=mapped_column(Integer,primary_key=True)

class TimePresets(Base):
    __tablename__='time_presets'
    id:Mapped[int]=mapped_column(Integer,primary_key=True,default=1)
    data:Mapped[list]=mapped_column(JSON)
    revision:Mapped[int]=mapped_column(Integer,default=1)
