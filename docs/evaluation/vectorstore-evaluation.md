# Vector Store Evaluation Runbook

- **Stage:** Vector Indexer → Vector Store
- **Registry file:** `pipeline/vectorstore/base.py`
- **Governed by:** [ADR 0011](../adr/0011-vector-store-selection.md)
- **Last updated:** 2026-06-22

---

## Why the vector store needs formal evaluation

The vector store is the query-time half of semantic search. It must:
1. Return the right chunks (recall/precision).
2. Never leak one tenant's data to another (isolation).
3. Handle repeated ingestion without duplicating entries (idempotency).
4. Do all of the above within latency budgets as the corpus grows.

Silent failures here include: returning no results (ANN index not built), returning stale
results after a reindex (cache not invalidated), or mixing tenant data (broken namespace).
None of these raise exceptions; they return wrong data.

---

## 1. Correctness tests [REQUIRED]

### 1.1 Contract conformance

| Test | Pass condition |
|---|---|
| `upsert(chunks, tenant_id)` accepts `list[Chunk]` | No exception; returns nothing (or count) |
| `query(text, tenant_id, k)` returns `list[tuple[float, str]]` | Score in [−1, 1]; text non-empty |
| `peek(tenant_id, limit)` returns `list[dict]` | Each dict has at least `text` and `id` keys |
| `upsert` with empty list is a no-op | No exception; store unchanged |
| `query` against empty store returns `[]` | Not an error; just empty |
| `query` with `k=1` returns at most 1 result | Never returns more than `k` |
| `query` with `k > corpus_size` returns all items | No error for k > n |

### 1.2 Upsert idempotency [REQUIRED]

The same chunk (same `chunk_id`) ingested twice must not appear twice in results.

```python
store.upsert(chunks, tenant_id)
store.upsert(chunks, tenant_id)          # same chunks, second time
results = store.query("test query", tenant_id, k=100)
ids = [r[1] for r in results]
assert len(ids) == len(set(ids)), "Duplicate chunks found after idempotent upsert"
```

| Condition | Pass |
|---|---|
| Count after double-upsert = count after single upsert | Idempotent |
| Top-1 score after double-upsert same as after single upsert | Deduplication does not alter scores |

### 1.3 Score ordering

Results must be returned in descending similarity order (highest score first):

```python
results = store.query(query, tenant_id, k=10)
scores = [r[0] for r in results]
assert scores == sorted(scores, reverse=True), "Results not sorted by score"
```

---

## 2. Tenant isolation [REQUIRED — zero tolerance]

This is a **hard gate**. Any failure in isolation means the backend cannot be used in a
multi-tenant deployment regardless of quality metrics.

### 2.1 Cross-tenant query returns zero results

```python
# Ingest corpus for tenant A
store.upsert(chunks_a, tenant_id="tenant-A")

# Query as tenant B (no data ingested)
results = store.query("any query", tenant_id="tenant-B", k=10)
assert results == [], f"Isolation breach: tenant B got {len(results)} results"
```

### 2.2 Peek does not cross tenants

```python
store.upsert(chunks_a, tenant_id="tenant-A")
peeked = store.peek(tenant_id="tenant-B", limit=100)
assert peeked == [], "Isolation breach: peek returned tenant-A data for tenant-B"
```

### 2.3 Parallel upsert isolation (thread safety)

Simulate the three-store fan-out: two threads upsert different tenant data simultaneously.
After both complete, verify each tenant's query returns only its own data.

```python
import threading
def ingest(tenant_id, chunks):
    store.upsert(chunks, tenant_id)

t1 = threading.Thread(target=ingest, args=("tenant-A", chunks_a))
t2 = threading.Thread(target=ingest, args=("tenant-B", chunks_b))
t1.start(); t2.start(); t1.join(); t2.join()

# Both should return only their own data
assert all("tenant-A-content" in r[1] for r in store.query("...", "tenant-A", k=10))
assert all("tenant-A-content" not in r[1] for r in store.query("...", "tenant-B", k=10))
```

| Backend | Isolation mechanism | Thread safety verified |
|---|---|---|
| chroma | per-tenant collection name | double-checked lock (see §5) |
| memory | per-tenant dict key | dict per-key isolation |
| pgvector (stub) | tenant_id column + mandatory WHERE | pending |
| qdrant (stub) | per-tenant collection or payload filter | pending |

