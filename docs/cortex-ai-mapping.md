# DealPrep AI Platform — Snowflake Cortex AI Architecture Mapping

> **Purpose:** Map what DealPrep has built against Snowflake Cortex AI's reference architecture,
> measure coverage, and identify gaps that become the next engineering backlog.
>
> **Date:** 2026-06-23 | **Platform phase:** 7 (multi-agent orchestration complete)

---

## 1. Snowflake Cortex AI — Reference Architecture

Cortex AI is Snowflake's production AI platform for enterprise data. Its components form a layered
stack:

```
┌─────────────────────────────────────────────────────────────────┐
│                    CORTEX AGENTS                                │
│   (multi-step reasoning, tool orchestration, HITL)             │
├────────────────────┬──────────────────────┬─────────────────────┤
│  CORTEX SEARCH     │  CORTEX ANALYST       │  CORTEX COMPLETE    │
│  (hybrid retrieval)│  (NL → SQL w/ semantic│  (hosted LLM        │
│                    │   model YAML)         │   inference layer)  │
├────────────────────┴──────────────────────┴─────────────────────┤
│  CORTEX DOCUMENT AI        │  CORTEX GUARD                      │
│  (extraction, parsing,     │  (PII, prompt injection,           │
│   classification)          │   content moderation)              │
├────────────────────────────┴────────────────────────────────────┤
│  SEMANTIC MODEL (YAML)                                          │
│  (metrics, dimensions, measures — the contract between         │
│   data and the Analyst LLM)                                    │
├─────────────────────────────────────────────────────────────────┤
│  DATA STORAGE LAYER                                             │
│  Snowflake Tables · Iceberg · Stages (landing zone)            │
│  Snowpipe (streaming) · Tasks (scheduling)                     │
├─────────────────────────────────────────────────────────────────┤
│  GOVERNANCE                                                     │
│  Row-access policies · Column masking · Audit log              │
│  Cost attribution per query/tenant                             │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. Component-by-Component Mapping

| Snowflake Cortex AI Component | DealPrep Equivalent | Files / Classes | Status |
|---|---|---|---|
| **Cortex Search** (hybrid semantic + keyword retrieval on unstructured data) | `VectorIndexer` + ChromaDB + MinilM embedder | `pipeline/indexing/vector.py`, `pipeline/vectorstore/chroma.py`, `pipeline/embedding/minilm.py` | ✅ Implemented |
| **Cortex Search — multi-backend** (pgvector, Qdrant, etc.) | Pluggable vector-store registry | `pipeline/vectorstore/` (Chroma, Memory, PgVector, Qdrant) | ✅ Implemented (Qdrant stub) |
| **Cortex Analyst** (natural-language → structured query with semantic model) | `StructuredIndexer` (Postgres FTS + JSONB) | `pipeline/indexing/structured.py`, `agents/structured_agent.py` | ⚠️ Partial — FTS/JSONB only; **no semantic model layer** |
| **Semantic Model (YAML)** — metrics, dimensions, measures contract | **Missing** | — | ❌ Gap → ADR 0014 |
| **Cortex Complete** (hosted LLM inference: Claude, Llama, Mistral) | `SynthesisAgent` + `AnthropicClient` (pluggable) | `agents/synthesis_agent.py`, `app/llm.py` | ✅ Implemented (template fallback when no key) |
| **Cortex Complete — multi-model routing** (switch models per tenant/task) | `get_llm_client()` singleton (Anthropic only) | `app/llm.py` | ⚠️ Partial — single provider; model routing not wired |
| **Cortex Document AI** (extraction, classification, layout parsing) | Pluggable extractor registry | `pipeline/extractors/` (PDF, HTML, JSON, CSV, Text; DOCX/XLSX/PPTX stubs) | ✅ Implemented (Office formats = stubs) |
| **Cortex Agents** (multi-step reasoning, tool orchestration) | `LangGraphOrchestrator` + `SequentialOrchestrator` | `agents/orchestrators/langgraph_orchestrator.py`, `agents/orchestrators/sequential.py` | ✅ Implemented |
| **Cortex Agents — fan-out / fan-in** (parallel tool calls) | `document_researcher_node` ∥ `structured_agent_node` ∥ `graph_agent_node` → `risk_scorer_node` | `agents/orchestrators/langgraph_orchestrator.py` | ✅ Implemented |
| **Cortex Agents — HITL (human-in-the-loop)** | `interrupt_before=["human_review_node"]` | `langgraph_orchestrator.py` + `POST /analyze/{id}/resume` | ✅ Implemented |
| **Cortex Agents — short-term memory (session state)** | `MemorySaver` checkpointer, thread_id = `{tenant}:{session}` | `langgraph_orchestrator.py` | ✅ Implemented |
| **Cortex Agents — long-term memory (cross-session)** | `AnalysisHistory` Postgres table + `LongTermMemoryStore` | `app/models.py`, `agents/memory/store.py` | ✅ Implemented |
| **Cortex Guard** (PII detection, prompt injection, output moderation, content policy) | **Missing** | — | ❌ Gap → ADR 0015 |
| **Cortex Fine-tuning** (domain-specific model customisation on private data) | **Missing** | — | ❌ Gap (Phase 9+) |
| **Knowledge Graph** *(beyond Cortex — DealPrep advantage)* | `GraphAgent` + Neo4j (1-hop relationship traversal) | `pipeline/indexing/graph/`, `agents/graph_agent.py` | ✅ DealPrep-unique capability |
| **Snowflake Stages** (data landing zone) | `data/` + `dropzone/` + `FileUploadConnector` | `connectors/file_upload.py`, `app/routers/sources.py` | ✅ Implemented |
| **Snowpipe / streaming ingestion** (real-time continuous load) | APScheduler manifest polling (batch/interval only) | `app/runner.py` | ⚠️ Partial — batch polling; no event-driven streaming |
| **Snowflake Tasks** (scheduled jobs) | APScheduler with per-source polling intervals | `app/runner.py` | ✅ Implemented |
| **Multi-tenancy & isolation** | `tenant_id` scoping across all stores; per-tenant profiles | `app/models.py`, `pipeline/vectorstore/chroma.py` (collections), `pipeline/indexing/graph/neo4j_client.py` (property filters) | ✅ Implemented |
| **Row-access policies / Column masking** (data governance at storage layer) | App-level `tenant_id` filter only — no storage-layer enforcement | — | ⚠️ Partial — app-enforced only |
| **Cost attribution per tenant** (LLM tokens, compute credits tracked per tenant) | **Missing** | — | ❌ Gap → ADR 0015 |
| **Evaluation pipeline** (automated quality gates, regression tracking) | Manual runbooks in `docs/evaluation/` | `docs/evaluation/` | ⚠️ Partial — human-run only; no CI automation |
| **Semantic caching** (skip LLM for near-duplicate queries) | **Missing** | — | ❌ Gap (Phase 9+) |
| **Universal / federated search** (ranked merge across all index types) | `UnifiedSearch` (vector + structured + graph, score-merged) | `app/search_service.py` | ✅ Implemented |
| **Connector plugin system** (extensible data sources) | `BaseConnector` ABC + `@register_connector` + `FileUpload` + `RestApi` | `connectors/` | ✅ Implemented |
| **Per-tenant pipeline profiles** (chunker/embedder/vector-store overrides) | `TenantPipelineProfile` ORM + `resolve_profile()` | `app/profiles.py`, `app/models.py` | ✅ Implemented |

---

## 3. Progress Summary

### Coverage by Layer

```
CORTEX AGENTS (orchestration)
  ├─ Fan-out / fan-in              ████████████████████ 100%
  ├─ HITL (human review)           ████████████████████ 100%
  ├─ Short-term memory             ████████████████████ 100%
  ├─ Long-term memory              ████████████████████ 100%
  └─ Conditional routing           ████████████████████ 100%

