"""POC stub extractors for Office formats (docx, xlsx, pptx).

Registered so the platform advertises these formats as a roadmap, but not
implemented for V1 — each needs a parsing dependency (python-docx, openpyxl,
python-pptx) and format-specific text/table handling. Per ADR 0008, a stub raises
``ExtractorError`` so an unsupported record is skipped + logged, never silently
mishandled. Replace any of these with a real extractor by implementing ``extract``
and flipping ``implemented = True``.
"""
from __future__ import annotations

from pipeline.contracts import ExtractionResult, RawRecord
from pipeline.extractors.base import BaseExtractor, ExtractorError
from pipeline.extractors.registry import register_extractor


class _StubExtractor(BaseExtractor):
    """Shared base for not-yet-implemented format extractors."""

    implemented = False
    _format = "?"
    _dependency = "?"

    def extract(self, raw_payload: RawRecord) -> ExtractionResult:
        """Always raises — this format is a documented POC stub (ADR 0008)."""
        raise ExtractorError(
            f"'{self._format}' extractor is a POC stub (needs {self._dependency}); see ADR 0008"
        )


@register_extractor("docx")
class DocxExtractor(_StubExtractor):
    """STUB: Word documents — would yield prose (text) + tables (structured)."""

    _format = "docx"
    _dependency = "python-docx"


@register_extractor("xlsx")
class XlsxExtractor(_StubExtractor):
    """STUB: Excel workbooks — would yield one structured record per row/sheet."""

    _format = "xlsx"
    _dependency = "openpyxl"


@register_extractor("pptx")
class PptxExtractor(_StubExtractor):
    """STUB: PowerPoint decks — would yield slide text (text documents)."""

    _format = "pptx"
    _dependency = "python-pptx"
