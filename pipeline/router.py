"""Format Router — dispatches each RawRecord to the right extractor by format."""
from __future__ import annotations

import logging

from pipeline.contracts import ExtractionResult, RawRecord
from pipeline.extractors.base import ExtractorError
from pipeline.extractors.registry import get_extractor

logger = logging.getLogger(__name__)


class FormatRouter:
    """Reads ``format_type`` and dispatches to the registered extractor.

    A record whose ``format_type`` has no registered extractor is logged and
    skipped — it never raises, so one unsupported record cannot crash an
    otherwise-good run (requirement 3).
    """

    def route(self, record: RawRecord) -> ExtractionResult:
        """Extract a single record, returning empty output on unknown/failed format.

        Args:
            record: A connector-produced, format-tagged record.

        Returns:
            The extractor's ``ExtractionResult``, or an empty result if the format
            is unregistered or extraction fails.
        """
        extractor_cls = get_extractor(record.format_type)
        if extractor_cls is None:
            logger.warning(
                "No extractor for format_type '%s' (ref=%s); skipping record",
                record.format_type, record.original_file_reference,
            )
            return ExtractionResult()
        try:
            return extractor_cls().extract(record)
        except ExtractorError as exc:
            logger.error(
                "Extractor for '%s' failed on ref=%s: %s",
                record.format_type, record.original_file_reference, exc,
            )
            return ExtractionResult()

    def route_many(self, records: list[RawRecord]) -> ExtractionResult:
        """Route a batch of records and merge their outputs into one result."""
        merged = ExtractionResult()
        for record in records:
            merged.extend(self.route(record))
        return merged
