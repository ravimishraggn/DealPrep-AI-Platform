"""Minimal secrets API (requirement 4 support).

Lets a team stash a secret value under a name, so manifests can reference it by
`secret_ref`. Values are write-only here — they are never returned. Backed by the
swappable SecretsVault (in-memory stub for V1).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field

from app.secrets import SecretsVault, get_vault

router = APIRouter(prefix="/secrets", tags=["secrets"])


class SecretIn(BaseModel):
    ref: str = Field(min_length=1, description="Secret name to reference from manifests")
    value: str = Field(min_length=1)


@router.post("", status_code=status.HTTP_201_CREATED)
def put_secret(payload: SecretIn, vault: SecretsVault = Depends(get_vault)) -> dict[str, str]:
    vault.set_secret(payload.ref, payload.value)
    # Never echo the value back.
    return {"ref": payload.ref, "status": "stored"}
