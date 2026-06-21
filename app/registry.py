"""Connector registry + auto-discovery (ADR D2, requirement 2).

This module is the *only* seam between the generic engine and concrete
connectors. The engine looks connectors up by key here; it never imports a
connector class directly. Adding a connector = drop a file in connectors/ and
decorate it — no edits here.
"""
from __future__ import annotations

import importlib
import logging
import pkgutil
from typing import Any

from pydantic import BaseModel

from app.secrets import SecretsVault, get_vault
from connectors.base import BaseConnector

logger = logging.getLogger(__name__)

CONNECTOR_REGISTRY: dict[str, type[BaseConnector]] = {}


def register_connector(key: str):
    """Class decorator that registers a BaseConnector under `key`."""

    def _decorator(cls: type[BaseConnector]) -> type[BaseConnector]:
        if not issubclass(cls, BaseConnector):
            raise TypeError(f"{cls.__name__} must subclass BaseConnector")
        if not hasattr(cls, "config_schema"):
            raise TypeError(f"{cls.__name__} must define a `config_schema`")
        if key in CONNECTOR_REGISTRY and CONNECTOR_REGISTRY[key] is not cls:
            raise ValueError(f"Connector key '{key}' already registered")
        CONNECTOR_REGISTRY[key] = cls
        logger.info("Registered connector '%s' -> %s", key, cls.__name__)
        return cls

    return _decorator


def discover(package: str = "connectors") -> dict[str, type[BaseConnector]]:
    """Import every module in `package` so decorators populate the registry.

    A failure in one connector module is logged and skipped so it can't take the
    whole platform down at startup.
    """
    pkg = importlib.import_module(package)
    for module in pkgutil.iter_modules(pkg.__path__):
        if module.name == "base":
            continue
        full_name = f"{package}.{module.name}"
        try:
            importlib.import_module(full_name)
        except Exception:  # noqa: BLE001 - isolate bad plugins
            logger.exception("Failed to import connector module %s; skipping", full_name)
    return CONNECTOR_REGISTRY


def get_connector_class(connector_type: str) -> type[BaseConnector]:
    try:
        return CONNECTOR_REGISTRY[connector_type]
    except KeyError as exc:
        known = ", ".join(sorted(CONNECTOR_REGISTRY)) or "(none)"
        raise KeyError(f"Unknown connector_type '{connector_type}'. Registered: {known}") from exc


def build_connector(
    connector_type: str,
    raw_config: dict[str, Any],
    vault: SecretsVault | None = None,
) -> tuple[BaseConnector, BaseModel]:
    """Factory: validate raw config and instantiate the connector with DI.

    Returns (connector_instance, validated_config). Raises KeyError for unknown
    type or ConfigValidationError for bad config.
    """
    cls = get_connector_class(connector_type)
    config = cls.validate_config(raw_config)
    connector = cls(config=config, vault=vault or get_vault())
    return connector, config
