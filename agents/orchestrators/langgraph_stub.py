"""LangGraph orchestrator — POC stub (ADR 0013 §Decision 1).

LangGraph is the target framework for production-scale orchestration (checkpointing,
streaming, graph visualization).  This stub keeps it in the capabilities menu and
blocks accidental use until the team is ready to invest in the dependency footprint.

To implement: install ``langgraph`` + ``langchain-core``, replace the body of
``LangGraphOrchestrator.analyze()`` with a real StateGraph, and set
``implemented = True``.
"""
from __future__ import annotations

from agents.base import AnalysisContext, AnalysisState
from agents.orchestrators.base import BaseOrchestrator


class LangGraphOrchestrator(BaseOrchestrator):
    """LangGraph-based orchestrator — not yet implemented (POC stub)."""

    name = "langgraph"
    implemented = False

    async def analyze(self, ctx: AnalysisContext) -> AnalysisState:
        raise NotImplementedError(
            "LangGraphOrchestrator is a POC stub. "
            "Install langgraph and implement the StateGraph, then set implemented=True."
        )
