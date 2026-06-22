"""Base interface + registry for chunking strategies."""
from __future__ import annotations

import importlib
import logging
import pkgutil
from abc import ABC, abstractmethod
from typing import ClassVar

logger = logging.getLogger(__name__)

CHUNKER_REGISTRY: dict[str, type["BaseChunker"]] = {}


class BaseChunker(ABC):
    """Splits document text into ``(chunk_text, section_type)`` units.

    Implementations vary in how they choose boundaries (structure, fixed size,
    sentences, semantics). The contract is identical so the document processor can
    swap strategies without changing anything downstream.
    """

    #: Selection key used in the pipeline profile (e.g. "section_aware").
    name: ClassVar[str]
    #: True for real implementations; False for POC stubs that raise on use.
    implemented: ClassVar[bool] = True

    @abstractmethod
    def chunk(self, text: str, default_section: str | None = None) -> list[tuple[str, str | None]]:
        """Split ``text`` into ordered ``(chunk_text, section_type)`` tuples.

        Args:
            text: The document text to split.
            default_section: Section label until a better one is detected.

        Returns:
            Ordered list of ``(chunk_text, section_type)`` tuples.
        """
        raise NotImplementedError


def register_chunker(name: str):
    """Class decorator registering a ``BaseChunker`` under ``name``."""

    def _decorator(cls: type[BaseChunker]) -> type[BaseChunker]:
        if not issubclass(cls, BaseChunker):
            raise TypeError(f"{cls.__name__} must subclass BaseChunker")
        cls.name = name
        if name in CHUNKER_REGISTRY and CHUNKER_REGISTRY[name] is not cls:
            raise ValueError(f"Chunker '{name}' already registered")
        CHUNKER_REGISTRY[name] = cls
        logger.info("Registered chunker '%s' -> %s", name, cls.__name__)
        return cls

    return _decorator


def discover_chunkers(package: str = "pipeline.chunking") -> dict[str, type[BaseChunker]]:
    """Import every module in the package so chunker decorators run."""
    pkg = importlib.import_module(package)
    for module in pkgutil.iter_modules(pkg.__path__):
        if module.name in {"base"}:
            continue
        try:
            importlib.import_module(f"{package}.{module.name}")
        except Exception:  # noqa: BLE001
            logger.exception("Failed to import chunker module %s", module.name)
    return CHUNKER_REGISTRY


def get_chunker(name: str) -> BaseChunker:
    """Instantiate the chunker registered under ``name``.

    Raises:
        KeyError: If no chunker is registered under ``name``.
        NotImplementedError: If the named chunker is a POC stub.
    """
    if name not in CHUNKER_REGISTRY:
        known = ", ".join(sorted(CHUNKER_REGISTRY)) or "(none)"
        raise KeyError(f"Unknown chunker '{name}'. Registered: {known}")
    cls = CHUNKER_REGISTRY[name]
    if not cls.implemented:
        raise NotImplementedError(
            f"Chunker '{name}' is a POC stub and not implemented yet. "
            f"Choose an implemented strategy (see ADR 0009)."
        )
    return cls()
