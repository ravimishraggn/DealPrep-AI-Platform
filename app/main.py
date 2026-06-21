"""FastAPI application entrypoint.

Phase 1: DB init + tenant registration. Later phases extend the lifespan with
connector discovery and the APScheduler pipeline runner.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.db import init_db
from app.registry import discover
from app.routers import secrets, sources, tenants
from app.runner import shutdown_runner, start_runner


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    discover()  # import connectors/ so plugins self-register (ADR D2)
    start_runner()  # APScheduler picks up active manifests (ADR D6)
    yield
    shutdown_runner()


app = FastAPI(
    title="DealPrep Ingestion Onboarding Platform",
    version="0.1.0",
    summary="Self-service, multi-tenant data ingestion onboarding (Phases 1-4).",
    lifespan=lifespan,
)

app.include_router(tenants.router)
app.include_router(sources.router)
app.include_router(secrets.router)


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok"}
