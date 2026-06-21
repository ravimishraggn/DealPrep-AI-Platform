"""SQLAlchemy engine, session factory, and declarative base.

Postgres is the platform relational store (ADR 0003). A SQLite URL is still
honoured so the pure-Python pipeline layers can be exercised without the full
docker stack, but the structured-records table uses Postgres-only types
(JSONB + tsvector) and therefore requires Postgres in any real run.
"""
from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

_is_sqlite = settings.database_url.startswith("sqlite")
# check_same_thread only matters for SQLite + the scheduler thread.
_connect_args = {"check_same_thread": False} if _is_sqlite else {}

engine = create_engine(
    settings.database_url,
    connect_args=_connect_args,
    pool_pre_ping=True,  # drop dead connections (Postgres) before handing them out
    future=True,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


class Base(DeclarativeBase):
    """Declarative base shared by every ORM model in the platform."""


def init_db() -> None:
    """Create all tables if absent.

    Imports ``app.models`` for its mapper-registration side effect, then issues
    ``CREATE TABLE IF NOT EXISTS`` for every mapped model on the configured engine.
    """
    from app import models  # noqa: F401  (side-effect: registers mappers)

    Base.metadata.create_all(bind=engine)


def get_session() -> Iterator[Session]:
    """FastAPI dependency yielding one Session per request, closed on exit."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
