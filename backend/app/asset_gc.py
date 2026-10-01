from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.asset_storage import PrivateAssetStorage
from app.config import get_settings
from app.database import get_session_factory, init_db
from app.models import AnalysisTask, Garment, ProviderOperation, PurchaseCandidate, StoredAsset, UploadSession

RETENTION_HOURS = 24


@dataclass(frozen=True)
class AssetGCResult:
    marked: int
    deleted: int
    skipped_referenced: int


def collect_unreferenced_assets(
    db: Session,
    storage: PrivateAssetStorage,
    *,
    now: datetime | None = None,
    dry_run: bool = False,
) -> AssetGCResult:
    current = now or datetime.now(timezone.utc)
    assets = list(db.execute(select(StoredAsset).order_by(StoredAsset.id).with_for_update()).scalars())
    by_id = {asset.id: asset for asset in assets}
    referenced: set[str] = set(db.execute(select(Garment.asset_id).where(Garment.asset_id.is_not(None))).scalars())
    referenced.update(db.execute(select(PurchaseCandidate.asset_id).where(PurchaseCandidate.asset_id.is_not(None))).scalars())
    referenced.update(db.execute(select(UploadSession.asset_id).where(UploadSession.asset_id.is_not(None))).scalars())
    # Local cancellation does not end an accepted remote job. Its read-only
    # cleanup still needs the original image until the supplier is terminal.
    unresolved_remote = select(ProviderOperation.id).where(
        ProviderOperation.task_id == AnalysisTask.id,
        ProviderOperation.provider_task_id.is_not(None),
        ProviderOperation.submission_status.in_(["submitting", "submitted", "unknown"]),
    ).correlate(AnalysisTask).exists()
    retained_task = or_(AnalysisTask.status.in_(["queued", "running"]), unresolved_remote)
    active_tasks = db.execute(select(AnalysisTask.input_refs).where(retained_task)).scalars()
    referenced.update(
        str((references or {}).get("asset_id"))
        for references in active_tasks
        if (references or {}).get("asset_id")
    )
    # Split results remain inputs to later tagging stages across worker restarts.
    operations = db.execute(
        select(ProviderOperation)
        .join(AnalysisTask, ProviderOperation.task_id == AnalysisTask.id)
        .where(retained_task)
    ).scalars()
    for operation in operations:
        outputs = (operation.result_data or {}).get("outputs") or []
        referenced.update(
            str(output["asset_id"])
            for output in outputs
            if isinstance(output, dict) and output.get("asset_id")
        )
        referenced.update(
            str(ref["id"])
            for ref in operation.result_refs or []
            if ref.get("resource_type") == "asset" and ref.get("id")
        )
    referenced.update(
        asset.thumbnail_asset_id
        for asset in assets
        if asset.id in referenced and asset.thumbnail_asset_id
    )

    marked = 0
    deleted = 0
    skipped_referenced = 0
    cutoff = current - timedelta(hours=RETENTION_HOURS)
    for asset in assets:
        if asset.id in referenced:
            if asset.unreferenced_since is not None and not dry_run:
                asset.unreferenced_since = None
                marked += 1
            skipped_referenced += 1
            continue
        if asset.unreferenced_since is None:
            marked += 1
            if not dry_run:
                asset.unreferenced_since = current
            continue
        if _as_aware(asset.unreferenced_since) > cutoff:
            continue
        if not dry_run:
            storage.delete(asset.storage_key)
            db.delete(asset)
        deleted += 1
    if not dry_run:
        db.commit()
    return AssetGCResult(marked=marked, deleted=deleted, skipped_referenced=skipped_referenced)


def _as_aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def main() -> None:
    parser = argparse.ArgumentParser(description="Reclaim private image assets after 24 hours without a live reference.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    init_db()
    settings = get_settings()
    with get_session_factory()() as db:
        result = collect_unreferenced_assets(
            db,
            PrivateAssetStorage(settings),
            dry_run=args.dry_run,
        )
    print(
        f"Asset GC {'preview' if args.dry_run else 'complete'}: "
        f"marked={result.marked} deleted={result.deleted} referenced={result.skipped_referenced}"
    )


if __name__ == "__main__":
    main()
