"""Vector indexing + semantic search — composes a pluggable embedder + store.

``VectorIndexer`` is now a thin orchestration over two pluggable choices:
an **embedder** (ADR 0010) and a **vector store** (ADR 0011), each selected by
name from its registry. This is what lets a tenant choose, e.g., local MiniLM
embeddings into ChromaDB, or hashing embeddings into the in-memory store, with no
code change. Tenant isolation is the store's responsibility.
"""
from __future__ import annotations

import logging

from app.config import settings
from pipeline.contracts import Chunk
from pipeline.embedding.base import get_embedder
from pipeline.vectorstore.base import get_vector_store

logger = logging.getLogger(__name__)


class VectorIndexer:
    """Embeds chunks with the chosen embedder and indexes them into the chosen store."""

    def __init__(self, embedder: str | None = None, vector_store: str | None = None) -> None:
        """Select the embedder and vector store by name (defaults from settings).

        Args:
            embedder: Registered embedder name (e.g. "minilm", "hashing"); ``None``
                uses the platform default.
            vector_store: Registered store name (e.g. "chroma", "memory"); ``None``
                uses the platform default.
        """
        self.embedder_name = embedder or settings.default_embedding
        self.store_name = vector_store or settings.default_vector_store

    @property
    def embedder(self):
        """The resolved (cached) embedder instance."""
        return get_embedder(self.embedder_name)

    @property
    def store(self):
        """The resolved (cached) vector store instance."""
        return get_vector_store(self.store_name)

    def embed_and_index(self, chunks: list[Chunk]) -> int:
        """Embed and upsert chunks into their tenants' vector namespaces.

        Args:
            chunks: Tagged chunks (each carries its ``tenant_id``).

        Returns:
            The number of chunks indexed.
        """
        if not chunks:
            return 0
        by_tenant: dict[str, list[Chunk]] = {}
        for chunk in chunks:
            by_tenant.setdefault(chunk.tenant_id, []).append(chunk)

        embedder, store = self.embedder, self.store
        total = 0
        for tenant_id, tenant_chunks in by_tenant.items():
            texts = [c.text for c in tenant_chunks]
            embeddings = embedder.embed(texts)
            store.upsert(
                tenant_id=tenant_id,
                ids=[c.chunk_id() for c in tenant_chunks],
                embeddings=embeddings,
                documents=texts,
                metadatas=[c.vector_metadata() for c in tenant_chunks],
            )
            total += len(tenant_chunks)
        logger.info(
            "vector-indexed %d chunk(s) via %s -> %s across %d tenant(s)",
            total, self.embedder_name, self.store_name, len(by_tenant),
        )
        return total

    def search(self, tenant_id: str, query: str, k: int = 5) -> list[dict]:
        """Semantic search within one tenant's vector namespace.

        Args:
            tenant_id: REQUIRED — the tenant whose vectors to search.
            query: Natural-language query text.
            k: Maximum number of results.

        Returns:
            ``[{text, score, metadata}]`` ordered by descending similarity.
        """
        if not tenant_id:
            raise ValueError("tenant_id is required for vector search")
        embedding = self.embedder.embed([query])[0]
        return self.store.query(tenant_id, embedding, k)

    def peek(self, tenant_id: str, limit: int = 50) -> dict:
        """Return a sample of what is stored in a tenant's vector namespace."""
        if not tenant_id:
            raise ValueError("tenant_id is required")
        data = self.store.peek(tenant_id, limit)
        data["embedder"] = self.embedder_name
        data["store"] = self.store_name
        return data
