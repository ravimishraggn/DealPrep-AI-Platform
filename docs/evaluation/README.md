# Pipeline Stage Evaluation — Index

This directory contains **enterprise evaluation runbooks** for every pluggable pipeline stage.
Each doc answers three questions the platform team and tenant teams need before trusting a
backend in production:

1. **Correctness** — is it producing the right output at all?
2. **Quality** — how good is the output, and how do you measure it?
3. **Operations** — latency, throughput, cost, failure modes, isolation.

---

## Documents

| Stage | Runbook | Primary concern |
|---|---|---|
| Extractor | [extractor-evaluation.md](extractor-evaluation.md) | Did it find all content, right shape, no data loss |
| Chunking | [chunking-evaluation.md](chunking-evaluation.md) | Chunk size/quality, boundary integrity, retrieval impact |
| Embedding | [embedding-evaluation.md](embedding-evaluation.md) | Semantic quality, latency, dimension stability |
| Vector store | [vectorstore-evaluation.md](vectorstore-evaluation.md) | Recall@K, isolation, idempotency, query latency |

---

## How to use these docs

- **Platform team** runs the full suite before approving a new backend (`implemented = True`).
- **Tenant team** runs the retrieval metrics section when choosing a profile that differs from
  the default. If the numbers pass the thresholds here, they own the choice; if they don't,
  they escalate to platform.
- **Security/compliance** checks the egress and data-residency sections in embedding and
  vector-store docs before any cloud-backed backend is approved.
- **On-call** uses the "red flags" section per runbook to diagnose silent degradation.

## When a new backend must pass evaluation before production approval

1. Run all sections marked **[REQUIRED]**.
2. All mandatory thresholds must pass.
3. Record results in a dated eval note appended to the relevant runbook under `## Eval log`.
4. Platform team updates `implemented = True` in the registry file and commits. Until then the
   backend stays a stub — `GET /capabilities` will show it and `PUT /profile` will reject it.

## What triggers a re-evaluation

| Trigger | Which runbooks |
|---|---|
| New backend added | All sections for that stage |
| Underlying library version bump (spaCy, sentence-transformers, chromadb) | All stages that use it |
| Corpus size > 5× the evaluation baseline | Vector store §3 (scalability) |
| Median search latency alert fires | Embedding §2 + vector store §3 |
| Tenant requests a profile change mid-corpus | Embedding §4 (consistency / reindex) |
| Security review flags a new data-egress path | Embedding §5, vector store §4 |
