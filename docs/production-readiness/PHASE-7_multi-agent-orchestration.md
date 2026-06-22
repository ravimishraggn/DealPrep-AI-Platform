# Production-Readiness & Operational Review — Phase 7 (Multi-Agent Orchestration)

- **Phase(s):** 7
- **Scope:** Five-agent pipeline (DocumentResearcher, StructuredAgent, GraphAgent, RiskScorer,
  SynthesisAgent) orchestrated by a lightweight asyncio fan-out/fan-in. New
  `POST /tenants/{id}/analyze` endpoint. LangGraph registered as a stub.
- **Roadmap ref:** [PRD](../PRD.md) §8 + §12 Phase 7
- **Implements:** [ADR 0013](../adr/0013-multi-agent-orchestration.md)
- **Reviews commit/branch:** `feat/ingestion-onboarding-platform` @ Phase 7 build
- **Date:** 2026-06-22
- **Lenses:** Principal Eng · SRE · Product · Security · Customer Success
- **Status:** Reviewed — verified live end-to-end

> **Live verification:** fresh tenant registered; sample JSON ingested through the full pipeline
> (vector + structured + graph indexed); `POST /analyze` returned risk_score=0.50 (medium),
> RiskScorer fired on `related_party_of` graph edge + adjustment language in vector chunks,
> SynthesisAgent produced template answer with 2 traceable citations; all 5 agents reported
> `status: success`, warm latencies 9–216ms. Template path confirmed (no API key set).

> Builds on [Phase 5–6 review](PHASE-5-6_retrieval-pipeline.md). The retrieval issues there
> (isolation, FTS params, ChromaDB race) are resolved; this review focuses on what Phase 7 adds.

---

## 0. TL;DR verdict & readiness scores

The agent orchestration layer is a thin, readable asyncio wrapper over the retrieval tools
built in Phases 5–6. The topology (3 parallel → 2 sequential) is correct, partial failure
handling works, and the rule-based risk scorer fires correctly on related-party signals. The
main operational risks are: no LLM cost controls, no per-call timeout, the template synthesis
fallback is functional but not production-grade for external users, and the MiniLM cold-start
adds ~50s to the first analyze request per process.

| Dimension | Score /10 | Note |
|---|---|---|
| Reliability | 6 | Partial failure isolation is good; no per-agent timeout; cold-start latency spike |
| Scalability | 3 | In-process asyncio; MiniLM loaded once per process; no concurrency limit on /analyze |
| Security | 2 | Inherits Phase 1–4 (no auth); SynthesisAgent sends corpus text to Claude if key present — prompt injection surface |
| Operability | 5 | Per-agent latency + status in response; no separate metrics/alerting for agent failures |
| Maintainability | 8 | Clean ABC hierarchy, registry pattern, typed contracts; LangGraph stub follows same pattern |
| Customer Experience | 6 | Single /analyze call, traceable citations, risk score; template answer not as useful as Claude synthesis |

**Go/No-Go:** **GO for internal design partners** (same condition as Phases 5–6); **NO-GO external**
until auth + cost controls + per-call timeout + Claude synthesis verified.

---

## 1. Critical issues (ship-blockers)

| # | Issue | Where | Why critical |
|---|---|---|---|
| 1 | No per-analyze-call timeout | `agents/orchestrators/sequential.py` | A slow Neo4j query or MiniLM embed call blocks the HTTP worker indefinitely; can exhaust the uvicorn thread pool |
| 2 | No LLM cost control | `agents/synthesis_agent.py` | When `DEALPREP_ANTHROPIC_API_KEY` is set, every `/analyze` call sends the full retrieval payload to Claude; no rate limit, no cost attribution, no per-tenant quota |
| 3 | No auth on `/analyze` | `app/routers/analyze.py` | Same as `/search` — any caller can analyze any tenant's data |
| 4 | Prompt injection via ingested text | `agents/synthesis_agent.py` | Corpus text is passed directly to the LLM prompt; a malicious document can instruct Claude to ignore the system prompt or leak data |

---

## 2. High-priority improvements

