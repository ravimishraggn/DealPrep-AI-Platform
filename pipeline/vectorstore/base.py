"""Base interface + cached registry for vector store backends."""
from __future__ import annotations

import importlib
import logging
import pkgutil
import threading
from abc import ABC, abstractmethod
from typing import ClassVar

logger = logging.getLogger(__name__)

VECTOR_STORE_REGISTRY: dict[str, type["BaseVectorStore"]] = {}
_instances: dict[str, "BaseVectorStore"] = {}
_lock = threading.Lock()


class BaseVectorStore(ABC):
    """Persists and similarity-searches embeddings, isolated per tenant.

    The store is embedding-agnostic: it receives vectors and returns nearest
    neighbours. Implementations decide how tenant isolation is realized (separate
    collections, namespaces, or row filters) — but it MUST be enforced.
    """

    #: Selection key used in the pipeline profile (e.g. "chroma").
    name: ClassVar[str]
    #: True for real implementations; False for POC stubs that raise on use.
    implemented: ClassVar[bool] = True

    @abstractmethod
    def upsert(
        self, tenant_id: str, ids: list[str], embeddings: list[list[float]],
        documents: list[str], metadatas: list[dict],
    ) -> None:
        """Insert/replace vectors (with text + metadata) for one tenant."""
        raise NotImplementedError

    @abstractmethod
    def query(self, tenant_id: str, embedding: list[float], k: int) -> list[dict]:
        """Return up to ``k`` nearest neighbours for a tenant.

        Returns:
            ``[{text, score, metadata}]`` ordered by descending similarity.
        """
        raise NotImplementedError

    @abstractmethod
    def peek(self, tenant_id: str, limit: int) -> dict:
        """Return a sample of stored vectors for the "show data" UI.

        Returns:
            ``{namespace, count, items: [{id, document, metadata}]}``.
        """
        raise NotImplementedError


def register_vector_store(name: str):
    """Class decorator registering a ``BaseVectorStore`` under ``name``."""

    def _decorator(cls: type[BaseVectorStore]) -> type[BaseVectorStore]:
        if not issubclass(cls, BaseVectorStore):
            raise TypeError(f"{cls.__name__} must subclass BaseVectorStore")
        cls.name = name
        if name in VECTOR_STORE_REGISTRY and VECTOR_STORE_REGISTRY[name] is not cls:
            raise ValueError(f"Vector store '{name}' already registered")
        VECTOR_STORE_REGISTRY[name] = cls
        logger.info("Registered vector store '%s' -> %s", name, cls.__name__)
        return cls

    return _decorator


def discover_vector_stores(package: str = "pipeline.vectorstore") -> dict[str, type[BaseVectorStore]]:
    """Import every module in the package so vector-store decorators run."""
    pkg = importlib.import_module(package)
    for module in pkgutil.iter_modules(pkg.__path__):
        if module.name in {"base"}:
            continue
        try:
            importlib.import_module(f"{package}.{module.name}")
        except Exception:  # noqa: BLE001
            logger.exception("Failed to import vector store module %s", module.name)
    return VECTOR_STORE_REGISTRY


def get_vector_store(name: str) -> BaseVectorStore:
    """Return a process-wide cached vector store instance for ``name``.

    Cached because a store may hold a client/connection. Raises ``KeyError`` for an
    unknown name or ``NotImplementedError`` for a stub.
    """
    if name in _instances:
        return _instances[name]
    if name not in VECTOR_STORE_REGISTRY:
        known = ", ".join(sorted(VECTOR_STORE_REGISTRY)) or "(none)"
        raise KeyError(f"Unknown vector store '{name}'. Registered: {known}")
    cls = VECTOR_STORE_REGISTRY[name]
    if not cls.implemented:
        raise NotImplementedError(
            f"Vector store '{name}' is a POC stub and not implemented yet (see ADR 0011)."
        )
    with _lock:
        if name not in _instances:
            _instances[name] = cls()
    return _instances[name]
