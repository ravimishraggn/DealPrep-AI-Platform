"""Unified retrieval API (requirement 8).

``POST /tenants/{tenant_id}/search`` returns vector, structured, and graph results
as three separately-labeled sets, each with source traceability. Response models
are fully typed so the auto-generated OpenAPI docs at ``/docs`` are useful.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Tenant
from app.profiles import resolve_profile
from app.search_service import get_search

router = APIRouter(prefix="/tenants/{tenant_id}", tags=["search"])


class SearchRequest(BaseModel):
    """Query payload for unified search."""

    query: str = Field(min_length=1, description="Natural-language or keyword query")
    k: int = Field(default=5, ge=1, le=50, description="Max results per engine")
    record_type: str | None = Field(
        default=None, description="Optional structured-record type filter (e.g. 'pdf_table_row')"
    )


class VectorHit(BaseModel):
    """A semantic match from the vector store, with traceability metadata."""

    text: str
    score: float = Field(description="Cosine similarity (1.0 = identical)")
    metadata: dict[str, Any]


class StructuredHit(BaseModel):
    """A keyword/structured match from Postgres, with traceability metadata."""

    fields: dict[str, Any]
    score: float = Field(description="Postgres ts_rank score")
    metadata: dict[str, Any]


class GraphHit(BaseModel):
    """A 1-hop relationship from the knowledge graph, with traceability."""

    subject: str
    relationship: str
    object: str
    object_type: str | None = None
    source_id: str | None = None
    file_ref: str | None = None


class SearchResponse(BaseModel):
    """Three separately-labeled result sets — no merging/ranking at this stage."""

    tenant_id: str
    query: str
    vector: list[VectorHit]
    structured: list[StructuredHit]
    graph: list[GraphHit]
    warnings: list[str] = Field(
        default_factory=list, description="Per-engine degradation notes (e.g. a store offline)"
    )


@router.post("/search", response_model=SearchResponse, summary="Unified vector+structured+graph search")
def unified_search(
    tenant_id: str,
    payload: SearchRequest,
    db: Session = Depends(get_session),
) -> SearchResponse:
    """Search a tenant's indexed data across all three engines in parallel.

    The path ``tenant_id`` scopes every engine; a request for an unknown tenant is
    rejected (404) rather than searching across tenants. Vector, structured, and
    graph results are returned separately, each traceable to its source document.

    Args:
        tenant_id: The tenant whose indexes to search (required, enforced).
        payload: The query, result count, and optional structured-type filter.

    Returns:
        A ``SearchResponse`` with three labeled result sets and any warnings.
    """
    if db.get(Tenant, tenant_id) is None:
        raise HTTPException(status_code=404, detail=f"Tenant '{tenant_id}' not found")

    # Query the vector store with the tenant's chosen embedder+store (must match index time).
    profile = resolve_profile(tenant_id, db)
    results = get_search().search(
        tenant_id, payload.query, payload.k, payload.record_type,
        embedding=profile.embedding, vector_store=profile.vector_store,
    )
    return SearchResponse(
        tenant_id=tenant_id,
        query=payload.query,
        vector=results["vector"],
        structured=results["structured"],
        graph=results["graph"],
        warnings=results["warnings"],
    )
