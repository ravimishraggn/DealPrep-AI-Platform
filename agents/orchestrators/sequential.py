"""SequentialOrchestrator — asyncio fan-out/fan-in, zero extra dependencies.

Phase 7 default (ADR 0013 §Decision 1).

Topology
--------
Phase 1 (parallel):  DocumentResearcher || StructuredAgent || GraphAgent
Phase 2 (sequential): RiskScorer(results from Phase 1)
Phase 3 (sequential): SynthesisAgent(all results)

A retrieval agent that raises does not block the other two or the synthesis —
its result is marked "failed" and a warning is appended to the state.
"""
from __future__ import annotations

import asyncio
import logging

from agents.base import AgentResult, AnalysisContext, AnalysisState
from agents.orchestrators.base import BaseOrchestrator
from agents.registry import AGENT_REGISTRY

logger = logging.getLogger(__name__)

_RETRIEVAL_AGENTS = ("document_researcher", "structured_agent", "graph_agent")
_POST_AGENTS = ("risk_scorer", "synthesis_agent")


class SequentialOrchestrator(BaseOrchestrator):
    """Lightweight asyncio orchestrator — no external framework required."""

    name = "sequential"
    implemented = True

    def __init__(self) -> None:
        # Instantiate agents once; they are stateless between runs.
        self._agents = {name: AGENT_REGISTRY[name]() for name in AGENT_REGISTRY
                        if name not in ("sequential", "langgraph")}

    async def analyze(self, ctx: AnalysisContext) -> AnalysisState:
        state = AnalysisState(context=ctx)

        # ── Phase 1: three retrieval agents run in parallel ──────────────────
        retrieval = [
            self._agents[name].run(state)
            for name in _RETRIEVAL_AGENTS
            if name in self._agents
        ]
        raw = await asyncio.gather(*retrieval, return_exceptions=True)
        for name, outcome in zip(_RETRIEVAL_AGENTS, raw):
            if isinstance(outcome, AgentResult):
                state.results[name] = outcome
                if outcome.status == "failed":
                    state.warnings.append(f"{name} failed: {outcome.error}")
            else:
                # asyncio.gather return_exceptions=True — outcome is an Exception
                state.results[name] = AgentResult(
                    agent=name, status="failed", payload={},
                    error=str(outcome),
                )
                state.warnings.append(f"{name} raised unexpectedly: {outcome}")
                logger.exception("agent %s raised", name, exc_info=outcome)

        # ── Phase 2 & 3: sequential post-processing ───────────────────────────
        for name in _POST_AGENTS:
            agent = self._agents.get(name)
            if agent is None:
                logger.warning("post-processing agent '%s' not registered; skipping", name)
                continue
            try:
                result = await agent.run(state)
                state.results[name] = result
                if result.status == "failed":
                    state.warnings.append(f"{name} failed: {result.error}")
            except Exception as exc:  # noqa: BLE001
                state.results[name] = AgentResult(
                    agent=name, status="failed", payload={}, error=str(exc),
                )
                state.warnings.append(f"{name} raised: {exc}")
                logger.exception("post-processing agent %s raised", name)

        return state


# Module-level singleton — one orchestrator per process.
_orchestrator: SequentialOrchestrator | None = None


def get_orchestrator() -> SequentialOrchestrator:
    """Return the process-wide cached orchestrator (instantiates on first call)."""
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = SequentialOrchestrator()
    return _orchestrator
