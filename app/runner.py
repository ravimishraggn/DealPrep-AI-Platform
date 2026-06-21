"""Generic pipeline runner (ADR D6, requirement 5).

APScheduler reads active manifests and runs the right connector's fetch() on its
configured interval. The runner only ever calls the BaseConnector interface and
the tenant-bound writer — it has no knowledge of any concrete connector.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.models import RunHistory, RunStatus, Source, SourceStatus
from app.registry import build_connector
from app.secrets import get_vault
from app.writer import TenantOutputWriter

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None
_SYNC_JOB_ID = "__sync_sources__"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware_utc(value: datetime | None) -> datetime | None:
    """SQLite drops tzinfo on round-trip, so cursors come back naive. Connectors
    are handed a tz-aware UTC datetime (or None) as a uniform contract."""
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _effective_interval(source: Source) -> int:
    """Honor the real poll_interval in prod; clamp to a short floor in dev demo
    mode so a scheduled run produces output during the curl walkthrough."""
    interval = source.poll_interval_seconds
    if settings.dev_mode:
        return max(min(interval, settings.dev_min_interval_seconds), 1)
    return max(interval, 1)


def run_source(source_id: str) -> None:
    """Execute one ingestion run for a source. Logged to run_history either way."""
    session = SessionLocal()
    try:
        source = session.get(Source, source_id)
        if source is None or source.status != SourceStatus.active:
            return

        started = _utcnow()
        run = RunHistory(
            source_id=source.id,
            tenant_id=source.tenant_id,
            status=RunStatus.failure,  # optimistic flip on success
            started_at=started,
        )
        try:
            # DI: build connector purely from registry + vault; runner stays generic.
            connector, _ = build_connector(source.connector_type, source.config, get_vault())
            records = connector.fetch(_as_aware_utc(source.last_cursor))

            output_path = None
            if records:
                writer = TenantOutputWriter(source.tenant_id)  # isolation enforced here
                output_path = writer.write(source.id, records)

            run.status = RunStatus.success
            run.record_count = len(records)
            run.output_path = output_path
            run.finished_at = _utcnow()

            source.last_run_at = run.finished_at
            source.last_run_status = RunStatus.success
            source.last_run_record_count = len(records)
            source.last_cursor = started  # next run pulls only newer records
            logger.info("source %s: ingested %d record(s)", source.id, len(records))
        except Exception as exc:  # noqa: BLE001 - one bad run must not kill the scheduler
            run.status = RunStatus.failure
            run.error = str(exc)
            run.finished_at = _utcnow()
            source.last_run_at = run.finished_at
            source.last_run_status = RunStatus.failure
            logger.exception("source %s run failed", source.id)

        session.add(run)
        session.commit()
    finally:
        session.close()


def _sync_jobs() -> None:
    """Pick up newly created/active sources and (re)schedule them.

    Runs periodically so sources added after startup begin running without a
    restart. Removed/paused sources have their jobs dropped.
    """
    if _scheduler is None:
        return
    session = SessionLocal()
    try:
        active = list(session.scalars(select(Source).where(Source.status == SourceStatus.active)))
        active_ids = {s.id for s in active}

        for source in active:
            job_id = f"source:{source.id}"
            interval = _effective_interval(source)
            if _scheduler.get_job(job_id) is None:
                _scheduler.add_job(
                    run_source, "interval", seconds=interval, id=job_id,
                    args=[source.id], next_run_time=_utcnow(), max_instances=1,
                    coalesce=True, replace_existing=True,
                )
                logger.info("scheduled source %s every %ss", source.id, interval)

        # Drop jobs for sources that are no longer active.
        for job in _scheduler.get_jobs():
            if job.id.startswith("source:") and job.id.split(":", 1)[1] not in active_ids:
                job.remove()
    finally:
        session.close()


def start_runner() -> None:
    global _scheduler
    if _scheduler is not None:
        return
    _scheduler = BackgroundScheduler(timezone="UTC")
    # Frequent sync in dev so new manifests start quickly; calmer in prod.
    sync_seconds = 5 if settings.dev_mode else 60
    _scheduler.add_job(
        _sync_jobs, "interval", seconds=sync_seconds, id=_SYNC_JOB_ID,
        next_run_time=_utcnow(), max_instances=1, coalesce=True,
    )
    _scheduler.start()
    logger.info("pipeline runner started (dev_mode=%s)", settings.dev_mode)


def shutdown_runner() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
