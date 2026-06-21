# Production-Readiness & Operational Review — Phase 1–4 (Ingestion Onboarding Platform / V1)

- **Phase(s):** 1–4
- **Scope:** Self-service multi-tenant data ingestion: tenant registration, connector plugin
  system, manifest submission + dry-run, secrets indirection, generic APScheduler pipeline
  runner, tenant-namespaced JSON output, status/run-history APIs, minimal browser console.
- **Roadmap ref:** [PRD](../PRD.md) §12 Phases 1–4
- **Reviews commit/branch:** `feat/ingestion-onboarding-platform` @ `c7e381d`
- **Implements:** [ADR 0001](../adr/0001-data-ingestion-onboarding-platform.md) · language split context [ADR 0002](../adr/0002-polyglot-language-allocation.md)
- **Date:** 2026-06-21
- **Lenses:** Principal Eng · SRE · Product · Security · Customer Success
- **Status:** Reviewed — feeds a proposed **ADR 0003 (V1 hardening)**

> This review has two layers. **Layer A** assumes nothing else is in place (raw V1 as built)
> and finds genuine ship-blockers. **Layer B** assumes the standard NFRs (auth, security
> review, CI/CD, monitoring, scaling, infra) are solved and focuses on the operational and
> customer-reality problems that **only emerge after release** — the durable value of this
> doc. Read Layer B even after the criticals are closed; those issues survive NFR work.

---

## 0. TL;DR verdict & readiness scores

The architecture is sound — clean connector registry seam, DI boundary, `secret_ref`
indirection, write-layer tenant isolation, ADRs. The **productionization is not**: three
security issues block external exposure, and several **data-correctness and operability gaps
will generate the bulk of support load** regardless of NFR maturity. The single most
load-bearing misconception is that **the submit-time dry-run is a durable health guarantee —
it is a 200 ms snapshot.** Most escalations are variations of steady-state reality diverging
from that snapshot.

| Dimension | Score /10 | Note |
|---|---|---|
| Reliability | 3 | SPOF scheduler, SQLite locks, secret-loss-on-restart, no retries/alerts |
| Scalability | 2 | In-process scheduler + SQLite + local disk + buffered fetch |
| Security | 1 | (Layer A) no auth, cross-tenant secrets, SSRF, arbitrary file read |
| Operability | 2 | No metrics/alerts/dashboards; manual recovery |
| Maintainability | 7 | Strong seams (registry, DI, isolation, ADRs). Debt: no migrations/tests, vault stub |
| Customer Experience | 4 | Good dry-run + UI, but no edit/pause/run-now/notifications |

**Go/No-Go:** **NO-GO for external/internet-exposed customers** until C1–C5 are fixed.
**GO for internal design partners on a trusted network** with C2/C3/C4 patched, Postgres+WAL
(or accepted low concurrency), failure + scheduler-liveness alerting, and a documented
"secrets reset on restart" caveat.

---

## 1. Critical issues — Layer A ship-blockers

| # | Issue | Where | Why critical |
|---|---|---|---|
| C1 | **Zero authentication / authorization** — every endpoint open; any caller can act on any `tenant_id` | all routers | Multi-tenant isolation collapses at the API edge; ADR 0001 D7 isolates only the *write* layer |
| C2 | **Secrets are global, not tenant-scoped** — one process-wide dict; `secret_ref` resolved with no tenant check | `app/secrets.py`, `app/routers/secrets.py` | Cross-tenant credential theft: Tenant A references Tenant B's `secret_ref` |
| C3 | **SSRF via `RestApiConnector.base_url`** — server fetches an arbitrary URL on dry-run and on schedule, no allowlist | `connectors/rest_api.py` | Point at cloud metadata / internal services → creds & internal data land in `data/` and `GET /runs` |
| C4 | **Arbitrary host file read via `FileUploadConnector.directory`** — any readable path; `.json` contents returned | `connectors/file_upload.py` | LFI + exfiltration of host files through run output |
| C5 | **In-memory vault loses all secrets on restart/deploy** — DB still references missing `secret_ref`s | `app/secrets.py` | Every deploy → silent failure storm, no alert; stale data |

> All five are invisible in a demo and surface in week one of real, distinct-tenant use.

## 2. High-priority improvements

