# ADR 0017 — Agent Orchestration Evaluation Framework

| Field | Value |
|---|---|
| **Status** | Proposed |
| **Date** | 2026-06-23 |
| **Deciders** | Platform Engineering, ML Quality |
| **Phase** | 8 — Evaluation & Observability |
| **Relates to** | ADR 0013 (orchestration), ADR 0015 (guardrails + audit), ADR 0016 (cost) |

---

## Context

The pluggable backends (extractors, chunkers, embedders, vector stores) each have evaluation
runbooks in `docs/evaluation/`.  The **orchestration layer** — the part that coordinates agents,
synthesises answers, scores risk, manages memory, and routes to HITL — has no equivalent
quality gate.

This matters because orchestration failures are qualitatively different from component failures:

| Component failure | Orchestration failure |
|---|---|
| ChromaDB returns wrong chunks | Synthesis agent ignores correct chunks from all three stores |
| Embedder produces wrong vectors | Fan-out completes but fan-in reducer discards some results |
| Risk scorer over-flags | Risk scorer fires HITL but human review feedback is not propagated to synthesis |
| — | Long-term memory loads wrong tenant's history |
| — | Session_id collision causes two analysts to share state |
| — | Parallel agents write to the same state key, last-write-wins silently drops data |

Component evals catch individual agent quality.  Only an **orchestration-level eval** catches how
agents interact, how state flows across nodes, and whether the final answer is trustworthy.

### Reference: How Snowflake Evaluates Cortex Agents

Snowflake's Cortex Agent evaluation (internal) measures:
- **Tool selection accuracy** — did the agent call the right tool for the query?
- **Answer groundedness** — is every claim in the answer supported by a retrieved document?
- **Latency SLA** — did the response arrive within the operator-defined timeout?
- **Cost per query** — did the query stay within the tenant's Cortex Budget?
- **Human review trigger rate** — is the HITL threshold calibrated to the right precision/recall?

DealPrep must implement equivalent measurements plus three areas Cortex does not expose:
knowledge-graph contribution, memory correctness, and checkpoint recovery.

---

## Decision

Define a **five-dimension evaluation framework** for the orchestration layer, with automated
test suites for each dimension.  Each dimension has a **pass gate** that must hold before the
orchestration layer is marked production-ready for a new deployment.

The framework produces a single **Orchestration Quality Score (OQS)** that aggregates all
dimensions on a 0–100 scale.  OQS ≥ 75 is the production gate.

---

## The Five Dimensions

---

### Dimension 1 — Answer Quality (Groundedness + Accuracy)

**What it measures:** Is the synthesised answer correct, and is every factual claim supported by
retrieved evidence?

**Why it matters:** An LLM can produce a confident, fluent, completely invented answer.
Groundedness testing is the only reliable way to catch this.

#### 1A — Groundedness Test

For each golden question in the test set, verify that every numeric value and named entity in
the answer can be found verbatim (or near-verbatim) in at least one `retrieved_chunk`,
`retrieved_record`, or `retrieved_triple` that was passed to the synthesis node.

```python
def groundedness_score(answer: str, evidence: list[dict]) -> float:
    """
    Returns: fraction of answer sentences that have at least one supporting evidence item.
    Method: sentence-level NLI classification (or exact-string check for numbers).
    """
    sentences = split_sentences(answer)
    supported = 0
    for sentence in sentences:
        for item in evidence:
            if _is_supported(sentence, item):
                supported += 1
                break
    return supported / len(sentences) if sentences else 0.0
```

**Pass gate:** groundedness_score ≥ **0.80** across the golden question set.

Practical implementation for Phase 8 (no NLI model available):
- Numbers: every numeric value in the answer must appear in at least one retrieved record field.
- Named entities: every ORG/PERSON named in the answer must appear in at least one chunk or triple.
- Verified via exact string match (sufficient for financial data; hallucinated $42.7M will not
  appear verbatim in any retrieved item).

#### 1B — Golden Question Accuracy

Maintain a **golden question set** (GQS) of 30 questions with known correct answers, drawn from
synthetic deal-room documents created for testing:

| Question type | Count | Example |
|---|---|---|
| Point lookup | 10 | "What is Acme Corp's EBITDA for FY2024?" |
| Comparison | 5 | "Which of PortCo A and PortCo B has higher revenue?" |
| Trend | 5 | "Did Acme Corp's EBITDA margin improve from 2022 to 2024?" |
| Risk flag | 5 | "Are there related-party transactions between Acme Corp and its CFO?" |
| Multi-store | 5 | "What does the knowledge graph say about Acme Corp's ownership, and does the financial data corroborate it?" |

