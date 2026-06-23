"""Base class for multi-agent orchestrators (ADR 0013).

Concrete implementations decide *how* agents are scheduled (asyncio fan-out,
LangGraph state machine, etc.).  The ABC is thin — one method, one result.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

from agents.base import AnalysisContext, AnalysisOutcome, AnalysisState


class BaseOrchestrator(ABC):
    """Runs the agent pipeline for one analysis request."""

    name: ClassVar[str]
    implemented: ClassVar[bool] = True

    @abstractmethod
    async def analyze(self, ctx: AnalysisContext, session_id: str | None = None) -> AnalysisOutcome:
        """Execute the full agent pipeline and return an ``AnalysisOutcome``.

        Args:
            ctx: Immutable query context (tenant, query, profile choices).
            session_id: Optional caller-supplied session identifier used for
                checkpointing and long-term memory lookup. Auto-generated if
                ``None``.

        Returns:
            ``AnalysisOutcome`` containing the completed (or interrupted) state,
            the resolved session_id, and flags for HITL interruption.
        """
        raise NotImplementedError
