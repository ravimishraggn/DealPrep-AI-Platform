"""Semantic chunking — embedding-based boundary detection (POC STUB)."""
from __future__ import annotations

from pipeline.chunking.base import BaseChunker, register_chunker


@register_chunker("semantic")
class SemanticChunker(BaseChunker):
    """STUB: split where the embedding of adjacent sentences diverges.

    Registered so it appears as a selectable platform option, but intentionally
    not implemented for V1 — it requires embedding every sentence and tuning a
    similarity-drop threshold (cost + quality trade-offs documented in ADR 0009).
    Selecting it raises a clear error rather than silently degrading.
    """

    implemented = False

    def chunk(self, text: str, default_section: str | None = None) -> list[tuple[str, str | None]]:
        """Not implemented — see class docstring and ADR 0009."""
        raise NotImplementedError("semantic chunking is a POC stub (see ADR 0009)")
