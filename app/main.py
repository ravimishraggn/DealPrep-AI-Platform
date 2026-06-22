"""FastAPI application entrypoint.

Phase 1: DB init + tenant registration. Later phases extend the lifespan with
connector discovery and the APScheduler pipeline runner.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.db import init_db
from app.registry import discover
from app.routers import inspect, search, secrets, sources, tenants
from app.runner import shutdown_runner, start_runner
from pipeline.chunking.base import discover_chunkers
from pipeline.embedding.base import discover_embedders
from pipeline.extractors.registry import discover_extractors
from pipeline.indexing.graph.neo4j_client import close_driver
from pipeline.vectorstore.base import discover_vector_stores


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    discover()  # import connectors/ so plugins self-register (ADR 0001 D2)
    discover_extractors()  # import extractors/ so they self-register (ADR 0004)
    discover_chunkers()  # register chunking strategies (ADR 0009)
    discover_embedders()  # register embedding backends (ADR 0010)
    discover_vector_stores()  # register vector store backends (ADR 0011)
    start_runner()  # APScheduler picks up active manifests (ADR 0001 D6)
    yield
    shutdown_runner()
    close_driver()  # release the Neo4j driver if it was opened


app = FastAPI(
    title="DealPrep Ingestion Onboarding Platform",
    version="0.1.0",
    summary="Self-service, multi-tenant data ingestion onboarding (Phases 1-4).",
    lifespan=lifespan,
)

app.include_router(tenants.router)
app.include_router(sources.router)
app.include_router(secrets.router)
app.include_router(search.router)
app.include_router(inspect.router)


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok"}


# Minimal browser console (static, no build step) served by the app itself.
app.mount("/ui", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="ui")


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/ui/")
