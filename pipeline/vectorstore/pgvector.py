"""Postgres pgvector store (POC STUB)."""
from __future__ import annotations

from pipeline.vectorstore.base import BaseVectorStore, register_vector_store


@register_vector_store("pgvector")
class PgVectorStore(BaseVectorStore):
    """STUB: vectors in Postgres via the pgvector extension.

    Registered as a selectable option but not implemented for V1. Attractive
    because it keeps vectors in the *same* Postgres already used for structured
    data (one store to operate, transactional with metadata) — see ADR 0011. Real
    use needs the pgvector extension and an ivfflat/hnsw index. Raises on use.
    """

    implemented = False

    def upsert(self, tenant_id, ids, embeddings, documents, metadatas) -> None:
        raise NotImplementedError("pgvector store is a POC stub (see ADR 0011)")

    def query(self, tenant_id, embedding, k) -> list[dict]:
        raise NotImplementedError("pgvector store is a POC stub (see ADR 0011)")

    def peek(self, tenant_id, limit) -> dict:
        raise NotImplementedError("pgvector store is a POC stub (see ADR 0011)")
