# ADR 0010 — Embedding backend: pluggable, and how to choose one

- **Status:** Accepted
- **Date:** 2026-06-22
- **Deciders:** Platform team (registry + defaults + approval), tenant teams (select within it)
- **Type:** Decision framework

## Context

The embedding model determines retrieval quality, recurring cost, latency, vector dimension,
and — critically for regulated finance — whether document text **leaves the environment**.
Different tenants have different constraints: a default tenant wants free local embeddings; an
AWS-resident tenant with a data-residency mandate wants Bedrock in-region; a tenant chasing
top recall may accept paid API embeddings. One hard-coded embedder cannot serve all three.

## Decision

Embedding is a **registry of backends** (`pipeline/embedding/`), each registered by name and
selected per tenant via the pipeline profile (ADR 0012). The vector store is dimension-aware
but model-agnostic. Stubs register but raise on use.

**Backends shipped:**

| Backend | Status | Dim | Data leaves env? | Cost | Best for |
|---|---|---|---|---|---|
| `minilm` (default) | ✅ real | 384 | no (local) | free | general use; private/regulated; default |
| `hashing` | ✅ real | 256 | no (local) | ~free | offline/air-gapped, CI, POC, torch-less fallback |
| `openai` | 🟡 stub | 1536 | **yes** | per-token | max recall when egress is acceptable |
| `bedrock` | 🟡 stub | 1024 | within AWS | per-token | AWS-resident, in-region data residency |

### Who chooses, and when
- **Platform team** owns the registry, sets the default (`minilm`), and **approves** backends
  for production — notably gating any backend that sends text to a third party against the
  tenant's data-classification/compliance posture.
- **Tenant team** selects an approved backend for their quality/cost/residency needs.
- **Security/compliance** must sign off before a tenant uses a backend that egresses text.
- **Revisit** when: retrieval recall is insufficient, embedding cost grows, a residency
  requirement appears, or a materially better model ships.

### Selection rule of thumb
1. Private/regulated data, no egress allowed → **minilm** (default).
2. Offline / air-gapped / CI / no torch → **hashing**.
3. Recall matters more than cost *and* egress is approved → **openai** (once implemented).
4. AWS-resident with in-region residency requirement → **bedrock** (once implemented).

## Consequences

**Positive**
- Each tenant gets embeddings matched to its quality/cost/compliance needs.
- The default keeps data local and free — the safe choice for finance.
- `hashing` guarantees the platform runs with **zero** model/network dependencies (POC/CI).

**Negative / trade-offs**
- **Dimension is part of a backend's identity.** Switching backends changes `dim`, which
  **invalidates a tenant's existing vectors** → a full reindex into a fresh collection. Never
  a hot swap. (The vector store keys collections per tenant; a reindex writes a new one.)
- Mixed backends across tenants complicate cost accounting and capacity planning.
- Third-party backends add API keys, rate limits, retries, and an egress-audit obligation.
