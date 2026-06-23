"""Multi-agent analysis endpoint (ADR 0013, Phase 7 / 7b).

``POST /tenants/{tenant_id}/analyze`` runs the full agent pipeline.
``GET  /tenants/{tenant_id}/analyze/{session_id}/status`` polls checkpoint state.
``POST /tenants/{tenant_id}/analyze/{session_id}/resume`` resumes a HITL-interrupted session.

Separate from ``/search``:
  • /search  returns raw result sets, deterministic, fast, no LLM cost.
  • /analyze orchestrates agents, calls the LLM, higher latency (~2–10 s).
"""
from __future__ import annotations

from typing import Any, Literal

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
    query: str = Field(min_length=1, description="Natural-language analytical question")
    k: int = Field(default=5, ge=1, le=20, description="Max results per retrieval agent")
    orchestrator: Literal["sequential", "langgraph"] = Field(
        default="sequential",
        description="Which orchestrator to use. 'langgraph' enables checkpointing and HITL.",
    )
    session_id: str | None = Field(
        default=None,
        description="Optional caller-supplied session ID for checkpoint continuity.",
    )


class AgentSummary(BaseModel):
    status: str
    latency_ms: float
    error: str | None = None


class AnalyzeResponse(BaseModel):
    tenant_id: str
    session_id: str
    orchestrator: str
    query: str
    answer: str | None
    risk_score: float | None = Field(default=None)
    citations: list[dict[str, Any]] = Field(default_factory=list)
    agent_results: dict[str, AgentSummary] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    interrupted: bool = Field(
        default=False,
        description="True when risk_score >= 0.7 and human approval is required.",
    )
    pending_approval: dict[str, Any] | None = Field(
        default=None,
        description="Present only when interrupted=True; describes the required action.",
    )


class CheckpointStatusResponse(BaseModel):
    status: str
    risk_score: float | None = None
    risk_signals: list[str] = Field(default_factory=list)
    next_nodes: list[str] = Field(default_factory=list)
    agent_timings: dict[str, float] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class ResumeRequest(BaseModel):
    approved: bool = Field(description="True = proceed to synthesis; False = abort analysis.")
    feedback: str | None = Field(default=None, description="Optional analyst note for the synthesiser.")


def _get_langgraph():
    """Lazy import so the endpoint still boots if langgraph is not installed."""
    try:
        from agents.orchestrators.langgraph_orchestrator import get_langgraph_orchestrator
        return get_langgraph_orchestrator()
    except ImportError as exc:
        raise HTTPException(
            status_code=501,
            detail="LangGraph orchestrator requires 'langgraph>=0.2.14'. Install it and restart.",
        ) from exc


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

    When ``orchestrator=langgraph`` and the risk score reaches the HITL threshold
    (>=0.7), the response sets ``interrupted=True`` and ``pending_approval`` with
    the resume endpoint. Call ``POST /analyze/{session_id}/resume`` to continue.
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

    if payload.orchestrator == "langgraph":
        outcome = await _get_langgraph().analyze(ctx, session_id=payload.session_id)
    else:
        outcome = await get_orchestrator().analyze(ctx, session_id=payload.session_id)

    state = outcome.state
    return AnalyzeResponse(
        tenant_id=tenant_id,
        session_id=outcome.session_id,
        orchestrator=outcome.orchestrator,
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
        interrupted=outcome.interrupted,
        pending_approval=outcome.pending_approval,
    )


@router.get(
    "/analyze/{session_id}/status",
    response_model=CheckpointStatusResponse,
    summary="Poll LangGraph checkpoint state for a session",
)
async def analyze_status(
    tenant_id: str,
    session_id: str,
    db: Session = Depends(get_session),
) -> CheckpointStatusResponse:
    """Return the current checkpoint state for a LangGraph session.

    Returns ``status: "interrupted"`` while waiting for human approval.
    Returns ``status: "completed"`` once the graph has reached END.
    Returns ``status: "not_found"`` if the session_id is unknown.
    """
    if db.get(Tenant, tenant_id) is None:
        raise HTTPException(status_code=404, detail=f"Tenant '{tenant_id}' not found")

    status = _get_langgraph().get_checkpoint_status(tenant_id, session_id)
    return CheckpointStatusResponse(**status)


@router.post(
    "/analyze/{session_id}/resume",
    response_model=AnalyzeResponse,
    summary="Resume a HITL-interrupted LangGraph analysis",
)
async def analyze_resume(
    tenant_id: str,
    session_id: str,
    payload: ResumeRequest,
    db: Session = Depends(get_session),
) -> AnalyzeResponse:
    """Resume a LangGraph analysis that paused at the human-review node.

    The caller must supply ``approved: true`` or ``approved: false``.
    - ``approved: true`` injects the analyst's decision and proceeds to synthesis.
    - ``approved: false`` aborts synthesis and saves the interrupted analysis to history.
    - ``feedback`` is forwarded to the synthesis prompt when approved.

    Returns the completed ``AnalyzeResponse`` (``interrupted: false``).
    """
    if db.get(Tenant, tenant_id) is None:
        raise HTTPException(status_code=404, detail=f"Tenant '{tenant_id}' not found")

    outcome = await _get_langgraph().resume(
        tenant_id=tenant_id,
        session_id=session_id,
        approved=payload.approved,
        feedback=payload.feedback,
    )
    state = outcome.state
    return AnalyzeResponse(
        tenant_id=tenant_id,
        session_id=outcome.session_id,
        orchestrator=outcome.orchestrator,
        query=state.context.query,
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
        interrupted=False,
        pending_approval=None,
    )
