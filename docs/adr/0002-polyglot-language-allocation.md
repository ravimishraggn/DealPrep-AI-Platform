# ADR 0002 — Polyglot Language Allocation (.NET primary, Python for the AI/data plane)

- **Status:** Proposed
- **Date:** 2026-06-21
- **Owner:** Ravi Mishra
- **Deciders:** DealPrep platform team
- **Related:** [ADR 0001](0001-data-ingestion-onboarding-platform.md) (ingestion platform, currently all-Python)

---

## 1. Context

The PRD (§7) already implies a polyglot system (.NET at the top tiers, Python for
orchestration/agents/retrieval/ingestion). The directive now is explicit: **.NET is the
primary language; use Python only where it genuinely fits.** This ADR turns that directive
into a concrete, per-layer allocation with a decision rule, and resolves the one layer that
actually straddles the boundary — **ingestion** — which ADR 0001 currently implements
entirely in Python.

The goal is not "rewrite everything in .NET." It is to put each responsibility in the
language that is *decisively* better for it, and to keep the .NET/Python boundary on a clean
network seam so we never split one tightly-coupled service across two runtimes.

---

## 2. Decision rule

Assign each component by asking two questions in order:

1. **Does it depend on the AI/ML/NLP ecosystem** (LLMs, embeddings, RAG, LangGraph, entity
   extraction, vector/graph reasoning)? → **Python.** This ecosystem is Python-first and
   not credibly replaceable on .NET today.
2. Otherwise, is it **system-of-record, security perimeter, transactional enterprise
   service, or Office integration**? → **.NET.** This is .NET's sweet spot: strong typing,
   ASP.NET Core, EF Core, Entra ID/OAuth, mature long-lived services, first-class Office
   add-ins.

In one line: **Python reasons; .NET governs.** Stores and infrastructure are
language-agnostic and accessed by whichever plane owns the data.

---

## 3. Per-layer allocation

| Layer (PRD §7) | Language | Why it's decisive |
|---|---|---|
| Presentation (chat UI, dashboard) | **.NET** (Blazor/ASP.NET) | Primary stack; not AI work. (A JS/React SPA is an acceptable substitute — still not Python.) |
| **Excel / PowerPoint plugin** | **.NET** | Decisive. Office Add-ins / Microsoft Graph are a .NET-native story; Python has no real Office add-in path. |
| API Gateway (auth, routing, validation) | **.NET** (ASP.NET Core, YARP) | The security perimeter. Entra ID/OAuth, rate limiting, request validation — .NET sweet spot. |
| Orchestration (LangGraph, fan-out/fan-in) | **Python** | Decisive. LangGraph + agent-orchestration tooling is Python-first. |
| Agent layer (specialist agents) | **Python** | Decisive. LLM SDKs, tool calling, agent frameworks richest in Python. |
| Retrieval / tooling (Vector/Hybrid/GraphRAG, via MCP) | **Python** | Decisive. Embeddings, LlamaIndex, RAG/GraphRAG live here. |
| **Ingestion** | **Split** | See §4 — control plane vs execution plane fall on opposite sides of the rule. |
| Knowledge & data (vector/graph/SQL/doc stores) | Agnostic | Databases. Python writes the AI-side data; .NET writes control-plane metadata. |
| Governance / audit logging (cross-cutting) | **.NET** | Compliance system-of-record for a regulated-finance product. |
| Observability (cost/latency) | Agnostic | OpenTelemetry from both planes into one backend. |
| MCP layer (cross-cutting) | **Python**-led | Reference SDK + tools are Python; .NET can host MCP clients where it calls tools. |

---

## 4. The crux: splitting Ingestion

Ingestion is the only layer that genuinely straddles the rule, because it bundles two
different jobs:

**(a) Control plane — "decide *what* runs."** Tenant registry, manifest store, source
management, scheduling authority, run-history/status APIs, secret references, governance/
audit, and the *policy* of tenant isolation. This is a transactional enterprise
system-of-record with **no AI dependency** → by the rule, **.NET** (ASP.NET Core + EF Core,
Quartz.NET/Hangfire for scheduling, Postgres).

**(b) Execution plane — "do *how* it runs."** The connector plugins (`fetch`), preprocessing,
entity/relationship extraction, embedding, and graph construction. These depend on the
Python data/AI ecosystem and feed directly into retrieval/graph layers → **Python**.