CORTEX SEARCH (retrieval)
  ├─ Semantic vector search        ████████████████████ 100%
  ├─ Keyword / FTS                 ████████████████████ 100%
  ├─ Multi-backend (pluggable)     ████████████████░░░░  80%  (Qdrant stub)
  └─ Semantic caching              ░░░░░░░░░░░░░░░░░░░░   0%

CORTEX ANALYST (structured query)
  ├─ FTS / JSONB retrieval         ████████████████████ 100%
  ├─ NL→SQL with semantic model    ████░░░░░░░░░░░░░░░░  20%  (ADR 0014 gap)
  └─ Metrics / dimensions layer    ░░░░░░░░░░░░░░░░░░░░   0%

CORTEX COMPLETE (LLM)
  ├─ Narrative synthesis           ████████████████████ 100%
  ├─ Template fallback (no key)    ████████████████████ 100%
  ├─ Multi-model routing           ████░░░░░░░░░░░░░░░░  20%  (Anthropic only)
  └─ Fine-tuning                   ░░░░░░░░░░░░░░░░░░░░   0%

CORTEX DOCUMENT AI (extraction)
  ├─ PDF, HTML, JSON, CSV, Text    ████████████████████ 100%
  └─ Office (DOCX, XLSX, PPTX)    ████░░░░░░░░░░░░░░░░  20%  (stubs)

