# ADR 0006 — Neo4j with property-based tenant tagging (not a database per tenant)

- **Status:** Accepted
- **Date:** 2026-06-21
- **Deciders:** DealPrep platform team
- **Maps to build request:** "ADR-003 (Neo4j with property-based tenant tagging instead of separate databases per tenant)"

## Context

The knowledge graph stores entities (companies, people, money, dates) and their
relationships (invested_in, board_member_of, related_party_of, …) so the platform can answer
relational questions ("does this comp share an investor with the target?"). It is the third
fixed data store.

Multi-database isolation (one Neo4j database per tenant) would be the strongest boundary, but
**Neo4j Community Edition supports only a single database** — multi-database is an Enterprise
feature. We need tenant isolation without Enterprise licensing.

## Decision

Run **one Neo4j Community instance** and isolate tenants by **mandatory property tagging**:

- Every node (`:Entity`) and every relationship (`:RELATED`) carries a `tenant_id` property.
- Traceability properties `source_id` and `original_file_reference` are tagged on every node
  and edge too.
- **All Cypher is centralized in one `Neo4jClient` helper.** There is no generic
  "run arbitrary Cypher" method. Every public method requires a `tenant_id` argument and
  every query hard-codes `{tenant_id: $tenant_id}` on all node and relationship patterns. A
  call without a tenant_id raises before touching the database.
- Relationship *types* are modeled as a `type` property on a single `:RELATED` edge label
  (Community lacks APOC dynamic relationship types), keeping the schema simple and the tenant
  filter uniform.

Isolation is therefore enforced by construction: the only way to query the graph is through a
helper that always filters by tenant.

## Consequences

**Positive**
- Works on free Neo4j Community; no Enterprise licensing.
- Centralizing Cypher in one tenant-filtered client means isolation cannot be skipped by a
  careless query elsewhere — the boundary is in one auditable file.
- Property tagging doubles as traceability (source_id, file_reference on every element).

**Negative / trade-offs (accepted)**
- **Weaker isolation than physical separation:** all tenants share one graph; a *bug* in the
  helper (a missing filter) would be a cross-tenant leak. Mitigation: one choke point, code
  review, and tests asserting filtering. This is the explicit trade accepted for V1.
- A shared graph can let entities with identical names across tenants sit side by side; the
  `tenant_id` in the uniqueness constraint keeps them distinct nodes.
- The single `:RELATED` edge with a `type` property is less idiomatic than native typed edges
  and makes some graph algorithms clumsier; acceptable for 1-hop retrieval, revisit if we add
  multi-hop reasoning.
- No per-tenant resource isolation: a heavy tenant can affect others on the shared instance.

**Relationship extraction note:** triples come from an LLM (Claude) when a key is configured,
else a deterministic rule-based fallback. The fallback operates at chunk granularity and can
over-connect co-occurring entities — a documented V1 limitation; the LLM path is more precise.

**Entity resolution note:** V1 dedupe is exact-normalized + fuzzy (SequenceMatcher ≥ 0.9) —
deliberately simple, not a full entity-resolution system.
