from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import AnalysisTask, ProviderOperation, User, new_id, utc_now


class TaskServiceError(Exception):
    def __init__(self, code: str, status_code: int, *, retryable: bool = False) -> None:
        self.code = code
        self.status_code = status_code
        self.retryable = retryable
        super().__init__(code)


def create_task(
    db: Session,
    user: User,
    kind: str,
    input_parameters: dict[str, object],
    idempotency_key: str,
    input_refs: dict[str, object] | None = None,
    queue_limit: int = 20,
) -> AnalysisTask:
    try:
        normalized_key = str(UUID(idempotency_key))
    except (TypeError, ValueError) as exc:
        raise TaskServiceError("invalid_idempotency_key", 422) from exc

    parameters = _canonical_parameters(input_parameters)
    fingerprint = hashlib.sha256(
        json.dumps({"kind": kind, "parameters": parameters}, sort_keys=True).encode()
    ).hexdigest()
    existing = db.execute(
        select(AnalysisTask).where(
            AnalysisTask.user_id == user.id,
            AnalysisTask.kind == kind,
            AnalysisTask.idempotency_key == normalized_key,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return _return_or_conflict(existing, fingerprint)

    # Serialize queue-cap checks for tasks belonging to the same account.
    db.execute(select(User.id).where(User.id == user.id).with_for_update()).scalar_one()

    queued_count = db.scalar(
        select(func.count()).select_from(AnalysisTask).where(
            AnalysisTask.user_id == user.id,
            AnalysisTask.status == "queued",
        )
    ) or 0
    if queued_count >= queue_limit:
        raise TaskServiceError("task_queue_full", 429, retryable=True)

    task = AnalysisTask(
        id=new_id(),
        user_id=user.id,
        kind=kind,
        status="queued",
        stage="validating",
        input_refs=input_refs or {},
        input_parameters=parameters,
        input_fingerprint=fingerprint,
        idempotency_key=normalized_key,
    )
    db.add(task)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raced = db.execute(
            select(AnalysisTask).where(
                AnalysisTask.user_id == user.id,
                AnalysisTask.kind == kind,
                AnalysisTask.idempotency_key == normalized_key,
            )
        ).scalar_one_or_none()
        if raced is None:
            raise
        return _return_or_conflict(raced, fingerprint)
    db.refresh(task)
    return task


def task_response(db: Session, task: AnalysisTask) -> dict[str, object]:
    operations = list(
        db.execute(
            select(ProviderOperation)
            .where(ProviderOperation.task_id == task.id)
            .order_by(ProviderOperation.created_at.desc())
        ).scalars()
    )
    submission_status = "not_submitted"
    if operations:
        statuses = [operation.submission_status for operation in operations]
        submission_status = "unknown" if "unknown" in statuses else statuses[0]
    error = None
    if task.error_code:
        error = {
            "code": task.error_code,
            "retryable": task.error_code in {"provider_timeout", "provider_unavailable", "provider_request_failed"},
        }
    operation_summaries = []
    for operation in operations:
        result_data = operation.result_data if isinstance(operation.result_data, dict) else {}
        usage = result_data.get("provider_usage")
        reported_cost = result_data.get("reported_cost")
        duration_ms = max(0, int((operation.updated_at - operation.created_at).total_seconds() * 1000))
        operation_summaries.append({
            "operation_key": operation.operation_key,
            "generation": operation.generation,
            "provider": operation.provider,
            "submission_status": operation.submission_status,
            "error_code": operation.error_code,
            "duration_ms": duration_ms,
            "usage": usage if isinstance(usage, dict) else None,
            "reported_cost": reported_cost if isinstance(reported_cost, dict) else None,
        })
    stage_durations_ms = {
        str(stage): max(0, int(duration))
        for stage, duration in (task.stage_durations_ms or {}).items()
        if isinstance(duration, int) and not isinstance(duration, bool)
    }
    if task.stage_started_at is not None:
        stage_end = utc_now() if task.status in {"queued", "running"} else task.updated_at
        active_duration = _elapsed_ms(task.stage_started_at, stage_end)
        stage_durations_ms[task.stage] = stage_durations_ms.get(task.stage, 0) + active_duration
    return {
        "id": task.id,
        "kind": task.kind,
        "status": task.status,
        "stage": task.stage,
        "submission_status": submission_status,
        "cancel_requested": task.cancel_requested,
        "action_required": submission_status == "unknown" or task.error_code == "provider_recovery_timeout",
        "attempt_count": task.attempt_count,
        "operations": operation_summaries,
        "stage_durations_ms": stage_durations_ms,
        "error": error,
        "result_refs": task.result_refs or [],
        "poll_after_seconds": 2 if task.status in {"queued", "running"} else None,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
    }


def advance_task_stage(task: AnalysisTask, stage: str, *, now: datetime | None = None) -> None:
    if task.stage == stage:
        return
    transition_time = now or utc_now()
    if task.stage_started_at is not None:
        durations = dict(task.stage_durations_ms or {})
        durations[task.stage] = int(durations.get(task.stage, 0)) + _elapsed_ms(task.stage_started_at, transition_time)
        task.stage_durations_ms = durations
    task.stage_started_at = transition_time
    task.stage = stage


def _elapsed_ms(start: datetime, end: datetime) -> int:
    start_utc = start.replace(tzinfo=timezone.utc) if start.tzinfo is None else start.astimezone(timezone.utc)
    end_utc = end.replace(tzinfo=timezone.utc) if end.tzinfo is None else end.astimezone(timezone.utc)
    return max(0, int((end_utc - start_utc).total_seconds() * 1000))


def encode_task_cursor(task: AnalysisTask, user_id: str, status: str | None) -> str:
    raw = json.dumps(
        {
            "user": user_id,
            "status": status,
            "created_at": task.created_at.isoformat(),
            "id": task.id,
        },
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_task_cursor(cursor: str, user_id: str, status: str | None) -> tuple[datetime, str]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        value = json.loads(raw)
        if value.get("user") != user_id or value.get("status") != status:
            raise ValueError
        created_at = datetime.fromisoformat(value["created_at"])
        if created_at.tzinfo is not None:
            created_at = created_at.astimezone(timezone.utc).replace(tzinfo=None)
        task_id = str(UUID(value["id"]))
        return created_at, task_id
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise TaskServiceError("invalid_cursor", 422) from exc


def list_tasks(
    db: Session,
    user: User,
    *,
    status: str | None,
    cursor: str | None,
    limit: int,
) -> tuple[list[AnalysisTask], bool]:
    query = select(AnalysisTask).where(AnalysisTask.user_id == user.id)
    if status:
        query = query.where(AnalysisTask.status == status)
    if cursor:
        created_at, task_id = decode_task_cursor(cursor, user.id, status)
        query = query.where(
            or_(AnalysisTask.created_at < created_at, and_(AnalysisTask.created_at == created_at, AnalysisTask.id < task_id))
        )
    rows = list(
        db.execute(query.order_by(AnalysisTask.created_at.desc(), AnalysisTask.id.desc()).limit(limit + 1)).scalars()
    )
    return rows[:limit], len(rows) > limit


def owned_task(db: Session, user: User, task_id: str) -> AnalysisTask | None:
    task = db.get(AnalysisTask, task_id)
    if task is None or task.user_id != user.id:
        return None
    return task


def _return_or_conflict(task: AnalysisTask, fingerprint: str) -> AnalysisTask:
    if task.input_fingerprint != fingerprint:
        raise TaskServiceError("idempotency_conflict", 409)
    return task


def _canonical_parameters(parameters: dict[str, object]) -> dict[str, object]:
    result = dict(parameters)
    url = result.get("url")
    if isinstance(url, str):
        result["url"] = url.strip()
    return result
