"""Extractor for ``format_type == 'csv'``."""
from __future__ import annotations

import csv
import io

from pipeline.contracts import ExtractionResult, RawRecord, StructuredRecord
from pipeline.extractors.base import BaseExtractor, ExtractorError
from pipeline.extractors.registry import register_extractor


@register_extractor("csv")
class CsvExtractor(BaseExtractor):
    """Maps CSV text to one structured record per data row.

    Uses the header row as field names; each subsequent row becomes a
    ``StructuredRecord`` whose ``fields`` is the row dict and whose ``search_text``
    is the row's values joined for full-text search.
    """

    def extract(self, raw_payload: RawRecord) -> ExtractionResult:
        """Parse CSV ``content`` (or the file at ``file_path``) into row records.

        Args:
            raw_payload: RawRecord whose ``content`` is CSV text, or whose
                ``file_path`` points at a ``.csv`` file.

        Returns:
            ExtractionResult with one structured record per CSV data row.
        """
        text = raw_payload.content
        if text is None and raw_payload.file_path:
            with open(raw_payload.file_path, encoding="utf-8", newline="") as fh:
                text = fh.read()
        if not isinstance(text, str):
            raise ExtractorError("csv extractor requires text content or a file_path")

        result = ExtractionResult()
        reader = csv.DictReader(io.StringIO(text))
        for row in reader:
            clean = {k: v for k, v in row.items() if k is not None}
            search_text = " | ".join(f"{k}: {v}" for k, v in clean.items() if v)
            result.structured_records.append(
                StructuredRecord(
                    record_type="csv_row",
                    fields=clean,
                    document_date=raw_payload.document_date,
                    original_file_reference=raw_payload.original_file_reference,
                    search_text=search_text or None,
                )
            )
        return result
