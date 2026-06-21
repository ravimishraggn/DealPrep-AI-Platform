
# DealPrep AI Platform — Product Requirements Document

**Version:** 1.0 (Draft)
**Owner:** Ravi Mishra
**Status:** Planning

> **Implementation:** Phases 1–4 (the self-service ingestion platform) are built and runnable.
> See **[PLATFORM_README.md](PLATFORM_README.md)** to run it locally,
> [ADR 0001](docs/adr/0001-data-ingestion-onboarding-platform.md) for the architecture decisions,
> and the **[Phase 1–4 production-readiness review](docs/production-readiness/PHASE-1-4_ingestion-platform.md)**
> for predicted operational reality. Each phase gets its own review — see
> [docs/production-readiness/](docs/production-readiness/README.md).

---

## 1. Executive Summary

DealPrep AI Platform is a multi-agent, AI-powered platform that helps private markets analysts (PE/VC) quickly understand *why* valuation numbers and comparable companies don't match across sources — work that today takes days of manual digging.

It is built as a **platform**, not a single app: any team can onboard their own data sources through a simple registration and configuration process, and the system handles ingestion, retrieval, reasoning, and reporting automatically.

---

## 2. Problem Statement

When a PE analyst values a target company, they compare it against "comparable" companies using metrics like EV/EBITDA multiples. In practice:

- Numbers from different sources (filings, deal databases, news) rarely match cleanly
- Reasons for mismatch are often buried in footnotes (accounting adjustments) or hidden relationships (shared investors, board overlaps, related-party transactions)
- Analysts manually cross-reference dozens of documents to explain a single discrepancy
- This takes days per deal and is easy to get wrong under deadline pressure

**In simple terms:** analysts need a system that doesn't just fetch numbers, but explains *why* numbers differ — including connections between companies that aren't obvious from a single document.

---

## 3. Goals

| Goal | Description |
|---|---|
| Reduce reconciliation time | Cut "why don't these numbers match" investigation from days to hours |
| Surface hidden connections | Detect relationships (shared investors, board overlaps) that explain valuation anomalies |
| Build a reusable platform | Any team should be able to onboard a new data source without writing pipeline code |
| Full traceability | Every answer must trace back to its exact source document (required for regulated finance) |

---

## 4. Target Users

- **Primary:** PE/VC analysts and associates doing due diligence
- **Secondary:** Other internal teams who want to plug their own data sources into the same platform (e.g., credit risk, compliance teams)

---

## 5. Core Use Case (V1 anchor scenario)

**"Valuation Discrepancy Detective"**

> Analyst asks: *"Why is Company A trading at a higher multiple than its comps?"*

The system:
1. Pulls valuation commentary and adjustments from filings (vector search)
2. Cross-checks exact financial figures and deal terms (structured search)
3. Traces ownership/board relationships between Company A and its "comps" (knowledge graph)
4. Returns a plain-language explanation, not just numbers — e.g., *"Company A's reported EBITDA includes $12M in revenue from a related entity owned by the same sponsor; once normalized, the multiple aligns with peers."*

---

## 6. In Scope / Out of Scope (V1)

**In scope:**
- SEC filings, deal/comps data, and structured cap table data as initial sources
- Multi-agent orchestration (research, structured-data check, relationship trace, synthesis)
- Self-service onboarding for new data sources (config-driven, no-code)
- Full source traceability on every answer

**Out of scope (later phases):**
- Real-time market data feeds
- Automated investment recommendations (system explains discrepancies; it does not make buy/sell calls)
- Non-English document sources

---

## 7. System Architecture (Layered)

| Layer | What it does | Built in |
|---|---|---|
| Presentation | Chat UI, dashboard, Excel/PPT plugin | .NET |
| API Gateway | Auth, routing, request validation — single front door | .NET |
| Orchestration | Decides which agents run, fan-out vs sequential, merges results | Python (LangGraph) |
| Agent Layer | Specialist agents: Document Researcher, Numbers Checker, Graph Agent, Risk Scorer, Report Writer | Python |
| Retrieval/Tooling | Vector RAG, Hybrid RAG, GraphRAG — tools the agents call | Python (exposed via MCP) |
| Knowledge & Data | Vector store, graph database, structured database, raw document store | Platform data layer |
| Ingestion | Acquisition, preprocessing, entity extraction, graph construction | Python |

