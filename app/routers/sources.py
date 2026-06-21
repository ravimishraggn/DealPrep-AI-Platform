"""Source manifest submission + status APIs (requirements 3 & 6)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.identifiers import new_id
from app.models import RunHistory, Source, Tenant
from app.registry import build_connector
from app.schemas import RunOut, SourceCreate, SourceOut
from app.secrets import SecretsVault, get_vault
from connectors.base import ConfigValidationError, ConnectorError

router = APIRouter(prefix="/tenants/{tenant_id}/sources", tags=["sources"])


def _require_tenant(tenant_id: str, db: Session) -> Tenant:
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail=f"Tenant '{tenant_id}' not found")
    return tenant


@router.post("", response_model=SourceOut, status_code=status.HTTP_201_CREATED)
def create_source(
    tenant_id: str,
    payload: SourceCreate,
    db: Session = Depends(get_session),
    vault: SecretsVault = Depends(get_vault),
) -> Source:
    """Submit a manifest: validate against the connector schema, dry-run
    test_connection(), and only persist if the dry-run succeeds (ADR D4)."""
    _require_tenant(tenant_id, db)

    # 1) Validate config against the connector's own schema (+ unknown-type check).
    try:
        connector, config = build_connector(payload.connector_type, payload.config, vault)
    except KeyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ConfigValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail={"message": "connector config validation failed", "errors": exc.errors},
        ) from exc

    # 2) Dry-run before saving — clear error if the connection can't be made.
    try:
        connector.test_connection()
    except ConnectorError as exc:
        raise HTTPException(
            status_code=422,
            detail={"message": "dry-run test_connection failed", "error": str(exc)},
        ) from exc

    # 3) Persist as an active source.
    source = Source(
        id=new_id(),
        tenant_id=tenant_id,
        connector_type=payload.connector_type,
        config=payload.config,
        secret_ref=getattr(config, "secret_ref", None),
        poll_interval_seconds=getattr(config, "poll_interval_seconds", 300),
    )
    db.add(source)
    db.commit()
    db.refresh(source)
    return source


@router.get("", response_model=list[SourceOut])
def list_sources(tenant_id: str, db: Session = Depends(get_session)) -> list[Source]:
    """List a team's configured sources and their last-run status (requirement 6)."""
    _require_tenant(tenant_id, db)
    return list(db.scalars(select(Source).where(Source.tenant_id == tenant_id)))


@router.get("/{source_id}/runs", response_model=list[RunOut])
def list_runs(
    tenant_id: str, source_id: str, db: Session = Depends(get_session)
) -> list[RunHistory]:
    """Run history for a source (requirement 6)."""
    _require_tenant(tenant_id, db)
    source = db.get(Source, source_id)
    # Tenant-scoped lookup: a source must belong to the path tenant.
    if source is None or source.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail=f"Source '{source_id}' not found for tenant")
    return list(
        db.scalars(
            select(RunHistory)
            .where(RunHistory.source_id == source_id)
            .order_by(RunHistory.started_at.desc())
        )
    )
