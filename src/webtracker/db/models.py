"""SQLAlchemy ORM models. Schema changes must be accompanied by an Alembic migration."""

from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class UTCDateTime(TypeDecorator):
    """Stores naive UTC (SQLite has no timezone support) and returns aware UTC."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetimes are not allowed; use UTC-aware values")
        return value.astimezone(dt.timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return value.replace(tzinfo=dt.timezone.utc)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class ResearchRun(Base):
    __tablename__ = "research_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)
    finished_at: Mapped[Optional[dt.datetime]] = mapped_column(UTCDateTime())
    status: Mapped[str] = mapped_column(String(16), default="running")  # running|succeeded|partial|failed
    trigger: Mapped[str] = mapped_column(String(16), default="manual")  # timer|manual|telegram
    agent_backend: Mapped[str] = mapped_column(String(32))
    config_name: Mapped[str] = mapped_column(String(100))
    config_hash: Mapped[str] = mapped_column(String(32), default="")
    records_returned: Mapped[int] = mapped_column(Integer, default=0)
    records_new: Mapped[int] = mapped_column(Integer, default=0)
    records_updated: Mapped[int] = mapped_column(Integer, default=0)
    records_invalid: Mapped[int] = mapped_column(Integer, default=0)
    notifications_sent: Mapped[int] = mapped_column(Integer, default=0)
    error_summary: Mapped[Optional[str]] = mapped_column(Text)

    errors: Mapped[List["AgentError"]] = relationship(back_populates="run", cascade="all, delete-orphan")


class Record(Base):
    __tablename__ = "records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(500))
    source_name: Mapped[str] = mapped_column(String(200))
    url: Mapped[str] = mapped_column(String(2048))
    url_fingerprint: Mapped[str] = mapped_column(String(64), unique=True)
    content_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    metadata_json: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    relevance_score: Mapped[float] = mapped_column(Float, default=0.0)
    explanation: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="new", index=True)
    discovered_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
    last_seen_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)
    notified_at: Mapped[Optional[dt.datetime]] = mapped_column(UTCDateTime())
    first_run_id: Mapped[Optional[int]] = mapped_column(ForeignKey("research_runs.id", ondelete="SET NULL"))

    sources: Mapped[List["RecordSource"]] = relationship(back_populates="record", cascade="all, delete-orphan")
    scores: Mapped[List["RecordScore"]] = relationship(back_populates="record", cascade="all, delete-orphan")
    status_events: Mapped[List["StatusEvent"]] = relationship(
        back_populates="record", cascade="all, delete-orphan", order_by="StatusEvent.id"
    )


class RecordSource(Base):
    __tablename__ = "record_sources"
    __table_args__ = (UniqueConstraint("record_id", "url_fingerprint", name="uq_record_sources_record_url"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    record_id: Mapped[int] = mapped_column(ForeignKey("records.id", ondelete="CASCADE"), index=True)
    source_name: Mapped[str] = mapped_column(String(200))
    url: Mapped[str] = mapped_column(String(2048))
    url_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    first_seen_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)
    last_seen_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)
    run_id: Mapped[Optional[int]] = mapped_column(ForeignKey("research_runs.id", ondelete="SET NULL"))

    record: Mapped[Record] = relationship(back_populates="sources")


class RecordScore(Base):
    __tablename__ = "record_scores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    record_id: Mapped[int] = mapped_column(ForeignKey("records.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[Optional[int]] = mapped_column(ForeignKey("research_runs.id", ondelete="SET NULL"))
    score: Mapped[float] = mapped_column(Float)
    agent_score: Mapped[float] = mapped_column(Float)
    criteria_json: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    explanation: Mapped[str] = mapped_column(Text, default="")
    scored_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)

    record: Mapped[Record] = relationship(back_populates="scores")


class StatusEvent(Base):
    __tablename__ = "status_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    record_id: Mapped[int] = mapped_column(ForeignKey("records.id", ondelete="CASCADE"), index=True)
    old_status: Mapped[Optional[str]] = mapped_column(String(32))
    new_status: Mapped[str] = mapped_column(String(32))
    actor: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)

    record: Mapped[Record] = relationship(back_populates="status_events")


class TrackedAction(Base):
    """Audit trail of actions taken on records (view, save, dismiss, notify...)."""

    __tablename__ = "tracked_actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    record_id: Mapped[Optional[int]] = mapped_column(ForeignKey("records.id", ondelete="CASCADE"), index=True)
    action: Mapped[str] = mapped_column(String(32))
    actor: Mapped[str] = mapped_column(String(64))
    details_json: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)


class AgentError(Base):
    __tablename__ = "agent_errors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[Optional[int]] = mapped_column(ForeignKey("research_runs.id", ondelete="CASCADE"), index=True)
    stage: Mapped[str] = mapped_column(String(32))  # agent|parse|validation|storage|notify
    error_type: Mapped[str] = mapped_column(String(100))
    message: Mapped[str] = mapped_column(Text)
    raw_excerpt: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)

    run: Mapped[Optional[ResearchRun]] = relationship(back_populates="errors")


class UserFeedback(Base):
    __tablename__ = "user_feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    record_id: Mapped[int] = mapped_column(ForeignKey("records.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(String(64))
    feedback: Mapped[str] = mapped_column(String(32))  # relevant|not_relevant
    comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)


Index("ix_records_status_score", Record.status, Record.relevance_score)