For each question, verify the answer contains the expected value / entity / conclusion.

**Pass gate:** correct answers on ≥ **85%** of golden questions.

---

### Dimension 2 — Agent Coordination (Fan-out / Fan-in Integrity)

**What it measures:** Did the parallel fan-out execute all eligible agents?  Did the fan-in
reducer preserve all agent outputs without data loss?

#### 2A — Fan-out Completeness

For each completed analysis, assert that every *eligible* agent produced a result:

```python
def assert_fanout_completeness(outcome: AnalysisOutcome, eligible: set[str]) -> None:
    for agent_name in eligible:
        assert agent_name in outcome.state.results, f"{agent_name} missing from results"
        assert outcome.state.results[agent_name].status in ("success", "failed"), \
            f"{agent_name} has unexpected status"
```

An agent that was skipped due to eligibility check is excluded from `eligible`.  An agent that
was eligible but absent from `results` is a **fan-out bug**.

**Pass gate:** 100% fan-out completeness across 100 test runs (zero eligible agents missing).

#### 2B — Reducer Integrity (No Data Loss Under Concurrency)

The `OrchestratorState` reducers (`operator.add` for lists, `_merge_dicts` for dicts) must be
proven safe under concurrent writes.  Test by injecting synthetic concurrent updates:

```python
async def test_reducer_no_data_loss():
    """Run 3 agents concurrently 50 times; assert no retrieved_chunks are dropped."""
    for _ in range(50):
        outcome = await orchestrator.analyze(ctx)
        total_chunks = len(outcome.state.results.get("document_researcher", {}).get("chunks", []))
        assert total_chunks == expected_chunk_count, "Reducer dropped chunks"
```

Also test: two agents simultaneously writing to `warnings` — assert both warnings appear in
final state (not just the last one written).

**Pass gate:** zero data loss across 50 concurrent runs.

#### 2C — Agent Failure Isolation

If one retrieval agent fails (raises), the other two must still complete and synthesis must
proceed with a degraded-but-non-empty answer:

```python
async def test_graph_agent_failure_isolation():
    # Force graph_agent to raise
    with mock.patch("agents.graph_agent.Neo4jClient.find_relationships", side_effect=RuntimeError):
        outcome = await orchestrator.analyze(ctx)
    assert outcome.state.results["graph_agent"].status == "failed"
    assert outcome.state.results["document_researcher"].status == "success"
    assert outcome.state.answer is not None  # synthesis ran despite graph failure
    assert any("graph_agent" in w for w in outcome.state.warnings)
```

**Pass gate:** synthesis completes with a non-None answer even when any single retrieval agent
fails (tested for each of the three agents independently).

---

### Dimension 3 — Risk Scorer Calibration

**What it measures:** Is the risk scorer triggering at the right threshold?  High false-positive
rate wastes analyst time.  High false-negative rate lets real discrepancies through.

#### 3A — Precision / Recall on Labelled Documents

Create a **risk labelled set** of 40 synthetic deal-room scenarios:
- 20 "truly risky" (related-party transactions, revenue restatements, pro-forma add-backs in
  excess of 30% of EBITDA).
- 20 "not risky" (clean financials, no related-party signals, stable EBITDA).

Run the risk scorer on all 40 and compute:

```python
precision = true_positives / (true_positives + false_positives)
recall    = true_positives / (true_positives + false_negatives)
f1        = 2 * precision * recall / (precision + recall)
```

**Pass gate:** precision ≥ **0.75**, recall ≥ **0.70**, F1 ≥ **0.72**.

#### 3B — HITL Trigger Rate Calibration

In production, the HITL gate fires when `risk_score ≥ 0.7`.  This threshold must be calibrated:
too low → analysts review every query (fatigue, cost); too high → real risks pass without review.

Track over a 30-day period:
- Total analyses run.
- HITL trigger count.
- Of HITL-triggered: how many were approved (true positive) vs rejected as false alarm.

**Target:** HITL trigger rate between **5% and 15%** of all queries; of those, ≥ 70% result in
analyst approval (not rejection as false alarm).

Adjustment mechanism: if trigger rate exceeds 15%, raise threshold to 0.75.  If trigger rate is
below 5% and any false negatives are found in post-hoc review, lower threshold to 0.65.

