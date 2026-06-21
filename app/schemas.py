"""API request/response models (transport layer, distinct from connector configs)."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field


# ---- Tenants (Phase 1) -------------------------------------------------------
class TenantCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    owner_email: EmailStr
    use_case: str = Field(min_length=1)


class TenantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    owner_email: str
    use_case: str
    namespace: str
    created_at: datetime


# ---- Sources / manifests (Phases 3-4) ---------------------------------------
class SourceCreate(BaseModel):
    connector_type: str = Field(min_length=1, description="Registered connector key, e.g. 'rest_api'")
    config: dict[str, Any] = Field(description="Connector-specific config; validated against its schema")


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: str
    record_count: int
    output_path: str | None
    error: str | None
    started_at: datetime
    finished_at: datetime | None


class SourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    connector_type: str
    config: dict[str, Any]
    secret_ref: str | None
    poll_interval_seconds: int
    status: str
    created_at: datetime
    last_run_at: datetime | None
    last_run_status: str | None
    last_run_record_count: int | None
