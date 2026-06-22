"""StructuredAgent — retrieves exact financial figures via Postgres FTS.

Runs as one leg of the parallel fan-out in Phase 7 (ADR 0013).  Calls
StructuredIndexer.search() directly — keyword / tsvector search only, no LLM.
"""
from __future__ import annotations

import asyncio
import time

from agents.base import AgentResult, AnalysisState, BaseAgent
from agents.registry import register_agent
from pipeline.indexing.structured import StructuredIndexer


@register_agent("structured_agent")
class StructuredAgent(BaseAgent):
    """Full-text + JSONB search over Postgres structured records."""

    def __init__(self) -> None:
        self._indexer = StructuredIndexer()

    async def run(self, state: AnalysisState) -> AgentResult:
        ctx = state.context
        start = time.perf_counter()
        try:
            hits = await asyncio.to_thread(
                self._indexer.search, ctx.tenant_id, ctx.query, ctx.k
            )
            return AgentResult(
                agent=self.name,
                status="success",
                payload={"records": hits},
                latency_ms=self._timed(start),
            )
        except Exception as exc:  # noqa: BLE001
            return self._failed(exc, start)
