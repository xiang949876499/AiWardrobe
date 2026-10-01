from __future__ import annotations

import argparse
import sys

from sqlalchemy import select

from app.database import get_session_factory, init_db
from app.models import ProviderCapacitySlot, ProviderOperation
from app.provider_capacity import release_unknown_slot


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inspect unknown provider submissions and release tracked capacity with an audit record."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list-unknown", help="List unknown provider operations that still hold a slot.")
    release = subparsers.add_parser("release-unknown", help="Release one unknown operation's local slot.")
    release.add_argument("operation_id")
    release.add_argument("--operator", required=True)
    release.add_argument("--reason", required=True)
    release.add_argument("--evidence", required=True)
    args = parser.parse_args()

    init_db()
    with get_session_factory()() as db:
        if args.command == "list-unknown":
            rows = db.execute(
                select(ProviderOperation, ProviderCapacitySlot)
                .join(ProviderCapacitySlot, ProviderCapacitySlot.operation_id == ProviderOperation.id)
                .where(ProviderOperation.submission_status == "unknown")
                .order_by(ProviderOperation.created_at)
            ).all()
            for operation, slot in rows:
                print(f"{operation.id}\t{operation.provider}\t{slot.id}\t{operation.created_at.isoformat()}")
            return

        try:
            audit = release_unknown_slot(
                db,
                args.operation_id,
                operator=args.operator,
                reason=args.reason,
                evidence=args.evidence,
            )
        except (LookupError, ValueError) as exc:
            db.rollback()
            print(str(exc), file=sys.stderr)
            raise SystemExit(2) from exc
        if audit is None:
            db.rollback()
            print("provider_slot_not_reserved", file=sys.stderr)
            raise SystemExit(2)
        db.commit()
        print(f"Released local slot {audit.slot_id}; operation remains unknown ({audit.operation_id}).")


if __name__ == "__main__":
    main()
