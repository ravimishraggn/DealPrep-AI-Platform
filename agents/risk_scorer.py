"""RiskScorer — computes a discrepancy risk signal from the retrieval results.

Rule-based V1 (ADR 0013 §Decision 5).  No LLM call — deterministic, free, and
testable.  LLM-based scoring is the Phase 8 upgrade path.

Scoring signals (additive, capped at 1.0):
  +0.40  Any graph relationship is "related_party_of"
  +0.25  Vector chunks contain financial adjustment keywords
  +0.20  Multiple distinct companies appear in structured records
  +0.15  Overlap between graph entity names and structured record values
"""
from __future__ import annotations

import re
import time

from agents.base import AgentResult, AnalysisState, BaseAgent
from agents.registry import register_agent

_ADJUSTMENT_KEYWORDS = re.compile(
    r"\b(adjustm|normaliz|exclud|related.?party|add.?back|non.?recurring|pro.?forma|restat)\w*",
    re.IGNORECASE,
)


@register_agent("risk_scorer")
class RiskScorer(BaseAgent):
    """Lightweight signal that flags potential valuation discrepancy risk."""

    async def run(self, state: AnalysisState) -> AgentResult:
        start = time.perf_counter()
        try:
            score, signals = self._score(state)
            state.risk_score = score
            return AgentResult(
                agent=self.name,
                status="success",
                payload={"risk_score": score, "signals": signals},
                latency_ms=self._timed(start),
            )
        except Exception as exc:  # noqa: BLE001
            return self._failed(exc, start)

    # ------------------------------------------------------------------ internals

    def _score(self, state: AnalysisState) -> tuple[float, list[str]]:
        signals: list[str] = []
        score = 0.0

        graph_result = state.results.get("graph_agent")
        vector_result = state.results.get("document_researcher")
        struct_result = state.results.get("structured_agent")

        # Signal 1: related-party edge in graph
        if graph_result and graph_result.status == "success":
            triples = graph_result.payload.get("triples", [])
            if any(t.get("relationship") == "related_party_of" for t in triples):
                score += 0.40
                signals.append("related_party edge detected in knowledge graph")

        # Signal 2: adjustment language in vector chunks
        if vector_result and vector_result.status == "success":
            chunks = vector_result.payload.get("chunks", [])
            all_text = " ".join(c.get("text", "") for c in chunks)
            matches = _ADJUSTMENT_KEYWORDS.findall(all_text)
            if matches:
                score += min(0.25, 0.10 * len(set(m.lower() for m in matches)))
                signals.append(f"adjustment/normalization language found ({len(set(matches))} keyword types)")

        # Signal 3: multiple distinct companies in structured records
        if struct_result and struct_result.status == "success":
            records = struct_result.payload.get("records", [])
            companies = set()
            for rec in records:
                fields = rec.get("fields", {})
                for v in fields.values():
                    if isinstance(v, str) and 3 < len(v) < 60 and v[0].isupper():
                        companies.add(v)
            if len(companies) > 2:
                score += 0.20
                signals.append(f"{len(companies)} distinct named values in structured data (possible comps)")

        # Signal 4: graph entity names overlap with structured record values
        if graph_result and struct_result and graph_result.status == struct_result.status == "success":
            g_names = {t.get("subject", "").lower() for t in graph_result.payload.get("triples", [])}
            g_names |= {t.get("object", "").lower() for t in graph_result.payload.get("triples", [])}
            s_values = set()
            for rec in struct_result.payload.get("records", []):
                for v in rec.get("fields", {}).values():
                    if isinstance(v, str):
                        s_values.add(v.lower())
            overlap = g_names & s_values
            if overlap:
                score += 0.15
                signals.append(f"entity names overlap between graph and structured store ({len(overlap)} shared)")

        return round(min(score, 1.0), 3), signals
