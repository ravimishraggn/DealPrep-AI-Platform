"""Extractor registry + auto-discovery (mirrors the connector registry).

The single seam between the FormatRouter and concrete extractors. Extractors
register themselves by ``format_type`` via the ``@register_extractor`` decorator;
``discover_extractors`` imports every module in the package so the decorators run.
"""
from __future__ import annotations

import importlib
import logging
import pkgutil

from pipeline.extractors.base import BaseExtractor

logger = logging.getLogger(__name__)

EXTRACTOR_REGISTRY: dict[str, type[BaseExtractor]] = {}


def register_extractor(format_type: str):
    """Class decorator registering a ``BaseExtractor`` under ``format_type``.

    Args:
        format_type: The connector ``format_type`` this extractor handles
            (e.g. "json", "pdf", "csv", "text").
    """

    def _decorator(cls: type[BaseExtractor]) -> type[BaseExtractor]:
        if not issubclass(cls, BaseExtractor):
            raise TypeError(f"{cls.__name__} must subclass BaseExtractor")
        key = format_type.lower()
        if key in EXTRACTOR_REGISTRY and EXTRACTOR_REGISTRY[key] is not cls:
            raise ValueError(f"Extractor for format '{key}' already registered")
        EXTRACTOR_REGISTRY[key] = cls
        logger.info("Registered extractor '%s' -> %s", key, cls.__name__)
        return cls

    return _decorator


def discover_extractors(package: str = "pipeline.extractors") -> dict[str, type[BaseExtractor]]:
    """Import every module in ``package`` so extractor decorators populate the registry.

    A failure importing one extractor module is logged and skipped so a single
    broken plugin cannot prevent the platform from starting.
    """
    pkg = importlib.import_module(package)
    for module in pkgutil.iter_modules(pkg.__path__):
        if module.name in {"base", "registry"}:
            continue
        full = f"{package}.{module.name}"
        try:
            importlib.import_module(full)
        except Exception:  # noqa: BLE001 - isolate bad plugins
            logger.exception("Failed to import extractor module %s; skipping", full)
    return EXTRACTOR_REGISTRY


def get_extractor(format_type: str) -> type[BaseExtractor] | None:
    """Return the extractor class for ``format_type``, or ``None`` if none registered."""
    return EXTRACTOR_REGISTRY.get(format_type.lower())
