"""Per-tenant pipeline profile API (ADR 0012).

Lets a team choose its chunking / embedding / vector-store strategies. Choices are
validated against the registries (must be implemented, never a stub). Absent
profile = platform defaults.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Tenant, TenantPipelineProfile
from app.profiles import resolve_profile, validate_profile

router = APIRouter(prefix="/tenants/{tenant_id}", tags=["profile"])


class ProfileIn(BaseModel):
    """Desired pipeline strategy selections for a tenant."""

    chunking: str = Field(description="Chunking strategy name (see /capabilities)")
    embedding: str = Field(description="Embedding backend name (see /capabilities)")
    vector_store: str = Field(description="Vector store backend name (see /capabilities)")


class ProfileOut(BaseModel):
    """A tenant's effective pipeline profile."""

    tenant_id: str
    chunking: str
    embedding: str
    vector_store: str
    is_default: bool = Field(description="True if no tenant override (platform defaults)")


def _require_tenant(tenant_id: str, db: Session) -> None:
    if db.get(Tenant, tenant_id) is None:
        raise HTTPException(status_code=404, detail=f"Tenant '{tenant_id}' not found")


@router.get("/profile", response_model=ProfileOut, summary="Get a tenant's pipeline profile")
def get_profile(tenant_id: str, db: Session = Depends(get_session)) -> ProfileOut:
    """Return the tenant's effective profile (override, or platform defaults)."""
    _require_tenant(tenant_id, db)
    p = resolve_profile(tenant_id, db)
    return ProfileOut(tenant_id=tenant_id, chunking=p.chunking, embedding=p.embedding,
                      vector_store=p.vector_store, is_default=p.is_default)


@router.put("/profile", response_model=ProfileOut, summary="Set a tenant's pipeline profile")
def set_profile(tenant_id: str, payload: ProfileIn, db: Session = Depends(get_session)) -> ProfileOut:
    """Validate and store a tenant's strategy choices.

    A choice that is unknown or a not-yet-implemented stub is rejected (422).
    Changing embedding/vector-store/chunking implies a reindex (see ADRs 0009-0011);
    this endpoint records the choice — it does not migrate existing data.
    """
    _require_tenant(tenant_id, db)
    try:
        validate_profile(payload.chunking, payload.embedding, payload.vector_store)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    row = db.get(TenantPipelineProfile, tenant_id)
    if row is None:
        row = TenantPipelineProfile(tenant_id=tenant_id, chunking=payload.chunking,
                                    embedding=payload.embedding, vector_store=payload.vector_store)
        db.add(row)
    else:
        row.chunking, row.embedding, row.vector_store = payload.chunking, payload.embedding, payload.vector_store
    db.commit()
    return ProfileOut(tenant_id=tenant_id, chunking=payload.chunking, embedding=payload.embedding,
                      vector_store=payload.vector_store, is_default=False)
