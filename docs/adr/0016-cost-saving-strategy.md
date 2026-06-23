# ADR 0016 — Cost-Saving Strategy Across Every Platform Layer

| Field | Value |
|---|---|
| **Status** | Proposed |
| **Date** | 2026-06-23 |
| **Deciders** | Platform Engineering, Finance |
| **Phase** | 8 — Cost Governance |
| **Relates to** | ADR 0015 (CostMeter), ADR 0013 (orchestration), ADR 0010 (embedding), ADR 0011 (vector store) |

---

## Context

The platform has seven distinct cost surfaces.  Without an explicit strategy, costs grow
proportionally to tenant count and document volume with no levers to pull.  Enterprise PE/VC
clients will require a credible cost model before production sign-off.

Snowflake Cortex AI manages cost via **Cortex Budget** (per-tenant credit caps), **model
routing** (smallest model that meets quality bar), **result caching** (skip the LLM for
near-duplicate queries), and **warehouse sizing** (compute isolated per workload).  DealPrep
must implement equivalent controls without Snowflake's managed infrastructure.

### The Seven Cost Surfaces

| # | Layer | Primary cost driver | Current behaviour |
|---|---|---|---|
| 1 | LLM / Synthesis | Token count × model price | Every query calls Sonnet regardless of complexity |
| 2 | Embedding | Model inference time + memory | MinilM is free locally; cloud stubs add per-token cost |
| 3 | Vector store | Storage size + query time | ChromaDB persists all chunks indefinitely |
| 4 | Knowledge graph | Neo4j bolt queries + write ops | All entities extracted even for short docs |
| 5 | Ingestion / extraction | CPU time + storage I/O | All documents re-processed on every run |
| 6 | Agent orchestration | Fan-out always runs all 3 agents | GraphAgent runs even when no graph data exists |
| 7 | Infrastructure | Postgres + ChromaDB + Neo4j running 24/7 | Always-on regardless of load |

---

## Decision

Adopt a **tiered cost-reduction strategy**: each layer gets a primary mechanism and a secondary
mechanism.  No mechanism compromises correctness or tenant isolation.  All mechanisms are
configurable — they default to `on` in production but can be disabled per-tenant or globally.

---

## Layer-by-Layer Strategy

---

### Layer 1 — LLM / Synthesis

**Primary: Model routing by task complexity**

Not every query needs Claude Sonnet.  Use the smallest model that meets the quality bar:

| Task | Model | Reason |
|---|---|---|
| NL→SQL (SemanticModelAgent) | `claude-haiku-4-5` | Structured task; schema context is enough; 10× cheaper than Sonnet |
| Injection detection classifier | `claude-haiku-4-5` | Binary classification; prompt is short |
| Narrative synthesis — routine query (risk_score < 0.3) | `claude-haiku-4-5` | Low-stakes answer; template fallback acceptable |
| Narrative synthesis — medium risk (0.3 ≤ risk_score < 0.7) | `claude-sonnet-4-6` | Nuanced synthesis needed |
| Narrative synthesis — high risk (risk_score ≥ 0.7, HITL triggered) | `claude-sonnet-4-6` | Analyst-reviewed; quality must be high |
| Relationship extraction (graph indexer) | `claude-haiku-4-5` | Entity pairs extraction; structured output |

Implementation: `app/llm.py` exposes `get_llm_client(task: str)` → selects model from a config
table.  Model choices are tenant-overridable via `TenantPipelineProfile`.

Estimated saving vs "always Sonnet": **~60% reduction** in LLM cost at typical M&A query mix
(70% routine, 25% medium, 5% high-risk).

**Secondary: Semantic result caching**

Cache the final `AnalyzeResponse` (answer + citations) keyed by
`sha256(tenant_id + normalized_query)`.  On cache hit, skip the entire orchestration pipeline.
TTL: 1 hour (configurable per tenant).

Cache store: Redis (preferred) or in-process `functools.lru_cache` (dev/single-worker mode).

```python
class SemanticCache:
    def get(self, tenant_id: str, query: str) -> AnalyzeResponse | None: ...
    def set(self, tenant_id: str, query: str, response: AnalyzeResponse, ttl_s: int = 3600) -> None: ...
    def invalidate_tenant(self, tenant_id: str) -> None: ...  # called after new ingestion
```

