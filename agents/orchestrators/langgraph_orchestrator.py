"""LangGraph orchestrator — production implementation (ADR 0013 §LangGraph addendum).

Replaces the sequential asyncio fan-out with a full LangGraph StateGraph that provides:
  - Typed shared state with Annotated reducers for safe concurrent fan-in
  - Short-term memory via MemorySaver (per-session checkpointing)
  - Long-term memory via Postgres AnalysisHistory (cross-session tenant context)
  - Fan-out / fan-in: 3 parallel retrieval nodes → risk_scorer (single convergence point)
  - Human-in-the-loop: interrupt_before="human_review_node" when risk_score >= 0.7
  - Conditional routing: high-risk → human review → synthesis/abort

Graph topology
--------------
START
  └─ load_memory_node
       ├─ document_researcher_node  ─┐
       ├─ structured_agent_node     ─┤  (parallel fan-out)
       └─ graph_agent_node          ─┘
                                      └─ risk_scorer_node
                                              ├─ (risk < 0.7) ─→ synthesis_node
                                              └─ (risk ≥ 0.7) ─→ [INTERRUPT] human_review_node
                                                                        ├─ (approved) ─→ synthesis_node
                                                                        └─ (rejected) ─→ save_memory_node
                                      synthesis_node
                                              └─ save_memory_node → END
"""
from __future__ import annotations

import asyncio
import logging
import operator
import re
import time
import uuid
from typing import Annotated, Any

from typing_extensions import TypedDict

from agents.base import AnalysisContext, AnalysisOutcome, AnalysisState, AgentResult
from agents.memory.store import get_memory_store
from agents.orchestrators.base import BaseOrchestrator
from app.llm import get_llm_client
from pipeline.indexing.graph.neo4j_client import Neo4jClient
from pipeline.indexing.structured import StructuredIndexer
from pipeline.indexing.vector import VectorIndexer

logger = logging.getLogger(__name__)

# ── 1. TYPED SHARED STATE ─────────────────────────────────────────────────────
# Annotated fields use reducers for safe concurrent fan-in from parallel nodes.
# Fields written by only one node use default last-write-wins.

def _merge_dicts(a: dict, b: dict) -> dict:
    return {**a, **b}


class OrchestratorState(TypedDict):
    # --- Immutable input context (set once at START, never modified) ----------
    tenant_id: str
    query: str
    k: int
    embedding: str | None
    vector_store: str | None
    session_id: str

    # --- Parallel fan-out accumulation fields (reducers make fan-in safe) -----
    # Each retrieval node writes to its own dedicated list; operator.add appends.
    retrieved_chunks: Annotated[list[dict], operator.add]
    retrieved_records: Annotated[list[dict], operator.add]
    retrieved_triples: Annotated[list[dict], operator.add]
    # warnings: any node may write; must not lose concurrent writes
    warnings: Annotated[list[str], operator.add]
    # agent_timings: each node writes {agent_name: ms}; dict merge has no conflict
    agent_timings: Annotated[dict[str, float], _merge_dicts]

    # --- Sequential stages (single writer, no reducer needed) ----------------
    risk_score: float | None
    risk_signals: list[str]
    answer: str | None
    citations: list[dict]

    # --- Human-in-the-loop (set by caller via update_state before resume) -----
    human_approved: bool | None
    human_feedback: str | None

    # --- Long-term memory (loaded at START, written at END) -------------------
    prior_analyses: list[dict]


# ── 2. NODE FUNCTIONS ─────────────────────────────────────────────────────────

async def load_memory_node(state: OrchestratorState) -> dict:
    """Load last-5 prior analyses for this tenant from Postgres long-term store."""
    prior = await asyncio.to_thread(
        get_memory_store().load_recent, state["tenant_id"], 5
    )
    return {"prior_analyses": prior}


