"""Extractor for ``format_type == 'pdf'`` (text pages + tables)."""
from __future__ import annotations

from pipeline.contracts import ExtractionResult, RawRecord, StructuredRecord, TextDocument
from pipeline.extractors.base import BaseExtractor, ExtractorError
from pipeline.extractors.registry import register_extractor


@register_extractor("pdf")
class PdfExtractor(BaseExtractor):
    """Extracts prose pages as text documents and tables as structured records.

    This is the canonical "one source record → both" case: each PDF page's text
    becomes a ``TextDocument`` (chunked + embedded for semantic search) while each
    detected table row becomes a ``StructuredRecord`` (Postgres + keyword search).
    Uses ``pdfplumber`` (imported lazily so the rest of the pipeline does not hard
    depend on it).
    """

    def extract(self, raw_payload: RawRecord) -> ExtractionResult:
        """Extract page text and table rows from a PDF file.

        Args:
            raw_payload: RawRecord whose ``file_path`` points at a PDF. (Binary
                content is read from disk, not passed inline.)

        Returns:
            ExtractionResult with one text document per non-empty page and one
            structured record per table row.

        Raises:
            ExtractorError: If ``file_path`` is missing or the file is unreadable.
        """
        if not raw_payload.file_path:
            raise ExtractorError("pdf extractor requires a file_path to the PDF")
        try:
            import pdfplumber  # lazy import; heavy dependency
        except ImportError as exc:  # pragma: no cover
            raise ExtractorError("pdfplumber is not installed") from exc

        ref = raw_payload.original_file_reference
        result = ExtractionResult()
        try:
            with pdfplumber.open(raw_payload.file_path) as pdf:
                for page_no, page in enumerate(pdf.pages, start=1):
                    page_text = page.extract_text() or ""
                    if page_text.strip():
                        result.text_documents.append(
                            TextDocument(
                                text=page_text,
                                title=f"page {page_no}",
                                section_type="pdf_page",
                                document_date=raw_payload.document_date,
                                original_file_reference=ref,
                                metadata={"page": page_no},
                            )
                        )
                    for table in page.extract_tables() or []:
                        result.structured_records.extend(
                            self._rows_from_table(table, page_no, raw_payload)
                        )
        except ExtractorError:
            raise
        except Exception as exc:  # noqa: BLE001 - surface any pdf parse failure cleanly
            raise ExtractorError(f"failed to parse PDF {ref}: {exc}") from exc
        return result

    @staticmethod
    def _rows_from_table(table, page_no, raw_payload) -> list[StructuredRecord]:
        """Turn one extracted table (list of rows) into StructuredRecords.

        The first row is treated as a header; subsequent rows become records whose
        ``fields`` map header → cell.
        """
        if not table or len(table) < 2:
            return []
        header = [(h or f"col_{i}").strip() for i, h in enumerate(table[0])]
        records: list[StructuredRecord] = []
        for row in table[1:]:
            fields = {header[i]: (cell or "").strip() for i, cell in enumerate(row) if i < len(header)}
            search_text = " | ".join(f"{k}: {v}" for k, v in fields.items() if v)
            records.append(
                StructuredRecord(
                    record_type="pdf_table_row",
                    fields=fields,
                    document_date=raw_payload.document_date,
                    original_file_reference=raw_payload.original_file_reference,
                    search_text=search_text or None,
                )
            )
        return records