Cache key normalisation: lowercase, strip punctuation, collapse whitespace.  Two queries that
differ only in punctuation or case return the same cached response.

Estimated saving: **40–70% LLM call reduction** for tenants that re-run similar due-diligence
queries across a deal lifecycle.

**Tertiary: Prompt compression**

Before sending to the LLM, truncate retrieved context to the minimum needed:
- Vector chunks: top-3 by score, max 400 chars each (not top-5 at 1000 chars).
- Structured records: top-3, field values only (drop metadata).
- Graph triples: max 8 (not unlimited).

Estimated token saving: **~35% fewer input tokens** with no measurable quality drop at p90
(validated in chunking evaluation — retrieval quality peaks at k=3 for financial docs).

---

### Layer 2 — Embedding

**Primary: Prefer local embedder; cloud only when explicitly selected**

`MinilmEmbedder` (all-MiniLM-L6-v2, 384-dim, CPU) is free to run — the model is downloaded once
and runs locally.  Cloud embedders (OpenAI `text-embedding-3-small`, Bedrock Titan) cost per
token and add network latency.

Rule: `TenantPipelineProfile.embedding` defaults to `"minilm"`.  Cloud embedders require an
explicit override.  Capabilities endpoint marks cloud backends with
`"cost_tier": "paid"` so UI/operators know before switching.

**Secondary: Embedding cache (dedup identical text)**

Many documents share boilerplate text (headers, legal disclaimers, cover pages).  Cache
embeddings by `sha256(text)`:

```python
class EmbeddingCache:
    # backed by sqlite (dev) or Redis (prod)
    def get(self, text: str) -> list[float] | None: ...
    def set(self, text: str, vector: list[float]) -> None: ...
```

On cache hit, the embedder returns the cached vector immediately — no model inference.
Expected hit rate for financial documents: **20–40%** (boilerplate is common in CIM/pitch decks).

**Tertiary: Batch embedding**

Rather than embedding each chunk individually, collect chunks into batches of 32 and call
`embedder.embed_batch(texts)`.  `SentenceTransformer.encode()` already vectorises batches
efficiently via mini-batch inference.  Batch embedding is **3–5× faster** than serial calls,
reducing wall-clock time and freeing the event loop sooner.

---

### Layer 3 — Vector Store

**Primary: Chunk TTL and eviction**

Chunks from deleted sources should be evicted immediately.  Additionally, introduce a
configurable **chunk TTL** (default: 365 days) per tenant.  Chunks older than TTL are evicted
during a nightly maintenance window.

```python
class VectorMaintenance:
    def evict_deleted_sources(self, tenant_id: str, source_ids: list[str]) -> int: ...
    def evict_expired_chunks(self, tenant_id: str, ttl_days: int = 365) -> int: ...
    def collection_stats(self, tenant_id: str) -> dict: ...  # size, doc count, oldest chunk
```

A `GET /tenants/{id}/vector-stats` endpoint exposes collection size so operators can make
eviction decisions.

**Secondary: Deduplication on ingest**

Before inserting a chunk into the vector store, hash its text (`sha256(text)`) and check if a
document with that hash already exists for the tenant.  On collision, skip the insert (the
existing vector is retained).  This prevents re-processing documents that are re-uploaded
unchanged.

Expected saving: **50–80% fewer inserts** for tenants that re-run ingestion after minor manifest
changes (connector re-fetch with no new content).

**Tertiary: Dimension-appropriate model per tenant**

For tenants with very large collections (> 500k chunks), consider switching to a 256-dim
quantised model.  Smaller dimensions reduce ChromaDB storage by 33% and query time by ~20%.
This is a tenant-level profile setting: `embedding: "minilm-256"` (stub for Phase 9).

---

### Layer 4 — Knowledge Graph

**Primary: Selective graph indexing by document type**

Not all document types benefit from graph extraction.  NER + relationship extraction is
expensive (spaCy pass + optional LLM call).  Skip graph indexing when:

- Document is `format_type: csv` or `format_type: json` (structured data → Postgres path only).
- Document is shorter than 500 tokens (no meaningful entity graph possible).
- Tenant profile sets `graph_enabled: false`.

Implementation: add `skip_graph: bool` to `PipelineProfile` and check before `GraphIndexer.run()`.

Estimated saving: **30–50% of graph indexing calls** eliminated for typical deal room that is
50% spreadsheet data.

