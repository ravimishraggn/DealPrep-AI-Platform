# Chunking Evaluation Runbook

- **Stage:** Document Processor → Chunker
- **Registry file:** `pipeline/chunking/base.py`
- **Governed by:** [ADR 0009](../adr/0009-chunking-strategy-selection.md)
- **Last updated:** 2026-06-22

---

## Why chunking quality is hard to see

Chunking is the invisible middle stage. A bad chunker — one that cuts mid-sentence, bleeds
section headers into wrong chunks, or produces wildly uneven sizes — does not raise an error.
It silently degrades vector search (wrong granularity → wrong embeddings) and the graph (NER
over half-sentences extracts garbage entities). The failure shows up two stages later as "search
is not finding what I know is in the data." This runbook defines how to detect chunking problems
**before** they corrupt downstream stages.

---

## 1. Correctness tests [REQUIRED]

### 1.1 Contract conformance

| Test | Pass condition |
|---|---|
| `chunk()` returns a `list[Chunk]` | Never raises on valid input; result is a list |
| Every `Chunk` has non-empty `text` | `len(c.text.strip()) > 0` for every chunk |
| Every `Chunk` carries `tenant_id` and `source_id` | Fields populated, not None |
| `chunk_id()` is deterministic | Same input → same chunk IDs across runs |
| `chunk_id()` is unique within a doc | No two chunks in the same document share an ID |
| Empty input returns empty list | No chunks, no exception |
| Single-sentence input returns 1 chunk | Doesn't return 0 chunks for non-empty text |

### 1.2 Boundary integrity

| Test | Pass condition |
|---|---|
| Chunks do not cut inside a word | `chunk.text` starts and ends on a word boundary |
| Chunks do not cut inside a sentence (section_aware, sentence_window) | Last char of chunk ends a sentence (`[.!?]` or heading boundary) |
| Section headers are not split across chunks (section_aware) | A line matching `^#{1,6}\s` appears only at the start of a chunk, never mid-chunk |
| Overlap does not bleed across section headers (section_aware) | Overlap tail from section A does not appear in section B's first chunk |

To verify header bleeding:

```python
text = "# Section A\nContent A.\n# Section B\nContent B."
chunks = chunker.chunk(TextDocument(text=text, ...), tenant_id, source_id)
# chunk 0 should contain "Section A" content; chunk 1 should not
assert "Content A" not in chunks[1].text or chunks[1].text.startswith("# Section B")
```

### 1.3 Coverage — no content lost

```
coverage = sum(len(c.text) for c in chunks) / len(original_text)
# Adjusted for overlap: slightly > 1.0 is expected and correct.
```

| Condition | Pass |
|---|---|
| coverage ≥ 0.99 (before overlap) | All text is represented in at least one chunk |
| coverage ≤ 1.5 | Overlap is not wildly inflated |
| Every paragraph of ≥ 50 chars appears in at least one chunk | No silent paragraph drops |

---

## 2. Quality metrics [REQUIRED]

### 2.1 Chunk size distribution

Run over a representative corpus (≥ 10 documents, diverse formats).

| Metric | section_aware | sentence_window | fixed_size |
|---|---|---|---|
| Mean chunk length (chars) | 300–2000 | 200–1500 | target ± 5% |
| Std deviation / Mean (CoV) | < 1.5 | < 1.2 | < 0.10 |
| Min chunk length | ≥ 50 | ≥ 30 | ≥ target × 0.5 |
| Max chunk length | ≤ max_chars × 1.1 | ≤ 3 × mean | = max_chars ± 1 |
| Chunks < 50 chars (noise) | ≤ 3% | ≤ 5% | 0% |
| Chunks > 3000 chars (too large) | ≤ 5% | ≤ 5% | 0% |

> Too-small chunks: embed mostly noise; too-large chunks: dilute the embedding signal. Both hurt
> retrieval precision. The ranges above are calibrated for the PE/VC use case (deal memos, CIMs).

### 2.2 Overlap quality (for overlapping chunkers)

```
actual_overlap_ratio = overlap_chars / chunk_chars
```

| Condition | Pass |
|---|---|
| Configured overlap ratio within ±15% of target | Overlap is what the profile requested |
| Overlap never crosses a section header | As per §1.2 |
| Overlap text at chunk N+1 start matches tail of chunk N | Can be verified character-by-character |

### 2.3 Retrieval quality (downstream impact) [RECOMMENDED before tenant profile change]

Using the eval corpus and a golden Q&A set (≥ 20 question/answer pairs where the answer is
known to appear in a specific paragraph):

| Metric | How to measure | Acceptable |
|---|---|---|
| **Precision@5** | Top-5 vector results contain the gold paragraph | ≥ 0.60 |
| **Recall@10** | Gold paragraph appears in top-10 | ≥ 0.75 |
| **MRR** (Mean Reciprocal Rank) | 1/rank of first gold result | ≥ 0.50 |

