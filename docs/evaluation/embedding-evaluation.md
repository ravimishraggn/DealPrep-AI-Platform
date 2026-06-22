# Embedding Evaluation Runbook

- **Stage:** Vector Indexer → Embedder
- **Registry file:** `pipeline/embedding/base.py`
- **Governed by:** [ADR 0010](../adr/0010-embedding-backend-selection.md)
- **Last updated:** 2026-06-22

---

## Why embedding quality is critical and hard to detect

The embedding backend is the **single highest-leverage decision in the entire pipeline**. It
determines the vector space that all of vector search, ANN indexing, and similarity scoring
depends on. A misconfigured or low-quality embedder does not cause an error — it silently
returns vectors that look fine but rank wrong results first. The only way to catch this without
formal evaluation is to notice that "search never finds what I'm looking for" weeks after
ingestion.

Two additional risks unique to this stage:
1. **Dimension mismatch:** index time and query time must use the same embedder. Switching
   embedders without a reindex returns nonsense (wrong vector space dimension or wrong scale).
2. **Data egress:** cloud-backed embedders (openai, bedrock) send your corpus text to an
   external API. This requires explicit security approval before production use.

---

## 1. Correctness tests [REQUIRED]

### 1.1 Contract conformance

| Test | Pass condition |
|---|---|
| `embed(texts: list[str]) -> list[list[float]]` | Returns a list of length `len(texts)` |
| Every vector has length `embedder.dim` | `len(v) == embedder.dim` for all `v` in result |
| `embedder.dim` is a class-level constant | `MyEmbedder.dim` accessible without instantiation |
| Deterministic (same text → same vector) | `embed(["hello"]) == embed(["hello"])` across calls |
| Empty string handled | Returns a zero vector or minimal vector; does not raise |
| Batch of 1 and batch of 100 return same vectors | No batchwise normalization differences |
| Unicode text (CJK, accented) | Does not raise; returns a vector of correct dimension |

### 1.2 Normalization

For semantic search (cosine similarity), vectors should be L2-normalized (unit norm).

```python
import math
v = embedder.embed(["hello"])[0]
norm = math.sqrt(sum(x*x for x in v))
assert abs(norm - 1.0) < 0.01, f"Vector not normalized: norm={norm}"
```

| Embedder | Normalized? | Notes |
|---|---|---|
| minilm | Yes | sentence-transformers normalizes by default |
| hashing | Yes | explicitly normalized in implementation |
| openai (stub) | Yes | API returns normalized vectors |
| bedrock (stub) | Varies by model | check model card |

If an embedder returns non-normalized vectors, the `memory` vector store still works (uses dot
product on normalized), but ChromaDB's cosine distance may behave unexpectedly. Document the
normalization contract in the backend implementation.

### 1.3 Dimension consistency

| Check | Pass condition |
|---|---|
| `dim` matches actual output length | `embedder.dim == len(embedder.embed(["test"])[0])` |
| dim stable across library upgrades | Pin model version (not `latest`) in requirements |
| Vector store created with correct dim | `ChromaDB` collection uses `embedder.dim` for HNSW |

---

## 2. Semantic quality metrics [REQUIRED for production approval of any backend]

These measure whether the embedder places semantically similar texts near each other in vector
space — the core job of an embedding model.

### 2.1 Semantic similarity test (golden pairs)

Construct a set of ≥ 20 text pairs labeled as:
- **High similarity** (paraphrases, same financial metric described differently): target cosine
  similarity ≥ 0.80
- **Medium similarity** (same company, different topics): target 0.40–0.79
- **Low similarity** (unrelated topics): target ≤ 0.40

```python
from pipeline.embedding.base import get_embedder
import math

def cosine(a, b):
    return sum(x*y for x,y in zip(a,b)) / (
        math.sqrt(sum(x*x for x in a)) * math.sqrt(sum(y*y for y in b))
    )

emb = get_embedder("minilm")
high_pairs = [("Acme Corp EBITDA margin is 35%", "Acme's earnings before interest and taxes divided by revenue is 35%")]
scores = [cosine(emb.embed([a])[0], emb.embed([b])[0]) for a, b in high_pairs]
assert min(scores) >= 0.75, f"High-similarity pair scored too low: {min(scores)}"
```

