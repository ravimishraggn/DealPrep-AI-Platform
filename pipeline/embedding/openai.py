"""OpenAI embeddings backend (POC STUB)."""
from __future__ import annotations

from pipeline.embedding.base import BaseEmbedder, register_embedder


@register_embedder("openai")
class OpenAIEmbedder(BaseEmbedder):
    """STUB: text-embedding-3-* via the OpenAI API.

    Registered as a selectable option but not implemented for V1. Real use needs
    an API key, network egress, per-token cost, and rate-limit/retry handling — the
    trade-offs are documented in ADR 0010. Selecting it raises a clear error.
    """

    dim = 1536
    implemented = False

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Not implemented — see class docstring and ADR 0010."""
        raise NotImplementedError("openai embedder is a POC stub (see ADR 0010)")
