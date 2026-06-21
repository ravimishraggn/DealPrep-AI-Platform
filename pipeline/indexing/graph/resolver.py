"""EntityResolver — dedupe entities against existing graph nodes (V1: exact + fuzzy)."""
from __future__ import annotations

from difflib import SequenceMatcher

from pipeline.indexing.graph.models import Entity, normalize_name


class EntityResolver:
    """Resolves extracted entities to canonical names before node creation.

    V1 strategy (intentionally simple per ADR 0006): exact match on the normalized
    name, else a fuzzy match above ``threshold`` against existing names of the same
    type, else treat the entity as new. Avoids over-engineering entity resolution
    at this stage.
    """

    def __init__(self, threshold: float = 0.9) -> None:
        """Configure the fuzzy-match similarity threshold (0–1)."""
        self.threshold = threshold

    def resolve(self, entity: Entity, existing: dict[str, str]) -> str:
        """Return the canonical name for ``entity`` given known names.

        Args:
            entity: The entity to resolve.
            existing: Map of ``normalized_name -> canonical display name`` for
                already-known entities of the same tenant.

        Returns:
            The canonical display name to use for this entity (an existing one if
            matched, otherwise the entity's own name).
        """
        if entity.norm in existing:
            return existing[entity.norm]
        best_name, best_ratio = entity.name, 0.0
        for norm, display in existing.items():
            ratio = SequenceMatcher(None, entity.norm, norm).ratio()
            if ratio > best_ratio:
                best_name, best_ratio = display, ratio
        return best_name if best_ratio >= self.threshold else entity.name
