# ADR 0005 — ChromaDB as the vector store, one collection per tenant

- **Status:** Accepted
- **Date:** 2026-06-21
- **Deciders:** DealPrep platform team
- **Maps to build request:** "ADR-002 (ChromaDB as the vector store engine for this stage)"

## Context

Semantic retrieval needs a vector store for chunk embeddings. The platform fixes three
data stores; the vector store is one of them. Constraints for this stage: run locally with
zero external services, embed with a **local** model (no per-call embedding cost or data
egress to a third party), and enforce hard tenant isolation.

## Decision

Use **ChromaDB in embedded persistent mode**, with **one collection per tenant**, and embed
with a **local sentence-transformers model** (`all-MiniLM-L6-v2`, 384-dim, cosine space).

- Embedded mode → a local directory, no separate server to operate (contrast: Postgres and
  Neo4j run as containers; Chroma intentionally does not).
- One collection per tenant (`tenant<sanitized_id>`) → isolation is **structural**: a query
  only ever targets one tenant's collection, so there is no cross-tenant query path to get
  wrong.
- Local embeddings → deterministic, free, private; the model is loaded once and cached
  process-wide.
- Each vector carries flat metadata (`tenant_id`, `source_id`, `document_date`,
  `section_type`, `original_file_reference`, `chunk_index`) for filtering + traceability.

## Consequences

**Positive**
- No vector-DB service to run for V1; trivial local setup.
- Per-collection isolation is simpler and safer than per-row metadata filtering — you cannot
  accidentally read another tenant's vectors.
- Local embeddings remove a major recurring cost and a data-egress/compliance concern.

**Negative / trade-offs**
- Embedded Chroma is single-node and not built for very large multi-tenant fleets; a
  collection-per-tenant model can produce many collections at scale. Revisit (server mode,
  or a managed vector DB) when tenant/vector counts grow — flagged for the next review.
- `all-MiniLM-L6-v2` is small (quality ceiling) and English-centric; fine for V1 recall,
  upgradeable behind the `embedding_model` setting.
- First embedding call pays a model load/download cost; mitigated by the cached singleton.
- Changing the embedding model invalidates existing vectors (different space) → a reindex
  migration, not a hot swap.

**Isolation note:** the per-tenant collection *is* the isolation boundary; `tenant_id` is
also stored in metadata for defense-in-depth and traceability.