### 2.4 Delete-one-tenant does not affect another

When a tenant is offboarded and their data deleted:

```python
store.delete_tenant("tenant-A")   # (future API — not yet implemented)
results_b = store.query("...", "tenant-B", k=10)
assert len(results_b) > 0, "Tenant B data was accidentally deleted"
```

> **Note:** `delete_tenant` is not yet implemented. This test is a future gate.
> Flag in the Phase 5–6 production review as a ship-blocker for GA.

---

## 3. Retrieval quality metrics [REQUIRED before production profile selection]

Using the golden Q&A corpus from the embedding evaluation (same 20 question/answer pairs):

| Metric | How to measure | chroma+minilm | memory+minilm | memory+hashing |
|---|---|---|---|---|
| **Recall@5** | Gold chunk in top-5 | ≥ 0.70 | ≥ 0.70 | ≥ 0.45 |
| **Precision@5** | Fraction of top-5 that are gold | ≥ 0.55 | ≥ 0.55 | ≥ 0.30 |
| **MRR** | 1/rank of first gold chunk | ≥ 0.55 | ≥ 0.55 | ≥ 0.30 |

> **memory** store uses exact cosine over all vectors — it is theoretically higher quality than
> ChromaDB's ANN (approximate) for small corpora (< 10K chunks). For large corpora, ANN is
> necessary; memory becomes impractically slow.

### 3.1 ANN approximation gap (ChromaDB HNSW)

For large corpora, ANN sacrifices some recall for speed. Measure the gap:

```
recall_gap = recall_exact - recall_ann
```

| Corpus size | Acceptable recall gap | Note |
|---|---|---|
| < 10K chunks | ≤ 0.02 | ANN nearly exact at small scale |
| 10K–100K | ≤ 0.05 | Tune HNSW `ef_construction` if gap > 0.05 |
| > 100K | ≤ 0.10 | Expected; consider increasing `ef` query param |

---

## 4. Performance benchmarks [REQUIRED]

### 4.1 Query latency

| Corpus size | chroma p50 | chroma p95 | memory p50 | memory p95 |
|---|---|---|---|---|
| 1K chunks | < 10 ms | < 50 ms | < 5 ms | < 20 ms |
| 10K chunks | < 20 ms | < 100 ms | < 50 ms | < 200 ms |
| 100K chunks | < 50 ms | < 200 ms | < 500 ms | < 2 s |
| 1M chunks | < 100 ms | < 500 ms | OOM risk — not recommended | |

`memory` store is O(N) per query (linear scan). It must not be used for corpora > 50K chunks
in production. ChromaDB's HNSW is O(log N) and scales to millions of vectors.

### 4.2 Upsert throughput

| Backend | Target throughput |
|---|---|
| chroma | ≥ 200 chunks/s (batched) |
| memory | ≥ 5000 chunks/s |
| pgvector (when implemented) | ≥ 500 chunks/s |
| qdrant (when implemented) | ≥ 1000 chunks/s |

Measure by batch-upserting 1000 chunks and timing wall clock. Report chunks/second.

### 4.3 Persistence after restart

For persistent stores (chroma, pgvector, qdrant):

```
1. Upsert N chunks
2. Kill and restart the server (or reload the client)
3. Query → must return the same N results
```

| Backend | Persistent? | Verified |
|---|---|---|
| chroma | Yes (disk `chroma_dir`) | Yes — verified live E2E |
| memory | No (in-RAM) | By design; document clearly |
| pgvector | Yes | Pending |
| qdrant | Yes | Pending |

**`memory` store must be documented as ephemeral**: data is lost on server restart. Tenants
using `memory` must re-ingest after restart. The profile chooser in the UI should display a
warning for this backend.

---

## 5. Concurrency and thread safety

### 5.1 ChromaDB double-checked lock (existing implementation)

The `get_chroma_client()` factory uses a `threading.Lock()` with double-checked init. Verify:

```python
import threading
clients = []
def get(): clients.append(get_chroma_client())
threads = [threading.Thread(target=get) for _ in range(20)]
[t.start() for t in threads]; [t.join() for t in threads]
assert len(set(id(c) for c in clients)) == 1, "Multiple Chroma clients created"
```

