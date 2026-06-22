# ADR 0009 — Chunking strategy: pluggable, and how to choose one

- **Status:** Accepted
- **Date:** 2026-06-22
- **Deciders:** Platform team (owns the registry + defaults), tenant teams (select within it)
- **Type:** Decision framework (not a one-time pick)

## Context

Chunking decides the unit of retrieval. The "right" chunker depends on the document
*shape* and the retrieval goal, and no single strategy is best for every tenant: a 10-K
filing wants structure-aware splits; a chat transcript wants sentence windows; a uniform log
stream wants fixed windows. Hard-coding one chunker would force a quality compromise on most
tenants. We need (a) chunking to be a **pluggable strategy**, and (b) a **documented rule for
who chooses which, and when**.

## Decision

Chunking is a **registry of strategies** (`pipeline/chunking/`), each registered by name via
`@register_chunker` and selected per tenant through the pipeline profile (ADR 0012). A
strategy is added by dropping a module — no core change. Stubs register but raise on use, so
the platform can advertise a roadmap option without pretending it works.

**Strategies shipped:**

| Strategy | Status | Boundary logic | Best for | Cost |
|---|---|---|---|---|
| `section_aware` (default) | ✅ real | headers + paragraph breaks, in-section overlap | structured prose: filings, memos, reports | low |
| `sentence_window` | ✅ real | whole sentences up to a size budget, sentence overlap | unstructured prose: transcripts, news, notes | low |
| `fixed_size` | ✅ real | uniform char windows + overlap, ignores structure | homogeneous/log-like text, predictable chunk counts | lowest |
| `semantic` | 🟡 stub | embedding-similarity drop between sentences | topically dense docs where structure is weak | high (embeds every sentence) |

### Who chooses, and when
- **Platform team** owns the registry, sets the **default** (`section_aware`), and decides
  which strategies are *approved* for production (stubs are not).
- **Tenant team** selects an approved strategy in their pipeline profile based on their
  dominant document shape. They cannot select a stub.
- **Revisit** when: retrieval quality is poor for a tenant (faithfulness/recall metrics),
  the dominant document type changes, or chunk-count-driven cost needs bounding.

### Selection rule of thumb
1. Documents have headings/sections → **section_aware**.
2. Flowing prose without structure → **sentence_window**.
3. Uniform/structureless or cost must be perfectly predictable → **fixed_size**.
4. Structure is weak but topics shift mid-document and quality justifies the cost →
   **semantic** (once implemented).

## Consequences

**Positive**
- Each tenant gets chunking matched to its corpus without core changes.
- Changing a chunker is a profile change; the boundary logic is isolated and testable.
- The stub pattern lets the platform show breadth for POCs without shipping half-working code.

**Negative / trade-offs**
- Changing a tenant's chunker **invalidates existing chunks** → a reindex (chunk ids/text
  change). Treated as a migration, not a hot swap.
- More strategies = more surface to test and document.
- Per-tenant variation makes "why did retrieval differ?" harder to debug — mitigated by
  recording the chosen strategy in the profile and (future) on each chunk's metadata.