| # | Issue | Impact |
|---|---|---|
| 1 | `asyncio.wait_for()` timeout per agent | Prevents hung analyze requests from blocking workers |
| 2 | Template answer quality for external users | Template bullets are functional but not useful enough for non-technical analysts — Claude synthesis is the real product |
| 3 | MiniLM cold-start on first /analyze call | 50s first-call latency; warm with a dummy call at server startup |
| 4 | Concurrency guard on /analyze | Multiple simultaneous analyze requests each embed in the same thread; add semaphore or async queue |
| 5 | LangGraph orchestrator (stub → real) | Checkpointing, streaming, graph visualization; needed for production resilience |

---

## 3. Customer reality vs design assumptions

| # | Assumption | What customers actually do | Business impact |
|---|---|---|---|
| 1 | Analysts ask one well-formed question | Analysts paste a whole paragraph from a memo or ask ambiguous questions | Graph entity lookup by substring fails; risk scorer misses signals buried in long prose |
| 2 | Corpus is English, clean text | Source docs include tables, abbreviations, footnotes, cross-references | NER entity extraction misses abbreviations; related-party signals in footnotes missed |
| 3 | One analyze call per analyst per deal | Analysts iterate 10–20 times refining their question | Cost control becomes critical at scale; without it a single analyst can spend $5–10 on Claude calls in one session |

---

## 4. First 90 days

**Week 1:**
- Add `asyncio.wait_for()` timeout (30s) on each agent call — prevents request hang
- Warm MiniLM at server startup (one dummy embed call in lifespan)
- Add `X-Tenant-Id` + API key header auth to `/analyze`

**Month 1:**
- Per-tenant analyze quota + cost attribution
- Prompt injection mitigations (content filtering before LLM prompt construction)
- LangGraph orchestrator (at least a basic StateGraph replacing the asyncio fan-out)

**Month 3:**
- Streaming SSE response from SynthesisAgent (token-by-token)
- LLM-based RiskScorer replacing the rule-based V1
- MCP tool layer exposing retrieval as external tools for agent callers

---

## 5. Top customer escalations (predicted)

| # | Complaint | Root cause | Sev | Freq | Resolution |
|---|---|---|---|---|---|
| 1 | "First analysis took 50 seconds" | MiniLM cold-start on first embed | High | Every process restart | Warmup call in lifespan |
| 2 | "Answer says 'no data found' after I uploaded" | Pipeline run not complete when /analyze called | Med | Frequent | Wait for run_stages all-success before analyzing; surface run status in /analyze response |
| 3 | "Risk score is 0 but I know there's a related-party" | Entity name in graph doesn't match substring in query | Med | Occasional | Fuzzy entity matching; expand graph lookup |
| 4 | "Answer is just bullet points" | No API key configured → template fallback | Low | Frequent (dev) | Document clearly; template is for dev/CI only |

---

## 6. Production ownership

The analyst interface (`/analyze`) now depends on **five services**: Postgres, ChromaDB,
Neo4j, the MiniLM model on disk, and optionally the Anthropic API. The existing
`run_stages` table covers ingestion; there is no equivalent for analyze calls. On-call needs:
- A structured log line per analyze call (tenant, query, risk_score, per-agent latency, total)
- Alert on `synthesis_agent.status == "failed"` (LLM quota exhausted, bad API key)
- Alert on total analyze latency p95 > 10s

---

## 7. Integration risks

| Risk | Detail |
|---|---|
| Profile mismatch at analyze time | If a tenant's profile was changed between ingest and analyze, vectors are queried with the new embedder but were indexed with the old one — silent wrong results. Same as Phase 5–6 reindex risk. |
| Neo4j offline → graph agent fails | Graph agent fails gracefully (warning added); but risk score misses all graph signals — may understate risk. Surface Neo4j health in /health or /capabilities. |
| Anthropic API key rotation | If key rotates mid-session, SynthesisAgent falls back to template silently. Retry with the new key; surface LLM status in agent_results. |

---

## 8. Technical debt created by Phase 7

- `SequentialOrchestrator` instantiates all registered agents at startup (including any
  future agents that need DB/network at init time). Should lazy-instantiate.
- `SynthesisAgent._build_prompt()` constructs a raw JSON string from retrieval results —
  no truncation guard for large corpora. A 500KB chunk corpus will exceed model context.
