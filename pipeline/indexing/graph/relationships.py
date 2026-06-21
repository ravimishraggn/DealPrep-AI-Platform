"""RelationshipExtractor — entity triples via LLM, with a rule-based fallback.

Given a chunk of text and the entities found in it, identify directed
relationships (subject, relationship, object). Uses Claude when an API key is
configured (see app.llm); otherwise applies deterministic keyword heuristics so
the graph is still populated without an LLM.
"""
from __future__ import annotations

import json
import logging
import re
from itertools import combinations

from app.llm import LLMClient, get_llm_client
from pipeline.indexing.graph.models import RELATIONSHIP_TYPES, Entity, Relationship

logger = logging.getLogger(__name__)

_SYSTEM = (
    "You extract business relationships between named entities from financial text. "
    "Only use these relationship types: " + ", ".join(sorted(RELATIONSHIP_TYPES)) + ". "
    "Respond with ONLY a JSON array of objects {subject, relationship, object}. "
    "Subject and object MUST be exact names from the provided entity list. "
    "Return [] if there are no clear relationships."
)


class RelationshipExtractor:
    """Identifies (subject, relationship, object) triples among entities."""

    def __init__(self, llm_client: LLMClient | None = None) -> None:
        """Use the provided LLM client, else auto-detect one (None → rule-based)."""
        self.llm = llm_client if llm_client is not None else get_llm_client()

    def extract(self, text: str, entities: list[Entity]) -> list[Relationship]:
        """Extract relationships among ``entities`` as described in ``text``.

        Args:
            text: The chunk text the entities were found in.
            entities: Entities found in this chunk (subjects/objects must be these).

        Returns:
            A list of validated ``Relationship`` triples (possibly empty).
        """
        if len(entities) < 2:
            return []
        if self.llm is not None:
            try:
                return self._extract_llm(text, entities)
            except Exception:  # noqa: BLE001 - never let an LLM error fail the run
                logger.exception("LLM relationship extraction failed; using rule-based fallback")
        return self._extract_rules(text, entities)

    # --- LLM path -----------------------------------------------------------
    def _extract_llm(self, text: str, entities: list[Entity]) -> list[Relationship]:
        """Call the LLM and parse/validate its JSON triples."""
        names = [e.name for e in entities]
        prompt = (
            f"Entities: {names}\n\nText:\n{text}\n\n"
            "Return the JSON array of relationships."
        )
        raw = self.llm.complete(_SYSTEM, prompt)
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        if not match:
            return []
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return []
        valid_names = {e.name for e in entities}
        out: list[Relationship] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            subj, rel, obj = item.get("subject"), item.get("relationship"), item.get("object")
            if subj in valid_names and obj in valid_names and rel in RELATIONSHIP_TYPES:
                out.append(Relationship(subject=subj, relationship=rel, object=obj))
        return out

    # --- Rule-based fallback ------------------------------------------------
    def _extract_rules(self, text: str, entities: list[Entity]) -> list[Relationship]:
        """Deterministic keyword heuristics over co-occurring entity pairs.

        Considers COMPANY/PERSON pairs that co-occur in the chunk and assigns at
        most one relationship per pair based on signal words present in the text.
        Intentionally simple for V1 (documented in ADR 0006).
        """
        low = text.lower()
        relevant = [e for e in entities if e.type in {"COMPANY", "PERSON"}]
        out: list[Relationship] = []
        for a, b in combinations(relevant, 2):
            rel = self._classify(low, a, b)
            if rel:
                out.append(rel)
        return out

    @staticmethod
    def _classify(low: str, a: Entity, b: Entity) -> Relationship | None:
        """Pick a single relationship for a co-occurring entity pair, or None."""
        person = a if a.type == "PERSON" else (b if b.type == "PERSON" else None)
        company = a if a.type == "COMPANY" else (b if b.type == "COMPANY" else None)

        if person and company and "board" in low:
            return Relationship(subject=person.name, relationship="board_member_of", object=company.name)
        if person and company and ("advis" in low):
            return Relationship(subject=person.name, relationship="advisor_to", object=company.name)
        if any(k in low for k in ("related party", "related-party", "related entity", "owned by", "sponsor")):
            return Relationship(subject=a.name, relationship="related_party_of", object=b.name)
        if "invest" in low:
            return Relationship(subject=a.name, relationship="invested_in", object=b.name)
        if a.type == "COMPANY" and b.type == "COMPANY" and any(
            k in low for k in ("compar", "peer", "comp", "versus", " vs ")
        ):
            return Relationship(subject=a.name, relationship="competitor_of", object=b.name)
        return None
