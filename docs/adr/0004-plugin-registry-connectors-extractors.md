# ADR 0004 — Plugin/registry pattern for connectors and extractors

- **Status:** Accepted
- **Date:** 2026-06-21
- **Deciders:** DealPrep platform team
- **Maps to build request:** "ADR-004 (Plugin/registry pattern for connectors and extractors)"
- **Extends:** ADR 0001 D2 (connector registry) to a second plugin axis (extractors).

## Context

The platform's core promise is **self-service onboarding without core code changes**: a
team adds a data source by submitting a manifest, and the system must already know how to
acquire *and now extract* that data. Phase 5–6 adds a second dimension of variability —
**file/format diversity** (json, pdf, csv, html, text, …) — on top of the existing
**source-protocol diversity** (REST, file, …).

If either dimension required editing a central dispatch (a giant `if format == ...`), every
new connector or format would touch shared code, gating onboarding on an engineering release.

## Decision

Use the **same registry + decorator + auto-discovery pattern for both axes**:

- **Connectors:** `@register_connector("rest_api")` populates `CONNECTOR_REGISTRY`;
  `discover()` imports `connectors/` at startup.
- **Extractors:** `@register_extractor("pdf")` populates `EXTRACTOR_REGISTRY`;
  `discover_extractors()` imports `pipeline/extractors/` at startup.

Each registry is the **single seam** between the generic engine and the plugins. The
FormatRouter looks up extractors by `format_type` and **never imports a concrete extractor**.
Auto-discovery isolates per-module import failures so one broken plugin can't stop startup.
A `format_type` with no registered extractor is **logged and skipped**, never fatal.

## Consequences

**Positive**
- Adding a connector or an extractor is a single new file + one decorator — provably no core
  edits (demonstrated by the <10-line connector in ADR 0001 and the four extractors here).
- The two axes compose: any connector can emit any `format_type`, and any extractor can
  consume it, because they meet only at the `RawRecord` contract.
- Unknown formats degrade gracefully (skip + log) instead of crashing a run.

**Negative / trade-offs**
- Registration is import-time and global; two plugins claiming the same key is a startup
  error (intentional — prevents silent shadowing).
- Auto-discovery means a plugin with an import-time side effect or heavy import cost is paid
  at startup; mitigated by per-module try/except and lazy heavy imports inside `extract()`
  (e.g. `pdfplumber` is imported only when a PDF is processed).
- No plugin versioning yet — a breaking change to a plugin's behavior is invisible to the
  registry. Deferred; flagged in the Phase 5–6 review.

**Why this enables self-service:** onboarding a new source or file type becomes a plugin
contribution, not a change to the engine — exactly the property that lets teams (or platform
engineers) extend the system without a coordinated core release.
