"""Structured indexing + keyword search over Postgres (ADR 0003).

Writes ``StructuredRecord``s into the ``structured_records`` table (JSONB fields +
generated tsvector) and searches them with Postgres full-text + optional JSONB
filters. Every read is filtered by ``tenant_id`` — there is no unfiltered path.
"""
from __future__ import annotations

import logging

from sqlalchemy import text

from app.db import SessionLocal
from app.models import StructuredRecordRow
from pipeline.contracts import StructuredRecord, parse_document_date

logger = logging.getLogger(__name__)


class StructuredIndexer:
    """Persists and searches structured records in Postgres, tenant-scoped."""

    def index_fields(
        self, records: list[StructuredRecord], tenant_id: str, source_id: str
    ) -> int:
        """Insert structured records for one tenant/source.

        Args:
            records: Structured records to persist (JSONB ``fields`` + FTS text).
            tenant_id: REQUIRED owning tenant — stamped on every row.
            source_id: REQUIRED originating source — stamped on every row.

        Returns:
            The number of rows inserted.
        """
        if not tenant_id or not source_id:
            raise ValueError("tenant_id and source_id are required for structured indexing")
        if not records:
            return 0
        session = SessionLocal()
        try:
            rows = [
                StructuredRecordRow(
                    tenant_id=tenant_id,
                    source_id=source_id,
                    record_type=rec.record_type,
                    document_date=parse_document_date(rec.document_date),
                    original_file_reference=rec.original_file_reference,
                    fields=rec.fields,
                    search_text=rec.search_text,
                )
                for rec in records
            ]
            session.add_all(rows)
            session.commit()
            logger.info("structured-indexed %d row(s) for tenant %s", len(rows), tenant_id)
            return len(rows)
        finally:
            session.close()

    def search(
        self, tenant_id: str, query: str, k: int = 5, record_type: str | None = None
    ) -> list[dict]:
        """Keyword (tsvector) search within one tenant's structured records.

        Args:
            tenant_id: REQUIRED — filters every row; a missing value raises.
            query: Keyword query (parsed via ``plainto_tsquery``).
            k: Maximum number of results.
            record_type: Optional filter on the logical record type.

        Returns:
            Result dicts with ``fields``, ``score`` (ts_rank), and traceability.
        """
        if not tenant_id:
            raise ValueError("tenant_id is required for structured search")
        session = SessionLocal()
        try:
            # Casts are explicit because psycopg3 cannot infer a bound parameter's
            # type when it only appears inside plainto_tsquery() / a NULL comparison.
            sql = text(
                """
                SELECT id, record_type, source_id, original_file_reference,
                       document_date, fields,
                       ts_rank(search_tsv, plainto_tsquery('english', cast(:q AS text))) AS rank
                FROM structured_records
                WHERE tenant_id = cast(:tenant_id AS text)
                  AND (cast(:record_type AS text) IS NULL OR record_type = cast(:record_type AS text))
                  AND search_tsv @@ plainto_tsquery('english', cast(:q AS text))
                ORDER BY rank DESC
                LIMIT :k
                """
            )
            result = session.execute(
                sql,
                {"q": query, "tenant_id": tenant_id, "record_type": record_type, "k": k},
            )
            out: list[dict] = []
            for row in result.mappings():
                out.append(
                    {
                        "fields": row["fields"],
                        "score": round(float(row["rank"]), 4),
                        "metadata": {
                            "record_type": row["record_type"],
                            "source_id": row["source_id"],
                            "original_file_reference": row["original_file_reference"],
                            "document_date": str(row["document_date"]) if row["document_date"] else "",
                        },
                    }
                )
            return out
        finally:
            session.close()