> ADR 0001's current build puts **both** in Python. Under this ADR, the connectors + runner
> + fetch/transform stay Python (they become the execution worker); the tenant/manifest/
> schedule/status/audit surface is the natural .NET candidate.

### The seam (how the two planes talk)
- **Control → execution:** the .NET control plane is the scheduler-of-record. On each tick
  it dispatches a typed job — `{tenant_id, source_id, connector_type, config, secret_ref,
  since_cursor}` — to Python workers over a **queue** (preferred: durable, at-least-once,
  back-pressure) or HTTP/gRPC for synchronous dry-runs.
- **Dry-run on manifest submit (ADR 0001 D4):** the .NET API calls the Python worker's
  `test_connection` synchronously (HTTP/gRPC) and only persists on success — same UX, now
  cross-plane.
- **Execution → stores/agents:** Python workers write to the vector/graph/structured stores
  and expose retrieval as **MCP** tools to the agent layer.
- **Secrets:** the `secret_ref` indirection (ADR 0001 D5) is what makes the split safe — the
  job contract carries a *name*; the Python worker resolves the value from the vault. No raw
  credential crosses the wire.
- **Tenant isolation (ADR 0001 D7):** policy is owned by .NET (which tenant a job belongs
  to); enforcement stays in the Python writer (the bound `TenantOutputWriter`). Isolation is
  therefore enforced on *both* sides of the seam.

---

## 5. Impact on the existing build & migration options

The all-Python ingestion platform from ADR 0001 is working and verified. This ADR does not
discard it — it repositions it. Three ways to get there, cheapest first:

- **Path A — Strategy now, .NET gateway first, control plane later (recommended).**
  Keep the Python ingestion service as-is for V1. Stand up the **.NET API gateway** next
  (it's required anyway for auth + Office) and have it front the Python service. Lift the
  control-plane tables (tenants/sources/run_history) into a .NET service *later*, only when
  enterprise auth/audit/SLA requirements justify the second service. Lowest risk; preserves
  working code; makes .NET primary at the perimeter immediately.

- **Path B — Split now.** Build the .NET control plane (tenant/manifest/schedule/status/
  audit) immediately and refactor the Python service down to a stateless **execution worker**
  (registry + connectors + runner behind a job/HTTP contract). Truest to ".NET primary," but
  it's a real re-architecture of code that currently passes end-to-end.

- **Path C — Stay all-Python for ingestion.** Treat .NET as primary only for presentation/
  gateway/Office and leave the entire ingestion subsystem in Python. Least polyglot friction,
  one fewer service — but ingestion's control plane then lives outside the .NET enterprise
  spine, which is in tension with the "system-of-record → .NET" rule.

---

## 6. Consequences

**Positive**
- Each responsibility lands in the language that is decisively better for it; no layer is
  forced onto a weak ecosystem.
- The .NET/Python boundary is a network seam (queue/HTTP/MCP), so neither plane can entangle
  the other; each scales and deploys independently.
- `secret_ref` + write-layer isolation from ADR 0001 already make a cross-plane split safe.

**Negative / trade-offs**
- Polyglot = two toolchains, two CI lanes, a shared contract to version, and cross-language
  observability to stitch. Justified only because the AI ecosystem leaves no real choice.
- Splitting ingestion (Path B) means a job/dry-run contract and refactoring working code.
- A typed contract between .NET and Python (job schema, dry-run API) becomes a first-class
  artifact that must be kept in sync (e.g. OpenAPI/JSON Schema or protobuf as source of truth).

**Deferred to follow-up ADRs**
- The concrete transport (message queue vs gRPC) and the contract IDL.
- Whether presentation is Blazor vs a JS SPA.
- .NET-side auth/audit design (Entra ID, audit store).

---

## 7. Recommendation

Adopt the allocation in §3 and the ingestion split in §4 as the **target architecture**, and
reach it via **Path A**: keep the verified Python ingestion service, add the .NET gateway as
the next build, and migrate the ingestion control plane into .NET only when enterprise
requirements demand it. This makes .NET primary at the perimeter now without throwing away
working code, and keeps the AI/data work in Python where it has to be.