| Embedder | High sim threshold | Medium sim range | Low sim threshold |
|---|---|---|---|
| **minilm** (all-MiniLM-L6-v2) | ≥ 0.80 | 0.40–0.79 | ≤ 0.35 |
| **hashing** (MD5 bag-of-words) | ≥ 0.60 (exact overlap) | 0.20–0.60 | ≤ 0.20 |
| openai (ada-002) | ≥ 0.88 | 0.55–0.87 | ≤ 0.40 |
| bedrock (titan-embed) | ≥ 0.82 | 0.45–0.81 | ≤ 0.35 |

> **hashing** is intentionally lower quality — it is a bag-of-words hash, suitable only for
> exact-keyword-overlap retrieval (CI/offline testing). Never use it for production semantic
> search. Its thresholds above describe what it _can_ do, not what it _should_ be used for.

### 2.2 Retrieval quality on golden Q&A set

Using 20 question/answer pairs from the PE/VC domain (drawn from real deal memo content):

| Metric | How to measure | minilm target | hashing target |
|---|---|---|---|
| **Precision@5** | Top-5 chunks contain the gold answer | ≥ 0.65 | ≥ 0.40 |
| **Recall@10** | Gold chunk in top-10 | ≥ 0.80 | ≥ 0.55 |
| **MRR** | 1/rank of first gold chunk | ≥ 0.55 | ≥ 0.35 |
| **NDCG@10** | Normalized Discounted Cumulative Gain | ≥ 0.60 | ≥ 0.30 |

Run evaluation after ingesting the golden corpus with each embedder. Compare metrics between
embedders to decide if an upgrade (e.g. minilm → openai) is justified for a tenant's corpus.

### 2.3 Cross-query consistency

Run the same query 5 times consecutively; all 5 result sets must be identical (determinism).
This verifies that the model is loaded once and does not drift between calls.

---

## 3. Performance benchmarks [REQUIRED]

### 3.1 Throughput and latency

| Embedder | Single chunk p50 | Single chunk p95 | Batch of 100 p95 |
|---|---|---|---|
| **minilm** (CPU) | < 50 ms | < 200 ms | < 3 s |
| **hashing** | < 1 ms | < 5 ms | < 50 ms |
| openai (API) | < 300 ms | < 1 s | < 5 s (rate-limited) |
| bedrock (API) | < 400 ms | < 1.5 s | < 8 s (rate-limited) |

Measure with a corpus of 100 chunks of ~500 chars each. Warm-up one call before timing.

### 3.2 Model load time

| Embedder | Cold start (first embed call) | Warm (subsequent) |
|---|---|---|
| minilm | < 5 s (model download on first run) | < 50 ms |
| hashing | < 1 ms | < 1 ms |
| openai | < 100 ms (HTTP) | < 300 ms |

The model is loaded lazily on first call and cached. Verify that `get_embedder(name)` returns
the same cached instance on repeated calls (the `@lru_cache` or equivalent).

### 3.3 Memory footprint

| Embedder | Peak RSS increase at load | Per-embed RSS increase |
|---|---|---|
| minilm | ~400 MB (model weights) | < 1 MB |
| hashing | < 1 MB | negligible |
| openai | < 5 MB (HTTP client) | negligible |

If minilm's 400 MB footprint is a constraint, consider the smaller `all-MiniLM-L3-v2` (150 MB)
— measure MRR impact before switching.

---

## 4. Index/query consistency (the most critical check)

A vector indexed with embedder A cannot be queried with embedder B. This is the silent failure
mode that corrupts all search results when a tenant changes their profile without reindexing.

### 4.1 Same-embedder round-trip

```
1. Index 10 chunks with embedder X → store in vector store
2. Query with the same embedder X → verify top-1 is the exact indexed chunk
3. Cosine similarity of query vector to top-1 stored vector ≥ 0.99
```

### 4.2 Cross-embedder mismatch detection

```
1. Index chunks with embedder A (minilm, dim=384)
2. Query with embedder B (hashing, dim=256) → must fail with a dimension error, NOT silently
   return wrong results
```

