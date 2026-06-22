"""Qdrant vector store (POC STUB)."""
from __future__ import annotations

from pipeline.vectorstore.base import BaseVectorStore, register_vector_store


@register_vector_store("qdrant")
class QdrantVectorStore(BaseVectorStore):
    """STUB: Qdrant — a dedicated, horizontally-scalable vector database.

    Registered as a selectable option but not implemented for V1. The right choice
    when vector volume outgrows embedded ChromaDB (managed/clustered, payload
    filtering, high QPS) — see ADR 0011. Real use needs a Qdrant endpoint and
    per-tenant collection/namespace. Raises on use.
    """

    implemented = False

    def upsert(self, tenant_id, ids, embeddings, documents, metadatas) -> None:
        raise NotImplementedError("qdrant store is a POC stub (see ADR 0011)")

    def query(self, tenant_id, embedding, k) -> list[dict]:
        raise NotImplementedError("qdrant store is a POC stub (see ADR 0011)")

    def peek(self, tenant_id, limit) -> dict:
        raise NotImplementedError("qdrant store is a POC stub (see ADR 0011)")
