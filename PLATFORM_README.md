# DealPrep Ingestion Onboarding Platform

A self-service, multi-tenant data ingestion platform. Teams **register once**, then
submit a **config manifest** (no code) describing their data source. A generic pipeline
engine handles acquisition for any registered team through a **plugin-based connector
system**.

This implements **Phases 1–4** of the DealPrep roadmap (see [the PRD](readme.md)).
Architecture decisions are recorded in
[ADR 0001](docs/adr/0001-data-ingestion-onboarding-platform.md). Predicted operational
reality and known V1 gaps are tracked in the
[Phase 1–4 production-readiness review](docs/production-readiness/PHASE-1-4_ingestion-platform.md).

---

## What it does

| Capability | Endpoint / mechanism |
|---|---|
| Register a team → tenant_id + namespace | `POST /tenants` |
| Submit a manifest (validated + dry-run before save) | `POST /tenants/{id}/sources` |
| Store a secret referenced by name | `POST /secrets` |
| List sources + last-run status | `GET /tenants/{id}/sources` |
| Run history per source | `GET /tenants/{id}/sources/{sid}/runs` |
| Scheduled, generic pipeline runner | APScheduler (in-process) |
| Tenant-isolated output | `data/{tenant_id}/{source_id}/*.json` |

A **minimal browser console** is served at **`/`** (→ `/ui/`) — register a tenant, store a
secret, submit + dry-run a source, and watch scheduled runs live, no curl needed.
Interactive API docs are at **`/docs`**.

---

## Architecture at a glance

```
POST /tenants                  -> tenants table (tenant_id + namespace)
POST /tenants/{id}/sources     -> validate(connector schema) -> test_connection() dry-run
                                  -> persist source (active)
APScheduler (per interval)     -> registry[connector_type](config, vault)   [DI]
                                  -> connector.fetch(since) -> TenantOutputWriter(tenant_id)
                                  -> data/{tenant_id}/...    + run_history row
```

- **`app/registry.py`** is the *only* seam between the generic engine and connectors. The
  engine never imports a concrete connector — it looks them up by key. Connectors
  self-register via a decorator and are auto-discovered at startup.
- **`app/writer.py`** (`TenantOutputWriter`) is the *only* path that writes ingested data,
  and it is bound to one `tenant_id`. Isolation is enforced at the **write layer**, not
  just the API — a buggy/hostile connector cannot write outside its namespace.
- **`app/secrets.py`** (`SecretsVault`) is a swappable interface. Manifests carry a
  `secret_ref` (a name); the raw value is resolved at runtime and never persisted. The
  in-memory impl swaps for AWS Secrets Manager / Vault with no caller changes.

---

## Project layout

```
app/
  main.py            FastAPI app + lifespan (init db, discover connectors, start scheduler)
  config.py          settings (DB URL, data dir, dev demo mode)
  db.py models.py    SQLAlchemy engine + tenants/sources/run_history tables
  schemas.py         API request/response models
  registry.py        CONNECTOR_REGISTRY + @register_connector + discover()  <-- the seam
  secrets.py         SecretsVault (ABC) + InMemoryVault
  writer.py          TenantOutputWriter (isolation guard)
  runner.py          APScheduler pipeline runner (DI over BaseConnector)
  routers/           tenants, sources, secrets
connectors/
  base.py            BaseConnector ABC
  rest_api.py        RestApiConnector + RestApiConfig
  file_upload.py     FileUploadConnector + FileUploadConfig
examples/
  stub_rest_api.py   tiny local REST endpoint for the demo
data/                tenant-namespaced output (gitignored)
```

---

## Run it locally

Requires Python 3.11+.

```powershell
# 1. install
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 2. start the API (SQLite DB + scheduler boot automatically)
.\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8077

# 3. (optional, for testing the REST connector) start the stub endpoint in another terminal
.\.venv\Scripts\python.exe examples\stub_rest_api.py 8099
```

Then open **http://127.0.0.1:8077/** in a browser and use the console:
register a tenant → (optional) store a secret → submit a source (the form pre-fills a
working config) → watch the **Sources** and **Run history** panels update as the scheduler
runs. The REST template points at the stub from step 3.

```bash
# macOS/Linux equivalent
python -m venv .venv && ./.venv/bin/pip install -r requirements.txt
./.venv/bin/uvicorn app.main:app --port 8077
```

