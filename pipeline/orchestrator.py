"""Full pipeline orchestration with parallel fan-out indexing (ADR 0007).

Chains, for one ingestion run:

    raw records -> FormatRouter -> Extractor -> DocumentProcessor
                -> [VectorIndexer | StructuredIndexer | GraphIndexer]  (parallel)

The three indexers are independent, so they run concurrently. Each stage's
outcome is returned as a ``StageResult`` for the runner to log to ``run_stages``.
A failure in one indexer is captured and does not abort the others.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from app.models import StageStatus
from pipeline.contracts import RawRecord
from pipeline.indexing.graph.indexer import GraphIndexer
from pipeline.indexing.structured import StructuredIndexer
from pipeline.indexing.vector import VectorIndexer
from pipeline.processor import DocumentProcessor
from pipeline.router import FormatRouter

logger = logging.getLogger(__name__)


@dataclass
class StageResult:
    """Outcome of one pipeline stage, for run_history logging."""

    stage: str
    status: StageStatus
    item_count: int = 0
    detail: str | None = None


@dataclass
class PipelineOutcome:
    """Aggregate result of running the pipeline for one ingestion batch."""

    stages: list[StageResult] = field(default_factory=list)
    chunks: int = 0
    structured: int = 0
    entities: int = 0
    relationships: int = 0


class PipelineOrchestrator:
    """Runs the extraction → processing → parallel-indexing pipeline for a batch."""

    def __init__(
        self,
        router: FormatRouter | None = None,
        processor: DocumentProcessor | None = None,
        vector: VectorIndexer | None = None,
        structured: StructuredIndexer | None = None,
        graph: GraphIndexer | None = None,
    ) -> None:
        """Wire pipeline stages; defaults are constructed if omitted."""
        self.router = router or FormatRouter()
        self.processor = processor or DocumentProcessor()
        self.vector = vector or VectorIndexer()
        self.structured = structured or StructuredIndexer()
        self.graph = graph or GraphIndexer()

    def run(
        self, records: list[dict[str, Any]], tenant_id: str, source_id: str, profile=None
    ) -> PipelineOutcome:
        """Process a batch of connector records through the whole pipeline.

        Args:
            records: Raw connector output dicts (RawRecord shape, format-tagged).
            tenant_id: Owning tenant (threaded through every stage for isolation).
            source_id: Originating source (traceability).
            profile: Optional ``PipelineProfile`` selecting this tenant's chunking
                and vector backend; ``None`` uses the platform defaults.

        Returns:
            A ``PipelineOutcome`` with per-stage results and aggregate counts.
        """
        outcome = PipelineOutcome()

        # Resolve profile-dependent stages (chunking + vector backend). Structured
        # and graph indexing are not profile-selectable (fixed Postgres + Neo4j).
        processor = DocumentProcessor(profile.chunking) if profile else self.processor
        vector = (
            VectorIndexer(profile.embedding, profile.vector_store) if profile else self.vector
        )

        # Stage 1: format-route + extract.
        raw_records = self._coerce(records)
        extraction = self.router.route_many(raw_records)
        outcome.stages.append(
            StageResult(
                "extract", StageStatus.success,
                item_count=len(extraction.text_documents) + len(extraction.structured_records),
            )
        )

        # Stage 2: document processing (chunk text / pass structured through).
        chunks, structured_records = processor.process(extraction, tenant_id, source_id)
        outcome.chunks = len(chunks)
        outcome.structured = len(structured_records)
        outcome.stages.append(
            StageResult("process", StageStatus.success, item_count=len(chunks) + len(structured_records))
        )

        # Stage 3: parallel fan-out into the three independent indexers.
        tasks = {
            "index_vector": lambda: vector.embed_and_index(chunks),
            "index_structured": lambda: self._index_structured(structured_records, tenant_id, source_id),
            "index_graph": lambda: self._index_graph(chunks, tenant_id, source_id),
        }
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {name: pool.submit(fn) for name, fn in tasks.items()}
            for name, future in futures.items():
                outcome.stages.append(self._collect(name, future, outcome))
        return outcome

    # --- stage helpers ------------------------------------------------------
    @staticmethod
    def _coerce(records: list[dict[str, Any]]) -> list[RawRecord]:
        """Validate raw dicts into RawRecord, skipping malformed entries."""
        out: list[RawRecord] = []
        for rec in records:
            try:
                out.append(RawRecord.model_validate(rec))
            except Exception:  # noqa: BLE001 - a malformed record is skipped, not fatal
                logger.warning("skipping malformed record: %s", str(rec)[:120])
        return out

    def _index_structured(self, records, tenant_id, source_id) -> int:
        """Structured-index records; returns count indexed."""
        return self.structured.index_fields(records, tenant_id, source_id)

    def _index_graph(self, chunks, tenant_id, source_id) -> dict[str, int]:
        """Graph-index chunks; returns {entities, relationships} counts."""
        return self.graph.index_chunks(chunks, tenant_id, source_id)

    @staticmethod
    def _collect(name: str, future, outcome: PipelineOutcome) -> StageResult:
        """Resolve one indexer future into a StageResult, capturing failures."""
        try:
            value = future.result()
        except Exception as exc:  # noqa: BLE001 - one indexer failing must not fail others
            logger.exception("pipeline stage %s failed", name)
            return StageResult(name, StageStatus.failure, detail=str(exc))
        if isinstance(value, dict):  # graph returns counts
            outcome.entities = value.get("entities", 0)
            outcome.relationships = value.get("relationships", 0)
            return StageResult(
                name, StageStatus.success,
                item_count=value.get("entities", 0) + value.get("relationships", 0),
                detail=f"{value.get('entities', 0)} entities, {value.get('relationships', 0)} relationships",
            )
        return StageResult(name, StageStatus.success, item_count=int(value))


_orchestrator: PipelineOrchestrator | None = None


def get_orchestrator() -> PipelineOrchestrator:
    """Return a process-wide cached orchestrator (stages reuse cached models)."""
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = PipelineOrchestrator()
    return _orchestrator
