from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import RateLimitEvent


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    remaining: int
    reset_at: datetime


def check_rate_limit(db: Session, scope: str, key: str, limit: int, window_seconds: int) -> RateLimitResult:
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(seconds=window_seconds)
    db.execute(delete(RateLimitEvent).where(RateLimitEvent.created_at < window_start))
    events = list(
        db.execute(
            select(RateLimitEvent)
            .where(RateLimitEvent.scope == scope, RateLimitEvent.key == key, RateLimitEvent.created_at >= window_start)
            .order_by(RateLimitEvent.created_at)
        ).scalars()
    )
    if len(events) >= limit:
        reset_at = _as_aware(events[0].created_at) + timedelta(seconds=window_seconds)
        return RateLimitResult(allowed=False, remaining=0, reset_at=reset_at)

    db.add(RateLimitEvent(scope=scope, key=key, created_at=now))
    return RateLimitResult(
        allowed=True,
        remaining=max(0, limit - len(events) - 1),
        reset_at=now + timedelta(seconds=window_seconds),
    )


def _as_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value
