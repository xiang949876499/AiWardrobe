import pytest
from datetime import timedelta
from io import BytesIO

from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.asset_gc import collect_unreferenced_assets
from app.asset_service import create_image_asset
from app.asset_storage import PrivateAssetStorage
from app.config import Settings
from app.database import Base
from app.models import AnalysisTask, Garment, ProviderOperation, User, utc_now


def _png() -> bytes:
    buffer = BytesIO()
    Image.new("RGBA", (24, 16), (240, 20, 80, 128)).save(buffer, format="PNG")
    return buffer.getvalue()


def test_private_assets_wait_24_hours_after_last_reference_before_reclamation(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'asset-gc.db'}")
    Base.metadata.create_all(engine)
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'settings.db'}",
        local_private_asset_dir=str(tmp_path / "private"),
        local_upload_dir=str(tmp_path / "public"),
        jwt_secret="test-secret-with-at-least-thirty-two-bytes",
    )
    storage = PrivateAssetStorage(settings)
    now = utc_now()
    with Session(engine) as db:
        user = User(email="asset-gc@example.com")
        db.add(user)
        db.flush()
        result = create_image_asset(db, settings, user.id, _png(), "image/png")
        db.flush()
        original_path = tmp_path / "private" / result.original.storage_key
        thumbnail_path = tmp_path / "private" / db.get(type(result.original), result.original.thumbnail_asset_id).storage_key

        first = collect_unreferenced_assets(db, storage, now=now)
        assert first.marked == 2
        assert first.deleted == 0
        assert original_path.exists() and thumbnail_path.exists()

        result.original.unreferenced_since = now - timedelta(hours=25)
        thumbnail = db.get(type(result.original), result.original.thumbnail_asset_id)
        thumbnail.unreferenced_since = now - timedelta(hours=25)
        second = collect_unreferenced_assets(db, storage, now=now)
        assert second.deleted == 2
        assert not original_path.exists() and not thumbnail_path.exists()


def test_referenced_private_asset_and_thumbnail_are_kept(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'asset-gc-reference.db'}")
    Base.metadata.create_all(engine)
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'settings.db'}",
        local_private_asset_dir=str(tmp_path / "private"),
        local_upload_dir=str(tmp_path / "public"),
        jwt_secret="test-secret-with-at-least-thirty-two-bytes",
    )
    now = utc_now()
    with Session(engine) as db:
        user = User(email="asset-gc-reference@example.com")
        db.add(user)
        db.flush()
        result = create_image_asset(db, settings, user.id, _png(), "image/png")
        result.original.unreferenced_since = now - timedelta(days=2)
        thumbnail = db.get(type(result.original), result.original.thumbnail_asset_id)
        thumbnail.unreferenced_since = now - timedelta(days=2)
        db.add(Garment(
            user_id=user.id,
            asset_id=result.original.id,
            image_url="",
            image_key="",
            category="top",
            status="ready",
            review_status="pending_review",
        ))
        db.flush()

        result_gc = collect_unreferenced_assets(db, PrivateAssetStorage(settings), now=now)
        assert result_gc.deleted == 0
        assert result.original.unreferenced_since is None
        assert thumbnail.unreferenced_since is None


def test_split_output_assets_survive_task_recovery_until_task_is_terminal(tmp_path) -> None:
    from uuid import uuid4

    engine = create_engine(f"sqlite:///{tmp_path / 'recovering-asset.db'}")
    Base.metadata.create_all(engine)
    settings = Settings(
        _env_file=None,
        local_private_asset_dir=str(tmp_path / "private"),
        local_upload_dir=str(tmp_path / "public"),
        jwt_secret="test-secret-with-at-least-thirty-two-bytes",
    )
    now = utc_now()
    with Session(engine) as db:
        user = User(email="recovering-assets@example.test")
        db.add(user)
        db.flush()
        output = create_image_asset(db, settings, user.id, _png(), "image/png")
        thumbnail = db.get(type(output.original), output.original.thumbnail_asset_id)
        output.original.unreferenced_since = now - timedelta(days=2)
        thumbnail.unreferenced_since = now - timedelta(days=2)
        task = AnalysisTask(
            user_id=user.id, kind="garment_split", status="running", stage="awaiting_resolution",
            input_refs={}, input_parameters={}, input_fingerprint="recovery-output",
            idempotency_key=str(uuid4()),
        )
        db.add(task)
        db.flush()
        db.add(ProviderOperation(
            task_id=task.id, operation_key="split:0", provider="runninghub",
            request_fingerprint="split-output", submission_status="completed",
            result_data={"outputs": [{"asset_id": output.original.id}]},
        ))
        db.commit()

        result = collect_unreferenced_assets(db, PrivateAssetStorage(settings), now=now)
        assert result.deleted == 0
        assert output.original.unreferenced_since is None
        assert thumbnail.unreferenced_since is None

        task.status = "cancelled"
        db.commit()
        collect_unreferenced_assets(db, PrivateAssetStorage(settings), now=now)
        collected = collect_unreferenced_assets(db, PrivateAssetStorage(settings), now=now + timedelta(hours=25))
        assert collected.deleted == 2


def test_cancelled_remote_task_keeps_input_until_supplier_terminal(tmp_path) -> None:
    from uuid import uuid4

    engine = create_engine(f"sqlite:///{tmp_path / 'cancelled-remote.db'}")
    Base.metadata.create_all(engine)
    settings = Settings(_env_file=None, local_private_asset_dir=str(tmp_path / "private"))
    now = utc_now()
    with Session(engine) as db:
        user = User(email="cancelled-remote@example.test")
        db.add(user)
        db.flush()
        image = create_image_asset(db, settings, user.id, _png(), "image/png")
        thumbnail = db.get(type(image.original), image.original.thumbnail_asset_id)
        image.original.unreferenced_since = thumbnail.unreferenced_since = now - timedelta(days=2)
        task = AnalysisTask(user_id=user.id, kind="garment_split", status="cancelled",
            input_refs={"asset_id": image.original.id}, input_fingerprint="cancelled-input", idempotency_key=str(uuid4()))
        db.add(task)
        db.flush()
        operation = ProviderOperation(task_id=task.id, provider="comfyui", operation_key="split:0",
            submission_status="submitted", provider_task_id="accepted-prompt", request_fingerprint="cancelled-input")
        db.add(operation)
        db.commit()
        assert collect_unreferenced_assets(db, PrivateAssetStorage(settings), now=now).deleted == 0
        operation.submission_status = "completed"
        db.commit()
        collect_unreferenced_assets(db, PrivateAssetStorage(settings), now=now)
        assert collect_unreferenced_assets(db, PrivateAssetStorage(settings), now=now + timedelta(hours=25)).deleted == 2


pytestmark = pytest.mark.usefixtures("legacy_worker")
