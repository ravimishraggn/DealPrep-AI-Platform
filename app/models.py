"""ORM models (ADR D8): tenants, sources, run_history."""
from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SourceStatus(str, enum.Enum):
    active = "active"
    paused = "paused"


class RunStatus(str, enum.Enum):
    success = "success"
    failure = "failure"


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    owner_email: Mapped[str] = mapped_column(String(320), nullable=False)
    use_case: Mapped[str] = mapped_column(Text, nullable=False)
    namespace: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    sources: Mapped[list["Source"]] = relationship(back_populates="tenant", cascade="all, delete-orphan")


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True, nullable=False)
    connector_type: Mapped[str] = mapped_column(String(80), nullable=False)
    # Validated manifest config (secret_ref inside, never a raw secret) — ADR D3/D5.
    config: Mapped[dict] = mapped_column(JSON, nullable=False)
    secret_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)
    poll_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    status: Mapped[SourceStatus] = mapped_column(Enum(SourceStatus), default=SourceStatus.active)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    # Last-run summary for the status API (ADR D8) — denormalized for cheap reads.
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_run_status: Mapped[RunStatus | None] = mapped_column(Enum(RunStatus), nullable=True)
    last_run_record_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_cursor: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    tenant: Mapped["Tenant"] = relationship(back_populates="sources")
    runs: Mapped[list["RunHistory"]] = relationship(back_populates="source", cascade="all, delete-orphan")


class RunHistory(Base):
    __tablename__ = "run_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), index=True, nullable=False)
    # tenant_id duplicated here so run logs are queryable/auditable per tenant
    # without a join (ADR D7 isolation is also visible in the audit trail).
    tenant_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    status: Mapped[RunStatus] = mapped_column(Enum(RunStatus), nullable=False)
    record_count: Mapped[int] = mapped_column(Integer, default=0)
    output_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    source: Mapped["Source"] = relationship(back_populates="runs")
