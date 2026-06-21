"""ID + namespace generation helpers."""
from __future__ import annotations

import re
import uuid


def new_id() -> str:
    return str(uuid.uuid4())


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "team"


def make_namespace(name: str) -> str:
    """Human-readable, collision-resistant namespace for a tenant.

    e.g. "Credit Risk" -> "credit-risk-3f9a2b". The suffix keeps namespaces
    unique even when two teams pick the same display name.
    """
    return f"{slugify(name)}-{uuid.uuid4().hex[:6]}"
