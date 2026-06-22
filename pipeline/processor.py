"""Document Processor — chunk text documents, pass structured records through.

Consumes an ``ExtractionResult`` and produces the two streams the indexers need:
``Chunk`` objects (from text documents, for vector + graph indexing) and
``StructuredRecord`` objects (passed straight through to structured indexing).
The chunking *strategy* is pluggable and selected by name (ADR 0009). Every chunk
is tagged with the full isolation/traceability set.
"""
from __future__ import annotations

from pipeline.chunking.base import BaseChunker, get_chunker
from pipeline.contracts import Chunk, ExtractionResult, StructuredRecord, TextDocument


class DocumentProcessor:
    """Routes extractor output: text → chunks (via a chosen strategy), structured → through."""

    def __init__(self, chunker: BaseChunker | str | None = None) -> None:
        """Select the chunking strategy.

        Args:
            chunker: A ``BaseChunker`` instance, a registered strategy name (e.g.
                "section_aware", "fixed_size", "sentence_window"), or ``None`` for
                the platform default ("section_aware").
        """
        if isinstance(chunker, BaseChunker):
            self.chunker = chunker
        else:
            self.chunker = get_chunker(chunker or "section_aware")

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