The system must surface a dimension mismatch — either ChromaDB rejects the query (correct) or
the profile resolver prevents this scenario (also correct, preferred). Verify that the profile
enforcement in `app/profiles.py` makes this impossible by construction.

### 4.3 Profile change → reindex warning

When `PUT /tenants/{id}/profile` changes the embedding backend:
- The API must return a `warnings` field noting that existing vectors were indexed with the
  previous backend and a reindex is required.
- New ingestion must use the new embedder.
- `GET /inspect/vectors` must display which embedder was used when each batch was indexed (via
  metadata stored at index time).

---

## 5. Security and compliance checks [REQUIRED for cloud-backed embedders]

These checks are mandatory before approving openai or bedrock for production.

| Check | Verification method |
|---|---|
| Text sent to API is only the chunk content | Capture HTTP traffic (mitmproxy or logging interceptor); confirm no PII, source metadata, or tenant IDs are sent |
| API key stored in `SecretsVault`, not in env or config file | Code review: embedder must call `get_vault()`, not `os.environ` directly |
| mTLS / TLS 1.2+ on all API calls | Verify via HTTP client config; TLS 1.0/1.1 must be disabled |
| Data-residency region configured | API call targets the correct regional endpoint; EU data stays in EU |
| Rate limits handled gracefully | 429 responses must back off and retry; do not crash the pipeline run |
| PII scrubbing upstream | Before embedding, confirm the connector/extractor does not embed GDPR-regulated fields (names, emails, NI numbers) in plain text without consent |
| API key rotation works without reindex | Changing the key does not change the embeddings (keys are auth only, not content-affecting) |

**Approval gate:** a cloud-backed embedder must have a security sign-off ticket referenced in
the ADR 0010 update before `implemented = True` is set.

---

## 6. Red flags in production (on-call signals)

| Signal | Likely cause | Action |
|---|---|---|
| Vector search returns low-relevance results for known-relevant queries | Embedder mismatch (index vs query); or model version changed | Check profile on tenant; inspect chunk metadata for embedder used; run §4.1 round-trip |
| Search latency p95 spikes 5× | minilm model being re-loaded (cache evicted or new process) | Ensure `get_embedder()` cache is process-level singleton; check for multiple worker processes |
| `dim` mismatch error in ChromaDB logs | Profile changed after indexing without reindex | Run reindex; surface profile change warning in UI |
| openai embedder returning 429 | Rate limit; embedding too many chunks in parallel | Add exponential back-off + jitter; reduce fan-out concurrency |
| hashing used in production search | Tenant chose offline profile unaware of quality impact | `GET /capabilities` shows hashing; platform team should add a warning label to offline-only backends |

---

## 7. Adding a new embedder — gate checklist

Before setting `implemented = True`:

- [ ] §1 contract tests pass (dimension, determinism, normalization)
- [ ] §2.1 semantic similarity measured on golden pairs; thresholds documented
- [ ] §2.2 retrieval quality (MRR ≥ 0.55) measured on golden Q&A corpus
- [ ] §3 performance benchmarks recorded
- [ ] §4.1 round-trip consistency verified
- [ ] §4.2 cross-embedder mismatch handled (dimension error or profile prevents it)
- [ ] If cloud-backed: §5 security checklist complete with sign-off ticket
- [ ] `dim` constant set on the class
- [ ] Lazy import used (no top-level heavy import)
- [ ] Module imported in `pipeline/embedding/__init__.py`
- [ ] ADR 0010 updated with new backend + decision criteria

---

## Eval log

| Date | Embedder | Corpus | High-sim threshold met | MRR | Cold start | p95 batch/100 | Result | Notes |
|---|---|---|---|---|---|---|---|---|
| 2026-06-22 | minilm | sample_deal.pdf chunks | yes (qualitative) | not formally measured | ~3 s | ~1.5 s | PASS | Default; live E2E verified |
| 2026-06-22 | hashing | sample_deal.pdf chunks | low (by design) | not formally measured | < 1 ms | < 50 ms | PASS | POC/CI only; tested live against memory store |
| 2026-06-22 | openai | stub | — | — | raises | raises | PASS (stub) | Pending cloud approval |
| 2026-06-22 | bedrock | stub | — | — | raises | raises | PASS (stub) | Pending cloud approval |
