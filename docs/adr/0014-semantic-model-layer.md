# ADR 0014 — Semantic Model Layer (Cortex Analyst Equivalent)

| Field | Value |
|---|---|
| **Status** | Proposed |
| **Date** | 2026-06-23 |
| **Deciders** | Platform Engineering |
| **Phase** | 8 — Semantic Query & Analyst Parity |
| **Closes gap** | `docs/cortex-ai-mapping.md` §5 — "Semantic Model layer" |

---

## Context

### The Problem

DealPrep's `StructuredAgent` today calls `StructuredIndexer.search()`, which executes a Postgres
full-text-search (`tsvector` + `tsquery`) against the `structured_record_rows` table and returns
matching JSONB blobs.  This works for keyword retrieval but it does not answer *analytical
questions*:

- **"What is Acme Corp's normalised EBITDA for FY2024?"** → FTS returns rows where "EBITDA" appears
  but cannot compute the value.
- **"Which portfolio companies have an EBITDA margin above 20%?"** → Cannot filter or aggregate
  across records.
- **"Show me revenue trend for Q1–Q4 2024 for tenant T-001"** → Cannot join or order time series.

Snowflake solves this with **Cortex Analyst**: an LLM that translates natural-language questions
into SQL using a **Semantic Model** — a YAML file that declares the tables, metrics, dimensions,
and measures the LLM is allowed to query.  The semantic model is the *contract* between the
financial data and the LLM; without it, the LLM either hallucinates table names or produces
incorrect aggregations.

### Why This Is the Highest-Priority Gap

| Gap | Impact |
|---|---|
| No NL→SQL path | Analysts must know column names to get numbers; the platform cannot answer "how much" or "which is highest" |
| No metric definitions | "EBITDA margin" means different things per tenant; without a canonical definition the LLM guesses |
| No dimension catalogue | "By quarter" requires a date-spine; without declared dimensions grouping queries fail |
| No guardrail on SQL scope | An unguided LLM might generate `SELECT *` across tenants (isolation risk) |

---

## Decision

Implement a **DealPrep Semantic Model** — a per-tenant YAML file that declares:

1. **Tables** — which Postgres tables / views the LLM may query for this tenant.
2. **Dimensions** — categorical columns available for grouping (company, period, currency, source_file).
3. **Measures** — numeric columns or expressions with canonical names and aggregation rules (revenue, ebitda, ebitda_margin, net_income).
4. **Filters** — mandatory clauses that are always injected (e.g. `tenant_id = :tenant_id`).
5. **Relationships** — foreign-key joins declared as named joins so the LLM can traverse them safely.

A new `SemanticModelAgent` reads the tenant's YAML, constructs a prompt that includes the schema
context, asks the LLM to produce a **single safe SQL SELECT**, validates it against an allowlist,
executes it via SQLAlchemy, and returns structured results.

The `StructuredAgent` becomes a router:
- Plain keyword lookup (no aggregation intent) → existing FTS path (fast, no LLM cost).
- Analytical intent detected → `SemanticModelAgent` NL→SQL path.

---

## Alternatives Considered

### A — Keep FTS only

FTS is fast and zero-LLM-cost.  **Rejected** because it cannot answer quantitative questions,
which are 80% of PE due-diligence queries ("what is the EBITDA?", "how does revenue trend?").

### B — Use an ORM query builder (SQLAlchemy Core expressions)

Generate SQLAlchemy expressions from parsed intent.  **Rejected** — requires a full intent parser
(entity extraction + aggregation classification) which is more engineering than the LLM approach,
less flexible, and harder to extend.

### C — Adopt Cortex Analyst directly (migrate to Snowflake)

Re-host data in Snowflake and use Cortex Analyst as a service.  **Rejected** at this phase —
Snowflake dependency is a major infrastructure change; the semantic model design here mirrors the
Cortex YAML schema so migration remains possible later.

### D — Vector-only: embed the question, return the closest record

Already available via `DocumentResearcher`.  **Rejected as a replacement** — vectors retrieve
*similar text*, not *computed values*.  Complementary but not sufficient.

---

## Architecture

### Semantic Model YAML Schema

