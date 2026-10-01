from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.models import ProviderCapacitySlot, ProviderOperation, ProviderSlotReleaseAudit


def reserve_provider_slot(
    db: Session,
    provider: str,
    operation_id: str,
    *,
    capacity: int = 4,
) -> str | None:
    if capacity < 1:
        raise ValueError("capacity_must_be_positive")
    existing = db.execute(
        select(ProviderCapacitySlot).where(ProviderCapacitySlot.operation_id == operation_id)
    ).scalar_one_or_none()
    if existing is not None:
        return existing.id

    dialect = db.get_bind().dialect.name
    rows = [
        {"id": f"{provider}:{index}", "provider": provider, "slot_index": index, "operation_id": None}
        for index in range(capacity)
    ]
    if dialect == "postgresql":
        db.execute(
            pg_insert(ProviderCapacitySlot)
            .values(rows)
            .on_conflict_do_nothing(index_elements=["provider", "slot_index"])
        )
    elif dialect == "sqlite":
        db.execute(
            sqlite_insert(ProviderCapacitySlot)
            .values(rows)
            .on_conflict_do_nothing(index_elements=["provider", "slot_index"])
        )
    else:
        for row in rows:
            if db.get(ProviderCapacitySlot, row["id"]) is None:
                db.add(ProviderCapacitySlot(**row))
        db.flush()

    slot = db.execute(
        select(ProviderCapacitySlot)
        .where(ProviderCapacitySlot.provider == provider, ProviderCapacitySlot.operation_id.is_(None))
        .order_by(ProviderCapacitySlot.slot_index)
        .limit(1)
        .with_for_update(skip_locked=True)
    ).scalar_one_or_none()
    if slot is None:
        return None
    slot.operation_id = operation_id
    db.flush()
    return slot.id


def release_provider_slot(db: Session, operation_id: str, reason: str) -> str | None:
    slot = db.execute(
        select(ProviderCapacitySlot)
        .where(ProviderCapacitySlot.operation_id == operation_id)
        .with_for_update()
    ).scalar_one_or_none()
    if slot is None:
        return None
    slot.operation_id = None
    operation = db.get(ProviderOperation, operation_id)
    if operation is not None:
        from app.models import utc_now

        operation.slot_released_at = utc_now()
        operation.slot_release_reason = reason
    db.flush()
    return slot.id


def release_unknown_slot(
    db: Session,
    operation_id: str,
    *,
    operator: str,
    reason: str,
    evidence: str,
) -> ProviderSlotReleaseAudit | None:
    operator, reason, evidence = operator.strip(), reason.strip(), evidence.strip()
    if not operator or not reason or not evidence:
        raise ValueError("audit_details_required")
    if len(operator) > 120 or len(reason) > 4000 or len(evidence) > 8000:
        raise ValueError("audit_details_too_long")
    operation = db.execute(
        select(ProviderOperation).where(ProviderOperation.id == operation_id).with_for_update()
    ).scalar_one_or_none()
    if operation is None:
        raise LookupError("provider_operation_not_found")
    if operation.submission_status != "unknown":
        raise ValueError("operation_not_unknown")
    slot = db.execute(
        select(ProviderCapacitySlot)
        .where(ProviderCapacitySlot.operation_id == operation_id)
        .with_for_update()
    ).scalar_one_or_none()
    if slot is None:
        return None

    from app.models import utc_now

    audit = ProviderSlotReleaseAudit(
        slot_id=slot.id,
        operation_id=operation_id,
        operator=operator,
        reason=reason,
        evidence=evidence,
        released_at=utc_now(),
    )
    db.add(audit)
    slot.operation_id = None
    operation.slot_released_at = audit.released_at
    operation.slot_release_reason = "operator_audited_release"
    db.flush()
    return audit
