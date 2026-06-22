# ADR 0012 — Per-tenant pipeline profile (platform defaults + tenant override)

- **Status:** Accepted
- **Date:** 2026-06-22
- **Deciders:** Platform team (defaults + approved set), tenant teams (select within it)
- **Type:** Governance / mechanism
- **Ties together:** ADR 0009 (chunking), 0010 (embedding), 0011 (vector store)

## Context

ADRs 0009–0011 each made a pipeline stage pluggable and gave a selection framework. Something
must (a) hold a tenant's chosen strategies, (b) resolve them consistently at *both* index time
and query time, and (c) stop a tenant selecting a non-production stub. Critically, **index and
query must use the same embedder + vector store** — a mismatch returns silently wrong results
(different vector space / different store). A single, validated profile per tenant is that
mechanism.

## Decision

Introduce a **`TenantPipelineProfile`** (one row per tenant: chunking, embedding,
vector_store). Resolution is centralized in `app/profiles.py`:

- **Resolve** = tenant override if present, else **platform defaults** (`settings.default_*`).
  An absent profile is normal and means "use defaults".
- **Validate** = every selection must be **registered and implemented**; selecting a stub or
  unknown name is rejected (422). This is where the platform team's "approved set" is enforced.
- **Apply consistently:** the runner resolves the profile and passes it to the orchestrator
  (chunking + vector backend); the search and inspect endpoints resolve the *same* profile so
  queries hit the embedder/store the data was indexed with. Structured (Postgres) and graph
  (Neo4j) stages are not tenant-selectable.
- **Discoverable:** `GET /capabilities` lists every selectable strategy (real vs stub) so a
  tenant (or the UI) chooses from the approved menu. `GET/PUT /tenants/{id}/profile` reads/sets
  it.

### Governance — who owns what
- **Platform team:** owns the registries, sets defaults, decides which strategies are
  production-approved (implemented), and operates shared infra (e.g. a future Qdrant).
- **Tenant team:** picks from the approved menu to fit its corpus, cost, and compliance needs.
- **Security/compliance:** gates any embedding backend that egresses text (ADR 0010).

## Consequences

**Positive**
- One validated place decides a tenant's strategies; index/query consistency is guaranteed by
  resolving the same profile on both paths.
- Stubs are advertised but unselectable for production — honesty enforced in code.
- Defaults mean tenants need zero configuration to get a sensible, fully-local pipeline.

**Negative / trade-offs**
- **Changing a profile does not migrate existing data.** New embedding/store/chunking applies
  to *future* ingestion; existing vectors/chunks remain under the old choice until the tenant
  is **reindexed**. A reindex tool is required follow-up (flagged in the Phase 5–6 review);
  until then, changing a profile mid-corpus yields mixed results.
- Per-tenant variation complicates capacity planning, cost attribution, and "why did retrieval
  differ?" debugging — mitigated by recording the profile and surfacing it in inspect/peek.
- The profile is coarse (one choice per stage for the whole tenant); per-source or per-format
  overrides are a future extension, not V1.
