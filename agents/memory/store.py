"""Long-term memory store for the agent orchestration layer (ADR 0013 §D).

Persists completed analyses to Postgres ``analysis_history`` so future sessions
for the same tenant can reference prior findings — patterns, recurring entities,
prior risk flags. The ``load_memory_node`` reads from here at graph START;
``save_memory_node`` writes to it at graph END.

This is explicitly *not* a vector store — plain SQL, last-N recency. The intent
is to surface patterns like "this entity has been flagged as related-party in 3 of
the last 5 analyses for this tenant," not semantic similarity retrieval.
"""
from __future__ import annotations

import logging

from app.db import SessionLocal
from app.models import AnalysisHistory

logger = logging.getLogger(__name__)


class LongTermMemoryStore:
    """Read/write interface to the analysis_history table."""

    def load_recent(self, tenant_id: str, limit: int = 5) -> list[dict]:
        """Return the ``limit`` most recent analyses for ``tenant_id``.

        Args:
            tenant_id: Tenant whose history to load.
            limit: Maximum number of records to return.

        Returns:
            List of dicts with query, risk_score, risk_signals, answer snippet,
            and created_at. Most recent first.
        """
        session = SessionLocal()
        try:
            rows = (
                session.query(AnalysisHistory)
                .filter(AnalysisHistory.tenant_id == tenant_id)
                .order_by(AnalysisHistory.created_at.desc())
                .limit(limit)
                .all()
            )
            return [
                {
                    "query": r.query,
                    "risk_score": r.risk_score,
                    "risk_signals": r.risk_signals or [],
                    "answer_snippet": (r.answer or "")[:300],
                    "created_at": str(r.created_at),
                    "session_id": r.session_id,
                }
                for r in rows
            ]
        except Exception:  # noqa: BLE001
            logger.exception("failed to load analysis history for tenant %s", tenant_id)
            return []
        finally:
            session.close()

    def save(
        self,
        tenant_id: str,
        session_id: str,
        query: str,
        risk_score: float | None,
        answer: str | None,
        citations: list,
        risk_signals: list,
        orchestrator: str = "sequential",
        interrupted: bool = False,
    ) -> None:
        """Persist a completed analysis to the long-term store.

        Args:
            tenant_id: Owning tenant.
            session_id: Unique session identifier (used for de-dup if re-saved).
            query: The analyst's original question.
            risk_score: Risk score from RiskScorer (None if scorer failed).
            answer: Synthesised answer text (None if synthesis failed).
            citations: List of citation dicts from SynthesisAgent.
            risk_signals: List of signal strings from RiskScorer.
            orchestrator: Which orchestrator ran this analysis.
            interrupted: Whether the analysis was interrupted (HITL, aborted).
        """
        session = SessionLocal()
        try:
            row = AnalysisHistory(
                tenant_id=tenant_id,
                session_id=session_id,
                query=query,
                orchestrator=orchestrator,
                risk_score=risk_score,
                answer=answer,
                citations=citations or [],
                risk_signals=risk_signals or [],
                interrupted=interrupted,
            )
            session.add(row)
            session.commit()
            logger.debug("saved analysis history for tenant %s session %s", tenant_id, session_id)
        except Exception:  # noqa: BLE001
            logger.exception("failed to save analysis history for tenant %s", tenant_id)
            session.rollback()
        finally:
            session.close()


_store: LongTermMemoryStore | None = None


def get_memory_store() -> LongTermMemoryStore:
    """Return the process-wide singleton memory store."""
    global _store
    if _store is None:
        _store = LongTermMemoryStore()
    return _store
