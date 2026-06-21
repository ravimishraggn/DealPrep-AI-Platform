"""ORM models for the platform relational store (Postgres — ADR 0003).

Holds operational/metadata tables (tenants, sources, run_history) and the
structured business-records table (``structured_records``) which uses JSONB for
per-tenant schema flexibility plus a generated ``tsvector`` column for full-text
search. Vector and graph data live in ChromaDB and Neo4j respectively.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Computed,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

# JSONB on Postgres, plain JSON on SQLite (so pure-Python tests can still map).
_JSONB = JSONB().with_variant(JSON(), "sqlite")


def _utcnow() -> datetime:
    """Current timezone-aware UTC time (used as a column default factory)."""
    return datetime.now(timezone.utc)


def _new_uuid() -> str:
    """Generate a random UUID4 string for primary keys."""
    return str(uuid.uuid4())


class SourceStatus(str, enum.Enum):
    active = "active"
    paused = "paused"


class RunStatus(str, enum.Enum):
    success = "success"
    failure = "failure"


class StageStatus(str, enum.Enum):
    """Outcome of a single pipeline stage within one run (ADR 0007)."""

    success = "success"
    failure = "failure"
    skipped = "skipped"


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
    stages: Mapped[list["RunStage"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class RunStage(Base):
    """Per-stage outcome of one pipeline run (extract / process / index_*).

    Each pipeline stage (ADR 0007 fan-out) appends a row here so operators can
    see exactly where a run succeeded, was skipped, or failed — tagged by stage
    name and tenant for traceability.
    """

    __tablename__ = "run_stages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("run_history.id"), index=True, nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    stage: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[StageStatus] = mapped_column(Enum(StageStatus), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    item_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    run: Mapped["RunHistory"] = relationship(back_populates="stages")


class StructuredRecordRow(Base):
    """A structured business record in Postgres (ADR 0003).

    The flexible, per-tenant field set lives in the ``fields`` JSONB column; the
    standard isolation/traceability columns (tenant_id, source_id,
    document_date, original_file_reference) are first-class columns. ``search_tsv``
    is a generated tsvector over ``search_text`` (a concatenation of text-bearing
    fields) enabling Postgres full-text/keyword search alongside JSONB filtering.
    """

    __tablename__ = "structured_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    # Mandatory tenant tag — every query MUST filter on this (ADR 0003 isolation).
    tenant_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    source_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    record_type: Mapped[str] = mapped_column(String(80), nullable=False, default="record")
    document_date: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    original_file_reference: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Per-tenant flexible field set.
    fields: Mapped[dict] = mapped_column(_JSONB, nullable=False, default=dict)

    # Concatenated text-bearing field values, and its generated FTS vector.
    search_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    search_tsv: Mapped[str | None] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('english', coalesce(search_text, ''))", persisted=True),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (
        # GIN index makes tsvector full-text search fast.
        Index("ix_structured_records_tsv", "search_tsv", postgresql_using="gin"),
        # Composite index for the common (tenant, type) filter.
        Index("ix_structured_records_tenant_type", "tenant_id", "record_type"),
    )
