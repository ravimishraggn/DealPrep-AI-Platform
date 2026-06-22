# Production-Readiness & Operational Reviews

One review document **per delivered phase**, capturing what will break, become painful, or
generate support load **once real customers use it** — the insights that only emerge from
operating software in production, not from "does it work."

These are deliberately separate from [ADRs](../adr/): ADRs record *decisions*; these record
*predicted operational reality* and feed the next phase's hardening work.

## Convention

- **One file per phase**, named `PHASE-<n>[-<n>]_<slug>.md` (a single phase or a phase range
  if shipped together, e.g. `PHASE-1-4_ingestion-platform.md`).
- Every file is **tagged at the top** with the phase(s), scope, roadmap reference, and the
  commit/branch it reviews.
- Copy [`_TEMPLATE.md`](_TEMPLATE.md) to start a new phase review.
- When a phase's findings are actioned, link the resulting ADR / PR back into the file.

> **Process rule:** every new phase ships with its own review file in this folder before it
> is considered "done." The next phase's review is written against what that phase actually
> built, not the plan.

## Index

| Phase(s) | Scope | Review | Verdict |
|---|---|---|---|
| 1–4 | Self-service ingestion onboarding platform (V1) | [PHASE-1-4_ingestion-platform.md](PHASE-1-4_ingestion-platform.md) | NO-GO external / GO internal (see doc) |
| 5–6 | Extraction → indexing → unified retrieval (Postgres + ChromaDB + Neo4j) | [PHASE-5-6_retrieval-pipeline.md](PHASE-5-6_retrieval-pipeline.md) | GO internal / NO-GO external (see doc) |
| 7 | Multi-agent orchestration (fan-out/fan-in + synthesis) | [PHASE-7_multi-agent-orchestration.md](PHASE-7_multi-agent-orchestration.md) | GO internal / NO-GO external (see doc) |
| 8 | Dashboard, monitoring, governance | _pending_ | — |

_Phases map to the roadmap in the [PRD](../PRD.md) §12._

---

## Related: pipeline stage evaluation

Per-stage evaluation runbooks (correctness tests, quality thresholds, performance benchmarks,
isolation checks) live in [`docs/evaluation/`](../evaluation/README.md). These are the
**pass/fail gates** that a new backend must clear before `implemented = True` is set in the
registry. Production-readiness reviews reference this evaluation index when recommending
backend upgrades or flagging measurement gaps.