```yaml
# data/tenants/{tenant_id}/semantic_model.yaml
version: "1"
tenant_id: "T-001"

tables:
  - name: structured_records
    description: "Financial KPI records extracted from diligence documents"
    schema: public
    sql_table: structured_record_rows          # actual Postgres table
    mandatory_filter: "tenant_id = :tenant_id" # always injected — tenant isolation

dimensions:
  - name: company
    label: "Portfolio Company"
    column: "fields->>'company_name'"
    type: string

  - name: period
    label: "Fiscal Period"
    column: "fields->>'period'"         # e.g. "FY2024", "Q1-2024"
    type: string

  - name: currency
    label: "Reporting Currency"
    column: "fields->>'currency'"
    type: string

  - name: source_file
    label: "Source Document"
    column: "fields->>'original_file_reference'"
    type: string

measures:
  - name: revenue
    label: "Revenue"
    expression: "(fields->>'revenue')::numeric"
    aggregation: sum
    description: "Total revenue as reported in source document"

  - name: ebitda
    label: "EBITDA"
    expression: "(fields->>'ebitda')::numeric"
    aggregation: sum

  - name: ebitda_margin
    label: "EBITDA Margin"
    expression: "ROUND((fields->>'ebitda')::numeric / NULLIF((fields->>'revenue')::numeric, 0) * 100, 2)"
    aggregation: avg
    description: "EBITDA as % of Revenue; pre-computed expression, not sum-aggregable"

  - name: net_income
    label: "Net Income"
    expression: "(fields->>'net_income')::numeric"
    aggregation: sum

allowed_tables:
  - structured_record_rows   # SQL generation is restricted to this list

sql_guardrails:
  max_rows: 500              # LIMIT injected if not present
  read_only: true            # only SELECT allowed; INSERT/UPDATE/DELETE rejected
  forbidden_patterns:        # regex blocklist applied before execution
    - "DROP"
    - "DELETE"
    - "UPDATE"
    - "INSERT"
    - "TRUNCATE"
    - "--"
    - ";"                    # no statement chaining
```

### Component Diagram

```
AnalyzeRequest
      │
      ▼
StructuredAgent.run(state)
      │
      ├─── "keyword intent" ──────────────────► StructuredIndexer.search()  (existing FTS path)
      │                                               │
      │                                               ▼
      │                                         AgentResult(payload={"records": [...]})
      │
      └─── "analytical intent" ──────────────► SemanticModelAgent
                                                      │
                                              ┌───────▼────────┐
                                              │ SemanticLoader  │  loads data/tenants/{id}/semantic_model.yaml
                                              └───────┬────────┘
                                                      │
                                              ┌───────▼────────┐
                                              │ NL→SQL Prompter │  system prompt = schema context + examples
                                              └───────┬────────┘
                                                      │
                                              LLM (Claude claude-haiku-4-5 for cost efficiency)
                                                      │
                                              ┌───────▼────────┐
                                              │ SQL Validator   │  allowlist + forbidden-pattern check
                                              └───────┬────────┘
                                                      │
                                              ┌───────▼────────┐
                                              │ SQLAlchemy exec │  parameterised, tenant_id injected
                                              └───────┬────────┘
                                                      │
                                              AgentResult(payload={"sql": ..., "rows": [...], "columns": [...]})
```

### Intent Detection (heuristic — Phase 8 V1)

A lightweight classifier determines which path to take.  Analytical intent is flagged when the
question contains any of:

- Aggregation words: *how much, total, sum, average, mean, max, min, highest, lowest*
- Comparison words: *compare, versus, vs, difference between, more than, less than*
- Trend words: *trend, over time, quarter, year, monthly, growth, decline*
- Ratio words: *margin, ratio, rate, percentage, per cent*
- Ranking words: *top, bottom, rank, best, worst*

All other queries fall through to FTS.  A false-negative (analytical query that falls through) is
safe — it just returns keyword results rather than computed values.  A false-positive (keyword query
routed to NL→SQL) incurs an LLM call but not a correctness failure.

---

## File Plan

