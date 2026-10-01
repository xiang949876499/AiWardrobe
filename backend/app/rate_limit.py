from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.models import RateLimitCounter, RateLimitEvent

EVENT_RETENTION_SECONDS = 24 * 60 * 60


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    remaining: int
    reset_at: datetime


def check_rate_limit(db: Session, scope: str, key: str, limit: int, window_seconds: int) -> RateLimitResult:
    now = datetime.now(timezone.utc)
    retention_start = now - timedelta(seconds=max(window_seconds, EVENT_RETENTION_SECONDS))
    db.execute(delete(RateLimitEvent).where(RateLimitEvent.created_at < retention_start))

    dialect = db.get_bind().dialect.name
    values = {
        "scope": scope,
        "key": key,
        "window_seconds": window_seconds,
        "count": 0,
        "window_started_at": now,
        "reset_at": now + timedelta(seconds=window_seconds),
    }
    if dialect == "postgresql":
        statement = pg_insert(RateLimitCounter).values(**values).on_conflict_do_nothing(
            index_elements=["scope", "key"]
        )
    elif dialect == "sqlite":
        statement = sqlite_insert(RateLimitCounter).values(**values).on_conflict_do_nothing(
            index_elements=["scope", "key"]
        )
    else:
        statement = None
    if statement is not None:
        inserted = db.execute(statement).rowcount == 1
        counter = db.execute(
            select(RateLimitCounter)
            .where(RateLimitCounter.scope == scope, RateLimitCounter.key == key)
            .with_for_update()
        ).scalar_one()
        reset_at = _as_aware(counter.reset_at)
        if inserted:
            legacy_events = list(
                db.execute(
                    select(RateLimitEvent)
                    .where(
                        RateLimitEvent.scope == scope,
                        RateLimitEvent.key == key,
                        RateLimitEvent.created_at >= now - timedelta(seconds=window_seconds),
                    )
                    .order_by(RateLimitEvent.created_at)
                ).scalars()
            )
            counter.count = len(legacy_events)
            if legacy_events:
                reset_at = _as_aware(legacy_events[0].created_at) + timedelta(seconds=window_seconds)
                counter.reset_at = reset_at
        if counter.window_seconds != window_seconds or now >= reset_at:
            counter.window_seconds = window_seconds
            counter.count = 0
            counter.window_started_at = now
            counter.reset_at = now + timedelta(seconds=window_seconds)
            reset_at = now + timedelta(seconds=window_seconds)
        if counter.count >= limit:
            return RateLimitResult(allowed=False, remaining=0, reset_at=reset_at)
        counter.count += 1
        return RateLimitResult(
            allowed=True,
            remaining=max(0, limit - counter.count),
            reset_at=reset_at,
        )

    # A fallback keeps tests and embedded deployments on other SQL dialects safe.
    window_start = now - timedelta(seconds=window_seconds)
    events = list(
        db.execute(
            select(RateLimitEvent)
            .where(
                RateLimitEvent.scope == scope,
                RateLimitEvent.key == key,
                RateLimitEvent.created_at >= window_start,
            )
            .order_by(RateLimitEvent.created_at)
        ).scalars()
    )
    if len(events) >= limit:
        return RateLimitResult(
            allowed=False,
            remaining=0,
            reset_at=_as_aware(events[0].created_at) + timedelta(seconds=window_seconds),
        )
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