async def document_researcher_node(state: OrchestratorState) -> dict:
    """Semantic search — parallel leg 1 of 3."""
    t0 = time.perf_counter()
    try:
        indexer = VectorIndexer(state.get("embedding"), state.get("vector_store"))
        chunks = await asyncio.to_thread(
            indexer.search, state["tenant_id"], state["query"], state["k"]
        )
        return {
            "retrieved_chunks": chunks,
            "agent_timings": {"document_researcher": round((time.perf_counter() - t0) * 1000, 1)},
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("document_researcher_node failed")
        return {
            "retrieved_chunks": [],
            "warnings": [f"document_researcher failed: {exc}"],
            "agent_timings": {"document_researcher": round((time.perf_counter() - t0) * 1000, 1)},
        }


async def structured_agent_node(state: OrchestratorState) -> dict:
    """Full-text + JSONB search — parallel leg 2 of 3."""
    t0 = time.perf_counter()
    try:
        indexer = StructuredIndexer()
        records = await asyncio.to_thread(
            indexer.search, state["tenant_id"], state["query"], state["k"]
        )
        return {
            "retrieved_records": records,
            "agent_timings": {"structured_agent": round((time.perf_counter() - t0) * 1000, 1)},
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("structured_agent_node failed")
        return {
            "retrieved_records": [],
            "warnings": [f"structured_agent failed: {exc}"],
            "agent_timings": {"structured_agent": round((time.perf_counter() - t0) * 1000, 1)},
        }


async def graph_agent_node(state: OrchestratorState) -> dict:
    """1-hop Neo4j entity lookup — parallel leg 3 of 3."""
    t0 = time.perf_counter()
    try:
        client = Neo4jClient()
        triples = await asyncio.to_thread(_graph_lookup, client, state["tenant_id"], state["query"], state["k"])
        return {
            "retrieved_triples": triples,
            "agent_timings": {"graph_agent": round((time.perf_counter() - t0) * 1000, 1)},
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("graph_agent_node failed")
        return {
            "retrieved_triples": [],
            "warnings": [f"graph_agent failed: {exc}"],
            "agent_timings": {"graph_agent": round((time.perf_counter() - t0) * 1000, 1)},
        }


def _graph_lookup(client: Neo4jClient, tenant_id: str, query: str, k: int) -> list[dict]:
    names = client.list_entities(tenant_id)
    low = query.lower()
    out: list[dict] = []
    for name in [n for n in names if n.lower() in low][:5]:
        out.extend(client.find_relationships(tenant_id, name, limit=k))
    return out


def risk_scorer_node(state: OrchestratorState) -> dict:
    """Rule-based risk scorer — fan-in point; waits for all three retrieval nodes."""
    score, signals = _compute_risk(state)
    return {"risk_score": score, "risk_signals": signals}


_ADJ_RE = re.compile(
    r"\b(adjustm|normaliz|exclud|related.?party|add.?back|non.?recurring|pro.?forma|restat)\w*",
    re.IGNORECASE,
)


def _compute_risk(state: OrchestratorState) -> tuple[float, list[str]]:
    signals: list[str] = []
    score = 0.0

    triples = state.get("retrieved_triples", [])
    chunks = state.get("retrieved_chunks", [])
    records = state.get("retrieved_records", [])
    prior = state.get("prior_analyses", [])

    if any(t.get("relationship") == "related_party_of" for t in triples):
        score += 0.40
        signals.append("related_party edge detected in knowledge graph")

    all_text = " ".join(c.get("text", "") for c in chunks)
    kw_hits = _ADJ_RE.findall(all_text)
    if kw_hits:
        score += min(0.25, 0.10 * len(set(m.lower() for m in kw_hits)))
        signals.append(f"adjustment/normalization language found ({len(set(kw_hits))} keyword types)")

    companies = {v for r in records for v in r.get("fields", {}).values()
                 if isinstance(v, str) and 3 < len(v) < 60 and v[0].isupper()}
    if len(companies) > 2:
        score += 0.20
        signals.append(f"{len(companies)} distinct named values in structured data")

    g_names = {t.get("subject", "").lower() for t in triples} | {t.get("object", "").lower() for t in triples}
    s_values = {str(v).lower() for r in records for v in r.get("fields", {}).values() if isinstance(v, str)}
    if g_names & s_values:
        score += 0.15
        signals.append(f"entity overlap between graph and structured store ({len(g_names & s_values)} shared)")

    # Long-term memory signal: recurring risk pattern
    prior_high = [p for p in prior if (p.get("risk_score") or 0) >= 0.5]
    if len(prior_high) >= 2:
        score = min(score + 0.10, 1.0)
        signals.append(f"recurring risk pattern: {len(prior_high)} of last {len(prior)} analyses flagged medium/high")

    return round(min(score, 1.0), 3), signals


def route_after_risk(state: OrchestratorState) -> str:
    """Conditional router: risk >= 0.7 → human review; else → synthesis."""
    return "human_review_node" if (state.get("risk_score") or 0) >= 0.7 else "synthesis_node"


def human_review_node(state: OrchestratorState) -> dict:
    """Human-in-the-loop gate.

    The graph is compiled with ``interrupt_before=["human_review_node"]`` so execution
    pauses BEFORE this node runs. When the analyst resumes (via the /resume endpoint),
    the caller has already updated state with ``human_approved`` and ``human_feedback``
    via ``graph.update_state()``. This node just records the decision.
    """
    approved = state.get("human_approved")
    feedback = state.get("human_feedback", "")
    if not approved:
        return {"warnings": ["Analysis synthesis aborted by human reviewer"]}
    if feedback:
        return {"warnings": []}  # feedback goes to synthesis via state
    return {}


def route_after_human(state: OrchestratorState) -> str:
    return "synthesis_node" if state.get("human_approved") else "save_memory_node"


async def synthesis_node(state: OrchestratorState) -> dict:
    """Synthesise findings via LLM (or template fallback)."""
    t0 = time.perf_counter()
    import json

    citations = _build_citations(state)
    feedback = state.get("human_feedback") or ""
    prior_context = _prior_context_summary(state.get("prior_analyses", []))

    llm = get_llm_client()
    if llm:
        system = (
            "You are a financial analysis assistant for a PE/VC valuation platform. "
            "Synthesise the retrieval results into a concise explanation of any valuation "
            "discrepancy. Cite sources by original_file_reference. ≤3 paragraphs. "
            "Do not invent facts not in the data."
            + (f"\n\nHuman reviewer note: {feedback}" if feedback else "")
            + (f"\n\nPrior session context: {prior_context}" if prior_context else "")
        )
        data = {
            "query": state["query"],
            "risk_score": state.get("risk_score"),
            "risk_signals": state.get("risk_signals", []),
            "document_excerpts": [
                {"text": c.get("text", "")[:400], "source": (c.get("metadata") or {}).get("original_file_reference")}
                for c in state.get("retrieved_chunks", [])[:5]
            ],
            "structured_records": [
                {"fields": r.get("fields", {}), "source": (r.get("metadata") or {}).get("original_file_reference")}
                for r in state.get("retrieved_records", [])[:5]
            ],
            "relationships": [
                {"subject": t.get("subject"), "rel": t.get("relationship"), "object": t.get("object")}
                for t in state.get("retrieved_triples", [])[:10]
            ],
        }
        answer = await asyncio.to_thread(llm.complete, system, json.dumps(data, indent=2, default=str))
    else:
        answer = _template_answer(state, citations, prior_context)

    return {
        "answer": answer,
        "citations": citations,
        "agent_timings": {"synthesis": round((time.perf_counter() - t0) * 1000, 1)},
    }


async def save_memory_node(state: OrchestratorState) -> dict:
    """Persist completed analysis to long-term Postgres store."""
    await asyncio.to_thread(
        get_memory_store().save,
        state["tenant_id"],
        state["session_id"],
        state["query"],
        state.get("risk_score"),
        state.get("answer"),
        state.get("citations", []),
        state.get("risk_signals", []),
        "langgraph",
        state.get("human_approved") is False,
    )
    return {}


# ── 3. GRAPH ASSEMBLY ─────────────────────────────────────────────────────────

def _build_graph():
    """Compile the LangGraph StateGraph with checkpointing and HITL interrupt."""
    try:
        from langgraph.graph import END, START, StateGraph
        from langgraph.checkpoint.memory import MemorySaver
    except ImportError as exc:
        raise ImportError(
            "langgraph is required for LangGraphOrchestrator. "
            "Run: pip install langgraph>=0.2.14"
        ) from exc

    workflow = StateGraph(OrchestratorState)

    # Register all nodes
    workflow.add_node("load_memory_node", load_memory_node)
    workflow.add_node("document_researcher_node", document_researcher_node)
    workflow.add_node("structured_agent_node", structured_agent_node)
    workflow.add_node("graph_agent_node", graph_agent_node)
    workflow.add_node("risk_scorer_node", risk_scorer_node)
    workflow.add_node("human_review_node", human_review_node)
    workflow.add_node("synthesis_node", synthesis_node)
    workflow.add_node("save_memory_node", save_memory_node)

    # START → load memory
    workflow.add_edge(START, "load_memory_node")

    # Fan-out: load_memory → all three retrievers in parallel
    workflow.add_edge("load_memory_node", "document_researcher_node")
    workflow.add_edge("load_memory_node", "structured_agent_node")
    workflow.add_edge("load_memory_node", "graph_agent_node")

    # Fan-in: all three → risk_scorer (LangGraph waits for all incoming edges)
    workflow.add_edge("document_researcher_node", "risk_scorer_node")
    workflow.add_edge("structured_agent_node", "risk_scorer_node")
    workflow.add_edge("graph_agent_node", "risk_scorer_node")

    # Conditional after risk: high risk → HITL, else → synthesis
    workflow.add_conditional_edges(
        "risk_scorer_node",
        route_after_risk,
        {"human_review_node": "human_review_node", "synthesis_node": "synthesis_node"},
    )

    # After human review: approved → synthesis, rejected → save_memory (abort)
    workflow.add_conditional_edges(
        "human_review_node",
        route_after_human,
        {"synthesis_node": "synthesis_node", "save_memory_node": "save_memory_node"},
    )

    workflow.add_edge("synthesis_node", "save_memory_node")
    workflow.add_edge("save_memory_node", END)

    # Compile with checkpointing + HITL interrupt
    checkpointer = MemorySaver()
    return workflow.compile(
        checkpointer=checkpointer,
        interrupt_before=["human_review_node"],
    )


# ── 4. ORCHESTRATOR CLASS ─────────────────────────────────────────────────────

class LangGraphOrchestrator(BaseOrchestrator):
    """LangGraph StateGraph orchestrator with checkpointing and HITL (ADR 0013)."""

    name = "langgraph"
    implemented = True

    def __init__(self) -> None:
        self._graph = _build_graph()

    def _thread_config(self, tenant_id: str, session_id: str) -> dict:
        """Build LangGraph config with a tenant-scoped thread_id.

        Thread ID convention: ``{tenant_id}:{session_id}`` — ensures that even if
        two tenants accidentally use the same session_id, their checkpoints are
        completely separate.
        """
        return {"configurable": {"thread_id": f"{tenant_id}:{session_id}"}}

    async def analyze(self, ctx: AnalysisContext, session_id: str | None = None) -> AnalysisOutcome:
        """Run the full LangGraph pipeline for one analysis request.

        Returns immediately with an interrupted ``AnalysisOutcome`` if risk >= 0.7
        triggers the human-review gate. Otherwise returns the completed state.
        """
        sid = session_id or str(uuid.uuid4())
        config = self._thread_config(ctx.tenant_id, sid)

        init_state: OrchestratorState = {
            "tenant_id": ctx.tenant_id,
            "query": ctx.query,
            "k": ctx.k,
            "embedding": ctx.embedding,
            "vector_store": ctx.vector_store,
            "session_id": sid,
            "retrieved_chunks": [],
            "retrieved_records": [],
            "retrieved_triples": [],
            "warnings": [],
            "agent_timings": {},
            "risk_score": None,
            "risk_signals": [],
            "answer": None,
            "citations": [],
            "human_approved": None,
            "human_feedback": None,
            "prior_analyses": [],
        }

        await self._graph.ainvoke(init_state, config=config)

        # Check if graph paused at human_review_node (interrupt_before)
        snap = self._graph.get_state(config)
        interrupted = "human_review_node" in (snap.next or [])
        gs = snap.values  # current graph state dict

        if interrupted:
            pending = {
                "reason": "High discrepancy risk detected — human approval required",
                "risk_score": gs.get("risk_score"),
                "risk_signals": gs.get("risk_signals", []),
                "query": gs.get("query"),
                "session_id": sid,
                "resume_endpoint": f"POST /tenants/{ctx.tenant_id}/analyze/{sid}/resume",
            }
            state = self._graph_state_to_analysis_state(gs, ctx)
            return AnalysisOutcome(
                state=state, session_id=sid, orchestrator="langgraph",
                interrupted=True, pending_approval=pending,
            )

        state = self._graph_state_to_analysis_state(gs, ctx)
        return AnalysisOutcome(state=state, session_id=sid, orchestrator="langgraph")

    async def resume(
        self, tenant_id: str, session_id: str, approved: bool, feedback: str | None = None
    ) -> AnalysisOutcome:
        """Resume an interrupted HITL analysis after the analyst makes a decision.

        Args:
            tenant_id: Scopes the checkpoint lookup.
            session_id: Session that was previously interrupted.
            approved: True = proceed to synthesis; False = abort.
            feedback: Optional analyst note forwarded to SynthesisAgent.

        Returns:
            Completed ``AnalysisOutcome`` (interrupted=False).
        """
        ctx_dummy = AnalysisContext(tenant_id=tenant_id, query="", k=5)
        config = self._thread_config(tenant_id, session_id)

        snap = self._graph.get_state(config)
        if "human_review_node" not in (snap.next or []):
            return AnalysisOutcome(
                state=self._graph_state_to_analysis_state(snap.values, ctx_dummy),
                session_id=session_id,
                orchestrator="langgraph",
                interrupted=False,
            )

        # Inject human decision into the checkpoint before resuming
        self._graph.update_state(
            config,
            {"human_approved": approved, "human_feedback": feedback or ""},
        )
        await self._graph.ainvoke(None, config=config)

        snap = self._graph.get_state(config)
        gs = snap.values
        ctx_dummy = AnalysisContext(tenant_id=tenant_id, query=gs.get("query", ""), k=gs.get("k", 5))
        return AnalysisOutcome(
            state=self._graph_state_to_analysis_state(gs, ctx_dummy),
            session_id=session_id,
            orchestrator="langgraph",
        )

    def get_checkpoint_status(self, tenant_id: str, session_id: str) -> dict:
        """Return current checkpoint status for a session (for polling/UI)."""
        config = self._thread_config(tenant_id, session_id)
        try:
            snap = self._graph.get_state(config)
            if snap is None or not snap.values:
                return {"status": "not_found"}
            gs = snap.values
            interrupted = "human_review_node" in (snap.next or [])
            return {
                "status": "interrupted" if interrupted else "completed",
                "risk_score": gs.get("risk_score"),
                "risk_signals": gs.get("risk_signals", []),
                "next_nodes": list(snap.next or []),
                "agent_timings": gs.get("agent_timings", {}),
                "warnings": gs.get("warnings", []),
            }
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "detail": str(exc)}

    # ------------------------------------------------------------------ helpers

    def _graph_state_to_analysis_state(self, gs: dict, ctx: AnalysisContext) -> AnalysisState:
        """Convert LangGraph state dict → the AnalysisState used by the router."""
        state = AnalysisState(context=ctx)

        chunks = gs.get("retrieved_chunks", [])
        records = gs.get("retrieved_records", [])
        triples = gs.get("retrieved_triples", [])
        timings = gs.get("agent_timings", {})

        for agent_name, latency in timings.items():
            state.results[agent_name] = AgentResult(
                agent=agent_name, status="success", payload={}, latency_ms=latency
            )

        state.risk_score = gs.get("risk_score")
        state.answer = gs.get("answer")
        state.citations = gs.get("citations", [])
        state.warnings = list(gs.get("warnings", []))

        # Attach retrieval payloads for router serialization
        if chunks:
            state.results.setdefault("document_researcher", AgentResult(
                agent="document_researcher", status="success",
                payload={"chunks": chunks}, latency_ms=timings.get("document_researcher", 0),
            ))
        if records:
            state.results.setdefault("structured_agent", AgentResult(
                agent="structured_agent", status="success",
                payload={"records": records}, latency_ms=timings.get("structured_agent", 0),
            ))
        if triples:
            state.results.setdefault("graph_agent", AgentResult(
                agent="graph_agent", status="success",
                payload={"triples": triples}, latency_ms=timings.get("graph_agent", 0),
            ))

        return state


# ── 5. HELPERS ────────────────────────────────────────────────────────────────

def _build_citations(state: OrchestratorState) -> list[dict]:
    out: list[dict] = []
    for c in state.get("retrieved_chunks", [])[:3]:
        out.append({"agent": "document_researcher", "text": c.get("text", "")[:300],
                    "score": c.get("score"), "file": (c.get("metadata") or {}).get("original_file_reference")})
    for r in state.get("retrieved_records", [])[:3]:
        out.append({"agent": "structured_agent", "fields": r.get("fields", {}),
                    "file": (r.get("metadata") or {}).get("original_file_reference")})
    for t in state.get("retrieved_triples", [])[:5]:
        out.append({"agent": "graph_agent", "subject": t.get("subject"),
                    "relationship": t.get("relationship"), "object": t.get("object"),
                    "file": t.get("file_ref")})
    return out


def _prior_context_summary(prior: list[dict]) -> str:
    if not prior:
        return ""
    high = [p for p in prior if (p.get("risk_score") or 0) >= 0.5]
    if not high:
        return ""
    return (
        f"{len(high)} of the last {len(prior)} analyses for this tenant were flagged "
        f"medium/high risk. Most recent: \"{high[0].get('query', '')}\" "
        f"(risk={high[0].get('risk_score', 'n/a')}, signals: "
        f"{', '.join((high[0].get('risk_signals') or [])[:2])})."
    )


def _template_answer(state: OrchestratorState, citations: list[dict], prior_context: str) -> str:
    rs = state.get("risk_score") or 0
    label = "low" if rs < 0.3 else "medium" if rs < 0.6 else "HIGH"
    lines = [f'Analysis of: "{state["query"]}"', f"", f"Risk signal: {rs:.2f} ({label})"]
    for sig in state.get("risk_signals", []):
        lines.append(f"  • {sig}")
    lines.append("")
    chunks = state.get("retrieved_chunks", [])
    if chunks:
        lines.append(f"Document findings ({len(chunks)} chunks):")
        for c in chunks[:3]:
            src = (c.get("metadata") or {}).get("original_file_reference", "unknown")
            lines.append(f"  • [{src}] {c.get('text', '')[:200].strip()}")
        lines.append("")
    records = state.get("retrieved_records", [])
    if records:
        lines.append(f"Financial data ({len(records)} records):")
        for r in records[:3]:
            src = (r.get("metadata") or {}).get("original_file_reference", "unknown")
            lines.append(f"  • [{src}] {', '.join(f'{k}: {v}' for k, v in list(r.get('fields', {}).items())[:4])}")
        lines.append("")
    triples = state.get("retrieved_triples", [])
    if triples:
        lines.append(f"Relationships ({len(triples)}):")
        for t in triples[:5]:
            lines.append(f"  • ({t.get('subject')}) -[{t.get('relationship')}]→ ({t.get('object')})")
        lines.append("")
    if prior_context:
        lines.append(f"Long-term pattern: {prior_context}")
    if not (chunks or records or triples):
        lines.append("No data found. Ingest documents for this tenant first.")
    lines.append("(Set DEALPREP_ANTHROPIC_API_KEY for narrative synthesis via Claude.)")
    return "\n".join(lines)


# ── 6. SINGLETON ──────────────────────────────────────────────────────────────

_lg_orchestrator: LangGraphOrchestrator | None = None


def get_langgraph_orchestrator() -> LangGraphOrchestrator:
    """Return the process-wide cached LangGraph orchestrator."""
    global _lg_orchestrator
    if _lg_orchestrator is None:
        _lg_orchestrator = LangGraphOrchestrator()
    return _lg_orchestrator
