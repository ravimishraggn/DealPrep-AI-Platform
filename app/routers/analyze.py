"""Multi-agent analysis endpoint (ADR 0013, Phase 7).

``POST /tenants/{tenant_id}/analyze`` runs the full agent pipeline —
DocumentResearcher || StructuredAgent || GraphAgent → RiskScorer → SynthesisAgent —
and returns a synthesised plain-language answer with citations and a risk score.

Separate from ``/search``:
  • /search  returns raw result sets, deterministic, fast, no LLM cost.
  • /analyze orchestrates agents, calls the LLM, higher latency (~2–10 s).
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from agents.base import AnalysisContext
from agents.orchestrators.sequential import get_orchestrator
from app.db import get_session
from app.models import Tenant
from app.profiles import resolve_profile

router = APIRouter(prefix="/tenants/{tenant_id}", tags=["analyze"])


class AnalyzeRequest(BaseModel):
    """Analysis query payload."""

    query: str = Field(min_length=1, description="Natural-language analytical question")
    k: int = Field(default=5, ge=1, le=20, description="Max results per retrieval agent")


class AgentSummary(BaseModel):
    """Per-agent execution summary in the response."""

    status: str
    latency_ms: float
    error: str | None = None


class AnalyzeResponse(BaseModel):
    """Full analysis response with narrative answer and evidence."""

    tenant_id: str
    query: str
    answer: str | None
    risk_score: float | None = Field(
        default=None,
        description="0.0–1.0 discrepancy risk signal (rule-based in V1)"
    )
    citations: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Source citations from retrieval agents"
    )
    agent_results: dict[str, AgentSummary] = Field(
        default_factory=dict,
        description="Per-agent latency and status"
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Degradation notes (e.g. a store offline, an agent that failed)"
    )


@router.post(
    "/analyze",
    response_model=AnalyzeResponse,
    summary="Multi-agent analysis with narrative synthesis",
)
async def analyze(
    tenant_id: str,
    payload: AnalyzeRequest,
    db: Session = Depends(get_session),
) -> AnalyzeResponse:
    """Run the full agent pipeline for one tenant and return a synthesised answer.

    Fan-out topology (ADR 0013):
    - **Phase 1 (parallel):** DocumentResearcher, StructuredAgent, GraphAgent
    - **Phase 2:** RiskScorer (waits for Phase 1)
    - **Phase 3:** SynthesisAgent (waits for Phase 2)

    Partial failure policy: a failing retrieval agent produces a warning but does
    not abort synthesis — the answer is produced from whatever data is available.

    Args:
        tenant_id: Scopes all retrieval; an unknown tenant is rejected (404).
        payload: The analytical question and result-count limit.

    Returns:
        Synthesised answer, risk score, citations, per-agent stats, and warnings.
    """
    if db.get(Tenant, tenant_id) is None:
        raise HTTPException(status_code=404, detail=f"Tenant '{tenant_id}' not found")

    profile = resolve_profile(tenant_id, db)
    ctx = AnalysisContext(
        tenant_id=tenant_id,
        query=payload.query,
        k=payload.k,
        embedding=profile.embedding,
        vector_store=profile.vector_store,
    )

    state = await get_orchestrator().analyze(ctx)

    return AnalyzeResponse(
        tenant_id=tenant_id,
        query=payload.query,
        answer=state.answer,
        risk_score=state.risk_score,
        citations=state.citations,
        agent_results={
            name: AgentSummary(
                status=r.status,
                latency_ms=round(r.latency_ms, 1),
                error=r.error,
            )
            for name, r in state.results.items()
        },
        warnings=state.warnings,
    )
