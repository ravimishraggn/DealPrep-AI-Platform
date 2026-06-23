# ADR 0013 — Multi-Agent Orchestration Layer (Phase 7)

- **Status:** Accepted
- **Date:** 2026-06-22
- **Deciders:** Platform team
- **Type:** Architecture — new capability layer
- **Implements:** [PRD §8 Multi-Agent Orchestration](../PRD.md#8-multi-agent-orchestration),
  [PRD §12 Phase 7](../PRD.md#12-phased-roadmap)
- **Builds on:** ADR 0007 (parallel fan-out), ADR 0012 (per-tenant profile)

---

## Context

Phases 1–6 built the data plane: ingest → extract → chunk → index (vector + structured +
graph), plus a unified search API that returns three labeled result sets. The search API is
a **tool** — it retrieves; it does not reason or synthesize.

Phase 7 adds the **reasoning layer**: specialist agents that each call a retrieval tool,
then a synthesis agent that reconciles their findings into one plain-language answer. This is
the "Valuation Discrepancy Detective" from the PRD's anchor use case — the layer that turns
three raw result sets into *"Company A's EBITDA includes $12M from a related entity owned by
the same sponsor; once normalized, the multiple aligns with peers."*

### What must be decided

1. **Orchestration framework:** LangGraph vs asyncio fan-out vs custom state machine
2. **Agent roster and responsibilities**
3. **Fan-out topology:** which agents run in parallel vs which depend on prior results
4. **How agents access retrieval tools** (and how tenant context flows through)
5. **Synthesis strategy:** how the final answer is produced (LLM vs rule-based)
6. **API surface:** new endpoint vs extending existing search

---

## Decision 1 — Orchestration framework: lightweight custom over LangGraph

### Options considered

| Option | Pros | Cons |
|---|---|---|
| **LangGraph** (PRD reference) | Full graph-state machine, streaming, checkpointing, built-in retry | +~15 heavy deps (langchain-core, pydantic-v1/v2 conflict risk); learning curve for the team; local debugging complexity; not in requirements.txt yet |
| **asyncio fan-out** (pure stdlib) | Zero new deps; same pattern as existing ThreadPoolExecutor fan-out; easy to debug | No checkpointing; no streaming; must hand-wire every dependency edge |
| **Custom lightweight orchestrator** | Thin wrapper over asyncio; explicit agent graph as a dict; readable; swappable to LangGraph later; fits "small and runnable locally" philosophy | More code than LangGraph; no auto-visualization of the graph |

**Decision: custom lightweight orchestrator using `asyncio`.**

Reasons:
- The project philosophy (every stage small and runnable locally) rules out a 15-library pull
  for Phase 7.
- The fan-out topology for Phase 7 is simple (3 parallel retrieval agents → 1 synthesis agent);
  it does not yet need a general-purpose graph state machine.
- The orchestrator is written behind a thin `BaseOrchestrator` ABC so LangGraph or another
  framework can be swapped in at Phase 8 without touching agent code — same pluggable pattern
  as extractors, chunkers, embedders.
- The existing `ThreadPoolExecutor(3)` fan-out in the pipeline (ADR 0007) proves this pattern
  works and is maintainable.

**LangGraph is the production target for a team operating at enterprise scale.** It is
registered as a stub (`implemented = False`) in the orchestrator registry — visible in
`GET /capabilities`, selectable as a profile option once the team is ready. This follows the
same pattern as openai/bedrock embedders.

---

## Decision 2 — Agent roster (Phase 7 V1)

Five agents cover the anchor use case. Each has a single responsibility and a typed input/output
contract.

| Agent | Role | Retrieval tool | Can run in parallel? |
|---|---|---|---|
| **DocumentResearcher** | Finds narrative valuation commentary, adjustments, footnotes | Vector search (`/search` vector results) | Yes — independent of others |
| **StructuredAgent** | Pulls exact financial figures and deal terms from the structured store | Structured search (`/search` structured results) | Yes — independent |
| **GraphAgent** | Traces entity relationships (ownership, board overlaps, related-party transactions) | Graph search (`/search` graph results) | Yes — independent |
| **RiskScorer** | Scores discrepancy risk from the combined findings of the three agents above | Calls DocumentResearcher + StructuredAgent + GraphAgent results | **Sequential** — waits for all three |
| **SynthesisAgent** | Produces one plain-language answer with full source citations | Calls all four results above | **Sequential** — last in chain |

Fan-out topology:
```
Query
  ├── DocumentResearcher ──┐
  ├── StructuredAgent ─────┤──→ RiskScorer ──→ SynthesisAgent → Answer
  └── GraphAgent ──────────┘
```

The three retrieval agents are strictly parallel (no shared state, no cross-dependency).
RiskScorer is a thin scoring layer that computes a structured risk signal from their results
(not an LLM call — rule-based for V1 to avoid cost and latency). SynthesisAgent is the only
agent that calls the LLM.

---

## Decision 3 — State schema

All agents share a typed `AnalysisContext` (passed in) and return a typed `AgentResult`.
The orchestrator builds up an `AnalysisState` as results arrive.

```python
@dataclass
class AnalysisContext:
    tenant_id: str
    query: str
    k: int = 5
    profile: PipelineProfile | None = None   # for consistent embedder/store selection

@dataclass
class AgentResult:
    agent: str                # name of the agent that produced this
    status: str               # "success" | "failed" | "skipped"
    payload: dict             # agent-specific structured output
    error: str | None = None  # populated on failure
    latency_ms: float = 0.0

@dataclass
class AnalysisState:
    context: AnalysisContext
    results: dict[str, AgentResult]  # agent name → result
    answer: str | None = None        # final synthesis
    citations: list[dict] = field(default_factory=list)
    risk_score: float | None = None
    warnings: list[str] = field(default_factory=list)
```

The state is immutable from the agent's perspective: each agent receives `AnalysisContext` only;
it cannot read or mutate other agents' results. The orchestrator aggregates all `AgentResult`s
and passes the assembled `AnalysisState` to RiskScorer and SynthesisAgent.

---

## Decision 4 — How agents access retrieval

Agents call `UnifiedSearch.search()` (the existing `app/search_service.py`) directly — not via
HTTP to themselves. This avoids localhost round-trips, port coupling, and serialization overhead
for an in-process call.

The profile flows from `AnalysisContext.profile` → `UnifiedSearch.search(embedding=..., vector_store=...)`,
exactly as the existing search router does. Tenant isolation is enforced by the search service
itself (mandatory `tenant_id` filter) — the orchestrator cannot bypass it.

```python
class DocumentResearcher(BaseAgent):
    async def run(self, ctx: AnalysisContext) -> AgentResult:
        results = UnifiedSearch().search(
            tenant_id=ctx.tenant_id,
            query=ctx.query,
            k=ctx.k,
            embedding=ctx.profile.embedding if ctx.profile else None,
            vector_store=ctx.profile.vector_store if ctx.profile else None,
        )
        return AgentResult(agent="document_researcher", status="success",
                           payload={"chunks": results.vector})
```

---

## Decision 5 — Synthesis strategy

**RiskScorer (V1):** rule-based. Computes a `risk_score` (0.0–1.0) based on:
- Whether graph results contain a `related_party_of` edge involving entities in structured results
- Whether structured results have EBITDA adjustments flagged in vector results
- Presence of overlap between entity names in graph results and companies in structured results

No LLM call here — the rule-based logic is deterministic, free, and testable. LLM-based risk
scoring is the Phase 8 upgrade path.

**SynthesisAgent:** calls `get_llm_client()`. If `DEALPREP_ANTHROPIC_API_KEY` is set, uses
Claude (`claude-haiku-4-5-20251001` as the default — fast and cheap for synthesis). Falls back
to a deterministic template-based synthesis if no key is set (the same offline-first philosophy
as the graph relationship extractor).

Template fallback produces:
```
Analysis of "{{query}}"

Document findings ({{n}} sources):
{{bullet list of vector results}}

Financial data ({{m}} records):
{{bullet list of structured results}}

Relationships:
{{bullet list of graph results}}

Risk signal: {{risk_score}}
```

The LLM prompt instructs the model to: (1) identify discrepancies across the three result sets,
(2) cite specific sources by `original_file_reference`, and (3) produce ≤ 3 paragraphs with no
invented facts beyond what the retrieval results contain.

---

## Decision 6 — API surface

New endpoint: `POST /tenants/{tenant_id}/analyze`

This is separate from `POST /tenants/{id}/search` because:
- `/search` returns raw result sets (deterministic, fast, no LLM cost).
- `/analyze` orchestrates agents, calls the LLM, and returns a narrative answer. Its latency
  is higher (~2–10 s) and it incurs LLM cost.

Response shape:
```json
{
  "tenant_id": "…",
  "query": "Why is Company A trading at a premium?",
  "answer": "Company A's reported EBITDA includes $12M in revenue from a related entity…",
  "risk_score": 0.82,
  "citations": [
    { "agent": "document_researcher", "text": "…", "file": "acme_cim.pdf", "score": 0.74 },
    { "agent": "structured_agent",    "fields": {"Company": "Acme", "EV/EBITDA": "13.5"} },
    { "agent": "graph_agent",         "subject": "Acme Corp", "rel": "related_party_of", "object": "Falcon Capital" }
  ],
  "agent_results": {
    "document_researcher": { "status": "success", "latency_ms": 120 },
    "structured_agent":    { "status": "success", "latency_ms": 80  },
    "graph_agent":         { "status": "success", "latency_ms": 95  },
    "risk_scorer":         { "status": "success", "latency_ms": 5   },
    "synthesis_agent":     { "status": "success", "latency_ms": 1800 }
  },
  "warnings": []
}
```

Partial failure policy: if one retrieval agent fails, the orchestrator marks its result as
`"status": "failed"`, adds a warning, and continues to synthesis with the remaining results.
A missing graph or structured result still produces a useful (if incomplete) answer. The only
hard stop is if all three retrieval agents fail.

---

## File layout

```
agents/
  base.py               BaseAgent ABC; AgentResult, AnalysisContext, AnalysisState contracts
  registry.py           AGENT_REGISTRY, @register_agent, discover_agents()
  document_researcher.py   ✅ real
  structured_agent.py      ✅ real
  graph_agent.py           ✅ real
  risk_scorer.py           ✅ real (rule-based V1)
  synthesis_agent.py       ✅ real (LLM + template fallback)
  orchestrators/
    base.py              BaseOrchestrator ABC
    sequential.py        Simple asyncio fan-out (default, implemented=True)
    langgraph.py         🟡 stub (implemented=False)
app/routers/
  analyze.py            POST /tenants/{id}/analyze
```

The orchestrator is itself a registry entry — `GET /capabilities` will include it, and a future
LangGraph implementation can be swapped in as a profile option.

---

## Consequences

**Positive**
- The anchor use case from the PRD (Valuation Discrepancy Detective) is now end-to-end:
  a single `POST /analyze` call runs all three retrieval tools and synthesizes an answer.
- Partial failure handling means one slow or broken store doesn't block the answer.
- Zero new required deps — asyncio is stdlib; LLM synthesis gracefully degrades to templates.
- LangGraph stub follows the same pattern as openai/bedrock: visible in capabilities,
  selectable when the team is ready, not blocking Phase 7.

**Negative / trade-offs**
- Custom orchestrator lacks LangGraph's checkpointing (resume from mid-workflow on failure),
  streaming (token-by-token answer), and graph visualization. These are Phase 8+ concerns.
- SynthesisAgent with Claude Haiku adds ~$0.002–0.005 per analyze call. With no rate limiting
  or cost attribution in Phase 7, an analyst can accidentally run up costs. Cost controls
  are flagged for Phase 8.
- Rule-based RiskScorer may miss nuanced discrepancies that require cross-entity reasoning.
  LLM-based scoring is the clear upgrade path but deferred to Phase 8.
- No streaming response in Phase 7: the entire synthesis must complete before any response
  is returned. For large corpora this may feel slow (5–10 s). SSE/streaming is Phase 8.

---

## Implementation addendum — LangGraph orchestrator (Phase 7b)

These are the **implementation-level decisions** made when promoting the LangGraph stub to
`implemented = True`. The architectural decision (use LangGraph for production) was already
made above; this section records *how*.

### A — State schema and reducers

`OrchestratorState` is a `TypedDict` compiled into the `StateGraph`. Fields written by
concurrent (parallel) nodes use `Annotated[T, reducer]` to guarantee safe fan-in:

| Field | Reducer | Rationale |
|---|---|---|
| `retrieved_chunks` | `operator.add` | Only DocumentResearcher writes; list-append is safe |
| `retrieved_records` | `operator.add` | Only StructuredAgent writes |
| `retrieved_triples` | `operator.add` | Only GraphAgent writes |
| `warnings` | `operator.add` | Any node may append a warning; must not lose any |
| `agent_timings` | `{**a, **b}` dict merge | Each node adds its own key; no conflict |

Fields written only by sequential nodes (risk_score, answer, human_approved) use the default
last-write-wins semantic — single writer at a time, no conflict.

### B — Fan-out/fan-in mechanism

Three parallel retrieval nodes are connected with **multiple edges from a common source**:
```
load_memory → document_researcher_node
load_memory → structured_agent_node
load_memory → graph_agent_node
```
LangGraph's super-step execution runs all three simultaneously. Fan-in is three **convergent
edges into a single node**:
```
document_researcher_node → risk_scorer_node
structured_agent_node    → risk_scorer_node
graph_agent_node         → risk_scorer_node
```
`risk_scorer_node` only starts when all three of its incoming edges are satisfied.
Reducers accumulate the partial state updates from the three parallel nodes before the
fan-in node sees a consistent state.

### C — Short-term memory: MemorySaver (checkpointing)

`MemorySaver` is the in-process checkpointer. Thread ID convention: `{tenant_id}:{session_id}`.
This guarantees sessions never cross tenant boundaries — a session_id collision with a different
tenant maps to a different thread_id and a different checkpoint sequence.

`MemorySaver` limitations: (1) in-RAM — lost on restart; (2) unbounded — long-running servers
accumulate all completed analysis states. Production upgrade path: `PostgresSaver` (LangGraph
has a first-party Postgres checkpointer). Flagged for Phase 8.

### D — Long-term memory: Postgres AnalysisHistory

A new `analysis_history` table in Postgres stores completed analyses (query, risk_score, answer,
citations, tenant_id). The `load_memory_node` at graph START loads the last 5 analyses for the
same tenant, so SynthesisAgent can reference patterns across sessions ("this entity has been
flagged as related-party in 3 prior analyses"). Not a vector store — plain SQL, last-5 recency.

### E — Human-in-the-loop: `interrupt_before`

The graph is compiled with `interrupt_before=["human_review_node"]`. When `risk_score >= 0.7`,
the conditional router directs to `human_review_node`. The graph pauses before entering the
node; `ainvoke` returns with partial state. The caller checks `graph.get_state(config).next`
— if non-empty, the graph is waiting.

Resume flow:
1. Analyst reviews risk signals via `GET /analyze/{session_id}/status`
2. Analyst approves/rejects via `POST /analyze/{session_id}/resume`
3. Router calls `graph.update_state(config, {"human_approved": True, "human_feedback": "…"})`
4. Router calls `graph.invoke(None, config)` — graph continues from `human_review_node`

`human_review_node` applies the approval: if `human_approved=False`, routing goes to `abort`
(skips synthesis, saves memory, returns partial result with warning).

### F — Dependency

`langgraph>=0.2.14` added to `requirements.txt`. Pulls `langchain-core>=0.2`.
This adds ~25 MB to the installed footprint. Acceptable for the agent layer.
The sequential orchestrator remains the default for environments that cannot install
langchain-core (CI, air-gapped, constrained Docker images).

## Follow-up actions (Phase 8 backlog)

| Action | Reason |
|---|---|
| LangGraph orchestrator (stub → real) | Checkpointing, streaming, graph visualization |
| LLM-based RiskScorer | Catch nuanced cross-entity discrepancies |
| Cost attribution per tenant per analyze call | Prevent runaway LLM spend |
| Streaming SSE endpoint for synthesis tokens | Reduce perceived latency |
| MCP tool layer (PRD §7) | Expose retrieval tools as MCP servers for external agent callers |
| Evaluation: RAGAS faithfulness + answer relevancy | PRD §11 success metrics |
