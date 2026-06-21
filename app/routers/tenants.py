"""Tenant registration API (Phase 1, requirement 1)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_session
from app.identifiers import make_namespace, new_id
from app.models import Tenant
from app.schemas import TenantCreate, TenantOut

router = APIRouter(prefix="/tenants", tags=["tenants"])


@router.post("", response_model=TenantOut, status_code=status.HTTP_201_CREATED)
def register_tenant(payload: TenantCreate, db: Session = Depends(get_session)) -> Tenant:
    """Register a team once; returns its tenant_id and isolated namespace."""
    tenant = Tenant(
        id=new_id(),
        name=payload.name,
        owner_email=str(payload.owner_email),
        use_case=payload.use_case,
        namespace=make_namespace(payload.name),
    )
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    return tenant


@router.get("/{tenant_id}", response_model=TenantOut)
def get_tenant(tenant_id: str, db: Session = Depends(get_session)) -> Tenant:
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail=f"Tenant '{tenant_id}' not found")
    return tenant
