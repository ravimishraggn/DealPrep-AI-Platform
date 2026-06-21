# ADR 0003 — Postgres as the consolidated relational + structured + full-text store

- **Status:** Accepted
- **Date:** 2026-06-21
- **Deciders:** DealPrep platform team
- **Maps to build request:** "ADR-001 (Postgres as consolidated relational + structured store)"
- **Supersedes:** ADR 0001 D1 (SQLite default) — SQLite is now a dev-only fallback.

## Context

Phase 5–6 introduces structured business records (e.g. extracted financial line
items, PDF tables) that need: (a) per-tenant schema flexibility — every tenant's
fields differ — and (b) keyword/full-text search over text-bearing fields,
alongside the existing operational metadata (tenants, manifests, run_history).

The platform data-layer strategy fixes **three** stores total (Postgres, ChromaDB,
Neo4j) and explicitly forbids adding more database technologies. So the choice is
*how* to serve relational metadata, flexible structured records, and keyword
search **without** introducing a separate document DB (Mongo) or a separate search
engine (Elasticsearch).

## Decision

Use **one Postgres instance** for all three relational responsibilities:

1. **Operational/metadata** — `tenants`, `sources`, `run_history`, `run_stages`
   as normal typed tables.
2. **Structured business records** — a single `structured_records` table where the
   variable per-tenant field set lives in a **JSONB** column (`fields`), with
   first-class columns for the isolation/traceability keys (`tenant_id`,
   `source_id`, `document_date`, `original_file_reference`, `record_type`).
3. **Keyword/full-text search** — a generated **`tsvector`** column
   (`search_tsv`, `GENERATED ALWAYS AS to_tsvector('english', search_text) STORED`)
   with a **GIN** index, populated from a concatenation of text-bearing fields.

Tenant isolation is enforced by a **mandatory `tenant_id` filter at the query
layer** (ADR 0001 D7 extended to reads). SQLAlchemy maps JSONB with a SQLite
`JSON` variant only so the pure-Python pipeline layers can be unit-tested without
Postgres; any real run requires Postgres because `tsvector` is Postgres-only.

## Consequences

**Positive**
- One engine, one backup/HA story, one connection pool — far less operational
  surface than SQL + Mongo + Elasticsearch.
- JSONB gives document-store flexibility *and* transactional integrity with the
  metadata in the same commit.
- `tsvector`/GIN gives good-enough keyword search co-located with the data, so
  structured + keyword results come from one query path.
- Generated column means the search vector can never drift from its source text.

**Negative / trade-offs**
- Postgres FTS is weaker than a dedicated search engine (no BM25 tuning, limited
  analyzers, English-only stemming as configured). Acceptable for V1; vector
  search (ChromaDB) covers semantic recall.
- JSONB is schemaless by design — bad tenant input lands silently; we rely on the
  extractor layer to shape `fields`.
- `tsvector` makes the table Postgres-bound; the SQLite fallback cannot create
  `structured_records`. This is intentional.
- A GIN index on a high-write table adds write amplification; fine at current
  scale, revisit under heavy ingest.

**Isolation note:** `tenant_id` is a first-class indexed column and every read in
`StructuredIndexer`/search filters on it; a request without `tenant_id` is rejected
rather than defaulting to all tenants.
