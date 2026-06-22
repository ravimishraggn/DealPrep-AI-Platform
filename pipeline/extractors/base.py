"""Abstract base class for format-specific extractors."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

from pipeline.contracts import ExtractionResult, RawRecord


class ExtractorError(Exception):
    """Raised when an extractor cannot process a record it was handed."""


class BaseExtractor(ABC):
    """Turns one ``RawRecord`` of a given format into normalized output.

    Concrete extractors are registered by ``format_type`` and may emit text
    documents, structured records, or both. The base class is deliberately
    minimal — the engine only ever calls ``extract``.
    """

    #: True for real extractors; False for POC stubs that raise ExtractorError.
    implemented: ClassVar[bool] = True

    @abstractmethod
    def extract(self, raw_payload: RawRecord) -> ExtractionResult:
        """Parse ``raw_payload`` into text documents and/or structured records.

        Args:
            raw_payload: The connector-produced record (already format-tagged).

        Returns:
            An ``ExtractionResult`` containing any extracted text documents and
            structured records.

        Raises:
            ExtractorError: If the payload is malformed for this format.
        """
        raise NotImplementedError
