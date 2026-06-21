"""BaseConnector contract (ADR D2/D3, requirement 2).

Every connector implements this interface and declares its own Pydantic config
schema. The generic engine only ever touches these methods — it has no knowledge
of any concrete connector.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, ClassVar

from pydantic import BaseModel, ValidationError

from app.secrets import SecretsVault


class ConnectorError(Exception):
    """Raised by test_connection()/fetch() when a connector cannot operate."""


class ConfigValidationError(Exception):
    """Raised when a raw manifest config fails its connector's schema."""

    def __init__(self, errors: list[dict[str, Any]]) -> None:
        self.errors = errors
        super().__init__(f"{len(errors)} config validation error(s)")


def _jsonable_errors(exc: ValidationError) -> list[dict[str, Any]]:
    """Pydantic error list, stripped of non-JSON-serializable bits (e.g. a
    ValueError carried in `ctx`), so it can be returned in an HTTP response."""
    clean: list[dict[str, Any]] = []
    for err in exc.errors(include_url=False):
        item = {k: v for k, v in err.items() if k != "ctx"}
        item["loc"] = list(err.get("loc", ()))
        if "ctx" in err:
            item["ctx"] = {k: str(v) for k, v in err["ctx"].items()}
        clean.append(item)
    return clean


class BaseConnector(ABC):
    # Each connector declares its own typed config model (ADR D3).
    config_schema: ClassVar[type[BaseModel]]

    def __init__(self, config: BaseModel, vault: SecretsVault) -> None:
        # `config` is already a validated model instance; `vault` is injected so
        # the connector resolves secret_ref -> value at runtime (ADR D5).
        self.config = config
        self.vault = vault

    @classmethod
    def validate_config(cls, raw: dict[str, Any]) -> BaseModel:
        """Validate a raw manifest dict against this connector's schema.

        Returns the typed config instance, or raises ConfigValidationError with
        structured, caller-friendly details.
        """
        try:
            return cls.config_schema.model_validate(raw)
        except ValidationError as exc:
            raise ConfigValidationError(_jsonable_errors(exc)) from exc

    @abstractmethod
    def test_connection(self) -> None:
        """Dry-run reachability/auth check. Raise ConnectorError on failure."""

    @abstractmethod
    def fetch(self, since_timestamp: datetime | None) -> list[dict[str, Any]]:
        """Return records produced since `since_timestamp` (None = full pull)."""