### 5.2 Memory store concurrent writes

The `memory` store uses a per-tenant dict. Concurrent upserts to the same tenant must not
corrupt the dict (Python dict is thread-safe for simple assignments; list appends in a loop
are not). Verify with 10 threads upserting different chunks to the same tenant.

---

## 6. Failure mode inventory

| Failure | Expected behaviour | Silent? |
|---|---|---|
| Chroma dir not writable | Exception on first upsert; surfaced to run_stages | No |
| Chroma collection creation race (two threads) | Double-checked lock prevents; single collection created | No race |
| memory store query on empty corpus | Returns `[]` | Yes — acceptable |
| pgvector/qdrant stub called | Raises; `implemented = False` prevents use | No |
| Dimension mismatch at query time | ChromaDB raises `InvalidDimensionException`; surfaces to run | No |
| `peek()` on unknown tenant | Returns `[]` | Yes — acceptable |

---

## 7. Backend comparison quick-guide

| Criterion | chroma | memory | pgvector | qdrant |
|---|---|---|---|---|
| Persistent | Yes | No | Yes | Yes |
| Scales beyond RAM | Yes (HNSW) | No | Yes | Yes |
| Multi-process safe | Yes (file lock) | No | Yes | Yes |
| Zero extra infra | Yes (embedded) | Yes | No (PG ext) | No (Docker) |
| Filtering (metadata) | Limited | Custom | Full SQL | Rich |
| Best for | Default/local | CI/tests/POC | PG-only infra | Scale + filter |
| Status | ✅ production | ✅ production | 🟡 stub | 🟡 stub |

---

## 8. Red flags in production (on-call signals)

| Signal | Likely cause | Action |
|---|---|---|
| `query()` returns empty for a corpus that has data | ANN index not built; collection name mismatch | Check `chroma_dir` content; verify collection name matches `tenant_id` |
| Query results have duplicate chunks | Idempotency not enforced on re-ingest | Run §1.2 idempotency test; check `chunk_id()` stability |
| Cross-tenant data visible in peek | Namespace bug in collection name | **Severity 1** — disable the backend; run §2.1 isolation test |
| Latency spikes as corpus grows | ANN index rebuild triggered | Monitor ChromaDB `ef_construction`; schedule off-peak reindex |
| `memory` store empty after server restart | Ephemeral store — expected | Document for tenant; provide reindex guidance |
| ChromaDB `InvalidDimensionException` | Embedder changed without reindex | Restore previous embedder profile; reindex with new profile |

---

## 9. Adding a new vector store — gate checklist

Before setting `implemented = True`:

- [ ] §1 contract tests pass (upsert, query, peek, idempotency, score ordering)
- [ ] §2 isolation tests pass (zero tolerance — all four subtests)
- [ ] §3 retrieval quality measured (Recall@5 ≥ 0.70)
- [ ] §4 performance benchmarks recorded at 1K, 10K, 100K chunks
- [ ] §4.3 persistence verified (or explicitly documented as ephemeral)
- [ ] §5 thread safety verified (concurrent upserts to same + different tenants)
- [ ] §6 failure modes verified
- [ ] Module imported in `pipeline/vectorstore/__init__.py`
- [ ] `GET /capabilities` lists it with `implemented: true`
- [ ] ADR 0011 updated with new backend + selection criteria

---

## Eval log

| Date | Backend | Corpus | Isolation | Idempotent | Recall@5 | p95 query | Persistent | Result | Notes |
|---|---|---|---|---|---|---|---|---|---|
| 2026-06-22 | chroma | sample_deal.pdf (2 tenants) | PASS | PASS (qualitative) | not formally measured | ~40 ms | Yes | PASS | Default; race-condition bug fixed; live E2E verified |
| 2026-06-22 | memory | sample_deal.pdf chunks | PASS | PASS | not formally measured | ~5 ms | No (ephemeral) | PASS | POC/CI; tested live with hashing profile |
| 2026-06-22 | pgvector | stub | — | — | — | raises | — | PASS (stub) | Pending PG extension |
| 2026-06-22 | qdrant | stub | — | — | — | raises | — | PASS (stub) | Pending Docker service |