**Cross-cutting concerns (apply to every layer):** governance/audit logging, observability (cost/latency tracking), security, and the MCP layer that standardizes how tools are exposed.

---

## 8. Multi-Agent Orchestration

- **Pattern:** Fan-out / fan-in
- **Fan-out:** independent sub-tasks run in parallel — e.g., Document Researcher and Graph Agent both run at the same time since neither needs the other's output
- **Fan-in:** a synthesis agent waits for all parallel results, reconciles them, and produces one answer
- **Sequential exception:** if an agent's input depends on another agent's output (e.g., Risk Scorer needs the Graph Agent's findings first), the orchestrator routes that as a chain instead of running in parallel

---

## 9. Data Ingestion (Self-Service Platform Layer)

**Goal:** any team should onboard a new data source by registering and filling out a configuration — not by writing code.

**Key components:**
- **Tenant registration** — teams sign up, get a namespace/tenant ID
- **Connector plugin registry** — reusable, pre-built connectors (REST API, file upload, SEC EDGAR, etc.); new source types added as plugins
- **Configuration manifest** — describes source type, connection details (secret reference only, never raw credentials), schedule, and routing
- **Validation & dry-run** — config is validated and test-connected before going live
- **Generic pipeline runner** — one engine processes any team's manifest using the right connector
- **Tenant isolation** — every record tagged by tenant so data doesn't mix across teams

**Full ingestion steps:** acquisition → preprocessing/cleaning → parallel paths for (a) unstructured documents → vector RAG, (b) structured data → hybrid RAG, (c) entity/relationship extraction → knowledge graph — all wrapped in lineage logging, data quality checks, and change detection.

---

## 10. Knowledge Graph (Core Schema)

**Entities (nodes):** companies, investors/sponsors, board members, executives, deals/transactions

**Relationships (edges):** invested-in, board-member-of, co-invested-with, competitor-of, prior-round-led-by, advisor-to, related-party-of

**Why it matters:** flat document search can't answer "is this comp actually independent, or does it share an investor with the target company?" — that requires traversing relationships, which only a graph can do efficiently.

---

## 11. Success Metrics

| Category | Metric |
|---|---|
| Retrieval quality | Faithfulness and answer relevancy (RAGAS-style scoring) |
| Entity accuracy | Entity resolution precision/recall across sources |
| Trust | Hallucination rate vs. single-agent RAG baseline |
| Business impact | Time-to-insight reduction vs. manual analyst process |
| Graph value-add | % of indirect-exposure risks/discrepancies caught only via graph traversal (not visible to flat search) |
| Platform health | Cost-per-query, onboarding time for a new team/data source |

---

## 12. Phased Roadmap

| Phase | Deliverable |
|---|---|
| Phase 1 | Tenant registration + manifest schema |
| Phase 2 | One working connector (REST API) to prove the plugin pattern |
| Phase 3 | Secrets vault integration + dry-run validation |
| Phase 4 | Generic pipeline runner, works across any registered tenant |
| Phase 5 | Vector RAG + Hybrid RAG live on ingested data |
| Phase 6 | Knowledge graph construction from extracted entities/relationships |
| Phase 7 | Multi-agent orchestration layer (fan-out/fan-in) wired to all retrieval tools |
| Phase 8 | Dashboard, monitoring, governance/audit logging |

---

## 13. Open Questions

- Which structured deal/comps data source is the first real connector target?
- What's the minimum viable knowledge graph schema for V1 — full entity set above, or a smaller starting subset?
- Should the dry-run/validation step include a human approval gate, or fully automated for V1?

---

*This document is meant to be a living reference — update it as architecture decisions are finalized during the build.*