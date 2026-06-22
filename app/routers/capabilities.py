"""Platform capabilities — what strategies are registered and selectable.

Exposes every pluggable choice (extractors, chunking, embedding, vector stores)
with whether each is a real implementation or a POC stub, plus the platform
defaults. Lets operators and the UI see "what can this platform do?" and populate
profile choosers — central to the self-service, POC-friendly story.
"""
from __future__ import annotations

from fastapi import APIRouter

from app.config import settings
from pipeline.chunking.base import CHUNKER_REGISTRY
from pipeline.embedding.base import EMBEDDER_REGISTRY
from pipeline.extractors.registry import EXTRACTOR_REGISTRY
from pipeline.vectorstore.base import VECTOR_STORE_REGISTRY

router = APIRouter(tags=["capabilities"])


@router.get("/capabilities", summary="List all registered, selectable pipeline strategies")
def capabilities() -> dict:
    """Return registered extractors, chunkers, embedders, and vector stores.

    Each entry reports whether it is a real implementation or a POC stub, so a
    tenant only ever selects approved/implemented strategies. Defaults reflect the
    platform-wide pipeline profile.
    """
    return {
        "extractors": [
            {"format_type": fmt, "implemented": cls.implemented}
            for fmt, cls in sorted(EXTRACTOR_REGISTRY.items())
        ],
        "chunking": [
            {"name": n, "implemented": cls.implemented}
            for n, cls in sorted(CHUNKER_REGISTRY.items())
        ],
        "embedding": [
            {"name": n, "implemented": cls.implemented, "dim": cls.dim}
            for n, cls in sorted(EMBEDDER_REGISTRY.items())
        ],
        "vector_stores": [
            {"name": n, "implemented": cls.implemented}
            for n, cls in sorted(VECTOR_STORE_REGISTRY.items())
        ],
        "defaults": {
            "chunking": settings.default_chunking,
            "embedding": settings.default_embedding,
            "vector_store": settings.default_vector_store,
        },
    }
