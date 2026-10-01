from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.models import PurchaseAnalysisSnapshot, PurchaseCandidate, UserDecisionContext, new_id, utc_now


def get_decision_context(db: Session, user_id: str, *, lock: bool = False) -> UserDecisionContext:
    statement = select(UserDecisionContext).where(UserDecisionContext.user_id == user_id)
    if lock:
        statement = statement.with_for_update()
    context = db.execute(statement).scalar_one_or_none()
    if context is not None:
        return context

    values = {"user_id": user_id, "wardrobe_revision": 0, "preference_revision": 0, "feedback_revision": 0}
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        db.execute(pg_insert(UserDecisionContext).values(**values).on_conflict_do_nothing(index_elements=["user_id"]))
    elif dialect == "sqlite":
        db.execute(sqlite_insert(UserDecisionContext).values(**values).on_conflict_do_nothing(index_elements=["user_id"]))
    else:
        db.add(UserDecisionContext(**values))
        db.flush()
    statement = select(UserDecisionContext).where(UserDecisionContext.user_id == user_id)
    if lock:
        statement = statement.with_for_update()
    return db.execute(statement).scalar_one()


def bump_revision(db: Session, user_id: str, field: str) -> UserDecisionContext:
    if field not in {"wardrobe_revision", "preference_revision", "feedback_revision"}:
        raise ValueError("invalid_decision_revision")
    context = get_decision_context(db, user_id, lock=True)
    setattr(context, field, getattr(context, field) + 1)
    context.updated_at = utc_now()
    db.flush()
    return context


def create_purchase_snapshot(
    db: Session,
    candidate: PurchaseCandidate,
    *,
    snapshot_revision: int | None = None,
    decision_payload: dict[str, object] | None = None,
) -> PurchaseAnalysisSnapshot:
    existing_revision = snapshot_revision or candidate.requested_snapshot_revision
    if existing_revision <= 0:
        candidate.requested_snapshot_revision = 1
        existing_revision = 1
    existing = db.execute(
        select(PurchaseAnalysisSnapshot).where(
            PurchaseAnalysisSnapshot.candidate_id == candidate.id,
            PurchaseAnalysisSnapshot.snapshot_revision == existing_revision,
        )
    ).scalar_one_or_none()
    if existing is not None:
        latest = db.get(PurchaseAnalysisSnapshot, candidate.latest_snapshot_id) if candidate.latest_snapshot_id else None
        if latest is None or existing.snapshot_revision > latest.snapshot_revision:
            candidate.latest_snapshot_id = existing.id
        return existing

    context = get_decision_context(db, candidate.user_id)
    decision_data = decision_payload or {
        "recommendation": candidate.recommendation,
        "score": candidate.decision_score,
        "reason_summary": candidate.reason_summary,
        "analysis": candidate.analysis or {},
        "actual_price": str(candidate.actual_price) if candidate.actual_price is not None else None,
        "currency": candidate.currency,
        "revision": candidate.revision,
    }
    analysis_data = decision_data.get("analysis") if isinstance(decision_data.get("analysis"), dict) else {}
    evidence_data = analysis_data.get("evidence") if isinstance(analysis_data.get("evidence"), dict) else {}
    rule_version = evidence_data.get("rule_version")
    if not isinstance(rule_version, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,39}", rule_version):
        rule_version = "purchase-v1"
    snapshot = PurchaseAnalysisSnapshot(
        id=new_id(),
        candidate_id=candidate.id,
        snapshot_revision=existing_revision,
        wardrobe_revision=context.wardrobe_revision,
        preference_revision=context.preference_revision,
        feedback_revision=context.feedback_revision,
        rule_version=rule_version,
        coverage=dict(analysis_data.get("coverage") or {}),
        evidence=dict(evidence_data),
        decision=decision_data,
        generated_at=utc_now(),
    )
    db.add(snapshot)
    db.flush()
    latest = db.get(PurchaseAnalysisSnapshot, candidate.latest_snapshot_id) if candidate.latest_snapshot_id else None
    if latest is None or existing_revision > latest.snapshot_revision:
        candidate.latest_snapshot_id = snapshot.id
    return snapshot
