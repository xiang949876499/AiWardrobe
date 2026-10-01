from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.asset_service import create_image_asset
from app.config import get_settings
from app.database import get_session_factory, init_db
from app.image_inputs import ImageInputError, prepare_image
from app.models import Garment, PurchaseCandidate, UploadSession
from app.storage import StorageService


@dataclass(frozen=True)
class AssetMigrationReport:
    scanned: int
    migrated: int
    skipped: int
    failed: tuple[dict[str, str], ...]


def migrate_legacy_assets(db: Session, settings, *, apply: bool = False) -> AssetMigrationReport:
    legacy = StorageService(settings)
    rows = (
        (Garment, "image_key", "asset_id"),
        (PurchaseCandidate, "image_key", "asset_id"),
        (UploadSession, "original_image_key", "asset_id"),
    )
    scanned = migrated = skipped = 0
    failures: list[dict[str, str]] = []
    for model, key_field, asset_field in rows:
        for row in db.execute(select(model).where(getattr(model, asset_field).is_(None))).scalars():
            key = str(getattr(row, key_field) or "")
            if not key:
                skipped += 1
                continue
            scanned += 1
            try:
                data = legacy.read_legacy(key)
                content_type = _content_type(key)
                prepare_image(data, content_type)
                if not apply:
                    continue
                stored = create_image_asset(db, settings, row.user_id, data, content_type)
                setattr(row, asset_field, stored.original.id)
                setattr(row, key_field, "")
                if isinstance(row, Garment) or isinstance(row, PurchaseCandidate):
                    row.image_url = ""
                    row.thumbnail_url = None
                else:
                    row.original_image_url = ""
                db.commit()
                migrated += 1
            except ImageInputError as exc:
                db.rollback()
                failures.append({"record": model.__name__, "id": str(row.id), "code": exc.code})
            except Exception as exc:
                db.rollback()
                code = "legacy_asset_read_failed" if isinstance(exc, (OSError, ValueError)) else "asset_migration_failed"
                failures.append({"record": model.__name__, "id": str(row.id), "code": code})
    if apply:
        db.commit()
    return AssetMigrationReport(scanned, migrated, skipped, tuple(failures))


def _content_type(key: str) -> str:
    suffix = key.lower().rsplit(".", 1)[-1] if "." in key else ""
    return {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}.get(suffix, "application/octet-stream")


def main() -> None:
    parser = argparse.ArgumentParser(description="Move legacy public uploads into private asset storage.")
    parser.add_argument("--apply", action="store_true", help="Write private copies and update database references. Default is validation-only.")
    args = parser.parse_args()
    init_db()
    settings = get_settings()
    with get_session_factory()() as db:
        report = migrate_legacy_assets(db, settings, apply=args.apply)
    print(json.dumps({"mode": "apply" if args.apply else "dry_run", **asdict(report)}, ensure_ascii=False, indent=2))
    if report.failed:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
