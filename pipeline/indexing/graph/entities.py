"""EntityExtractor — NER over text documents using spaCy."""
from __future__ import annotations

import logging
import threading

from pipeline.indexing.graph.models import Entity

logger = logging.getLogger(__name__)

# spaCy entity labels → our coarse types.
_LABEL_MAP = {
    "ORG": "COMPANY",
    "PERSON": "PERSON",
    "MONEY": "MONEY",
    "DATE": "DATE",
}

_nlp = None
_nlp_lock = threading.Lock()


def get_nlp():
    """Return a process-wide cached spaCy pipeline (``en_core_web_sm``).

    Loaded lazily and guarded by a lock. Raises a clear error if the model has
    not been downloaded (``python -m spacy download en_core_web_sm``).
    """
    global _nlp
    if _nlp is None:
        with _nlp_lock:
            if _nlp is None:
                import spacy

                try:
                    _nlp = spacy.load("en_core_web_sm", disable=["lemmatizer", "tagger"])
                except OSError as exc:  # pragma: no cover
                    raise RuntimeError(
                        "spaCy model 'en_core_web_sm' not found. Run: "
                        "python -m spacy download en_core_web_sm"
                    ) from exc
    return _nlp


class EntityExtractor:
    """Extracts coarse-typed named entities (companies, people, money, dates)."""

    def extract(self, text: str) -> list[Entity]:
        """Run NER over ``text`` and return de-duplicated entities.

        Args:
            text: The chunk/document text to analyze.

        Returns:
            A list of ``Entity`` objects, de-duplicated within this text by
            (normalized name, type).
        """
        nlp = get_nlp()
        doc = nlp(text)
        seen: set[tuple[str, str]] = set()
        out: list[Entity] = []
        for ent in doc.ents:
            etype = _LABEL_MAP.get(ent.label_)
            if etype is None:
                continue
            entity = Entity(name=ent.text.strip(), type=etype)
            key = (entity.norm, entity.type)
            if not entity.norm or key in seen:
                continue
            seen.add(key)
            out.append(entity)
        return out