| # | Issue | Impact |
|---|---|---|
| H1 | APScheduler in-process & single-node; `--workers N` ⇒ N schedulers ⇒ duplicate runs/races; process death stops all scheduling | Correctness + SPOF |
| H2 | SQLite under write-every-interval scheduler; no WAL / `busy_timeout` | "database is locked"; likely first incident |
| H3 | No retention; full re-pull without `timestamp_field` writes a file every interval; `run_history` grows unbounded | Disk + DB blowup |
| H4 | `GET /sources` & `GET /runs` unpaginated — return all rows | Huge payloads / OOM |
| H5 | No source update / pause / delete; no tenant delete | DB surgery; blocks erasure |
| H6 | No retry/backoff/circuit-breaker; no failure alerting; ignores upstream `429/Retry-After` | Silent staleness + retry storms |
| H7 | Synchronous `httpx` dry-run blocks a threadpool worker up to `timeout_seconds` | Cheap DoS via slow host |
| H8 | `fetch()` buffers all records in memory (up to `max_pages × page_size`) | OOM on large sources |
| H9 | No migrations (Alembic); `create_all` only adds tables | Schema change = manual migration/data loss |
| H10 | Unhandled `IntegrityError` on namespace collision; no `owner_email` dedup | 500s; duplicate tenants |
| H11 | No quotas/rate limits; prod poll-interval floor is 1 s | One tenant saturates scheduler/egress |
| H12 | No encryption at rest; ingested data plaintext JSON on local disk | Fails regulated-finance bar |

---

## 3. Customer reality vs design assumptions (Layer B — survives NFR work)

| # | Assumption baked in | What customers actually do | Why eng missed it | How discovered | Business impact |
|---|---|---|---|---|---|
| A1 | Dry-run success ⇒ source healthy | Set-and-forget | Tested submit, not week-3 steady state | Data silently stops; source still "exists" | Stale data → wrong valuations |
| A2 | `file_upload` mtime > `last_cursor` = "new file" | Land files via `unzip`/`rsync`/`cp -p`/S3 restore (preserve old mtime) | mtime looked like an obvious newness signal | "Dropped 400 files, ingested 0" | #1 onboarding-failure ticket |
| A3 | `poll_interval` = freshness SLA | Set 30 s for "real-time"; slow source on short interval | `coalesce=True`+`max_instances=1` collapse missed/overlapping runs | "1-min polling but data is 20 min stale" | Trust erosion |
| A4 | Output = raw `{records:[…]}` envelope per run | Expect deduped, mapped, queryable data | "acquisition" heard as "pipeline" | Find thousands of files repeating the same rows | "Your product produces garbage" |
| A5 | "No-code manifest" covers real sources | Bring OAuth2-refresh, HMAC, cursor/Link pagination, GraphQL, mTLS | Demo connectors were page-number + bearer | Config can't express their reality | "No-code" promise breaks on 3rd real API |
| A6 | Persisted `config` re-validated against **current** schema every run (`build_connector` in `run_source`) | Nothing — *you* tighten a schema in a release | Re-validation felt cheap/safe | Every existing source of that type fails next run, silently | Self-inflicted mass outage from a "minor" change |
| A7 | `test_connection` ≈ "it works" | Rely on it | Only GETs page 1, no data assertions | Validates, then fails on page 2 / pagination mismatch | "Passed validation but never worked" |
| A8 | `timestamp_field` parses via `fromisoformat` | Point at epoch-millis / RFC822 / tz-naive APIs | ISO felt standard | since-filter no-ops (dupes) or over-filters (gaps) | Silently wrong data found in an audit |
| A9 | Store secret → then submit | Submit first, or rotate at provider and forget the vault | Ordering obvious to author | Dry-run "secret not found"; or post-rotation failures | Confusing onboarding; rotation outages |
| A10 | Namespace is system-generated (`slug-6hex`) | Want a stable, chosen namespace for downstream joins | Generated felt simpler | Hard-code it downstream, re-register, it changes | Broken downstream integrations |

## 4. First 90 days

**Week 1 — "doesn't work / works too much":** `file_upload` ingests nothing (A2); duplicate
records (A4/A8); "dry-run passed but no runs" (A9 / C5); "how do I edit a source?" (H5).
Ops: boot thundering-herd (`next_run_time=now` for every job), tenants self-rate-limit their
own providers with tiny intervals.

**Month 1 — "can't manage what I built":** no pause/delete/edit; `run_history` already tens
of thousands of rows; `GET /runs` slow; "same data in 50 files — dedupe?"; provider rotated
keys, everything broke, no alert. Enhancement asks crystallize: edit/pause/delete, run-now,
notifications, dedup, sample-preview.

