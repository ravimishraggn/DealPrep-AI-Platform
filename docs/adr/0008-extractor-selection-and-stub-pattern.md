# ADR 0008 — Extractor selection: format-driven, with a working-vs-stub pattern

- **Status:** Accepted
- **Date:** 2026-06-22
- **Deciders:** Platform team (owns registry + which formats are real), tenant teams (bring data)
- **Type:** Decision framework
- **Builds on:** ADR 0004 (extractor registry)

## Context

Unlike chunking/embedding/vector-store — which a tenant *chooses* — the extractor is **chosen
by the data**: a record's `format_type` deterministically selects its extractor (ADR 0004).
There is no judgement call per record. The real questions for an enterprise platform are:
(1) which formats do we support *for real* vs advertise as a *roadmap stub*, and (2) who
decides to promote a stub to real, and when. Shipping a broad menu of half-working extractors
is worse than a small set of solid ones plus honest stubs.

## Decision

Keep extraction **format-driven** (the FormatRouter dispatches by `format_type`), and adopt an
explicit **working-vs-stub** convention:

- **Real extractors** (`implemented = True`) fully produce text and/or structured output:
  `json`, `csv`, `text`, `html`, `pdf` (text pages + tables).
- **Stub extractors** (`implemented = False`) register the format so the platform *advertises*
  it (visible via `GET /capabilities`), but `extract()` raises `ExtractorError("… POC stub …")`.
  The router catches that and **skips + logs** the record — it never crashes a run or silently
  mishandles data: `docx`, `xlsx`, `pptx`.
- An **unregistered** format is also skipped + logged (ADR 0004), so unknown and
  not-yet-implemented are both safe.

**Promotion path:** turning a stub into a real extractor = implement `extract()`, flip
`implemented = True`, add its parsing dependency — one file, no core change.

### Who decides, and when
- **Platform team** owns which formats are real vs stub, and prioritizes promoting a stub when
  enough tenants need that format (or a single high-value tenant requires it). Promotion gates
  on adding a vetted parsing dependency and tests.
- **Tenant teams** don't pick extractors; they bring data. If their format is a stub, that
  record is skipped with a clear log — the signal to prioritize promotion.
- **Revisit** when: a stubbed format shows up repeatedly in skipped-record logs, or a tenant
  blocks on it.

### Why stubs at all
The platform is also a **POC vehicle**: `GET /capabilities` lets a prospect see the full format
menu (real + roadmap) without us shipping fragile parsers. Honesty is enforced in code — a stub
*cannot* pretend to work, because it raises.

## Consequences

**Positive**
- Small, solid set of real extractors; broad advertised menu via capabilities.
- Unknown and stubbed formats are both non-fatal (skip + log) — one record never breaks a run.
- Promoting a format is a one-file change with a clear checklist (deps + tests + flag).

**Negative / trade-offs**
- A tenant uploading a stubbed format gets *nothing* for those files until promotion; they must
  watch `run_stages`/logs to notice the skip (mitigated by capabilities visibility).
- `format_type` correctness depends on the connector (file-extension mapping today); a
  mislabeled record routes to the wrong/again-skipped extractor.
- The real `pdf` extractor runs NER-quality risks downstream (page text includes tables) —
  documented in ADR 0006, not an extractor-selection concern.
