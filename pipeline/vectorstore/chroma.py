"""ChromaDB vector store — embedded, one collection per tenant (default backend)."""
from __future__ import annotations

import re
import threading

from app.config import settings
from pipeline.vectorstore.base import BaseVectorStore, register_vector_store

_client = None
_client_lock = threading.Lock()


def _get_client():
    """Process-wide cached embedded ChromaDB client (lock-guarded creation).

    The HTTP (search) thread and scheduler (index) thread can race here; creating
    two clients for the same path corrupts ChromaDB's native bindings, so creation
    must happen exactly once.
    """
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                import chromadb

                settings.chroma_dir_path.mkdir(parents=True, exist_ok=True)
                _client = chromadb.PersistentClient(path=str(settings.chroma_dir_path))
    return _client


def _collection_name(tenant_id: str) -> str:
    """Valid, collision-free ChromaDB collection name for a tenant."""
    safe = re.sub(r"[^a-zA-Z0-9]", "", tenant_id)[:48] or "default"
    return f"tenant{safe}"


@register_vector_store("chroma")
class ChromaVectorStore(BaseVectorStore):
    """Embedded ChromaDB. Tenant isolation = one collection per tenant.

    Default backend: zero external services, persists to a local directory, and a
    tenant only ever touches its own collection (structural isolation).
    """

    def _collection(self, tenant_id: str):
        """Get-or-create this tenant's cosine-distance collection."""
        return _get_client().get_or_create_collection(
            name=_collection_name(tenant_id),
            metadata={"hnsw:space": "cosine", "tenant_id": tenant_id},
        )

    def upsert(self, tenant_id, ids, embeddings, documents, metadatas) -> None:
        """Upsert vectors into the tenant's collection."""
        self._collection(tenant_id).upsert(
            ids=ids, embeddings=embeddings, documents=documents, metadatas=metadatas
        )

    def query(self, tenant_id, embedding, k) -> list[dict]:
        """Cosine-nearest neighbours from the tenant's collection."""
        res = self._collection(tenant_id).query(query_embeddings=[embedding], n_results=k)
        docs = (res.get("documents") or [[]])[0]
        metas = (res.get("metadatas") or [[]])[0]
        dists = (res.get("distances") or [[]])[0]
        return [
            {"text": d, "score": round(1.0 - float(dist), 4), "metadata": m}
            for d, m, dist in zip(docs, metas, dists)
        ]

    def peek(self, tenant_id, limit) -> dict:
        """Sample stored vectors for the inspect/"show data" UI."""
        col = self._collection(tenant_id)
        got = col.get(limit=limit, include=["documents", "metadatas"])
        ids = got.get("ids") or []
        docs = got.get("documents") or []
        metas = got.get("metadatas") or []
        items = [
            {"id": ids[i], "document": docs[i] if i < len(docs) else "",
             "metadata": metas[i] if i < len(metas) else {}}
            for i in range(len(ids))
        ]
        return {"namespace": _collection_name(tenant_id), "count": col.count(), "items": items}