**Month 3 — "depending on it, and it's fragile":** a connector-schema change (A6) or a
provider API change wipes a class of sources, customer-discovered; storage/`run_history`
growth raises cost questions; the raw envelope has become a **contract** the downstream AI
plane parses (can't change it); customers ask for backfill/replay after finding months of
mis-filtered data (A8).

## 5. Top customer escalations & support tickets

| # | Complaint | Root cause | Sev | Freq | Resolution |
|---|---|---|---|---|---|
| 1 | "Uploaded files, nothing ingested" | mtime ≤ cursor (A2) | High | Very high | Explain; cursor reset → seen-set redesign |
| 2 | "Same record thousands of times" | No dedup / full re-pull (A4/A8) | High | Very high | Configure incremental; dedup keys |
| 3 | "Dry-run passed but no data" | Page-1-only test (A7) / secret gone (A9/C5) | High | High | Inspect run_history; re-store secret |
| 4 | "Can't edit/delete my source" | No lifecycle (H5) | Med | Very high | DB surgery → ship CRUD |
| 5 | "Stale despite fast polling" | coalesce/overlap (A3) | Med | High | Explain; add freshness metric |
| 6 | "All sources broke after your release" | Schema re-validation (A6) | Critical | Low-Med | Rollback; schema versioning |
| 7 | "Provider rotated key, silent death" | No alerting; runtime resolve | High | Med | Re-store; consecutive-failure alerts |
| 8 | "You're hammering our API" | No backoff / interval floor (H6/H11) | High | Med | Raise interval; 429 honoring + jitter |
| 9 | "My API needs OAuth2 / cursor pagination" | Connector too narrow (A5) | Med | High | Custom connector; richer config |
| 10 | "Wrong timezone / missing records" | `fromisoformat` (A8) | High | Med | Custom parse; document formats |
| 11 | "Where's my data? It's just files" | Raw-dump expectation (A4) | Med | High | Explain; build query/export |
| 12 | "Namespace changed on re-register" | Generated namespace (A10) | Med | Low-Med | Stable namespaces/aliasing |
| 13 | "0-record run — broken?" | No run diagnostics | Low | Very high | Structured run reasons |
| 14 | "Need to backfill last month" | No replay; cursor moved past data | High | Med | Cursor reset; backfill feature |
| 15 | "Two of us manage this, one has access" | Tenant = single owner_email | Med | Med | Multi-user/RBAC |
| 16 | "Source ran twice, data doubled" | Multi-worker schedulers (H1) | High | Low | Distributed lock |
| 17 | "Large source timed out / OOM" | Buffered fetch (H8) | Med | Low-Med | Streaming |
| 18 | "Typo in config, can't fix" | No update (H5) | Med | High | CRUD |
| 19 | "Run history won't load" | Unpaginated (H4) | Low-Med | Med | Pagination |
| 20 | "Compliance needs who-changed-what" | No config audit | Med | Low | Config audit log |

## 6. Production ownership stories

**The silent unzip.** Partner onboards by `unzip`-ing a historical archive into the watched
dir → zero ingestion. Two days lost assuming a glob/permission bug; an engineer finally
`stat`s the files — mtimes from 2019, all `< last_cursor`. Fix: cursor reset + seen-set
redesign. **Lesson:** "new" is a business event, not a filesystem timestamp.

**The minor schema tightening.** A dev adds validation to `RestApiConfig`; CI green. After
deploy, `run_source` re-validates **persisted** configs and a cohort of older sources fails
*at fetch time* — no endpoint errored. Customer-discovered via stale data; `run_history.error`
shows `ConfigValidationError`. Fix: revert + **schema versioning** (validate against the
saved version). **Lesson:** the moment you re-read stored config, your Pydantic schema is a
published API with backward-compat obligations.

**The reconciliation that didn't.** Analyst finds ingested comps off by a few rows.
`timestamp_field` pointed at epoch-millis; `fromisoformat` threw; `_filter_since` fell
through inconsistently → both gaps and dupes. Visible only via row-level diff. Fix: explicit
per-connector timestamp format + reject-on-unparseable. **Lesson:** silent data-quality bugs
beat outages to the bottom of the trust barrel — in finance they surface as audit findings.

## 7. Integration & data-migration risks

- **Pagination polymorphism:** page-number is the minority pattern; cursor tokens, `Link`
  headers, `offset/limit`, `since_id`, GraphQL `after` are unsupported.
- **Auth polymorphism:** OAuth2 refresh, HMAC/SigV4, mTLS unexpressible.
- **Upstream schema drift:** envelope passes fields through untyped; downstream consumers
  break with no warning.
- **Downstream envelope lock-in:** `{tenant_id, source_id, ingested_at, record_count,
  records}` becomes a frozen contract the moment the Python AI plane parses it — **add
  `schema_version` to the envelope now.**
- **Control/execution split (ADR 0002):** the job + **dry-run round-trip** become a versioned
  cross-language interface; "what control plane thinks ran" vs "what executed" is a classic
  dual-system reconciliation headache.
- **Config schema = migration surface:** every connector-schema change migrates all
  persisted `sources.config`; worsened by per-run re-validation (A6).
- **`run_history` firehose:** append-only, no rollup/partition/TTL — largest, slowest table;
  migrated last and most painfully.
- **mtime → seen-set migration (A2 fix):** requires backfilling state or a one-time
  re-ingest — a customer-visible event.

## 8. Technical debt created by success

- **Per-run config re-validation** turns Pydantic schemas into a frozen public API (A6).
- **Output envelope** becomes an unversioned integration contract.
- **mtime file watching** (A2) is a correctness bug, not a gap — needs a state store.
- **In-process registry + scheduler** = shared blast radius; no independent connector rollout.
- **`run_history` overloaded** as observability + audit + dedup state — needs splitting.
- **Wall-clock `last_cursor`** conflates "where in the data" with "what time is it" — root of
  gap/dupe bugs; needs a real per-connector watermark/bookmark abstraction.

## 9. Top production risks (ranked)

| # | Risk | Prob. | Bus. impact | Mitigation difficulty |
|---|---|---|---|---|
| 1 | Anonymous access (no auth, C1) | High | Critical | Medium |
| 2 | SSRF to internal/metadata (C3) | High | Critical | Medium |
| 3 | Cross-tenant secret access (C2) | High | Critical | Low |
| 4 | Arbitrary file read (C4) | High | Critical | Low |
| 5 | Secret loss on restart (C5) | High | High | Low |
| 6 | SQLite lock failures (H2) | High | High | Low |
| 7 | Multi-worker duplicate runs (H1) | High | High | Medium |
| 8 | Scheduler SPOF (H1) | Medium | High | Medium |
| 9 | Unbounded storage/history (H3) | High | Medium | Low |
| 10 | Silent failures, no alerting (H6) | High | High | Medium |
| 11 | Duplicate-record data correctness (A4/A8) | High | Medium | Medium |
| 12 | Retry storms vs upstreams (H6) | Medium | Medium | Low |
| 13 | Threadpool exhaustion via slow dry-run (H7) | Medium | Medium | Low |
| 14 | Runaway sources, no lifecycle (H5) | High | Medium | Low |
| 15 | Schema-evolution mass failure (A6) | Medium | High | Low |
| 16 | OOM on large fetch (H8) | Medium | Medium | Medium |
| 17 | No migrations → deploy data loss (H9) | Medium | High | Low |
| 18 | No encryption at rest (H12) | High | High | Medium |
| 19 | No config/actor audit | Medium | High | Medium |
| 20 | Unpaginated list endpoints (H4) | Medium | Medium | Low |

## 10. Lessons only learned in production / what only a prod engineer would notice

- **"New" is a business event, not a timestamp** — real delivery tools carry old mtimes;
  any time-based newness heuristic silently drops data.
- **The default template is the product** — whatever the pre-filled config does, ~90% ship.
  Today's REST template omits `timestamp_field`, so the product's *de facto* behavior is
  "produce duplicates."
- **A point-in-time validation creates permanent false confidence** — customers anchor on
  "it validated" and never re-check; you own steady-state health.
- **Stored config is a published API the moment you re-read it** (`build_connector` in
  `run_source`).
- **`coalesce=True`+`max_instances=1` silently converts downtime into permanent data gaps.**
- **Silent correctness bugs cost more than outages** in regulated finance.
- **Reconciliation is the real feature** — customers want a provable, gap-free, dup-free,
  mapped dataset; the raw dump is the first 20%.

## 11. Recommended actions feeding the next phase (→ proposed ADR 0003)

Highest-leverage, non-generic fixes (small, prevent the most common escalations):

| Action | Effort | Prevents | Tracked by |
|---|---|---|---|
| Tenant-scope the vault (`get_secret(tenant_id, ref)`) | ~30 min | C2 cross-tenant theft | ADR 0003 / PR |
| SSRF guardrails (block private IPs/metadata; host allowlist) | 1–2 h | C3 | ADR 0003 / PR |
| Restrict `file_upload.directory` to a configured root | ~1 h | C4 | ADR 0003 / PR |
| SQLite WAL + `busy_timeout`; jitter `next_run_time` | minutes | H2 + boot herd | ADR 0003 / PR |
| Make default UI/template config incremental + idempotent | small | A4/A8 duplicate class | ADR 0003 / PR |
| Replace `file_upload` mtime with a seen-set/state store | medium | A2 | ADR 0003 / PR |
| Version connector config schemas; stop blind re-validation | medium | A6 mass outage | ADR 0003 / PR |
| Add `schema_version` to output envelope | minutes | downstream lock-in | ADR 0003 / PR |
| Source edit/pause/delete + "run now" + run-detail diagnostics | medium | H5 + many tickets | ADR 0003 / PR |
| Minimal API key on all routes | small | C1 | ADR 0003 / PR |

> When actioned, link the ADR 0003 PRs here and flip **Status** to *Actioned*.
