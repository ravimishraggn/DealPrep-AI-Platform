# ADR 0011 — Vector store backend: pluggable, and how to choose one

- **Status:** Accepted
- **Date:** 2026-06-22
- **Deciders:** Platform team (registry + defaults + approval), tenant teams (select within it)
- **Type:** Decision framework

## Context

The vector store determines durability, scale ceiling, operational burden, query features
(payload filtering), and where vectors physically live. A small POC tenant wants zero
infrastructure; a tenant with millions of vectors wants a dedicated, clustered DB; a tenant
that wants one fewer system to operate may prefer vectors *inside* the Postgres they already
run. A single hard-coded store cannot satisfy all of these — and ADR 0005 already flagged
embedded ChromaDB's scale ceiling. Hence: make the vector store **pluggable**, and document
**who picks which, and when**.

## Decision

The vector store is a **registry of backends** (`pipeline/vectorstore/`), each registered by
name and selected per tenant via the pipeline profile (ADR 0012). `VectorIndexer` composes a
chosen **embedder** (ADR 0010) with a chosen **store**; the store is embedding-agnostic and
owns tenant isolation. Stubs register but raise on use.

**Backends shipped:**

| Backend | Status | Isolation | Durable | Scale | Ops burden | Best for |
|---|---|---|---|---|---|---|
| `chroma` (default) | ✅ real | collection/tenant | yes (local dir) | single-node | none (embedded) | default; local/POC → small prod |
| `memory` | ✅ real | dict/tenant | **no** (RAM) | tiny | none | unit tests, CI, air-gapped demos |
| `pgvector` | 🟡 stub | row filter/tenant | yes | medium | reuse existing Postgres | "one fewer system"; transactional with metadata |
| `qdrant` | 🟡 stub | collection/namespace | yes | **high** (clustered) | a service to run | large vector volume, high QPS, payload filters |

### Who chooses, and when
- **Platform team** owns the registry, sets the default (`chroma`), approves backends for
  production, and runs any shared infrastructure (a Qdrant cluster, pgvector enablement).
- **Tenant team** selects an approved backend based on volume, durability, and feature needs.
- **Revisit** when: a tenant's vector count approaches the embedded store's ceiling, durability
  guarantees are needed beyond a local dir, query-time payload filtering is required, or
  operating a separate vector DB is no longer justified vs pgvector.

### Selection rule of thumb
1. Default / local / small-to-medium production → **chroma**.
2. Tests / CI / air-gapped, durability not needed → **memory**.
3. Already operating Postgres and want to avoid a new system → **pgvector** (once implemented).
4. Large scale / high QPS / advanced filtering → **qdrant** (or another dedicated DB, once
   implemented).

## Consequences

**Positive**
- Tenants scale from "no infrastructure" (chroma/memory) to "dedicated cluster" (qdrant)
  without code changes — only a profile change.
- The embedder/store split means embedding model and storage scale independently.
- `memory` makes the entire vector path runnable with **no external dependency** for CI/POC.

**Negative / trade-offs**
- Switching a tenant's store requires **re-indexing** that tenant (vectors must be rewritten
  into the new backend) — a migration, never a hot swap. The same is true when the embedder's
  dimension changes (ADR 0010).
- Each backend has its own isolation mechanism; correctness depends on each implementation
  enforcing it. The base contract documents the obligation, but it is not compiler-enforced.
- More backends = more operational runbooks, capacity models, and tests.
- Cross-tenant backend variety complicates platform-wide capacity planning and cost reporting.