| File | Purpose |
|---|---|
| `pipeline/semantic/loader.py` | Load + validate tenant YAML; cache per tenant_id |
| `pipeline/semantic/validator.py` | SQL allowlist + forbidden-pattern guard; tenant_id injection |
| `pipeline/semantic/prompter.py` | Build NL→SQL system prompt from semantic model |
| `agents/semantic_model_agent.py` | `SemanticModelAgent(BaseAgent)` — orchestrates loader→prompter→LLM→validator→execute |
| `app/routers/semantic.py` | `GET /tenants/{id}/semantic-model` (read) + `PUT /tenants/{id}/semantic-model` (upload) |
| `data/tenants/{id}/semantic_model.yaml` | Per-tenant semantic model files |
| `docs/evaluation/semantic-model-evaluation.md` | Quality gates: SQL correctness, answer accuracy, isolation test |

---

## SQL Safety Guarantees

The validator enforces three layers before execution:

1. **Forbidden-pattern blocklist** — regex against `DROP`, `DELETE`, `UPDATE`, `INSERT`,
   `TRUNCATE`, statement chaining (`;`), comment injection (`--`).
2. **Table allowlist** — parse the generated SQL (via `sqlparse`) and assert every table reference
   appears in `allowed_tables`.
3. **Mandatory filter injection** — append `AND tenant_id = :tenant_id` to every WHERE clause;
   if no WHERE exists, prepend one.  This enforces tenant isolation even if the LLM omits it.
4. **LIMIT cap** — if the generated SQL has no LIMIT, inject `LIMIT 500`.

Execution is parameterised via SQLAlchemy `text()` with bound parameters — no f-string or
string concatenation of user input ever reaches the database.

---

## Cost Model

| Scenario | Model | Tokens (est.) | Cost (per call) |
|---|---|---|---|
| Analytical intent → NL→SQL | `claude-haiku-4-5` | ~800 in / ~150 out | ~$0.0002 |
| Keyword intent → FTS | (none) | 0 | $0 |
| Estimated mix (70% keyword, 30% analytical) | — | — | ~$0.00006/query |

Haiku is used for NL→SQL because the task is structured (schema context + SQL output) and
the cost difference vs Sonnet is 10×.  Sonnet is reserved for narrative synthesis.

---

## Evaluation Gates (before `implemented = True`)

See `docs/evaluation/semantic-model-evaluation.md` (to be written alongside implementation):

1. **SQL correctness** — for 20 golden questions, generated SQL returns the expected row count and
   value.  Pass threshold: ≥ 85%.
2. **Isolation test** — a query against tenant T-001 must never return rows from T-002 even if the
   LLM omits the tenant filter.
3. **Injection resistance** — 10 adversarial prompts (`;DROP TABLE`, `OR 1=1`, comment injection)
   must all be blocked before reaching the database.
4. **Latency** — NL→SQL round-trip (including LLM call) ≤ 3 s at p95 under single-thread load.
5. **Fallback** — if the LLM returns invalid SQL (validator rejects), the agent falls back to FTS
   and appends a warning.  This must be tested explicitly.

---

## Consequences

**Positive:**
- Closes the biggest gap with Cortex Analyst; DealPrep can now answer "how much" and "which is
  highest" questions over financial KPI records.
- Semantic model YAML is tenant-managed — each PE firm can define their own EBITDA, revenue, and
  margin definitions without code changes.
- SQL safety layer means even a misbehaving LLM cannot cross tenant boundaries.

**Negative / Risks:**
- LLM-generated SQL can be incorrect for complex multi-table joins; Phase 8 V1 is intentionally
  scoped to single-table queries against `structured_record_rows`.
- Requires each tenant to maintain a `semantic_model.yaml`; bootstrap tooling (auto-generate from
  observed field names) is a follow-on task.
- Adding a second LLM call per analytical query increases cost and latency; intent detection must
  be accurate to avoid unnecessary calls.

---

## Implementation Order

1. Write `SemanticLoader` + `SemanticValidator` (no LLM needed — pure config + SQL parsing).
2. Write `SemanticModelAgent` with mock LLM (hard-coded SQL template) — verify isolation and
   validator in integration tests.
3. Wire real LLM (`claude-haiku-4-5`) + prompter.
4. Update `StructuredAgent` to route on intent.
5. Add `PUT /tenants/{id}/semantic-model` upload endpoint.
6. Write evaluation doc + run 20-question golden set.
7. Set `SemanticModelAgent.implemented = True`.
