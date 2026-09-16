from datetime import datetime
from sqlalchemy import String, DateTime, Integer, Boolean, JSON
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from services.common.core import uid, now

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = 'users'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    name: Mapped[str] = mapped_column(String(80))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default='student')
    group_role: Mapped[str] = mapped_column(String(20), default='none', server_default='none')
    status: Mapped[str] = mapped_column(String(20), default='pending')
    subgroup: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

class Session(Base):
    __tablename__ = 'sessions'
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    csrf: Mapped[str] = mapped_column(String(80))
    expires: Mapped[datetime] = mapped_column(DateTime, index=True)
    public_id: Mapped[str] = mapped_column(String(36), default=uid, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    device: Mapped[str] = mapped_column(String(200), default='')

class TwoFactor(Base):
    __tablename__ = 'two_factor'
    user_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    secret: Mapped[str] = mapped_column(String(256), default='')
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    pending_until: Mapped[datetime] = mapped_column(DateTime, default=now)
    last_step: Mapped[int] = mapped_column(Integer, default=-1)
    recovery_hashes: Mapped[list] = mapped_column(JSON, default=list)

class LoginChallenge(Base):
    __tablename__ = 'login_challenges'
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    expires: Mapped[datetime] = mapped_column(DateTime)
    attempts: Mapped[int] = mapped_column(Integer, default=0)

class Invitation(Base):
    __tablename__ = 'invitations'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires: Mapped[datetime] = mapped_column(DateTime)
    uses: Mapped[int] = mapped_column(Integer, default=0)
    max_uses: Mapped[int] = mapped_column(Integer, default=1)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)

class Reset(Base):
    __tablename__ = 'password_resets'
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36))
    expires: Mapped[datetime] = mapped_column(DateTime)

class Audit(Base):
    __tablename__ = 'audit'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    actor: Mapped[str] = mapped_column(String(80))
    action: Mapped[str] = mapped_column(String(80))
    target: Mapped[str] = mapped_column(String(80))
    at: Mapped[datetime] = mapped_column(DateTime, default=now)
    data: Mapped[dict] = mapped_column(JSON, default=dict)

class Gate(Base):
    __tablename__ = 'gates'
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[int] = mapped_column(Integer, default=0)
    until: Mapped[datetime] = mapped_column(DateTime, default=now)
