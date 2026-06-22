"""Local sentence-transformers embedder (all-MiniLM-L6-v2) — default backend."""
from __future__ import annotations

import logging
import threading

from app.config import settings
from pipeline.embedding.base import BaseEmbedder, register_embedder

logger = logging.getLogger(__name__)

_model = None
_model_lock = threading.Lock()


@register_embedder("minilm")
class MinilmEmbedder(BaseEmbedder):
    """Local, free, private embeddings via sentence-transformers.

    Default backend: no per-call cost, no data egress, good general recall. The
    model (``DEALPREP_EMBEDDING_MODEL``, default all-MiniLM-L6-v2) loads lazily and
    is cached process-wide. 384-dim, cosine space.
    """

    dim = 384

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed texts with the cached sentence-transformers model (normalized)."""
        global _model
        if _model is None:
            with _model_lock:
                if _model is None:
                    from sentence_transformers import SentenceTransformer

                    logger.info("Loading embedding model %s", settings.embedding_model)
                    _model = SentenceTransformer(settings.embedding_model)
        return _model.encode(texts, normalize_embeddings=True).tolist()
