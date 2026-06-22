"""Unified retrieval across vector, structured, and graph stores (requirement 8).

Runs the three searches in parallel and returns their result sets **separately
labeled** — no merging or ranking (that is the orchestration agent's job later).
``tenant_id`` is mandatory and threaded into every engine; there is no
cross-tenant path. A store being unavailable degrades to an empty set for that
engine plus a warning, never a failed request.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from pipeline.indexing.graph.neo4j_client import Neo4jClient
from pipeline.indexing.structured import StructuredIndexer
from pipeline.indexing.vector import VectorIndexer

logger = logging.getLogger(__name__)


class UnifiedSearch:
    """Fan-out search over ChromaDB, Postgres, and Neo4j for one tenant."""

    def __init__(self) -> None:
        """Construct the profile-independent engine clients."""
        self.structured = StructuredIndexer()
        self.graph = Neo4jClient()

    def search(
        self, tenant_id: str, query: str, k: int = 5, record_type: str | None = None,
        embedding: str | None = None, vector_store: str | None = None,
    ) -> dict:
        """Run vector + structured + graph search in parallel for one tenant.

        Args:
            tenant_id: REQUIRED — the only tenant whose data is searched.
            query: The natural-language / keyword query.
            k: Max results per engine.
            record_type: Optional structured-record type filter.
            embedding: Embedder backend to query with — MUST match what the tenant
                indexed with (from its profile); ``None`` uses the platform default.
            vector_store: Vector store backend to query — likewise from the profile.

        Returns:
            A dict with separately labeled ``vector``, ``structured``, and
            ``graph`` result lists plus a ``warnings`` list for degraded engines.
        """
        if not tenant_id:
            raise ValueError("tenant_id is required for search")

        # Vector search must use the SAME embedder + store the tenant indexed with.
        vector = VectorIndexer(embedding, vector_store)
        warnings: list[str] = []

        def _vector():
            return vector.search(tenant_id, query, k)

        def _structured():
            return self.structured.search(tenant_id, query, k, record_type)

        def _graph():
            return self._graph_search(tenant_id, query, k)

        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {
                "vector": pool.submit(_vector),
                "structured": pool.submit(_structured),
                "graph": pool.submit(_graph),
            }
            results: dict[str, list] = {}
            for name, future in futures.items():
                try:
                    results[name] = future.result()
                except Exception as exc:  # noqa: BLE001 - degrade, don't fail the request
                    logger.exception("search engine %s failed", name)
                    results[name] = []
                    warnings.append(f"{name} search unavailable: {exc}")

        return {
            "vector": results["vector"],
            "structured": results["structured"],
            "graph": results["graph"],
            "warnings": warnings,
        }

    def _graph_search(self, tenant_id: str, query: str, k: int) -> list[dict]:
        """1-hop graph lookup for any known entity name mentioned in the query.

        Lists the tenant's entities, finds those whose name appears in the query
        (case-insensitive), and returns their direct relationships. Deliberately
        no multi-hop reasoning (left to the future orchestration agent).
        """
        names = self.graph.list_entities(tenant_id)
        low_query = query.lower()
        mentioned = [n for n in names if n.lower() in low_query]
        out: list[dict] = []
        for name in mentioned[:5]:
            out.extend(self.graph.find_relationships(tenant_id, name, limit=k))
        return out


_search: UnifiedSearch | None = None


def get_search() -> UnifiedSearch:
    """Return a process-wide cached UnifiedSearch instance."""
    global _search
    if _search is None:
        _search = UnifiedSearch()
    return _search
