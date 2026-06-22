"""Agent registry — same decorator + auto-discovery pattern as connectors (ADR 0004).

Importing a module that contains ``@register_agent`` is enough to register it.
``discover_agents()`` in ``app/main.py`` lifespan does the importing.
"""
from __future__ import annotations

import importlib
import logging
import pkgutil
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agents.base import BaseAgent

logger = logging.getLogger(__name__)

AGENT_REGISTRY: dict[str, type["BaseAgent"]] = {}


def register_agent(name: str):
    """Class decorator that adds an agent to the registry under ``name``."""
    def decorator(cls):
        AGENT_REGISTRY[name] = cls
        cls.name = name
        logger.debug("registered agent: %s (implemented=%s)", name, getattr(cls, "implemented", True))
        return cls
    return decorator


def discover_agents() -> None:
    """Import every module in the ``agents/`` package so decorators self-register."""
    import agents as pkg
    for mod in pkgutil.iter_modules(pkg.__path__):
        if mod.name in ("base", "registry") or mod.ispkg:
            continue
        full = f"agents.{mod.name}"
        try:
            importlib.import_module(full)
        except Exception:
            logger.exception("failed to import agent module %s", full)
