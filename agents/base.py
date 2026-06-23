"""Shared contracts for the multi-agent orchestration layer (ADR 0013).

Every agent in the system receives an ``AnalysisState`` and produces an
``AgentResult``.  Retrieval agents read only ``state.context``; post-processing
agents (RiskScorer, SynthesisAgent) additionally read ``state.results``.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import ClassVar


@dataclass
class AnalysisContext:
    """Immutable query context threaded through all agents."""

    tenant_id: str
    query: str
    k: int = 5
    embedding: str | None = None       # resolved from tenant profile at orchestrator entry
    vector_store: str | None = None    # resolved from tenant profile at orchestrator entry


@dataclass
class AgentResult:
    """Typed output from a single agent run."""

    agent: str
    status: str                        # "success" | "failed" | "skipped"
    payload: dict
    error: str | None = None
    latency_ms: float = 0.0


@dataclass
class AnalysisState:
    """Mutable accumulator built up by the orchestrator between phases."""

    context: AnalysisContext
    results: dict[str, AgentResult] = field(default_factory=dict)
    answer: str | None = None
    citations: list[dict] = field(default_factory=list)
    risk_score: float | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class AnalysisOutcome:
    """Richer return type from orchestrators — wraps state + metadata."""

    state: AnalysisState
    session_id: str
    orchestrator: str
    interrupted: bool = False
    pending_approval: dict | None = None  # populated when interrupted=True


class BaseAgent(ABC):
    """Minimal interface every agent must satisfy."""

    name: ClassVar[str]
    implemented: ClassVar[bool] = True

    @abstractmethod
    async def run(self, state: AnalysisState) -> AgentResult:
        """Execute the agent and return a single ``AgentResult``.

        Agents must not mutate ``state``; the orchestrator does that.

        Args:
            state: Current analysis state (context + prior results).

        Returns:
            An ``AgentResult`` with ``status`` set to "success", "failed",
            or "skipped"; ``payload`` is agent-specific.
        """
        raise NotImplementedError

    # ------------------------------------------------------------------ helpers

    def _timed(self, start: float) -> float:
        """Wall-clock elapsed milliseconds since ``start``."""
        return (time.perf_counter() - start) * 1000

    def _failed(self, exc: Exception, start: float) -> AgentResult:
        """Build a failed ``AgentResult`` from an unexpected exception."""
        return AgentResult(
            agent=self.name,
            status="failed",
            payload={},
            error=str(exc),
            latency_ms=self._timed(start),
        )
