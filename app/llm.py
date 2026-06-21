"""Pluggable LLM client for relationship extraction.

The platform must run end-to-end whether or not an Anthropic API key is present.
When ``DEALPREP_ANTHROPIC_API_KEY`` is set, ``AnthropicClient`` performs real
Claude calls; otherwise ``get_llm_client()`` returns ``None`` and callers fall
back to a deterministic rule-based path (see RelationshipExtractor). This keeps
the LLM an optional enhancement, not a hard dependency.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from app.config import settings

logger = logging.getLogger(__name__)


class LLMClient(ABC):
    """Minimal text-completion interface used by relationship extraction."""

    @abstractmethod
    def complete(self, system: str, prompt: str) -> str:
        """Return the model's text response to ``prompt`` under ``system`` guidance.

        Args:
            system: System-prompt guidance (role, output format constraints).
            prompt: The user message containing the task and data.

        Returns:
            The model's raw text response.
        """
        raise NotImplementedError


class AnthropicClient(LLMClient):
    """LLMClient backed by the Anthropic Messages API (Claude)."""

    def __init__(self, api_key: str, model: str) -> None:
        """Construct a client for ``model`` authenticated with ``api_key``."""
        import anthropic  # lazy import

        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model

    def complete(self, system: str, prompt: str) -> str:
        """Call Claude once and return the concatenated text content."""
        resp = self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(block.text for block in resp.content if block.type == "text")


def get_llm_client() -> LLMClient | None:
    """Return an ``AnthropicClient`` if a key is configured, else ``None``.

    ``None`` signals callers to use their deterministic fallback so the pipeline
    still runs without an API key.
    """
    if settings.anthropic_api_key:
        try:
            return AnthropicClient(settings.anthropic_api_key, settings.relationship_model)
        except Exception:  # noqa: BLE001 - missing SDK / bad key must not break the run
            logger.exception("Failed to init Anthropic client; falling back to rule-based")
            return None
    return None
