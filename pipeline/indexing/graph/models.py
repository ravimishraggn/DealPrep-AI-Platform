"""Graph data contracts: entities and relationship triples."""
from __future__ import annotations

import re

from pydantic import BaseModel, Field

# Canonical relationship vocabulary for V1 (subject -[type]-> object).
RELATIONSHIP_TYPES = {
    "invested_in",
    "board_member_of",
    "related_party_of",
    "competitor_of",
    "advisor_to",
    "subsidiary_of",
}


def normalize_name(name: str) -> str:
    """Lower-case, strip punctuation/whitespace, and drop common company suffixes.

    Produces a comparison key for entity resolution so "Acme Corp." and
    "acme corp" resolve to the same node.
    """
    text = name.lower().strip()
    text = re.sub(r"[.,]", "", text)
    text = re.sub(r"\b(inc|corp|corporation|llc|ltd|limited|co|company|plc)\b", "", text)
    return re.sub(r"\s+", " ", text).strip()


class Entity(BaseModel):
    """A named entity extracted from text (a future graph node).

    ``type`` is a coarse category (COMPANY, PERSON, MONEY, DATE). ``norm`` is the
    resolution key derived from ``name``.
    """

    name: str
    type: str
    norm: str = Field(default="")

    def model_post_init(self, __context) -> None:
        """Populate ``norm`` from ``name`` when not explicitly provided."""
        if not self.norm:
            self.norm = normalize_name(self.name)


class Relationship(BaseModel):
    """A directed (subject, relationship, object) triple between two entities."""

    subject: str
    relationship: str
    object: str
