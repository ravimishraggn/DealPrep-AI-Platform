"""Vector indexing + semantic search over ChromaDB (ADR 0005).

Embeds chunks with a local sentence-transformers model and stores them in a
per-tenant ChromaDB collection — tenant isolation is structural (a tenant only
ever touches its own collection). Heavy imports (torch, chromadb) are lazy so
importing this module is cheap.
"""
from __future__ import annotations

import logging
import re
import threading

from app.config import settings
from pipeline.contracts import Chunk

logger = logging.getLogger(__name__)

_embedder = None
_embedder_lock = threading.Lock()
_chroma_client = None


def get_embedder():
    """Return a process-wide cached sentence-transformers model.

    Loaded lazily on first use (the model download/load is expensive) and guarded
    by a lock so concurrent pipeline threads share one instance.
    """
    global _embedder
    if _embedder is None:
        with _embedder_lock:
            if _embedder is None:
                from sentence_transformers import SentenceTransformer

                logger.info("Loading embedding model %s", settings.embedding_model)
                _embedder = SentenceTransformer(settings.embedding_model)
    return _embedder


def get_chroma_client():
    """Return a process-wide cached embedded ChromaDB persistent client."""
    global _chroma_client
    if _chroma_client is None:
        import chromadb

        settings.chroma_dir_path.mkdir(parents=True, exist_ok=True)
        _chroma_client = chromadb.PersistentClient(path=str(settings.chroma_dir_path))
    return _chroma_client


def _collection_name(tenant_id: str) -> str:
    """Derive a valid, collision-free ChromaDB collection name for a tenant.

    ChromaDB names must be 3–63 chars of [a-zA-Z0-9._-] starting/ending
    alphanumeric; we sanitize the tenant_id and prefix it.
    """
    safe = re.sub(r"[^a-zA-Z0-9]", "", tenant_id)[:48] or "default"
    return f"tenant{safe}"


class VectorIndexer:
    """Embeds chunks and indexes them into a tenant-scoped ChromaDB collection."""

    def _collection(self, tenant_id: str):
        """Get-or-create this tenant's cosine-distance collection."""
        client = get_chroma_client()
        return client.get_or_create_collection(
            name=_collection_name(tenant_id),
            metadata={"hnsw:space": "cosine", "tenant_id": tenant_id},
        )

    def embed_and_index(self, chunks: list[Chunk]) -> int:
        """Embed and upsert chunks into their tenants' collections.

        Args:
            chunks: Tagged chunks (each carries its ``tenant_id``).

        Returns:
            The number of chunks indexed.
        """
        if not chunks:
            return 0
        # Group by tenant so each collection is written once (chunks are normally
        # all one tenant, but never assume it).
        by_tenant: dict[str, list[Chunk]] = {}
        for chunk in chunks:
            by_tenant.setdefault(chunk.tenant_id, []).append(chunk)

        embedder = get_embedder()
        total = 0
        for tenant_id, tenant_chunks in by_tenant.items():
            texts = [c.text for c in tenant_chunks]
            embeddings = embedder.encode(texts, normalize_embeddings=True).tolist()
            self._collection(tenant_id).upsert(
                ids=[c.chunk_id() for c in tenant_chunks],
                embeddings=embeddings,
                documents=texts,
                metadatas=[c.vector_metadata() for c in tenant_chunks],
            )
            total += len(tenant_chunks)
        logger.info("vector-indexed %d chunk(s) across %d tenant(s)", total, len(by_tenant))
        return total

    def search(self, tenant_id: str, query: str, k: int = 5) -> list[dict]:
        """Semantic search within one tenant's collection.

        Args:
            tenant_id: REQUIRED — the tenant whose collection to search. Isolation
                is structural; there is no cross-tenant query path.
            query: Natural-language query text.
            k: Maximum number of results.

        Returns:
            A list of result dicts with ``text``, ``score`` (1 - cosine distance),
            and traceability ``metadata``.
        """
        if not tenant_id:
            raise ValueError("tenant_id is required for vector search")
        embedder = get_embedder()
        query_emb = embedder.encode([query], normalize_embeddings=True).tolist()
        res = self._collection(tenant_id).query(query_embeddings=query_emb, n_results=k)

        docs = (res.get("documents") or [[]])[0]
        metas = (res.get("metadatas") or [[]])[0]
        dists = (res.get("distances") or [[]])[0]
        out: list[dict] = []
        for text, meta, dist in zip(docs, metas, dists):
            out.append(
                {
                    "text": text,
                    "score": round(1.0 - float(dist), 4),  # cosine distance -> similarity
                    "metadata": meta,
                }
            )
        return out
