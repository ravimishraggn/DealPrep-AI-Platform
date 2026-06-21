"""FastAPI application entrypoint.

Phase 1: DB init + tenant registration. Later phases extend the lifespan with
connector discovery and the APScheduler pipeline runner.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.db import init_db
from app.routers import tenants


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # (Phase 2) discover_connectors() and (Phase 4) start scheduler will hook in here.
    yield
    # (Phase 4) scheduler shutdown will hook in here.


app = FastAPI(
    title="DealPrep Ingestion Onboarding Platform",
    version="0.1.0",
    summary="Self-service, multi-tenant data ingestion onboarding (Phases 1-4).",
    lifespan=lifespan,
)

app.include_router(tenants.router)


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok"}
