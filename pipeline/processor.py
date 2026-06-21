"""Document Processor — chunk text documents, pass structured records through.

Consumes an ``ExtractionResult`` and produces the two streams the indexers need:
``Chunk`` objects (from text documents, for vector + graph indexing) and
``StructuredRecord`` objects (passed straight through to structured indexing).
Every chunk is tagged with the full isolation/traceability set.
"""
from __future__ import annotations

import re

from app.config import settings
from pipeline.contracts import Chunk, ExtractionResult, StructuredRecord, TextDocument

# A paragraph break is one or more blank lines.
_PARA_SPLIT = re.compile(r"\n\s*\n+")
# A markdown-style header line.
_MD_HEADER = re.compile(r"^\s{0,3}#{1,6}\s+\S")


def _is_header(block: str) -> bool:
    """Heuristically decide whether a text block is a section header.

    A block is treated as a header if it is a single short line that is either a
    markdown header, ALL-CAPS, or Title Case without terminal sentence
    punctuation — the boundaries a human reader would recognize as a section start.
    """
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
    # Title Case: most words capitalized.
    words = [w for w in re.split(r"\s+", line) if w]
    if 1 <= len(words) <= 10:
        caps = sum(1 for w in words if w[:1].isupper())
        return caps >= max(1, len(words) - 1)
    return False


def _clean_header(line: str) -> str:
    """Strip markdown ``#`` markers from a header line."""
    return re.sub(r"^\s*#+\s*", "", line.strip())


class Chunker:
    """Splits text into overlapping, boundary-respecting chunks.

    Honors paragraph boundaries (blank lines) and section headers so a chunk never
    straddles a section start. Chunks grow toward ``chunk_target_chars`` and carry
    ``chunk_overlap_chars`` of trailing context into the next chunk for retrieval
    continuity.
    """

    def __init__(self, target_chars: int | None = None, overlap_chars: int | None = None) -> None:
        """Configure chunk size and overlap (defaults come from settings)."""
        self.target = target_chars or settings.chunk_target_chars
        self.overlap = overlap_chars or settings.chunk_overlap_chars

    def chunk(self, text: str, default_section: str | None = None) -> list[tuple[str, str | None]]:
        """Split ``text`` into ``(chunk_text, section_type)`` tuples.

        Args:
            text: The document text to split.
            default_section: Section label to use until a header is encountered.

        Returns:
            A list of ``(chunk_text, section_type)`` tuples in document order.
        """
        blocks = [b.strip() for b in _PARA_SPLIT.split(text.replace("\r\n", "\n")) if b.strip()]
        chunks: list[tuple[str, str | None]] = []
        buf: list[str] = []          # only real, new content blocks
        buf_len = 0
        section = default_section
        overlap_prefix = ""          # trailing context carried from the previous chunk

        def flush(carry: bool) -> None:
            # Emit a chunk only when there is genuinely new content in `buf`, so a
            # carried-over overlap tail never becomes a standalone chunk. Overlap is
            # carried only on size-based splits (carry=True), never across a section
            # header (carry=False), so headers/sections don't bleed forward.
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
                # A header starts a new section → close the current chunk, no overlap.
                flush(carry=False)
                section = _clean_header(block)
                continue
            if buf_len > 0 and buf_len + len(block) > self.target:
                flush(carry=True)
            buf.append(block)
            buf_len += len(block)
        flush(carry=False)
        return [(t, s) for t, s in chunks if t.strip()]


class DocumentProcessor:
    """Routes extractor output: text → chunks, structured → straight through."""

    def __init__(self, chunker: Chunker | None = None) -> None:
        """Use the provided chunker or a default one built from settings."""
        self.chunker = chunker or Chunker()

    def process(
        self, result: ExtractionResult, tenant_id: str, source_id: str
    ) -> tuple[list[Chunk], list[StructuredRecord]]:
        """Convert an ExtractionResult into tagged chunks and structured records.

        Args:
            result: The normalized extractor output.
            tenant_id: Owning tenant (stamped on every chunk for isolation).
            source_id: Originating source (stamped on every chunk for trace).

        Returns:
            ``(chunks, structured_records)`` — chunks for vector/graph indexing and
            the (unchanged) structured records for structured indexing.
        """
        chunks: list[Chunk] = []
        for doc in result.text_documents:
            chunks.extend(self._chunk_document(doc, tenant_id, source_id))
        return chunks, list(result.structured_records)

    def _chunk_document(self, doc: TextDocument, tenant_id: str, source_id: str) -> list[Chunk]:
        """Chunk a single text document and tag each chunk with full metadata."""
        out: list[Chunk] = []
        for index, (text, section) in enumerate(self.chunker.chunk(doc.text, doc.section_type)):
            out.append(
                Chunk(
                    text=text,
                    tenant_id=tenant_id,
                    source_id=source_id,
                    document_date=doc.document_date,
                    section_type=section or doc.section_type,
                    original_file_reference=doc.original_file_reference,
                    chunk_index=index,
                    title=doc.title,
                )
            )
        return out
