# ADR 0019 — Observability & Monitoring

| Field | Value |
|---|---|
| **Status** | Proposed |
| **Date** | 2026-06-23 |
| **Deciders** | Platform Engineering, DevOps |
| **Phase** | 9 — Production Readiness |
| **PRD reference** | §7 "Cross-cutting concerns: observability (cost/latency tracking)" and §12 Phase 8 "monitoring" |

---

## Context

The platform has no observability beyond Python `logging` calls and the `agent_timings` dict
returned in the API response.  In production, this means:

- A slow LangGraph run has no trace.  You cannot tell if the delay was in ChromaDB, Neo4j, or
  the LLM call.
- A spike in error rate produces no alert.  The on-call engineer finds out from a user complaint.
- You cannot answer "which tenant is generating the most LLM cost this week?" without writing
  a one-off SQL query.
- You cannot see whether the parallelism efficiency ADR 0017 §5B target (≥ 0.75) is being
  met in production.

PE/VC clients will ask these questions in a production sign-off:
- "How do you know when the system is degraded?"
- "What is the p99 latency for an analysis?"
- "How do you know if a tenant's data is stale?"

Without observability, the answer is "we don't" — which blocks deployment.

### Three Pillars of Observability

| Pillar | What it captures | Current state |
|---|---|---|
| **Logs** | Discrete events with context (error, warning, info) | Python `logging` — unstructured, no correlation ID |
| **Metrics** | Aggregated numbers over time (latency histograms, error rates, counts) | None |
| **Traces** | Causal chain of one request across all services | None |

All three are needed.  Metrics without traces tell you *something is wrong* but not *where*.
Traces without metrics tell you about individual requests but not trends.

---

## Decision

Adopt **OpenTelemetry (OTel)** as the instrumentation standard and **Prometheus + Grafana** as
the backend.  OTel is vendor-neutral — the same instrumentation code exports to Datadog, Jaeger,
Tempo, or any other backend without code changes.

### Why OpenTelemetry + Prometheus/Grafana (not Datadog/New Relic)

| Option | Cost | Self-hosted | Vendor lock-in |
|---|---|---|---|
| **OTel + Prometheus + Grafana** | Free (open-source) | Yes (Docker Compose) | None |
| Datadog | $15–30/host/month | No | High |
| New Relic | Free tier limited | No | High |
| AWS X-Ray + CloudWatch | Pay per use | No | AWS lock-in |

For a platform that runs in deal rooms that may be air-gapped, self-hosted open-source is the
only viable option.

---

## Architecture

```
DealPrep FastAPI app
    │
    │  OpenTelemetry SDK (traces + metrics)
    ▼
OTel Collector (sidecar)
    ├──► Prometheus (metrics scrape)  ──► Grafana (dashboards)
    └──► Tempo / Jaeger (traces)      ──► Grafana (trace UI)

Grafana reads both Prometheus and Tempo so you can click from a
slow metric spike → the individual trace that caused it.
```

In `docker-compose.yml`, add: `otel-collector`, `prometheus`, `grafana`, `tempo`.

---

## What We Instrument

### A. Structured Logging

Replace bare `logging.getLogger()` calls with **structlog**, which outputs JSON lines:

```python
# Before (unstructured — hard to parse in production)
logger.info("analysis complete for tenant %s", tenant_id)

# After (structured — every field is queryable in Grafana Loki)
import structlog
log = structlog.get_logger()
log.info("analysis_complete",
    tenant_id=tenant_id,
    session_id=session_id,
    orchestrator=orchestrator,
    risk_score=risk_score,
    total_latency_ms=total_ms,
    agents_run=list(agent_results.keys()),
    interrupted=interrupted,
)
```

Every log line gets a `correlation_id` (= `session_id`) automatically via structlog context
variables.  This lets you grep all log lines for one analysis across the entire request lifecycle.

**Key log events to instrument:**

| Event | Level | Extra fields |
|---|---|---|
| Request received | INFO | tenant_id, endpoint, orchestrator |
| Agent started | DEBUG | agent_name, session_id |
| Agent completed | INFO | agent_name, status, latency_ms |
| Agent failed | WARNING | agent_name, error, latency_ms |
| Risk score computed | INFO | risk_score, signals_count |
| HITL triggered | WARNING | session_id, risk_score |
| LLM call started | DEBUG | model, input_tokens_est |
| LLM call completed | INFO | model, input_tokens, output_tokens, cost_usd, latency_ms |
| Analysis complete | INFO | session_id, total_ms, interrupted, cached |
| Cache hit | INFO | cache_key_hash |
| Ingestion run started | INFO | source_id, connector_type |
| Ingestion run completed | INFO | source_id, docs_processed, chunks_written |
| Budget exceeded | WARNING | tenant_id, budget_usd, used_usd |
| Guard blocked request | WARNING | guard_type, verdict |

### B. Metrics (Prometheus)

Expose a `/metrics` endpoint (Prometheus scrape format) via `prometheus-fastapi-instrumentator`.

