"""DocumentResearcher — retrieves narrative commentary via vector (semantic) search.

Runs as one leg of the parallel fan-out in Phase 7 (ADR 0013).  Calls the
VectorIndexer directly so only the vector store is queried (not the full
UnifiedSearch fan-out, which would duplicate the work done by the other agents
running in parallel).
"""
from __future__ import annotations

import asyncio
import time

from agents.base import AgentResult, AnalysisState, BaseAgent
from agents.registry import register_agent
from pipeline.indexing.vector import VectorIndexer


@register_agent("document_researcher")
class DocumentResearcher(BaseAgent):
    """Semantic search over the tenant's vector store for narrative content."""

    async def run(self, state: AnalysisState) -> AgentResult:
        ctx = state.context
        start = time.perf_counter()
        try:
            indexer = VectorIndexer(ctx.embedding, ctx.vector_store)
            hits = await asyncio.to_thread(indexer.search, ctx.tenant_id, ctx.query, ctx.k)
            return AgentResult(
                agent=self.name,
                status="success",
                payload={"chunks": hits},
                latency_ms=self._timed(start),
            )
        except Exception as exc:  # noqa: BLE001
            return self._failed(exc, start)