#### 3C — Risk Signal Attribution

For every HITL-triggered analysis, verify that `risk_signals` contains at least one human-
readable signal string (not an empty list).  An empty `risk_signals` with a high `risk_score`
is a scorer bug.

**Pass gate:** 100% of analyses with `risk_score ≥ 0.5` must have `len(risk_signals) ≥ 1`.

---

### Dimension 4 — Memory Correctness (Short-term + Long-term)

**What it measures:** Does state flow correctly within a session (short-term) and across sessions
(long-term)?  Memory bugs can cause tenant data contamination — the most serious failure mode.

#### 4A — Short-term Memory (Checkpoint / Resume)

Test the full HITL interrupt → resume cycle:

```python
async def test_checkpoint_resume_correctness():
    # Step 1: run analysis that triggers HITL
    outcome = await lg_orchestrator.analyze(high_risk_ctx)
    assert outcome.interrupted is True
    session_id = outcome.session_id

    # Step 2: verify checkpoint state is persisted
    status = lg_orchestrator.get_checkpoint_status(tenant_id, session_id)
    assert status["status"] == "interrupted"
    assert status["risk_score"] >= 0.7

    # Step 3: resume with approval
    resumed = await lg_orchestrator.resume(tenant_id, session_id, approved=True, feedback="Verified.")
    assert resumed.interrupted is False
    assert resumed.state.answer is not None
    # Step 4: verify human_feedback was forwarded to synthesis
    assert "Verified." in resumed.state.answer or resumed.state.warnings == []
```

**Pass gate:** 100% of interrupted sessions resume to a correct completed state.

#### 4B — Thread ID Isolation (No State Bleed)

Run two concurrent analyses for different tenants with the same session_id and verify that each
session sees only its own state:

```python
async def test_thread_id_isolation():
    async with asyncio.TaskGroup() as tg:
        t1 = tg.create_task(lg_orchestrator.analyze(ctx_tenant_A, session_id="shared-id"))
        t2 = tg.create_task(lg_orchestrator.analyze(ctx_tenant_B, session_id="shared-id"))
    outcome_A, outcome_B = t1.result(), t2.result()
    assert outcome_A.state.context.tenant_id == "T-A"
    assert outcome_B.state.context.tenant_id == "T-B"
    # Verify no cross-contamination
    assert outcome_A.state.results.keys() == outcome_B.state.results.keys()  # same structure
    # If A is high-risk and B is low-risk, their risk_scores must differ
```

**Pass gate:** zero state bleed across 100 concurrent session pairs.

#### 4C — Long-term Memory Accuracy

Verify that `load_memory_node` loads the correct tenant's history (not another tenant's):

```python
async def test_long_term_memory_tenant_isolation():
    # Seed 5 analyses for T-A and 5 for T-B into analysis_history
    prior_A = await asyncio.to_thread(memory_store.load_recent, "T-A", 5)
    prior_B = await asyncio.to_thread(memory_store.load_recent, "T-B", 5)
    assert all(r["session_id"].startswith("T-A-") for r in prior_A)
    assert all(r["session_id"].startswith("T-B-") for r in prior_B)
    assert set(r["session_id"] for r in prior_A).isdisjoint(set(r["session_id"] for r in prior_B))
```

Also verify recency: `prior_analyses[0]` must be the most recently saved analysis for the tenant.

**Pass gate:** 100% isolation + correct ordering across 20 tenant pairs.

#### 4D — Memory Staleness (MemorySaver Leak)

`MemorySaver` stores checkpoints in-process RAM.  Under a long-running server with many
sessions, this can grow without bound.  Measure:

```python
def test_memory_saver_bounded():
    import tracemalloc
    tracemalloc.start()
    for i in range(200):
        await orchestrator.analyze(ctx, session_id=str(i))
    current, peak = tracemalloc.get_traced_memory()
    assert peak < 500 * 1024 * 1024  # < 500 MB for 200 sessions
```

**Pass gate:** peak RAM for 200 sessions < 500 MB.  If exceeded, add `MemorySaver` TTL eviction
(discard checkpoints for sessions older than 24 hours).

---

### Dimension 5 — Latency & Cost SLA

**What it measures:** Does the orchestration layer meet the latency and cost targets that make
it usable in a live due-diligence session?

#### 5A — End-to-End Latency