CORTEX GUARD (safety)             ░░░░░░░░░░░░░░░░░░░░   0%  (ADR 0015 gap)

GOVERNANCE
  ├─ Multi-tenant isolation        ████████████████████ 100%  (app-level)
  ├─ Storage-layer enforcement     ████░░░░░░░░░░░░░░░░  20%  (app only, not DB)
  └─ Cost attribution              ░░░░░░░░░░░░░░░░░░░░   0%

DATA LAYER
  ├─ Landing zone (batch)          ████████████████████ 100%
  ├─ Scheduling (APScheduler)      ████████████████████ 100%
  └─ Streaming ingestion           ██░░░░░░░░░░░░░░░░░░  10%  (polling only)

BEYOND CORTEX (DealPrep-unique)
  ├─ Knowledge graph (Neo4j)       ████████████████████ 100%
  └─ Risk scoring engine           ████████████████████ 100%
```

### Overall Cortex AI Coverage: ~72%

| Category | Coverage |
|---|---|
| Agents / Orchestration | **100%** |
| Retrieval (Search) | **90%** |
| Structured Query | **40%** |
| LLM / Synthesis | **70%** |
| Document Extraction | **70%** |
| Safety / Guard | **0%** |
| Governance | **40%** |
| Data Layer | **70%** |
| **Weighted overall** | **~72%** |

---

## 4. Terminology Crosswalk

| Snowflake Cortex Term | DealPrep Term | Notes |
|---|---|---|
| Cortex Search Service | `VectorIndexer` + vector-store backend | DealPrep adds Neo4j as a third retrieval leg |
| Cortex Analyst | `StructuredAgent` + `StructuredIndexer` | DealPrep uses FTS; Cortex uses NL→SQL via semantic model |
| Semantic Model (YAML) | *(missing)* | Core ADR 0014 deliverable |
| Cortex Complete | `SynthesisAgent` → `AnthropicClient` | Same pattern: LLM call with system prompt + context |
| Cortex Agents | `LangGraphOrchestrator` / `SequentialOrchestrator` | LangGraph is the production-grade equivalent |
| Tool (in Cortex Agents) | `BaseAgent` implementation | `DocumentResearcher`, `StructuredAgent`, `GraphAgent` are "tools" in Cortex language |
| Stage (landing zone) | `data/` + `dropzone/` + `FileUploadConnector` | Same concept; Cortex uses S3-compatible Snowflake stages |
| Snowpipe (streaming) | `RestApiConnector` + APScheduler polling | Gap: DealPrep is batch-only |
| Task (scheduled job) | APScheduler job | Same concept |
| Warehouse (compute) | `asyncio.to_thread()` thread pool | DealPrep executes on local threads; Cortex uses isolated Snowflake warehouses |
| Cortex Guard | *(missing)* | Core ADR 0015 deliverable |
| Row Access Policy | `tenant_id` filter on every DB/vector/graph query | DealPrep enforces in app code, not at storage layer |
| Column Masking | *(missing)* | PII masking not yet implemented |
| Credit / cost tracking | *(missing)* | Per-tenant token metering not implemented |
| `SNOWFLAKE.CORTEX.COMPLETE()` SQL function | `get_llm_client().complete(system, user)` | Same abstraction; DealPrep wraps Anthropic SDK |
| `SNOWFLAKE.CORTEX.EMBED_TEXT_768()` | `MinilmEmbedder.embed()` | Same concept; DealPrep uses local 384-dim model |
| Cortex Fine-tuning | *(future)* | Phase 9+ — requires labelled DealPrep-domain dataset |
| Checkpoint (Cortex Agents state) | `MemorySaver` (LangGraph) | Identical concept; DealPrep uses in-memory, Cortex uses Snowflake tables |

---

## 5. Gap Registry (Inputs for ADRs)

| Gap | Severity | ADR | Phase Target |
|---|---|---|---|
| Semantic Model layer — no YAML contract between structured data and the StructuredAgent | **High** | ADR 0014 | Phase 8 |
| Safety / Guardrails — no PII detection, no prompt-injection protection, no output moderation | **High** | ADR 0015 | Phase 8 |
| Multi-model LLM routing — only Anthropic wired; Cortex supports Claude, Mistral, Llama, Arctic | Medium | ADR 0016 (future) | Phase 9 |
| Cost attribution — no per-tenant LLM token metering or budget enforcement | Medium | ADR 0015 (§cost) | Phase 8 |
| Streaming ingestion — APScheduler polling only; no Snowpipe-equivalent event-driven path | Medium | ADR 0017 (future) | Phase 9 |
| Office extractors (DOCX, XLSX, PPTX) — stubs; ADR 0008 deferred them | Medium | Existing ADR 0008 | Phase 8 |
| Evaluation pipeline as code — runbooks are manual; no CI regression gates | Low | (attach to ADR 0015) | Phase 8 |
| Semantic caching — repeated similar queries re-run the full LLM pipeline | Low | ADR 0018 (future) | Phase 9+ |
| Storage-layer tenant enforcement — DB row-level security not enforced below app | Low | ADR 0015 (§governance) | Phase 8 |
| Cortex Fine-tuning — no domain-specific model customisation | Low | Phase 9+ | Phase 9+ |

---

## 6. What DealPrep Has That Cortex AI Does Not

| DealPrep Capability | Why It Matters |
|---|---|
| **Neo4j knowledge graph** (entities + 1-hop relationships) | Cortex has no graph layer; DealPrep can surface related-party ownership chains that FTS/vector search misses |
| **Rule-based risk scorer** (additive signal model) | Cortex Agents can call tools but have no built-in domain-specific risk heuristic; DealPrep's scorer is PE/VC-domain-aware |
| **Pluggable chunking strategies** (section-aware, sentence-window, semantic) | Cortex Search handles chunking internally with no user control; DealPrep exposes strategy selection per tenant |
| **Per-tenant pipeline profiles** | Cortex binds a Cortex Search service at setup time; DealPrep lets each tenant independently choose embedder + vector store + chunker |
| **HITL interrupt/resume at the graph level** | Cortex Agents HITL is callback-based; DealPrep's `interrupt_before` + `/resume` endpoint is a cleaner REST contract |

---

## 7. Next Steps

1. **ADR 0014** — Semantic Model Layer: define a YAML schema for financial metrics/dimensions; wire `StructuredAgent` to generate SQL from it via LLM. This closes the biggest gap with Cortex Analyst.
2. **ADR 0015** — Safety & Guardrails Layer: PII redaction pre-ingestion, prompt-injection detection on queries, output content policy, per-tenant token budgets.
3. **Office extractors** — implement DOCX/XLSX/PPTX behind existing stubs (ADR 0008 deferred).
4. **Evaluation CI** — wrap the runbooks in pytest + GitHub Actions so quality gates run automatically on each backend addition.
