"""GraphAgent — traces entity relationships via 1-hop Neo4j traversal.

Runs as one leg of the parallel fan-out in Phase 7 (ADR 0013).  Identifies
entity names mentioned in the query, then returns their direct relationships.
Multi-hop traversal and deeper graph reasoning are Phase 8+ extensions.
"""
from __future__ import annotations

import asyncio
import time

from agents.base import AgentResult, AnalysisState, BaseAgent
from agents.registry import register_agent
from pipeline.indexing.graph.neo4j_client import Neo4jClient


@register_agent("graph_agent")
class GraphAgent(BaseAgent):
    """1-hop relationship lookup for entity names mentioned in the query."""

    def __init__(self) -> None:
        self._client = Neo4jClient()

    async def run(self, state: AnalysisState) -> AgentResult:
        ctx = state.context
        start = time.perf_counter()
        try:
            triples = await asyncio.to_thread(self._lookup, ctx.tenant_id, ctx.query, ctx.k)
            return AgentResult(
                agent=self.name,
                status="success",
                payload={"triples": triples},
                latency_ms=self._timed(start),
            )
        except Exception as exc:  # noqa: BLE001
            return self._failed(exc, start)

    def _lookup(self, tenant_id: str, query: str, k: int) -> list[dict]:
        names = self._client.list_entities(tenant_id)
        low = query.lower()
        mentioned = [n for n in names if n.lower() in low]
        out: list[dict] = []
        for name in mentioned[:5]:
            out.extend(self._client.find_relationships(tenant_id, name, limit=k))
        return out