> **Dev demo mode** is on by default (`DEALPREP_DEV_MODE=true`): poll intervals are
> clamped to ~10s so a scheduled run produces output within the walkthrough below. Set
> `DEALPREP_DEV_MODE=false` to honor each manifest's real `poll_interval_seconds`.

---

## End-to-end walkthrough (curl)

Start the included stub REST endpoint in a second terminal so the REST connector has
something to poll:

```bash
python examples/stub_rest_api.py 8099
```

**1 — Register a tenant** → returns `tenant_id` + `namespace`:

```bash
curl -s -X POST http://127.0.0.1:8077/tenants \
  -H 'Content-Type: application/json' \
  -d '{"name":"Comps Team","owner_email":"analyst@comps.example.com","use_case":"Pull comps multiples"}'
# { "id": "<TENANT_ID>", "namespace": "comps-team-ab12cd", ... }
```

**2 — Store the secret the manifest will reference** (raw value never goes in the manifest):

```bash
curl -s -X POST http://127.0.0.1:8077/secrets \
  -H 'Content-Type: application/json' \
  -d '{"ref":"comps-api-token","value":"super-secret-xyz"}'
```

**3 — Submit a REST API source manifest.** This validates the config against
`RestApiConfig` and runs `test_connection()` as a **dry-run before saving**:

```bash
curl -s -X POST http://127.0.0.1:8077/tenants/<TENANT_ID>/sources \
  -H 'Content-Type: application/json' \
  -d '{
        "connector_type": "rest_api",
        "config": {
          "base_url": "http://127.0.0.1:8099/",
          "auth_type": "bearer",
          "secret_ref": "comps-api-token",
          "records_path": "data",
          "poll_interval_seconds": 300
        }
      }'
# 201 -> source persisted as "active" (dry-run passed)
```

A bad manifest is rejected with a clear `422` and **nothing is saved** — e.g. an
unreachable `base_url`:

```json
{ "detail": { "message": "dry-run test_connection failed",
              "error": "could not reach http://127.0.0.1:1/: ..." } }
```

**4 — Watch the scheduled run land in the tenant namespace** (wait ~10s in dev mode):

```bash
curl -s http://127.0.0.1:8077/tenants/<TENANT_ID>/sources          # last_run_status: "success"
curl -s http://127.0.0.1:8077/tenants/<TENANT_ID>/sources/<SOURCE_ID>/runs

ls data/<TENANT_ID>/<SOURCE_ID>/        # JSON output file written by the runner
```

The output file is tenant-tagged:

```json
{ "tenant_id": "<TENANT_ID>", "source_id": "<SOURCE_ID>",
  "ingested_at": "2026-06-21T11:17:27+00:00", "record_count": 3,
  "records": [ {"id": 1, "company": "Acme Corp", "ev_ebitda": 11.2}, ... ] }
```

### File upload connector (second example)

```bash
mkdir -p dropzone && echo '{"deal":"Project Falcon","amount":42000000}' > dropzone/deal1.json

curl -s -X POST http://127.0.0.1:8077/tenants/<TENANT_ID>/sources \
  -H 'Content-Type: application/json' \
  -d '{"connector_type":"file_upload","config":{"directory":"dropzone","glob":"*.json"}}'
```

The runner ingests new files only — the first run picks up `deal1.json`; later runs
report 0 records until a new file appears (incremental, cursor-based watch).

---

## Adding a new connector in under 10 lines

No core engine file changes. Drop one file in `connectors/`, declare a Pydantic config,
and decorate the class. Auto-discovery registers it on the next startup.

```python
# connectors/echo.py
from pydantic import BaseModel
from app.registry import register_connector
from connectors.base import BaseConnector

class EchoConfig(BaseModel):
    message: str

@register_connector("echo")
class EchoConnector(BaseConnector):
    config_schema = EchoConfig
    def test_connection(self): pass                       # dry-run check
    def fetch(self, since): return [{"echo": self.config.message}]
```

That's it. Restart the app and `"connector_type": "echo"` is a valid manifest type —
the registry, manifest validation, dry-run, scheduler, isolation writer, and run-history
logging all work for it automatically, because they only ever talk to the `BaseConnector`
interface.

---

## Notes / limitations (V1)

- In-process APScheduler (single node) and an in-memory `SecretsVault` are deliberate
  dev-grade stubs; both are isolated behind interfaces for later swap (see ADR §5).
- APIs are unauthenticated in this layer — auth is owned by the API gateway tier per the
  PRD. Add it before any non-local deployment.
- SQLite is the local default; set `DEALPREP_DATABASE_URL` to a Postgres URL to switch.
```
