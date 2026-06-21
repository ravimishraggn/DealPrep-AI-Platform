# ADR 0007 — Parallel fan-out indexing across vector, structured, and graph engines

- **Status:** Accepted
- **Date:** 2026-06-21
- **Deciders:** DealPrep platform team
- **Maps to build request:** "ADR-005 (Parallel fan-out indexing after document processing)"

## Context

After a document is processed, the same content must land in **three independent stores**:
ChromaDB (vectors), Postgres (structured + FTS), and Neo4j (graph). These three writes share
no data dependency — none consumes another's output. Running them sequentially makes a run's
latency the *sum* of three slow operations (embedding, SQL insert, NER + LLM + graph writes),
and couples their failure modes (a Neo4j hiccup would delay vector indexing).

## Decision

After `DocumentProcessor`, **fan out the three indexers in parallel** and **fan in** their
results:

- A `ThreadPoolExecutor(max_workers=3)` submits `index_vector`, `index_structured`, and
  `index_graph` concurrently. Threads (not processes) are sufficient because each stage is
  I/O- or native-bound and releases the GIL (DB sockets, the Neo4j driver, and torch/embedding
  all release it).
- **Per-stage isolation of failure:** each future is resolved independently; one indexer
  raising is captured as a failed `StageResult` and does **not** abort the others.
- **Per-stage observability:** every stage (`extract`, `process`, `index_vector`,
  `index_structured`, `index_graph`) is logged to the `run_stages` table, tagged by run and
  tenant, so operators see exactly where a run succeeded, was skipped, or failed.
- The orchestrator is invoked automatically by the pipeline runner after a successful fetch —
  zero manual steps between acquisition and search-ready indexes.

## Consequences

**Positive**
- Run latency ≈ the *slowest* indexer, not the sum — typically a 2–3× wall-clock win.
- Decoupled failure: a transient Neo4j or LLM problem degrades the graph for that run while
  vectors and structured data still index, and the failure is visible in `run_stages`.
- Independent stages are independently testable and replaceable.

**Negative / trade-offs**
- Partial success is now a real state: a run can be "indexed in 2 of 3 stores." Search must
  tolerate a store being behind, and operators must watch per-stage failure rates (not just
  run success). The denormalized `last_run_status` reflects fetch, not per-store completeness.
- Three concurrent writers per run raises peak resource use (embedding RAM + DB + driver
  connections); bounded here by a 3-worker pool per run, but many concurrent runs multiply it.
- Thread-based parallelism shares one process; a hard crash in a native lib (e.g. torch) can
  still take the worker down. Acceptable for V1; a queue/worker split is the scale path.
- No cross-store transactionality — the three stores can diverge if one write fails. Reconciled
  by re-running the source (idempotent upserts), not by distributed transactions.