**Metrics to define:**

```python
from prometheus_client import Counter, Histogram, Gauge

# Analysis metrics
analysis_requests_total = Counter(
    "dealprep_analysis_requests_total",
    "Total analysis requests",
    labelnames=["tenant_id", "orchestrator", "interrupted"],
)

analysis_duration_seconds = Histogram(
    "dealprep_analysis_duration_seconds",
    "End-to-end analysis latency",
    labelnames=["orchestrator"],
    buckets=[0.5, 1.0, 2.0, 4.0, 8.0, 15.0, 30.0],
)

agent_duration_seconds = Histogram(
    "dealprep_agent_duration_seconds",
    "Per-agent execution latency",
    labelnames=["agent_name", "status"],
    buckets=[0.1, 0.3, 0.5, 1.0, 2.0, 5.0],
)

# LLM metrics
llm_tokens_total = Counter(
    "dealprep_llm_tokens_total",
    "LLM tokens consumed",
    labelnames=["tenant_id", "model", "token_type"],  # token_type: input|output
)

llm_cost_usd_total = Counter(
    "dealprep_llm_cost_usd_total",
    "Cumulative LLM cost in USD",
    labelnames=["tenant_id", "model"],
)

# Risk metrics
risk_score_histogram = Histogram(
    "dealprep_risk_score",
    "Distribution of risk scores",
    labelnames=["tenant_id"],
    buckets=[0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
)

hitl_trigger_total = Counter(
    "dealprep_hitl_trigger_total",
    "Times HITL was triggered",
    labelnames=["tenant_id", "outcome"],  # outcome: approved|rejected
)

# Ingestion metrics
ingestion_runs_total = Counter(
    "dealprep_ingestion_runs_total",
    "Total ingestion runs",
    labelnames=["source_id", "status"],  # status: success|failed|skipped
)

chunks_indexed_total = Counter(
    "dealprep_chunks_indexed_total",
    "Chunks written to vector store",
    labelnames=["tenant_id", "store_backend"],
)

# Store health
vector_store_size = Gauge(
    "dealprep_vector_store_chunks",
    "Current chunk count per tenant",
    labelnames=["tenant_id"],
)

# Guard metrics
guard_blocks_total = Counter(
    "dealprep_guard_blocks_total",
    "Requests blocked by guardrails",
    labelnames=["guard_type", "tenant_id"],  # guard_type: pii|injection|budget
)
```

### C. Distributed Traces (OpenTelemetry)

Instrument every LangGraph node and agent with OTel spans so a single analysis request has a
waterfall trace showing every step:

```python
from opentelemetry import trace

tracer = trace.get_tracer("dealprep.orchestrator")

async def document_researcher_node(state: OrchestratorState) -> dict:
    with tracer.start_as_current_span("document_researcher") as span:
        span.set_attribute("tenant_id", state["tenant_id"])
        span.set_attribute("query_length", len(state["query"]))
        # ... agent logic ...
        span.set_attribute("chunks_retrieved", len(chunks))
        span.set_attribute("top_score", chunks[0]["score"] if chunks else 0)
        return {"retrieved_chunks": chunks, ...}
```

**What a trace looks like in Grafana:**

```
analyze POST /tenants/T-001/analyze          [2.1s total]
  └── load_memory_node                        [45ms]
  ├── document_researcher_node                [320ms]  ← parallel
  ├── structured_agent_node                   [180ms]  ← parallel
  │   └── semantic_model_agent (NL→SQL)       [160ms]
  │       └── LLM call (claude-haiku)         [140ms]
  └── graph_agent_node                        [410ms]  ← parallel
  └── risk_scorer_node                        [12ms]
  └── [INTERRUPT]                             [HITL paused]
  ... resumed after 4m 22s ...
  └── synthesis_node                          [890ms]
      └── LLM call (claude-sonnet-4-6)        [850ms]
  └── save_memory_node                        [38ms]
```

You can click any span to see the attributes, logs, and whether it succeeded.

---

## Grafana Dashboards

Four dashboards, each auto-provisioned from JSON files in `monitoring/dashboards/`:

### Dashboard 1 — Platform Health

Real-time view for on-call:
- Request rate (req/min) — time series
- Error rate (%) — time series
- p50 / p95 / p99 latency — time series
- Active sessions (in-progress analyses) — gauge
- Alert panel: any metric breaching its SLA

### Dashboard 2 — Agent Performance

For engineering:
- Per-agent latency heatmap (p50/p95 per agent over time)
- Agent error rate by agent name
- Fan-out parallelism efficiency (max(agent_latencies) / wall_clock)
- HITL trigger rate by day

### Dashboard 3 — Cost & Usage

For product/finance:
- LLM cost by tenant by day (stacked bar)
- Token usage by model (pie)
- Top 5 tenants by cost this month
- Budget utilisation per tenant (gauge)
- Cost per query trend

### Dashboard 4 — Ingestion Health

