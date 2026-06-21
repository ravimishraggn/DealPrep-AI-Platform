"""Store-inspection + file-upload ingest endpoints for the test/console UI.

The ``/inspect/*`` endpoints expose *what is stored in each of the three data
stores* for a tenant, so the UI can render "show data" panels. ``/ingest/upload``
saves uploaded files to the tenant's dropzone and ensures a file_upload source
exists, so the scheduler auto-chains the full pipeline. Everything is
tenant-scoped — never cross-tenant.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_session
from app.identifiers import new_id
from app.models import RunHistory, RunStage, Source, StructuredRecordRow, Tenant
from pipeline.indexing.graph.neo4j_client import Neo4jClient
from pipeline.indexing.vector import VectorIndexer

router = APIRouter(prefix="/tenants/{tenant_id}", tags=["inspect"])


def _require_tenant(tenant_id: str, db: Session) -> None:
    """Raise 404 unless the tenant exists (no cross-tenant defaulting)."""
    if db.get(Tenant, tenant_id) is None:
        raise HTTPException(status_code=404, detail=f"Tenant '{tenant_id}' not found")


@router.get("/inspect/structured", summary="Postgres: stored structured records")
def inspect_structured(
    tenant_id: str, limit: int = 50, db: Session = Depends(get_session)
) -> dict[str, Any]:
    """Return the tenant's structured records (Postgres JSONB rows)."""
    _require_tenant(tenant_id, db)
    rows = db.scalars(
        select(StructuredRecordRow)
        .where(StructuredRecordRow.tenant_id == tenant_id)
        .order_by(StructuredRecordRow.created_at.desc())
        .limit(limit)
    ).all()
    return {
        "store": "postgres",
        "count": len(rows),
        "items": [
            {
                "record_type": r.record_type,
                "original_file_reference": r.original_file_reference,
                "document_date": str(r.document_date) if r.document_date else None,
                "fields": r.fields,
            }
            for r in rows
        ],
    }


@router.get("/inspect/vectors", summary="ChromaDB: stored chunks + embeddings")
def inspect_vectors(tenant_id: str, limit: int = 50, db: Session = Depends(get_session)) -> dict[str, Any]:
    """Return a sample of the tenant's vector-store chunks (ChromaDB)."""
    _require_tenant(tenant_id, db)
    data = VectorIndexer().peek(tenant_id, limit)
    return {"store": "chromadb", **data}


@router.get("/inspect/graph", summary="Neo4j: stored entities + relationships")
def inspect_graph(tenant_id: str, limit: int = 100, db: Session = Depends(get_session)) -> dict[str, Any]:
    """Return the tenant's graph entities and relationships (Neo4j)."""
    _require_tenant(tenant_id, db)
    data = Neo4jClient().list_graph(tenant_id, limit)
    return {"store": "neo4j", **data}


@router.get("/inspect/runs", summary="Pipeline run history + per-stage status")
def inspect_runs(tenant_id: str, limit: int = 20, db: Session = Depends(get_session)) -> dict[str, Any]:
    """Return recent ingestion runs with their per-stage outcomes."""
    _require_tenant(tenant_id, db)
    runs = db.scalars(
        select(RunHistory)
        .where(RunHistory.tenant_id == tenant_id)
        .order_by(RunHistory.started_at.desc())
        .limit(limit)
    ).all()
    out = []
    for run in runs:
        stages = db.scalars(select(RunStage).where(RunStage.run_id == run.id)).all()
        out.append(
            {
                "run_id": run.id,
                "status": run.status.value,
                "record_count": run.record_count,
                "started_at": run.started_at.isoformat() if run.started_at else None,
                "stages": [
                    {"stage": s.stage, "status": s.status.value, "item_count": s.item_count, "detail": s.detail}
                    for s in stages
                ],
            }
        )
    return {"count": len(out), "runs": out}


@router.post("/ingest/upload", summary="Upload files → auto-ingest through the pipeline")
async def ingest_upload(
    tenant_id: str,
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    """Save uploaded files to the tenant's dropzone and ensure a file_upload source.

    The scheduler then auto-chains extraction → indexing for the new files. Returns
    the source id and the saved filenames.
    """
    _require_tenant(tenant_id, db)
    dropzone = settings.data_dir_path / "uploads" / tenant_id
    dropzone.mkdir(parents=True, exist_ok=True)

    saved: list[str] = []
    for upload in files:
        name = (upload.filename or "file").replace("/", "_").replace("\\", "_")
        (dropzone / name).write_bytes(await upload.read())
        saved.append(name)

    # Reuse an existing file_upload source for this dropzone, or create one.
    existing = db.scalars(
        select(Source).where(Source.tenant_id == tenant_id, Source.connector_type == "file_upload")
    ).all()
    source = next((s for s in existing if s.config.get("directory") == str(dropzone)), None)
    if source is None:
        source = Source(
            id=new_id(), tenant_id=tenant_id, connector_type="file_upload",
            config={"directory": str(dropzone), "glob": "*"}, poll_interval_seconds=300,
        )
        db.add(source)
        db.commit()
        db.refresh(source)

    return {"source_id": source.id, "saved": saved, "message": "uploaded; pipeline will index shortly"}
