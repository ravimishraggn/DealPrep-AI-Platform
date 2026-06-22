"""Section-aware chunking — respects headers and paragraph boundaries (default)."""
from __future__ import annotations

import re

from app.config import settings
from pipeline.chunking.base import BaseChunker, register_chunker

_PARA_SPLIT = re.compile(r"\n\s*\n+")
_MD_HEADER = re.compile(r"^\s{0,3}#{1,6}\s+\S")


def _is_header(block: str) -> bool:
    """Heuristically decide whether a text block is a section header."""
    line = block.strip()
    if "\n" in line or len(line) > 80 or not line:
        return False
    if _MD_HEADER.match(line):
        return True
    if line[-1] in ".!?,:;":
        return False
    letters = [c for c in line if c.isalpha()]
    if letters and all(c.isupper() for c in letters):
        return True
    words = [w for w in re.split(r"\s+", line) if w]
    if 1 <= len(words) <= 10:
        return sum(1 for w in words if w[:1].isupper()) >= max(1, len(words) - 1)
    return False


def _clean_header(line: str) -> str:
    """Strip markdown ``#`` markers from a header line."""
    return re.sub(r"^\s*#+\s*", "", line.strip())


@register_chunker("section_aware")
class SectionAwareChunker(BaseChunker):
    """Default strategy: never straddle a header; split at paragraph boundaries.

    Grows chunks toward ``chunk_target_chars`` and carries ``chunk_overlap_chars``
    of trailing context into the next chunk, but only within a section — overlap
    never crosses a header. Best for structured prose (filings, memos, reports).
    """

    def __init__(self, target_chars: int | None = None, overlap_chars: int | None = None) -> None:
        """Configure target chunk size and overlap (defaults from settings)."""
        self.target = target_chars or settings.chunk_target_chars
        self.overlap = overlap_chars or settings.chunk_overlap_chars

    def chunk(self, text: str, default_section: str | None = None) -> list[tuple[str, str | None]]:
        """Split text into section-labeled, boundary-respecting chunks."""
        blocks = [b.strip() for b in _PARA_SPLIT.split(text.replace("\r\n", "\n")) if b.strip()]
        chunks: list[tuple[str, str | None]] = []
        buf: list[str] = []
        buf_len = 0
        section = default_section
        overlap_prefix = ""

        def flush(carry: bool) -> None:
            nonlocal buf, buf_len, overlap_prefix
            if not buf:
                if not carry:
                    overlap_prefix = ""
                return
            body = "\n\n".join(buf).strip()
            text_out = f"{overlap_prefix}\n\n{body}".strip() if overlap_prefix else body
            if text_out:
                chunks.append((text_out, section))
            overlap_prefix = text_out[-self.overlap :] if (carry and self.overlap) else ""
            buf = []
            buf_len = 0

        for block in blocks:
            if _is_header(block):
                flush(carry=False)
                section = _clean_header(block)
                continue
            if buf_len > 0 and buf_len + len(block) > self.target:
                flush(carry=True)
            buf.append(block)
            buf_len += len(block)
        flush(carry=False)
        return [(t, s) for t, s in chunks if t.strip()]