- RiskScorer regex patterns are hardcoded strings. Should be configurable per domain
  (PE/VC patterns differ from credit risk or compliance).

---

## 9. Top production risks (ranked)

| # | Risk | Prob. | Bus. impact | Mitigation difficulty |
|---|---|---|---|---|
| 1 | Prompt injection via corpus text | Medium | High (data leak, instruction override) | Medium (content filtering layer) |
| 2 | No LLM cost control → runaway spend | High (dev/internal) | Medium ($10–100/day per active user) | Low (add per-tenant quota) |
| 3 | Three-store divergence (existing) | Medium | High (wrong answers, missed signals) | High (needs reconciliation tool) |
| 4 | /analyze hangs on slow Neo4j query | Low | Medium (thread pool exhaustion) | Low (asyncio.wait_for timeout) |

---

## 10. Lessons only learned running it live

1. **MiniLM cold-start dominates first-call latency** (52s). In tests with fake embedders
   this is invisible. Only visible when running the real stack end-to-end. Warmup call at
   server startup is mandatory for acceptable UX.

2. **RiskScorer fires correctly but incompletely** — the rule-based `related_party_of`
   signal works because the graph builds the right edge from NER. But the EBITDA adjustment
   signal (+0.25) requires the keywords to appear in vector chunk text, not in the structured
   record. Analysts often store adjustments in structured tables only; the rule misses those.

3. **The template answer is actually useful for debugging** (shows exactly what each agent
   found, labeled by agent) but is too raw for an analyst. The value of Claude synthesis is
   not decoration — it surfaces the *why* that connects the three result sets.

4. **Graph entity lookup by substring is fragile** for short company names. "Acme" in the
   query hits "Acme Corp" in the graph. "ACME" (all caps) would not. The `lower()` normalization
   helps but full fuzzy matching is needed for real deal data.

---

## 11. Recommended actions feeding Phase 8

| Action | Effort | Prevents | Tracked by |
|---|---|---|---|
| Per-agent asyncio timeout | Small | Request hang / thread exhaustion | ADR 0013 follow-up |
| MiniLM warmup in lifespan | Trivial | 50s first-call latency | — |
| LLM cost quota per tenant | Medium | Runaway spend | Phase 8 |
| Prompt injection content filter | Medium | Data leak via LLM | Phase 8 security |
| LangGraph orchestrator (real) | Large | Checkpointing, streaming, visualization | ADR 0013 stub → implemented |
| Structured log per analyze call | Small | Observability gap for /analyze | Phase 8 |

---

## what major challenges i have solved, need story in layman language

**The hardest problem: making five independent workers cooperate without any of them knowing the others exist.**

Imagine you send out three researchers to three different libraries at the same time — one to a book of financial summaries, one to a spreadsheet of deal numbers, one to a relationship map of who owns what. Each researcher works independently and comes back with their own pile of notes. Then a fourth person scores "how risky is this?" based on all three piles, and a fifth person writes the final explanation.

The engineering challenge is: how do you start all three library trips simultaneously (so you don't wait for each one to finish before starting the next), and how do you pass each researcher's notes to the next person in the right order? Do it wrong and you waste minutes; do it wrong in a subtler way and the risk scorer sees partial results and gives you a wrong risk score.

The solution is Python's `asyncio.gather()` — a mechanism that says "start all three at once, and tell me when the last one finishes." Once all three finish, the results flow in a controlled order to the risk scorer and then the synthesis writer. The agent contracts (`BaseAgent.run()` → `AgentResult`) ensure each researcher always hands back notes in the same format, so the synthesis writer doesn't need to know whether it's talking to the library-book researcher or the spreadsheet researcher.

**The second hard problem: the system works without a brain.**

The synthesis agent that writes the final explanation optionally calls Claude (the LLM). But what if the Claude API key isn't set? Rather than crashing or returning nothing, the system uses a deterministic template — it bullet-points each agent's findings in a human-readable way. This means the platform works for development, testing, and demos with zero external dependencies, and upgrades to a real narrative answer the moment a key is configured. Same pattern as the graph extractor in Phase 6: the hard work is right, the LLM just makes it prettier.
