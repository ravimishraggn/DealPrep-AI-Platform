"""Typed data contracts that flow between pipeline stages.

These models are the *interfaces* between connectors, extractors, the document
processor, and the three indexers. Keeping them explicit means each stage can be
developed, tested, and replaced independently — no stage reaches into another's
internals.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field


def parse_document_date(value: str | None) -> date | None:
    """Best-effort parse of an ISO-8601 string (or ``None``) into a ``date``.

    Returns ``None`` if the value is missing or unparseable, so a bad upstream
    timestamp degrades gracefully rather than failing a whole record.
    """
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except (ValueError, TypeError):
        return None


class RawRecord(BaseModel):
    """The connector output contract — one fetched item, tagged by format.

    A connector's ``fetch()`` returns a list of these. ``format_type`` drives the
    FormatRouter; ``content`` carries inline data (a JSON object, CSV/HTML/plain
    text), while binary formats (e.g. PDF) instead set ``file_path`` to the landed
    file. ``original_file_reference`` is the traceability handle threaded through
    every downstream artifact.
    """

    format_type: str = Field(description="e.g. 'json', 'pdf', 'html', 'csv', 'text'")
    content: Any = Field(default=None, description="Inline payload for text-like formats")
    file_path: str | None = Field(default=None, description="Path to landed file for binary formats")
    original_file_reference: str = Field(description="Filename / URL / id for source traceability")
    document_date: str | None = Field(default=None, description="ISO-8601 date/time of the document")
    metadata: dict[str, Any] = Field(default_factory=dict)


class TextDocument(BaseModel):
    """Unstructured text produced by an extractor, destined for chunking + vectors.

    ``title`` and ``section_type`` are optional hints the chunker and graph stages
    use; ``document_date`` and ``original_file_reference`` carry traceability.
    """

    text: str
    title: str | None = None
    section_type: str | None = None
    document_date: str | None = None
    original_file_reference: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class StructuredRecord(BaseModel):
    """A structured row produced by an extractor, destined for Postgres.

    ``fields`` is the flexible per-tenant key/value set (JSONB at rest);
    ``record_type`` labels the logical kind (e.g. 'pdf_table_row', 'json_record').
    ``search_text`` is the concatenation of text-bearing values used for FTS.
    """

    record_type: str = "record"
    fields: dict[str, Any] = Field(default_factory=dict)
    document_date: str | None = None
    original_file_reference: str
    search_text: str | None = None


class ExtractionResult(BaseModel):
    """Normalized extractor output — a single source record can yield both.

    For example a PDF yields ``text_documents`` (prose pages) *and*
    ``structured_records`` (extracted tables).
    """

    text_documents: list[TextDocument] = Field(default_factory=list)
    structured_records: list[StructuredRecord] = Field(default_factory=list)

    def extend(self, other: "ExtractionResult") -> None:
        """Merge another result's documents and records into this one."""
        self.text_documents.extend(other.text_documents)
        self.structured_records.extend(other.structured_records)


class Chunk(BaseModel):
    """A chunk of text ready for embedding + vector indexing.

    Every chunk is tagged with the full isolation/traceability set required by the
    indexers: ``tenant_id``, ``source_id``, ``document_date``, ``section_type``,
    and ``original_file_reference``, plus its ordinal position in the document.
    """

    text: str
    tenant_id: str
    source_id: str
    document_date: str | None = None
    section_type: str | None = None
    original_file_reference: str
    chunk_index: int = 0
    title: str | None = None

    def chunk_id(self) -> str:
        """Stable, tenant-scoped id for this chunk (used as the vector store id)."""
        ref = self.original_file_reference.replace("/", "_").replace("\\", "_")
        return f"{self.tenant_id}:{self.source_id}:{ref}:{self.chunk_index}"

    def vector_metadata(self) -> dict[str, Any]:
        """Flat metadata dict stored alongside the embedding for filtering/trace."""
        return {
            "tenant_id": self.tenant_id,
            "source_id": self.source_id,
            "document_date": self.document_date or "",
            "section_type": self.section_type or "",
            "original_file_reference": self.original_file_reference,
            "chunk_index": self.chunk_index,
            "title": self.title or "",
        }
