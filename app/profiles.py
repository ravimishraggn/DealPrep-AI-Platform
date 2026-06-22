"""Per-tenant pipeline profile resolution + validation (ADR 0012).

A profile is the set of pluggable strategy choices a tenant makes — chunking,
embedding, vector store. This module is the single place those choices are
resolved (tenant override → platform default) and validated (must be registered
and implemented, never a stub).
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.config import settings
from app.models import TenantPipelineProfile
from pipeline.chunking.base import CHUNKER_REGISTRY
from pipeline.embedding.base import EMBEDDER_REGISTRY
from pipeline.vectorstore.base import VECTOR_STORE_REGISTRY


@dataclass
class PipelineProfile:
    """Resolved strategy choices for one tenant's pipeline."""

    chunking: str
    embedding: str
    vector_store: str
    is_default: bool = True


def platform_default() -> PipelineProfile:
    """Return the platform-default profile from settings."""
    return PipelineProfile(
        chunking=settings.default_chunking,
        embedding=settings.default_embedding,
        vector_store=settings.default_vector_store,
        is_default=True,
    )


def resolve_profile(tenant_id: str, db: Session) -> PipelineProfile:
    """Return the tenant's profile, falling back to platform defaults.

    Args:
        tenant_id: The tenant whose profile to resolve.
        db: Active DB session.

    Returns:
        The tenant's stored ``PipelineProfile``, or the platform default if none.
    """
    row = db.get(TenantPipelineProfile, tenant_id)
    if row is None:
        return platform_default()
    return PipelineProfile(row.chunking, row.embedding, row.vector_store, is_default=False)


def _check(name: str, registry: dict, kind: str) -> None:
    """Validate one selection is registered and not a stub, else raise ValueError."""
    if name not in registry:
        known = ", ".join(sorted(registry))
        raise ValueError(f"unknown {kind} '{name}'. Available: {known}")
    if not registry[name].implemented:
        raise ValueError(f"{kind} '{name}' is a POC stub and cannot be selected for production")


def validate_profile(chunking: str, embedding: str, vector_store: str) -> None:
    """Validate a profile's three choices against the registries.

    Raises:
        ValueError: If any choice is unknown or a not-implemented stub.
    """
    _check(chunking, CHUNKER_REGISTRY, "chunking strategy")
    _check(embedding, EMBEDDER_REGISTRY, "embedding backend")
    _check(vector_store, VECTOR_STORE_REGISTRY, "vector store")
