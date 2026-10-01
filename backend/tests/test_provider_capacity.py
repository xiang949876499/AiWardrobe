from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app.models import (
    AnalysisTask,
    ProviderOperation,
    ProviderSlotReleaseAudit,
    User,
)
from app.provider_capacity import release_unknown_slot, reserve_provider_slot


def test_provider_capacity_is_bounded_and_unknown_release_is_audited(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'provider-slots.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        user = User(email="slots@example.com")
        db.add(user)
        db.flush()
        task = AnalysisTask(
            user_id=user.id,
            kind="purchase_image",
            input_fingerprint="a" * 64,
            idempotency_key="00000000-0000-4000-8000-000000000001",
        )
        db.add(task)
        db.flush()
        operations = [
            ProviderOperation(
                task_id=task.id,
                operation_key=f"tag:{index}",
                provider="openai",
                request_fingerprint=str(index) * 64,
                submission_status="unknown" if index == 0 else "submitting",
            )
            for index in range(5)
        ]
        db.add_all(operations)
        db.flush()

        allocated = [reserve_provider_slot(db, "openai", item.id, capacity=4) for item in operations]
        assert all(allocated[:4])
        assert allocated[4] is None

        audit = release_unknown_slot(
            db,
            operations[0].id,
            operator="oncall@example.com",
            reason="provider dashboard confirms the operation is no longer active",
            evidence="ticket-123 and provider operation page",
        )
        assert audit is not None
        assert operations[0].submission_status == "unknown"
        assert db.scalar(select(ProviderSlotReleaseAudit).where(ProviderSlotReleaseAudit.id == audit.id))
        assert reserve_provider_slot(db, "openai", operations[4].id, capacity=4) is not None


def test_unknown_slot_release_requires_audit_details(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'provider-slot-audit.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        user = User(email="unknown@example.com")
        db.add(user)
        db.flush()
        task = AnalysisTask(
            user_id=user.id,
            kind="purchase_image",
            input_fingerprint="b" * 64,
            idempotency_key="00000000-0000-4000-8000-000000000002",
        )
        db.add(task)
        db.flush()
        operation = ProviderOperation(
            task_id=task.id,
            operation_key="tag:0",
            provider="openai",
            request_fingerprint="c" * 64,
            submission_status="unknown",
        )
        db.add(operation)
        db.flush()
        reserve_provider_slot(db, "openai", operation.id, capacity=1)

        try:
            release_unknown_slot(db, operation.id, operator="", reason="", evidence="")
        except ValueError as exc:
            assert str(exc) == "audit_details_required"
        else:
            raise AssertionError("release without audit details must fail")
