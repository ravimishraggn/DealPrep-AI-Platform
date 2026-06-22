"""Sentence-window chunking — group whole sentences up to a size budget."""
from __future__ import annotations

import re

from app.config import settings
from pipeline.chunking.base import BaseChunker, register_chunker

# Naive sentence splitter (no NLP dependency): split on ., !, ? followed by space.
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


@register_chunker("sentence_window")
class SentenceWindowChunker(BaseChunker):
    """Groups consecutive sentences into chunks up to ``target`` chars.

    Never cuts mid-sentence, with a configurable number of overlapping sentences
    between chunks for retrieval continuity. Good middle ground for unstructured
    prose without clear headers (news, transcripts, free-text notes).
    """

    def __init__(self, target_chars: int | None = None, overlap_sentences: int = 1) -> None:
        """Configure size budget and how many sentences overlap between chunks."""
        self.target = target_chars or settings.chunk_target_chars
        self.overlap_sentences = max(overlap_sentences, 0)

    def chunk(self, text: str, default_section: str | None = None) -> list[tuple[str, str | None]]:
        """Pack sentences into chunks under the size budget, with sentence overlap."""
        sentences = [s.strip() for s in _SENTENCE.split(text.strip()) if s.strip()]
        if not sentences:
            return []
        chunks: list[tuple[str, str | None]] = []
        buf: list[str] = []
        size = 0
        for sentence in sentences:
            if size + len(sentence) > self.target and buf:
                chunks.append((" ".join(buf), default_section))
                buf = buf[-self.overlap_sentences :] if self.overlap_sentences else []
                size = sum(len(s) for s in buf)
            buf.append(sentence)
            size += len(sentence)
        if buf:
            chunks.append((" ".join(buf), default_section))
        return chunks