**Secondary: Relationship extraction routing**

`RelationshipExtractor` has two modes — LLM (Claude Haiku) and rule-based (regex + heuristic).
Use LLM only when the entity density justifies it (> 5 named entities per 500 tokens).
Otherwise use rule-based (zero LLM cost).

```python
def _should_use_llm(self, entities: list[str], text_tokens: int) -> bool:
    return len(entities) / (text_tokens / 500) > 5
```

Estimated saving: **60–70% fewer LLM calls** in the graph indexer (most chunks have sparse
entity density).

---

### Layer 5 — Ingestion / Extraction

**Primary: Content-hash deduplication (skip re-ingestion)**

Before processing a fetched document, compute `sha256(raw_bytes)`.  If a `RunHistory` row
already exists for this tenant + source + content_hash with `status = completed`, skip
extraction entirely.

```python
def _already_processed(self, tenant_id: str, source_id: str, content_hash: str) -> bool:
    return db.query(RunHistory).filter_by(
        tenant_id=tenant_id, source_id=source_id, content_hash=content_hash, status="completed"
    ).count() > 0
```

Add `content_hash: str | None` column to `RunHistory`.

Estimated saving: **70–90% of extraction CPU** for connectors that poll frequently but source
documents change infrequently (e.g. a SharePoint folder polled every 15 minutes).

**Secondary: Incremental extraction (since_timestamp)**

`BaseConnector.fetch(since_timestamp)` already accepts a watermark.  Ensure all connectors
pass a real since-timestamp from the last successful run, not `epoch`.  This is already in the
contract but not enforced — the runner must read the last-run timestamp from `RunHistory` and
pass it.

Estimated saving: **80–95% fewer fetched bytes** for connectors over stable document
repositories.

**Secondary: Chunking cost reduction**

`SectionAwareChunker` is the best-quality strategy but also the most expensive (regex + two-pass
splitting).  For high-volume low-priority ingestion, allow tenants to configure
`chunking: "fixed_size"` in their profile.  Fixed-size is **3× faster** and produces comparable
retrieval quality for financial tabular documents.

---

### Layer 6 — Agent Orchestration

**Primary: Agent eligibility check (skip unavailable agents)**

Before spinning up all three retrieval agents in fan-out, check whether each agent has data for
this tenant:

```python
class AgentEligibilityChecker:
    def document_researcher_eligible(self, tenant_id: str) -> bool:
        # returns False if vector collection is empty
        return VectorIndexer().collection_size(tenant_id) > 0

    def structured_agent_eligible(self, tenant_id: str) -> bool:
        return StructuredIndexer().record_count(tenant_id) > 0

    def graph_agent_eligible(self, tenant_id: str) -> bool:
        return Neo4jClient().entity_count(tenant_id) > 0
```

If an agent is ineligible, it is skipped without running at all (saves one `asyncio.to_thread`
spin-up and the query overhead).  Ineligible agents appear in `agent_results` as
`status: "skipped"`.

Estimated saving: **significant for early-stage tenants** who have not yet indexed all three
store types.

**Secondary: Early termination on definitive answer**

If `risk_score < 0.1` and the `DocumentResearcher` returned ≥ 3 high-confidence chunks
(score > 0.90), skip `StructuredAgent` and `GraphAgent` — the vector answer is sufficient.
This is opt-in via `TenantPipelineProfile.fast_path_enabled: bool = False`.

Estimated saving: **~30% of retrieval cost** for tenants with dense, well-structured document
collections where vector search dominates.

**Tertiary: Orchestrator selection guidance**

| Scenario | Recommended orchestrator | Reason |
|---|---|---|
| Simple factual query, no history | `sequential` | Zero LangGraph overhead; 50–100 ms faster |
| Repeated tenant with history | `langgraph` | Long-term memory amortises LangGraph startup cost |
| High-risk document (> 5 related-party signals) | `langgraph` | HITL support required |
| Batch analysis (non-interactive) | `sequential` | MemorySaver not needed; lower memory overhead |

---

### Layer 7 — Infrastructure

**Primary: Idle-awareness for Neo4j**

Neo4j Community Edition runs as a Docker container.  For tenants with `graph_enabled: false` or
deployments where no graph data has been indexed, close the bolt connection pool when idle
for > 10 minutes.  Reconnect lazily on first graph query.