Measure by embedding chunks with the tenant's configured embedder (default: minilm) and
running vector search against the golden questions. Compare across chunking strategies on the
same corpus.

> **Rule of thumb:** if section_aware MRR ≥ 0.50 on your corpus, it is the right choice. If
> your corpus has very uniform prose without headers, try sentence_window — it often lifts MRR
> by 0.05–0.15. If you need deterministic chunk boundaries for compliance (e.g. "always split
> every N chars"), use fixed_size and accept the MRR cost.

---

## 3. Performance benchmarks [REQUIRED]

| Doc size | p50 target | p95 target |
|---|---|---|
| < 5 KB (JSON/text) | < 10 ms | < 50 ms |
| 5–50 KB | < 50 ms | < 200 ms |
| 50–500 KB (long PDF) | < 500 ms | < 2 s |
| > 500 KB | < 2 s | < 10 s |

Run with 20 docs per bucket; report p50 and p95 per strategy. section_aware and sentence_window
parse text character-by-character so scale linearly with input size; fixed_size is O(N) and
fastest.

### Memory ceiling

Chunking should not hold the entire document in memory beyond the function call. RSS delta
per 500 KB document < 5 MB after GC.

---

## 4. Failure mode inventory

| Failure | Expected behaviour | Silent? |
|---|---|---|
| `implemented = False` stub called | `NotImplementedError` raised by `get_chunker()` | No |
| Unknown chunker name | `KeyError` from registry | No |
| Input with only whitespace | Returns empty list | Yes — acceptable |
| Input larger than 10 MB | Chunker completes within 30 s; no OOM | Must not crash |
| Null/None text | `AttributeError` or explicit guard → never silent empty on valid input | No |

---

## 5. Strategy comparison guide

Use this when a tenant's search quality is poor and they want to experiment.

| Corpus type | Recommended strategy | Why |
|---|---|---|
| Financial docs (CIMs, memos) with clear `##` headers | **section_aware** | Keeps each section in its own chunk; queries about a section hit exactly the right chunk |
| Regulatory filings, dense prose, few/no headers | **sentence_window** | Sentence-level overlap preserves context across sentence breaks |
| Batch ETL pipelines, compliance mandating fixed sizes | **fixed_size** | Deterministic; easy to audit and reproduce |
| Large-scale semantic clustering (future) | **semantic** | Not yet implemented (stub); revisit when corpus > 1M chunks |

Before switching a tenant's profile mid-corpus, read the reindex warning in
[ADR 0012](../adr/0012-per-tenant-pipeline-profile.md). Existing chunks will not be
re-chunked; only new ingestion uses the new strategy. A full reindex is required for
consistency.

---

## 6. Red flags in production (on-call signals)

| Signal | Likely cause | Action |
|---|---|---|
| Vector search Precision@5 drops from baseline | Chunk size distribution shifted (source format changed) | Re-run §2.1 on recent ingested docs; compare to baseline |
| Chunks with `len < 20` appear in inspect output | Section headers being indexed as standalone chunks | Check section_aware `flush()` logic; headers alone should be dropped |
| Overlap text matches entire adjacent chunk | Overlap ratio too high or max_chars too small | Review profile `overlap` and `max_chars` settings |
| `chunk_id` collisions (duplicate IDs in vector store) | Non-deterministic chunk ID generation | Check `Chunk.chunk_id()` hash inputs include all discriminating fields |
| Graph extracts garbage entities (single letters, numbers) | Chunks too small; NER is running on sentence fragments | Increase `max_chars` or switch to section_aware |

---

## 7. Adding a new chunker — gate checklist

Before setting `implemented = True`:

- [ ] All §1 contract tests pass
- [ ] §2.1 size distribution metrics recorded on representative corpus
- [ ] §2.2 overlap quality verified (if overlapping)
- [ ] §2.3 retrieval quality measured (MRR ≥ 0.50 on the target corpus)
- [ ] §3 performance benchmarks recorded
- [ ] §4 failure modes verified
- [ ] Module imported in `pipeline/chunking/__init__.py`
- [ ] `GET /capabilities` lists it with `implemented: true`
- [ ] ADR 0009 updated with the new strategy and selection rationale

---

## Eval log

| Date | Strategy | Corpus | Mean chunk (chars) | CoV | MRR | p95 latency | Result | Notes |
|---|---|---|---|---|---|---|---|---|
| 2026-06-22 | section_aware | sample_deal.pdf + JSON | ~650 | ~1.1 | not formally measured | < 200 ms | PASS | Default; boundary bleed bug fixed (overlap flush) |
| 2026-06-22 | fixed_size | sample_deal.pdf | ~500 | ~0.05 | not formally measured | < 100 ms | PASS | |
| 2026-06-22 | sentence_window | sample_deal.pdf | ~420 | ~0.9 | not formally measured | < 250 ms | PASS | |
| 2026-06-22 | semantic | — | stub | — | — | raises | PASS (stub) | |
