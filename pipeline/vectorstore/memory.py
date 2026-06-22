"""In-process vector store — pure-Python cosine search (real, for POC/tests)."""
from __future__ import annotations

import threading

from pipeline.vectorstore.base import BaseVectorStore, register_vector_store


@register_vector_store("memory")
class InMemoryVectorStore(BaseVectorStore):
    """Dependency-free in-RAM vector store with exact cosine search.

    Tenant isolation = a separate per-tenant dict. Not durable and not scalable,
    but real and instant — ideal for POCs, unit tests, and air-gapped demos where
    standing up ChromaDB is overkill. Vectors are assumed L2-normalized, so the
    dot product is the cosine similarity.
    """

    def __init__(self) -> None:
        """Initialize the per-tenant store and its lock."""
        # tenant_id -> id -> {vec, document, metadata}
        self._data: dict[str, dict[str, dict]] = {}
        self._lock = threading.Lock()

    def upsert(self, tenant_id, ids, embeddings, documents, metadatas) -> None:
        """Insert/replace vectors for a tenant by id."""
        with self._lock:
            bucket = self._data.setdefault(tenant_id, {})
            for i, _id in enumerate(ids):
                bucket[_id] = {"vec": embeddings[i], "document": documents[i], "metadata": metadatas[i]}

    def query(self, tenant_id, embedding, k) -> list[dict]:
        """Exact top-k cosine search within the tenant's bucket."""
        bucket = self._data.get(tenant_id, {})
        scored = [
            {"text": v["document"], "score": round(_dot(embedding, v["vec"]), 4), "metadata": v["metadata"]}
            for v in bucket.values()
        ]
        scored.sort(key=lambda r: r["score"], reverse=True)
        return scored[:k]

    def peek(self, tenant_id, limit) -> dict:
        """Sample stored vectors for the inspect/"show data" UI."""
        bucket = self._data.get(tenant_id, {})
        items = [
            {"id": _id, "document": v["document"], "metadata": v["metadata"]}
            for _id, v in list(bucket.items())[:limit]
        ]
        return {"namespace": f"memory:{tenant_id}", "count": len(bucket), "items": items}


def _dot(a: list[float], b: list[float]) -> float:
    """Dot product (= cosine for normalized vectors); 0 if dimensions differ."""
    if len(a) != len(b):
        return 0.0
    return float(sum(x * y for x, y in zip(a, b)))
