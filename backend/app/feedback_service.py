from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import FeedbackEvent


def active_excluded_garments(db: Session, user_id: str) -> set[str]:
    events = db.execute(
        select(FeedbackEvent.garment_ids).where(
            FeedbackEvent.user_id == user_id,
            FeedbackEvent.value == "not_helpful",
            FeedbackEvent.reason_code == "avoid_item",
            FeedbackEvent.revoked_at.is_(None),
        )
    ).scalars()
    return {garment_id for ids in events for garment_id in ids or []}


def matching_feedback_adjustment(
    db: Session,
    user_id: str,
    *,
    style_labels: list[str],
    occasion: str | None,
    temperature_bucket: str | None = None,
) -> int:
    if not occasion:
        return 0
    normalized_styles = {_normalize(value) for value in style_labels if value.strip()}
    events = list(db.execute(
        select(FeedbackEvent).where(
            FeedbackEvent.user_id == user_id,
            FeedbackEvent.value == "not_helpful",
            FeedbackEvent.reason_code.in_(["style_mismatch", "weather_mismatch"]),
            FeedbackEvent.revoked_at.is_(None),
            FeedbackEvent.occasion == occasion,
        )
    ).scalars())
    adjustments: set[str] = set()
    for event in events:
        context = event.context or {}
        reason = event.reason_code or ""
        if reason == "style_mismatch":
            old_styles = {_normalize(str(value)) for value in context.get("style_labels", [])}
            if normalized_styles & old_styles:
                adjustments.add(reason)
        elif reason == "weather_mismatch" and temperature_bucket:
            if context.get("temperature_bucket") == temperature_bucket:
                adjustments.add(reason)
    return -5 * min(3, len(adjustments))


def _normalize(value: str) -> str:
    return " ".join(value.casefold().split())
