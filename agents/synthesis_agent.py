"""SynthesisAgent — produces a plain-language answer from all retrieval results.

Uses the existing LLM client (ADR 0013 §Decision 5):
  - DEALPREP_ANTHROPIC_API_KEY present → Claude (claude-haiku-4-5-20251001 default)
  - No key → deterministic template that bullet-points each result set

The system prompt instructs Claude to cite sources, identify discrepancies, and
produce ≤ 3 paragraphs — no invented facts beyond what the retrieval results contain.
"""
from __future__ import annotations

import asyncio
import json
import time

from agents.base import AgentResult, AnalysisState, BaseAgent
from agents.registry import register_agent
from app.llm import get_llm_client

_SYSTEM = """\
You are a financial analysis assistant for a PE/VC valuation platform.

You will receive three sets of retrieval results about a company or deal:
1. Document excerpts (from SEC filings, CIMs, or deal memos)
2. Structured financial data (tables, metrics, deal terms)
3. Entity relationships (ownership, board overlaps, related-party transactions)

Your task:
- Identify discrepancies between the sources (e.g. EBITDA figures that differ, \
related-party revenue that inflates multiples).
- Explain *why* numbers differ when the data supports it.
- Cite sources by their "original_file_reference" or "file_ref" fields.
- Produce a concise answer in ≤ 3 paragraphs.
- Do NOT invent facts not present in the data.
- If the data is insufficient to explain a discrepancy, say so explicitly.
"""


@register_agent("synthesis_agent")
class SynthesisAgent(BaseAgent):
    """Produces a narrative answer by synthesising retrieval results via LLM."""

    async def run(self, state: AnalysisState) -> AgentResult:
        start = time.perf_counter()
        try:
            answer, citations = await asyncio.to_thread(self._synthesise, state)
            state.answer = answer
            state.citations = citations
            return AgentResult(
                agent=self.name,
                status="success",
                payload={"answer": answer, "citations": citations},
                latency_ms=self._timed(start),
            )
        except Exception as exc:  # noqa: BLE001
            return self._failed(exc, start)

    # ------------------------------------------------------------------ internals

    def _synthesise(self, state: AnalysisState) -> tuple[str, list[dict]]:
        citations = self._build_citations(state)
        llm = get_llm_client()
        if llm:
            answer = llm.complete(_SYSTEM, self._build_prompt(state, citations))
        else:
            answer = self._template_answer(state, citations)
        return answer, citations

    def _build_citations(self, state: AnalysisState) -> list[dict]:
        out: list[dict] = []
        vec = state.results.get("document_researcher")
        if vec and vec.status == "success":
            for c in (vec.payload.get("chunks") or [])[:3]:
                out.append({
                    "agent": "document_researcher",
                    "text": c.get("text", "")[:300],
                    "score": c.get("score"),
                    "file": (c.get("metadata") or {}).get("original_file_reference"),
                })
        struct = state.results.get("structured_agent")
        if struct and struct.status == "success":
            for r in (struct.payload.get("records") or [])[:3]:
                out.append({
                    "agent": "structured_agent",
                    "fields": r.get("fields", {}),
                    "file": (r.get("metadata") or {}).get("original_file_reference"),
                })
        graph = state.results.get("graph_agent")
        if graph and graph.status == "success":
            for t in (graph.payload.get("triples") or [])[:5]:
                out.append({
                    "agent": "graph_agent",
                    "subject": t.get("subject"),
                    "relationship": t.get("relationship"),
                    "object": t.get("object"),
                    "file": t.get("file_ref"),
                })
        return out

    def _build_prompt(self, state: AnalysisState, citations: list[dict]) -> str:
        vec_chunks = (state.results.get("document_researcher") or AgentResult("", "skipped", {})).payload.get("chunks", [])
        struct_recs = (state.results.get("structured_agent") or AgentResult("", "skipped", {})).payload.get("records", [])
        graph_triples = (state.results.get("graph_agent") or AgentResult("", "skipped", {})).payload.get("triples", [])
        risk = state.risk_score

        data = {
            "query": state.context.query,
            "risk_score": risk,
            "document_excerpts": [
                {"text": c.get("text", "")[:500], "score": c.get("score"),
                 "source": (c.get("metadata") or {}).get("original_file_reference")}
                for c in vec_chunks[:5]
            ],
            "structured_records": [
                {"fields": r.get("fields", {}),
                 "source": (r.get("metadata") or {}).get("original_file_reference")}
                for r in struct_recs[:5]
            ],
            "relationships": [
                {"subject": t.get("subject"), "rel": t.get("relationship"),
                 "object": t.get("object"), "source": t.get("file_ref")}
                for t in graph_triples[:10]
            ],
        }
        return json.dumps(data, indent=2, default=str)

    def _template_answer(self, state: AnalysisState, citations: list[dict]) -> str:
        ctx = state.context
        vec = (state.results.get("document_researcher") or AgentResult("", "skipped", {})).payload.get("chunks", [])
        struct = (state.results.get("structured_agent") or AgentResult("", "skipped", {})).payload.get("records", [])
        graph = (state.results.get("graph_agent") or AgentResult("", "skipped", {})).payload.get("triples", [])
        risk = state.risk_score

        lines = [f'Analysis of: "{ctx.query}"', ""]

        risk_label = "low" if (risk or 0) < 0.3 else "medium" if (risk or 0) < 0.6 else "HIGH"
        lines.append(f"Risk signal: {risk or 0:.2f} ({risk_label})")
        if state.results.get("risk_scorer") and state.results["risk_scorer"].status == "success":
            for sig in state.results["risk_scorer"].payload.get("signals", []):
                lines.append(f"  • {sig}")
        lines.append("")

        if vec:
            lines.append(f"Document findings ({len(vec)} source chunks):")
            for c in vec[:3]:
                src = (c.get("metadata") or {}).get("original_file_reference", "unknown")
                lines.append(f"  • [{src}] {c.get('text', '')[:200].strip()}")
            lines.append("")

        if struct:
            lines.append(f"Financial data ({len(struct)} structured records):")
            for r in struct[:3]:
                src = (r.get("metadata") or {}).get("original_file_reference", "unknown")
                fields_str = ", ".join(f"{k}: {v}" for k, v in list(r.get("fields", {}).items())[:4])
                lines.append(f"  • [{src}] {fields_str}")
            lines.append("")

        if graph:
            lines.append(f"Relationships ({len(graph)} identified):")
            for t in graph[:5]:
                lines.append(f"  • {t.get('subject')} —[{t.get('relationship')}]→ {t.get('object')}")
            lines.append("")

        if not (vec or struct or graph):
            lines.append("No data found. Ingest documents for this tenant first.")

        lines.append("(Set DEALPREP_ANTHROPIC_API_KEY for narrative synthesis via Claude.)")
        return "\n".join(lines)