**Secondary: ChromaDB collection sizing alerts**

`GET /tenants/{id}/vector-stats` (from Layer 3) feeds a collection-size alert.  Operators are
notified when a single tenant's collection exceeds a configurable threshold (default: 500k
vectors) — at that point, migration to `pgvector` or `qdrant` is recommended for better cost
efficiency at scale.

**Tertiary: Postgres connection pooling**

`SessionLocal` creates a new connection per request.  Under load, this exhausts the connection
pool.  Add `pool_size=10, max_overflow=5` to the SQLAlchemy engine in `app/db.py`.  Fewer
connections = lower Postgres memory overhead = smaller cloud VM needed.

---

## Summary Cost Model

Assuming a mid-size tenant: 10,000 documents, 1,000 chunks/doc average, 500 queries/day.

| Layer | Mechanism | Estimated saving |
|---|---|---|
| LLM | Model routing (Haiku for low-risk) | 60% |
| LLM | Semantic result cache | 40–70% of remaining calls |
| LLM | Prompt compression | 35% token reduction |
| Embedding | Local MinilM default | 100% vs cloud (no per-call cost) |
| Embedding | Embedding dedup cache | 20–40% inference savings |
| Vector store | Chunk dedup on ingest | 50–80% fewer inserts |
| Graph | Skip CSV/JSON docs | 30–50% fewer graph calls |
| Graph | Rule-based relationship extraction | 60–70% fewer LLM calls in indexer |
| Ingestion | Content-hash dedup | 70–90% extraction skip rate |
| Orchestration | Agent eligibility check | Variable (0–100% per skipped agent) |
| **Combined** | | **~80% overall cost reduction vs baseline** |

---

## Implementation Priority (Phase 8)

| Priority | Mechanism | Effort | Saving |
|---|---|---|---|
| P0 | LLM model routing by task | S (config table change) | High |
| P0 | Content-hash dedup on ingestion | M (add column + check) | High |
| P0 | Agent eligibility check in orchestrator | S (3 DB count queries) | Medium |
| P1 | Embedding dedup cache | M (sqlite-backed cache) | Medium |
| P1 | Chunk TTL + eviction | M (maintenance job) | Medium |
| P1 | Graph selective indexing (skip CSV/JSON) | S (profile flag check) | Medium |
| P2 | Semantic result cache (Redis) | L (Redis dep + invalidation logic) | High |
| P2 | Prompt compression (top-3 / 400 char) | S (slice in synthesis_node) | Medium |
| P3 | Relationship extraction routing | M (entity density heuristic) | Low-Medium |

---

## File Plan

| File | Purpose |
|---|---|
| `app/llm.py` | Update `get_llm_client(task)` — model routing table |
| `pipeline/guards/cost_meter.py` | Already in ADR 0015 — add `remaining_budget()` |
| `pipeline/cache/embedding_cache.py` | `EmbeddingCache` (sha256 → vector) |
| `pipeline/cache/result_cache.py` | `SemanticCache` (query → AnalyzeResponse) |
| `pipeline/maintenance/vector_maintenance.py` | `VectorMaintenance` (TTL eviction, dedup) |
| `app/runner.py` | Add content_hash check before processing |
| `app/models.py` | Add `content_hash` column to `RunHistory` |
| `agents/orchestrators/langgraph_orchestrator.py` | Add `AgentEligibilityChecker` before fan-out |
| `app/routers/vector_stats.py` | `GET /tenants/{id}/vector-stats` |

---

## Consequences

**Positive:**
- Credible cost model for enterprise sales — customers can see cost levers, not a black box.
- Model routing alone saves ~60% LLM cost with zero quality trade-off for routine queries.
- Content-hash dedup makes re-ingestion free for unchanged documents — connectors can poll
  aggressively without multiplying cost.

**Negative / Risks:**
- Semantic result cache can return stale answers after new document ingestion.  Cache
  invalidation on `source` update is mandatory — `invalidate_tenant()` must be called from
  the runner's post-ingest hook.
- Agent eligibility checks add 3 DB round-trips before fan-out.  These should be cached for
  the duration of the request (100 ms cost, not per-node).
- "Fast path" early termination is opt-in only — incorrect use could silently suppress
  structured or graph evidence that contradicts the vector answer.
