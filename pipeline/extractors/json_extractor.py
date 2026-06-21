"""Extractor for ``format_type == 'json'``."""
from __future__ import annotations

from typing import Any

from pipeline.contracts import ExtractionResult, RawRecord, StructuredRecord, TextDocument
from pipeline.extractors.base import BaseExtractor, ExtractorError
from pipeline.extractors.registry import register_extractor


def _text_bearing(obj: dict[str, Any]) -> str:
    """Concatenate string/number leaf values of a dict for FTS + semantic search."""
    parts: list[str] = []
    for key, value in obj.items():
        if isinstance(value, (str, int, float)):
            parts.append(f"{key}: {value}")
    return " | ".join(parts)


@register_extractor("json")
class JsonExtractor(BaseExtractor):
    """Maps a JSON object to a structured record *and* a searchable text document.

    A JSON record is primarily structured (it becomes a Postgres row), but its
    text-bearing fields are also emitted as a ``TextDocument`` so the same record
    is reachable by semantic vector search — demonstrating the "one record → both"
    contract.
    """

    def extract(self, raw_payload: RawRecord) -> ExtractionResult:
        """Produce one StructuredRecord and one TextDocument from a JSON object.

        Args:
            raw_payload: A RawRecord whose ``content`` is a JSON object (dict). A
                list is treated as a single record under ``{"items": [...]}``.

        Returns:
            ExtractionResult with one structured record and (if any text-bearing
            fields exist) one text document.
        """
        content = raw_payload.content
        if content is None:
            raise ExtractorError("json extractor received empty content")
        obj: dict[str, Any] = content if isinstance(content, dict) else {"items": content}

        search_text = _text_bearing(obj)
        result = ExtractionResult()
        result.structured_records.append(
            StructuredRecord(
                record_type="json_record",
                fields=obj,
                document_date=raw_payload.document_date,
                original_file_reference=raw_payload.original_file_reference,
                search_text=search_text or None,
            )
        )
        if search_text:
            result.text_documents.append(
                TextDocument(
                    text=search_text,
                    section_type="json_record",
                    document_date=raw_payload.document_date,
                    original_file_reference=raw_payload.original_file_reference,
                )
            )
        return result
