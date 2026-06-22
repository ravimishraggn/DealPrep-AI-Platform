"""Extractor for ``format_type == 'html'`` (real — tag-stripped text)."""
from __future__ import annotations

import re

from pipeline.contracts import ExtractionResult, RawRecord, TextDocument
from pipeline.extractors.base import BaseExtractor, ExtractorError
from pipeline.extractors.registry import register_extractor

_SCRIPT_STYLE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


@register_extractor("html")
class HtmlExtractor(BaseExtractor):
    """Strips HTML to visible text and emits it as one text document.

    A dependency-free, good-enough extractor for V1 (drops script/style, removes
    tags, collapses whitespace). A richer DOM-aware variant could be added later as
    a separate strategy without touching this one.
    """

    def extract(self, raw_payload: RawRecord) -> ExtractionResult:
        """Extract visible text from HTML ``content`` (or an .html file).

        Args:
            raw_payload: RawRecord whose ``content`` is HTML, or whose
                ``file_path`` points at an HTML file.

        Returns:
            ExtractionResult with one text document of the stripped text.
        """
        html = raw_payload.content
        if html is None and raw_payload.file_path:
            with open(raw_payload.file_path, encoding="utf-8") as fh:
                html = fh.read()
        if not isinstance(html, str):
            raise ExtractorError("html extractor requires text content or a file_path")

        text = _WS.sub(" ", _TAG.sub(" ", _SCRIPT_STYLE.sub(" ", html))).strip()
        if not text:
            raise ExtractorError("html extractor produced no text")
        return ExtractionResult(
            text_documents=[
                TextDocument(
                    text=text, section_type="html",
                    document_date=raw_payload.document_date,
                    original_file_reference=raw_payload.original_file_reference,
                )
            ]
        )
