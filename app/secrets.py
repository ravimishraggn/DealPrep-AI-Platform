"""Secrets handling (ADR D5, requirement 4).

Manifests reference a secret by name (`secret_ref`); the real value is resolved
from a vault at test/fetch time and never persisted. The in-memory impl is a stub
that can be swapped for AWS Secrets Manager / HashiCorp Vault by implementing the
same `SecretsVault` interface — callers don't change.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class SecretNotFoundError(KeyError):
    """Raised when a secret_ref does not resolve in the vault."""


class SecretsVault(ABC):
    @abstractmethod
    def get_secret(self, ref: str) -> str:
        """Return the secret value for `ref`, or raise SecretNotFoundError."""

    @abstractmethod
    def set_secret(self, ref: str, value: str) -> None:
        ...

    def has_secret(self, ref: str) -> bool:
        try:
            self.get_secret(ref)
            return True
        except SecretNotFoundError:
            return False


class InMemoryVault(SecretsVault):
    """Dev-only vault backed by a process-local dict. Not for production."""

    def __init__(self, initial: dict[str, str] | None = None) -> None:
        self._store: dict[str, str] = dict(initial or {})

    def get_secret(self, ref: str) -> str:
        try:
            return self._store[ref]
        except KeyError as exc:
            raise SecretNotFoundError(ref) from exc

    def set_secret(self, ref: str, value: str) -> None:
        self._store[ref] = value


# Process-wide default vault instance (DI seam — swap this binding to change backends).
_vault: SecretsVault = InMemoryVault()


def get_vault() -> SecretsVault:
    return _vault
