"""Base class for multi-agent orchestrators (ADR 0013).

Concrete implementations decide *how* agents are scheduled (asyncio fan-out,
LangGraph state machine, etc.).  The ABC is thin — one method, one result.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

from agents.base import AnalysisContext, AnalysisState


class BaseOrchestrator(ABC):
    """Runs the agent pipeline for one analysis request."""

    name: ClassVar[str]
    implemented: ClassVar[bool] = True

    @abstractmethod
    async def analyze(self, ctx: AnalysisContext) -> AnalysisState:
        """Execute the full agent pipeline and return the completed state.

        Args:
            ctx: Immutable query context (tenant, query, profile choices).

        Returns:
            Completed ``AnalysisState`` with answer, citations, risk score,
            per-agent results, and any warnings.
        """
        raise NotImplementedError