| Orchestrator | Scenario | p50 target | p95 target |
|---|---|---|---|
| `sequential` | All agents healthy, no LLM | < 800 ms | < 1.5 s |
| `sequential` | All agents healthy, with LLM (Haiku) | < 2 s | < 4 s |
| `langgraph` | All agents healthy, no LLM | < 1.2 s | < 2.5 s |
| `langgraph` | All agents healthy, with LLM (Sonnet) | < 4 s | < 8 s |
| `langgraph` | HITL interrupted (before resume) | < 2 s | < 3 s |

Measurement: `agent_timings` dict in `OrchestratorState` captures per-node latency.  A test
harness aggregates over 50 runs per scenario.

**Pass gate:** p95 latency within table targets above for all scenarios.

#### 5B — Fan-out Parallelism Efficiency

The three retrieval agents run in parallel.  Their combined wall-clock time must be close to
`max(t_doc, t_struct, t_graph)`, not `sum(t_doc, t_struct, t_graph)`.  Define:

```
parallelism_efficiency = max(agent_latencies) / total_fanout_wall_time
```

A ratio ≥ **0.75** means the parallel execution is delivering ≥ 75% of the theoretical speedup.
A ratio below 0.50 suggests the event loop is serialising the agents (e.g. blocking I/O on the
main thread rather than `asyncio.to_thread`).

**Pass gate:** parallelism_efficiency ≥ **0.75** across 20 runs with all three agents healthy.

#### 5C — Cost per Query

Using `CostMeter` (ADR 0015) output, track actual LLM token cost per query type:

| Query type | Input tokens (est.) | Output tokens (est.) | Cost target (Haiku) | Cost target (Sonnet) |
|---|---|---|---|---|
| Low-risk (no LLM synthesis) | 0 | 0 | $0.00 | $0.00 |
| Low-risk with Haiku synthesis | ~600 | ~200 | < $0.0002 | N/A |
| Medium-risk with Sonnet synthesis | ~800 | ~300 | N/A | < $0.003 |
| High-risk HITL + Sonnet synthesis | ~1,000 | ~400 | N/A | < $0.004 |

**Pass gate:** 95th-percentile cost per query within targets above.

---

## Orchestration Quality Score (OQS)

The OQS is a weighted aggregate of the five dimension scores:

| Dimension | Weight | Score (0–100) | Weighted |
|---|---|---|---|
| 1 — Answer Quality | 35% | pass_rate × 100 | |
| 2 — Agent Coordination | 25% | pass_rate × 100 | |
| 3 — Risk Scorer Calibration | 15% | F1 × 100 | |
| 4 — Memory Correctness | 15% | pass_rate × 100 | |
| 5 — Latency & Cost SLA | 10% | pass_rate × 100 | |
| **OQS** | 100% | | **≥ 75 required** |

Any dimension scoring **0** (complete failure) blocks production regardless of OQS — no amount
of high groundedness compensates for zero fan-out completeness or zero memory isolation.

---

## Evaluation Infrastructure

### Golden Dataset

Synthetic deal-room documents created for testing — never real client data:
- 5 companies (Acme Corp, BetaCo, GammaTech, DeltaFund, EpsilonCap)
- 3 document types per company (CIM / pitch deck as PDF, financial model as CSV, cap table as JSON)
- 2 "scenario" variants per company (clean + risky)
- Stored in `tests/fixtures/golden_deal_room/`

Golden question set (`tests/fixtures/golden_questions.json`):
```json
[
  {
    "id": "GQ-001",
    "question": "What is Acme Corp's EBITDA for FY2024?",
    "expected_value": "12.4",
    "expected_unit": "million USD",
    "type": "point_lookup",
    "requires_agents": ["structured_agent", "document_researcher"]
  },
  ...
]
```

### Test Harness

```python
# tests/evaluation/test_orchestration_eval.py

class OrchestrationEvalSuite:
    """Full five-dimension evaluation suite."""

    async def run_all(self) -> OQSReport:
        d1 = await self.eval_answer_quality()
        d2 = await self.eval_agent_coordination()
        d3 = await self.eval_risk_scorer()
        d4 = await self.eval_memory_correctness()
        d5 = await self.eval_latency_cost()
        return OQSReport(dimensions=[d1, d2, d3, d4, d5])

    async def eval_answer_quality(self) -> DimensionResult: ...
    async def eval_agent_coordination(self) -> DimensionResult: ...
    async def eval_risk_scorer(self) -> DimensionResult: ...
    async def eval_memory_correctness(self) -> DimensionResult: ...
    async def eval_latency_cost(self) -> DimensionResult: ...
```

