# ADR 0001 — Self-Service Multi-Tenant Data Ingestion Onboarding Platform

- **Status:** Accepted
- **Date:** 2026-06-21
- **Owner:** Ravi Mishra
- **Deciders:** DealPrep platform team
- **Context scope:** Phases 1–4 of the DealPrep roadmap (tenant registration → connector plugins → secrets + dry-run → generic pipeline runner)

---

## 1. Context

The DealPrep PRD (`readme.md`, §9) calls for a self-service ingestion layer: any team
should onboard a data source by **registering once** and **submitting a config manifest**
(no code), after which a **generic pipeline engine** acquires the data using a
**plugin-based connector system**.

This ADR records the architectural decisions for that ingestion-onboarding platform so
the build is consistent and the extensibility goal ("add a connector in <10 lines, no core
edits") is provable. It deliberately covers only Phases 1–4. Retrieval (RAG/GraphRAG),
the agent layer, and the .NET presentation/gateway tiers are **out of scope** here and
will get their own ADRs.

### Drivers / requirements

1. Tenant registration with an isolated namespace per team.
2. Pluggable connectors behind one interface (`validate_config`, `test_connection`, `fetch`).
3. No-code manifest submission, validated against each connector's own schema.
4. Secrets referenced by name only — raw credentials never stored in manifests.
5. A scheduler that runs each active manifest's connector on its interval.
6. **Tenant isolation enforced at the data-write layer**, not just the API.
7. Status / run-history APIs.

---

## 2. Decisions

### D1 — Framework: FastAPI + SQLAlchemy + SQLite (dev) / Postgres (prod)
FastAPI for the HTTP layer (async, Pydantic-native validation, auto OpenAPI). SQLAlchemy
ORM so the same models run on SQLite locally and Postgres in production with no code change.
SQLite is the chosen default for the local deliverable; the DB URL is config-driven so a
Postgres swap is a settings change only.

**Why:** Pydantic v2 is the validation engine we already need for manifests, so FastAPI
gives request validation and connector-config validation through one mechanism.

### D2 — Connector plugin system: abstract base + decorator registry + auto-discovery
- `BaseConnector` (ABC) defines the contract: `validate_config()`, `test_connection()`,
  `fetch(since_timestamp)`, plus a `config_schema` class attribute (a Pydantic model).
- A module-level `@register_connector("rest_api")` decorator records the class in a global
  `CONNECTOR_REGISTRY: dict[str, type[BaseConnector]]`.
- On startup the engine **imports every module in `connectors/`** (package auto-discovery),
  which triggers the decorators and populates the registry. The core engine never imports a
  concrete connector by name.

**Why:** This is what makes "drop a file in `/connectors`, no core edits" literally true.
The registry is the single seam between the generic engine and connector specifics.

**Rejected alternative:** Python entry points / setuptools plugins — more ceremony, requires
reinstall to register, overkill for an in-repo plugin folder.

### D3 — Per-connector Pydantic config schemas
Each connector owns its config model (e.g. `RestApiConfig`, `FileUploadConfig`). Manifest
validation = `connector.config_schema.model_validate(manifest.config)`. Validation errors
are returned to the caller as structured 422 detail.

**Why:** Keeps connector-specific knowledge inside the connector; the engine only knows
"there is a schema, validate against it."

### D4 — Manifest submission does a real dry-run before persisting
`POST /tenants/{id}/sources` flow: parse → validate config against schema → instantiate
connector → `test_connection()` → **only on success persist** the source as `active`.
A failed dry-run returns a clear error and writes nothing.

**Why:** Requirement 3. Catches bad endpoints/secrets at submission time, not at 2 a.m. in
the scheduler.

### D5 — Secrets: `SecretsVault` interface, in-memory impl now, swappable later
An abstract `SecretsVault` with `get_secret(ref)` / `set_secret(ref, value)`. The default
`InMemoryVault` is a dict. Manifests carry `secret_ref` (a name); the connector resolves the
real value from the vault **at fetch/test time**, never persisting it.

**Why:** Requirement 4 + forward-compat with AWS Secrets Manager / HashiCorp Vault — only
the impl changes, callers don't.

### D6 — Generic pipeline runner via dependency injection on `BaseConnector`
APScheduler reads all `active` manifests and schedules each on its `poll_interval`. For each
run it: looks up the connector class in the registry, builds the typed config, injects the
`SecretsVault`, calls `fetch(since_timestamp)`, then hands records to a **tenant-scoped
writer**. The runner references only the `BaseConnector` interface and the writer — never a
concrete connector type.

**Why:** Requirement + architecture note (DI so the runner is connector-agnostic).

**Dev demo mode:** in development the runner clamps each source's effective interval to a
short floor (`DEV_MIN_INTERVAL_SECONDS`, default 10s) so a scheduled run produces output
within the curl walkthrough. In production the real `poll_interval` is honored. Controlled
by a settings flag.

### D7 — Tenant isolation enforced at the write layer
A `TenantOutputWriter(tenant_id)` is the *only* path that writes ingested data. It derives the
output path from `tenant_id` (`data/{tenant_id}/...`) and refuses to write outside that
namespace (path-traversal guarded). The connector receives a writer already bound to the
tenant and cannot choose an arbitrary destination.

**Why:** Requirement 6 — isolation must hold even if a connector or manifest is buggy or
hostile. Enforcing it in the writer (not the route handler) means every code path that writes
data goes through the same guard.

### D8 — Persistence model
Three tables: `tenants`, `sources` (manifests, with `connector_type`, JSON `config`,
`secret_ref`, `status`, `last_run_*`), and `run_history` (per-run row: `source_id`,
`tenant_id`, `status`, `record_count`, `started_at`, `finished_at`, `error`).
Status APIs read from `sources` (last-run summary) and `run_history` (full log).

---

## 3. Component / data flow

```
POST /tenants                  -> tenants table  (tenant_id + namespace)
POST /tenants/{id}/sources     -> validate(schema) -> test_connection() [dry-run]
                                  -> persist source (active)
APScheduler (per interval)     -> registry[connector_type](config, vault)
                                  -> fetch(since) -> TenantOutputWriter(tenant_id)
                                  -> data/{tenant_id}/*.json  + run_history row
GET  /tenants/{id}/sources            -> sources + last run status
GET  /tenants/{id}/sources/{sid}/runs -> run_history
```

The registry is the only seam between the generic engine and connectors. The writer is the
only seam to durable output, and it is tenant-bound.

---

## 4. Proposed layout

```
app/
  main.py            # FastAPI app + startup (discover connectors, start scheduler)
  config.py          # settings (DB URL, data dir)
  db.py              # SQLAlchemy engine/session
  models.py          # Tenant, Source, RunHistory
  schemas.py         # API request/response Pydantic models
  registry.py        # CONNECTOR_REGISTRY + @register_connector + discover()
  secrets.py         # SecretsVault ABC + InMemoryVault
  writer.py          # TenantOutputWriter (isolation guard)
  runner.py          # APScheduler pipeline runner (DI over BaseConnector)
  routers/
    tenants.py       # POST /tenants
    sources.py       # POST/GET sources, GET runs
connectors/
  base.py            # BaseConnector ABC
  rest_api.py        # RestApiConnector + RestApiConfig
  file_upload.py     # FileUploadConnector + FileUploadConfig
data/                # tenant-namespaced output (gitignored)
README.md            # run instructions + "add a connector in <10 lines"
```

---

## 5. Consequences

**Positive**
- New connector = one file + one decorator; core engine untouched (extensibility proven).
- Tenant isolation centralized in one guarded writer — auditable, hard to bypass.
- Secrets indirection means the in-memory stub swaps for a real vault with no caller changes.
- SQLite→Postgres is a config flip via SQLAlchemy.

**Negative / trade-offs**
- In-process APScheduler is single-node; not horizontally scalable. Acceptable for V1; a
  later ADR can move to a distributed scheduler/queue (e.g. Celery/Arq) if needed.
- In-memory vault and SQLite are dev-grade — explicitly stubs, not production posture.
- Auto-import of `connectors/` means an import-time error in one connector surfaces at
  startup; mitigated by isolating discovery failures per-module and logging them.

**Deferred (future ADRs)**
- AuthN/AuthZ on the APIs (currently open; gateway tier owns this per PRD §7).
- Downstream routing into vector/graph/structured stores (Phases 5–6).
- Distributed scheduling and at-least-once delivery guarantees.

---

## 6. Build order (incremental, matches request)

1. **Scaffold** the structure above (empty/stub modules).
2. **Phase 1 — Registration:** `tenants` table + `POST /tenants`, fully working & verified.
3. **Phase 2 — Connectors:** `BaseConnector`, registry/discovery, `RestApiConnector`,
   `FileUploadConnector`.
4. **Phase 3 — Manifests + secrets:** `POST sources` with schema validation, dry-run,
   `SecretsVault`.
5. **Phase 4 — Runner + status:** APScheduler runner, `TenantOutputWriter`, status/run APIs.
6. **README** with example curl commands for the full happy path.

Each phase is runnable before the next begins.
