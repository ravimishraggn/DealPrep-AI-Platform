"""Fixed-size chunking — uniform character windows with overlap."""
from __future__ import annotations

from app.config import settings
from pipeline.chunking.base import BaseChunker, register_chunker


@register_chunker("fixed_size")
class FixedSizeChunker(BaseChunker):
    """Splits text into uniform ``target``-char windows with fixed overlap.

    Ignores document structure entirely. Cheapest and most predictable; good for
    homogeneous text, log-like data, or when downstream cost depends on a stable
    chunk count. Worst for structured documents (can cut mid-sentence).
    """

    def __init__(self, target_chars: int | None = None, overlap_chars: int | None = None) -> None:
        """Configure window size and overlap (defaults from settings)."""
        self.target = target_chars or settings.chunk_target_chars
        self.overlap = overlap_chars or settings.chunk_overlap_chars

    def chunk(self, text: str, default_section: str | None = None) -> list[tuple[str, str | None]]:
        """Slide a fixed window over the text, stepping by (target - overlap)."""
        text = text.strip()
        if not text:
            return []
        step = max(self.target - self.overlap, 1)
        out: list[tuple[str, str | None]] = []
        for start in range(0, len(text), step):
            piece = text[start : start + self.target].strip()
            if piece:
                out.append((piece, default_section))
            if start + self.target >= len(text):
                break
        return out