Run with: `pytest tests/evaluation/ -v --tb=short`

The eval suite is added to CI as a **weekly scheduled job** (not per-commit — it requires the
full Docker stack and takes ~10 minutes).  A regression from a previously passing OQS triggers
a Slack alert.

### OQS Report Output

```
┌──────────────────────────────────────────────────────────┐
│  DealPrep Orchestration Quality Score (OQS)              │
│  Run date: 2026-06-23  Orchestrator: langgraph           │
├──────────────────┬──────────┬──────────┬─────────────────┤
│  Dimension       │  Score   │  Weight  │  Weighted score │
├──────────────────┼──────────┼──────────┼─────────────────┤
│  Answer quality  │  88/100  │  35%     │  30.8           │
│  Coordination    │  96/100  │  25%     │  24.0           │
│  Risk calibration│  78/100  │  15%     │  11.7           │
│  Memory          │  100/100 │  15%     │  15.0           │
│  Latency & cost  │  82/100  │  10%     │   8.2           │
├──────────────────┴──────────┴──────────┼─────────────────┤
│                              OQS       │  89.7  ✅ PASS  │
└────────────────────────────────────────┴─────────────────┘
Pass gates:
  ✅  Groundedness ≥ 0.80    (actual: 0.87)
  ✅  Golden QA ≥ 85%         (actual: 88%)
  ✅  Fan-out completeness    (100 / 100 runs)
  ✅  Reducer no data loss    (50 / 50 runs)
  ✅  Agent failure isolation (3 / 3 scenarios)
  ✅  Risk precision ≥ 0.75   (actual: 0.80)
  ✅  Risk recall ≥ 0.70      (actual: 0.75)
  ✅  HITL cycle correctness  (100%)
  ✅  Thread isolation        (100 / 100 pairs)
  ✅  LT memory isolation     (20 / 20 pairs)
  ✅  RAM < 500 MB / 200 sess (actual: 312 MB)
  ⚠️  p95 latency (langgraph, Sonnet): 9.1 s  [target: < 8 s]
```

---

## When to Re-run the Evaluation

| Trigger | Dimensions to re-run |
|---|---|
| New orchestrator implementation (e.g. Temporal-based) | All five |
| Change to `OrchestratorState` schema or reducers | D2 (coordination), D4 (memory) |
| Change to risk scorer signals or threshold | D3 (risk calibration) |
| New agent added to the fan-out | D1 (answer quality), D2 (coordination) |
| LLM model version upgrade | D1 (answer quality), D5 (cost) |
| New tenant onboarded to production | D4 (memory isolation — smoke test only) |
| Weekly CI schedule | All five |

---

## File Plan

| File | Purpose |
|---|---|
| `tests/evaluation/test_orchestration_eval.py` | Full five-dimension eval suite |
| `tests/evaluation/oqs_report.py` | `OQSReport` dataclass + text/JSON formatter |
| `tests/fixtures/golden_deal_room/` | Synthetic tenant documents (PDF, CSV, JSON) |
| `tests/fixtures/golden_questions.json` | 30 labelled golden questions |
| `tests/fixtures/risk_labelled_set.json` | 40 scenarios with known risk classification |
| `docs/evaluation/orchestration-evaluation.md` | Human-readable runbook + dated OQS log |

---

## Consequences

**Positive:**
- OQS gives a single number that is explainable to a customer: "Our orchestration quality score
  is 89 — here's what each dimension means."
- Dimension 2 (coordinator integrity) catches the class of bugs that component-level evals
  miss entirely: reducer data loss, fan-out skipping, state bleed between sessions.
- Risk calibration gate (Dimension 3) provides a formal reason to adjust the HITL threshold
  rather than guessing.

**Negative / Risks:**
- Golden dataset is synthetic — real deal-room documents will have distributions the synthetic
  set doesn't cover.  Plan: supplement with anonymised real documents after first production
  client (with permission).
- Eval suite requires the full Docker stack (Postgres + ChromaDB + Neo4j) — cannot run in a
  lightweight CI container without services.  Use `pytest -m unit` to skip in fast CI.
- OQS of 89 might create false confidence if one dimension (e.g. latency) has a known-bad
  outlier that is masked by other high scores.  The mandatory zero-floor on any dimension
  partially addresses this.
