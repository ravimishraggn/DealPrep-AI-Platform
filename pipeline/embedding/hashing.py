"""Deterministic hashing embedder — dependency-free, offline (real, for POC/tests)."""
from __future__ import annotations

import hashlib
import math
import re

from pipeline.embedding.base import BaseEmbedder, register_embedder

_TOKEN = re.compile(r"[a-z0-9]+")


@register_embedder("hashing")
class HashingEmbedder(BaseEmbedder):
    """Hashes tokens into a fixed-dimension bag-of-words vector (no model, no deps).

    Captures lexical (keyword) overlap, not deep semantics — but it is fast,
    deterministic, free, fully offline, and needs no torch. Ideal for POCs, CI,
    air-gapped demos, or as a fallback when a model cannot be loaded.
    """

    dim = 256

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one L2-normalized hashed bag-of-words vector per text."""
        return [self._embed_one(t) for t in texts]

    def _embed_one(self, text: str) -> list[float]:
        """Hash each token into a bucket, count, then L2-normalize."""
        vec = [0.0] * self.dim
        for token in _TOKEN.findall(text.lower()):
            bucket = int(hashlib.md5(token.encode()).hexdigest(), 16) % self.dim
            vec[bucket] += 1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]
