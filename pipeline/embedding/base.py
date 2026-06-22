"""Base interface + cached registry for embedding backends."""
from __future__ import annotations

import importlib
import logging
import pkgutil
import threading
from abc import ABC, abstractmethod
from typing import ClassVar

logger = logging.getLogger(__name__)

EMBEDDER_REGISTRY: dict[str, type["BaseEmbedder"]] = {}
_instances: dict[str, "BaseEmbedder"] = {}
_lock = threading.Lock()


class BaseEmbedder(ABC):
    """Turns text into fixed-dimension vectors.

    Implementations differ in model, dimension, cost, and whether they call out to
    a third party. ``dim`` must be stable for a backend (changing it invalidates
    stored vectors). The contract is identical so the vector store is agnostic.
    """

    #: Selection key used in the pipeline profile (e.g. "minilm").
    name: ClassVar[str]
    #: Output vector dimension.
    dim: ClassVar[int]
    #: True for real implementations; False for POC stubs that raise on use.
    implemented: ClassVar[bool] = True

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one L2-normalized vector per input text.

        Args:
            texts: Input strings to embed.

        Returns:
            A list of ``dim``-length float vectors, aligned with ``texts``.
        """
        raise NotImplementedError


def register_embedder(name: str):
    """Class decorator registering a ``BaseEmbedder`` under ``name``."""

    def _decorator(cls: type[BaseEmbedder]) -> type[BaseEmbedder]:
        if not issubclass(cls, BaseEmbedder):
            raise TypeError(f"{cls.__name__} must subclass BaseEmbedder")
        cls.name = name
        if name in EMBEDDER_REGISTRY and EMBEDDER_REGISTRY[name] is not cls:
            raise ValueError(f"Embedder '{name}' already registered")
        EMBEDDER_REGISTRY[name] = cls
        logger.info("Registered embedder '%s' -> %s", name, cls.__name__)
        return cls

    return _decorator


def discover_embedders(package: str = "pipeline.embedding") -> dict[str, type[BaseEmbedder]]:
    """Import every module in the package so embedder decorators run."""
    pkg = importlib.import_module(package)
    for module in pkgutil.iter_modules(pkg.__path__):
        if module.name in {"base"}:
            continue
        try:
            importlib.import_module(f"{package}.{module.name}")
        except Exception:  # noqa: BLE001
            logger.exception("Failed to import embedder module %s", module.name)
    return EMBEDDER_REGISTRY


def get_embedder(name: str) -> BaseEmbedder:
    """Return a process-wide cached embedder instance for ``name``.

    Embedders are cached because constructing them can load a model. Raises
    ``KeyError`` for an unknown name or ``NotImplementedError`` for a stub.
    """
    if name in _instances:
        return _instances[name]
    if name not in EMBEDDER_REGISTRY:
        known = ", ".join(sorted(EMBEDDER_REGISTRY)) or "(none)"
        raise KeyError(f"Unknown embedder '{name}'. Registered: {known}")
    cls = EMBEDDER_REGISTRY[name]
    if not cls.implemented:
        raise NotImplementedError(
            f"Embedder '{name}' is a POC stub and not implemented yet (see ADR 0010)."
        )
    with _lock:
        if name not in _instances:
            _instances[name] = cls()
    return _instances[name]
