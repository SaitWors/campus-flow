"""Notification data is owned only by this service. Private keys and capabilities never leave its API."""
from datetime import datetime
from sqlalchemy import Boolean, DateTime, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from services.common.core import now, uid


class Base(DeclarativeBase):
    pass


class Guard(Base):
    __tablename__ = 'guards'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)


class Preference(Base):
    __tablename__ = 'preferences'
    user_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    data: Mapped[dict] = mapped_column(JSON)
    revision: Mapped[int] = mapped_column(Integer, default=1)


class Announcement(Base):
    __tablename__ = 'announcements'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    request_key: Mapped[str] = mapped_column(String(140), unique=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    actor_id: Mapped[str] = mapped_column(String(36))
    actor_name: Mapped[str] = mapped_column(String(80))
    data: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    withdrawn: Mapped[bool] = mapped_column(Boolean, default=False)


class Notification(Base):
    __tablename__ = 'notifications'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    source_key: Mapped[str] = mapped_column(String(160))
    category: Mapped[str] = mapped_column(String(24))
    data: Mapped[dict] = mapped_column(JSON)
    announcement_id: Mapped[str] = mapped_column(String(36), default='', index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    popup_dismissed: Mapped[bool] = mapped_column(Boolean, default=False)
    __table_args__ = (UniqueConstraint('user_id', 'source_key'),)


class Subscription(Base):
    __tablename__ = 'subscriptions'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    endpoint_hash: Mapped[str] = mapped_column(String(64), unique=True)
    data: Mapped[dict] = mapped_column(JSON)
    session_hash: Mapped[str] = mapped_column(String(64))
    label: Mapped[str] = mapped_column(String(60))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Delivery(Base):
    __tablename__ = 'deliveries'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    notification_id: Mapped[str] = mapped_column(String(36), index=True)
    subscription_id: Mapped[str] = mapped_column(String(36), index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    due_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    state: Mapped[str] = mapped_column(String(16), default='pending')
    __table_args__ = (UniqueConstraint('notification_id', 'subscription_id'),)


class Cursor(Base):
    __tablename__ = 'cursors'
    source: Mapped[str] = mapped_column(String(24), primary_key=True)
    seq: Mapped[int] = mapped_column(Integer, default=0)


class Audit(Base):
    __tablename__ = 'audit'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    actor: Mapped[str] = mapped_column(String(80))
    action: Mapped[str] = mapped_column(String(32))
    target: Mapped[str] = mapped_column(String(36))
    at: Mapped[datetime] = mapped_column(DateTime, default=now)


class PushKey(Base):
    __tablename__ = 'push_keys'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    private_key: Mapped[str] = mapped_column(String(500))
    public_key: Mapped[str] = mapped_column(String(100))