For operations:
- Ingestion runs per day by status (success / failed / skipped)
- Chunks indexed per day by tenant
- Source-level last-run timestamp (highlight staleness > 24h)
- Content-hash dedup hit rate

---

## Alerting Rules

Define in `monitoring/alerts/rules.yaml` (Prometheus alertmanager format):

```yaml
groups:
  - name: dealprep_platform
    rules:
      - alert: HighErrorRate
        expr: rate(dealprep_analysis_requests_total{status="error"}[5m]) > 0.05
        for: 2m
        annotations:
          summary: "Error rate > 5% for 2 minutes"

      - alert: HighLatency
        expr: histogram_quantile(0.95, dealprep_analysis_duration_seconds) > 10
        for: 5m
        annotations:
          summary: "p95 analysis latency > 10 seconds"

      - alert: BudgetExhausted
        expr: dealprep_llm_cost_usd_total > on(tenant_id) group_left dealprep_tenant_budget_usd
        annotations:
          summary: "Tenant {{ $labels.tenant_id }} has exceeded LLM budget"

      - alert: IngestionStale
        expr: time() - dealprep_ingestion_last_run_timestamp > 86400
        annotations:
          summary: "Source {{ $labels.source_id }} has not been ingested in > 24h"

      - alert: HITLBacklog
        expr: dealprep_hitl_pending_count > 5
        for: 10m
        annotations:
          summary: "5+ analyses waiting for human review — analysts may be overloaded"
```

---

## docker-compose additions

```yaml
# docker-compose.yml additions for Phase 9
services:

  otel-collector:
    image: otel/opentelemetry-collector-contrib:latest
    volumes:
      - ./monitoring/otel-config.yaml:/etc/otel-collector-config.yaml
    command: ["--config=/etc/otel-collector-config.yaml"]
    ports:
      - "4317:4317"   # OTLP gRPC
      - "4318:4318"   # OTLP HTTP

  prometheus:
    image: prom/prometheus:latest
    volumes:
      - ./monitoring/prometheus.yml:/etc/prometheus/prometheus.yml
      - ./monitoring/alerts/:/etc/prometheus/rules/
    ports:
      - "9090:9090"

  grafana:
    image: grafana/grafana:latest
    volumes:
      - ./monitoring/dashboards:/var/lib/grafana/dashboards
      - ./monitoring/provisioning:/etc/grafana/provisioning
    ports:
      - "3000:3000"
    environment:
      GF_SECURITY_ADMIN_PASSWORD: ${GRAFANA_PASSWORD:-dealprep}

  tempo:
    image: grafana/tempo:latest
    ports:
      - "3200:3200"
```

---

## File Plan

| File | Purpose |
|---|---|
| `app/telemetry.py` | OTel SDK setup — `configure_telemetry(app)` called in `main.py` |
| `app/metrics.py` | All Prometheus metric definitions (single source of truth) |
| `app/logging_config.py` | structlog config — JSON output + correlation_id context |
| `monitoring/otel-config.yaml` | OTel collector routing (→ Prometheus + Tempo) |
| `monitoring/prometheus.yml` | Scrape config |
| `monitoring/alerts/rules.yaml` | Alert rules |
| `monitoring/dashboards/` | Grafana dashboard JSON files (auto-provisioned) |
| `monitoring/provisioning/` | Grafana datasource + dashboard provisioning config |
| `requirements.txt` | Add: `opentelemetry-sdk`, `opentelemetry-exporter-otlp`, `prometheus-fastapi-instrumentator`, `structlog` |

---

## Implementation Order

1. `app/logging_config.py` + structlog — swap all `logger.info()` calls.  No new dependencies on startup.
2. `app/metrics.py` — define all metrics; add `/metrics` endpoint via instrumentator.
3. Add Prometheus + Grafana to `docker-compose.yml`; confirm scrape works.
4. Add OTel spans to agents and orchestrator nodes.
5. Add Tempo to docker-compose; confirm trace waterfall in Grafana.
6. Write alert rules; configure alertmanager to send to Slack/email.
7. Provision all four dashboards.

---

## Consequences

**Positive:**
- On-call can answer "what is wrong and where?" from a single Grafana URL instead of reading
  raw Python logs.
- Cost dashboard gives product and finance a live view of LLM spend without SQL queries.
- OTel is vendor-neutral — exporting to Datadog or Honeycomb later requires only a config
  change in `otel-config.yaml`, not code changes.
- Trace waterfall makes it obvious when a tenant's ChromaDB collection has grown so large that
  vector search is the latency bottleneck.

**Negative / Risks:**
- structlog requires a one-time migration of all existing `logger.info()` calls — this is
  mechanical but touches many files.
- OTel adds ~5 ms overhead per span.  At 10 spans per analysis, this is ~50 ms — acceptable
  but must be measured.
- Grafana + Prometheus + Tempo + OTel Collector adds four new Docker services to local dev.
  The docker-compose file should have a `--profile monitoring` flag so developers can opt out
  of the full stack.
