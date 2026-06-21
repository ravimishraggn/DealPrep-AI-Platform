"""Extractor for ``format_type == 'text'``."""
from __future__ import annotations

from pipeline.contracts import ExtractionResult, RawRecord, TextDocument
from pipeline.extractors.base import BaseExtractor, ExtractorError
from pipeline.extractors.registry import register_extractor


@register_extractor("text")
class PlainTextExtractor(BaseExtractor):
    """Maps plain text to a single text document for chunking + vector search."""

    def extract(self, raw_payload: RawRecord) -> ExtractionResult:
        """Wrap plain text ``content`` (or a text file) as one TextDocument.

        Args:
            raw_payload: RawRecord whose ``content`` is text, or whose
                ``file_path`` points at a text file.

        Returns:
            ExtractionResult with a single text document.
        """
        text = raw_payload.content
        if text is None and raw_payload.file_path:
            with open(raw_payload.file_path, encoding="utf-8") as fh:
                text = fh.read()
        if not isinstance(text, str) or not text.strip():
            raise ExtractorError("text extractor requires non-empty text content")

        return ExtractionResult(
            text_documents=[
                TextDocument(
                    text=text,
                    section_type="document",
                    document_date=raw_payload.document_date,
                    original_file_reference=raw_payload.original_file_reference,
                )
            ]
        )
