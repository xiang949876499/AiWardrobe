from uuid import uuid4
from io import BytesIO

import anyio
from PIL import Image

from fastapi.testclient import TestClient

from tests.conftest import login


def _small_jpeg() -> bytes:
    image = BytesIO()
    Image.new("RGB", (24, 16), "white").save(image, format="JPEG")
    return image.getvalue()


def test_purchase_task_is_persisted_and_idempotency_key_binds_its_input(client: TestClient) -> None:
    token = login(client)
    idempotency_key = str(uuid4())
    headers = {
        "Authorization": f"Bearer {token}",
        "Idempotency-Key": idempotency_key,
    }
    payload = {"url": "https://shop.example.com/products/linen-shirt"}

    first = client.post("/v2/tasks/purchase-analysis", headers=headers, json=payload)
    replay = client.post("/v2/tasks/purchase-analysis", headers=headers, json=payload)
    conflict = client.post(
        "/v2/tasks/purchase-analysis",
        headers=headers,
        json={"url": "https://shop.example.com/products/other"},
    )

    assert first.status_code == 202
    assert replay.status_code == 202
    assert replay.json()["id"] == first.json()["id"]
    assert first.json()["status"] == "queued"
    assert first.json()["kind"] == "purchase_url"
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "idempotency_conflict"

    from app.database import get_session_factory
    from app.models import AnalysisTask

    with get_session_factory()() as db:
        task = db.get(AnalysisTask, first.json()["id"])
        assert task is not None
        assert task.input_parameters["url"] == payload["url"]

    fetched = client.get(
        f"/v2/tasks/{first.json()['id']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert fetched.status_code == 200


def test_v2_purchase_task_rejects_private_url_before_enqueue(client: TestClient) -> None:
    token = login(client)
    response = client.post(
        "/v2/tasks/purchase-analysis",
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": str(uuid4())},
        json={"url": "http://127.0.0.1/latest/meta-data/"},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "unsafe_url_destination"


def test_worker_processes_purchase_task_once_and_records_provider_operation(monkeypatch, client: TestClient) -> None:
    from app.ai import AiAnalysis
    from app.config import get_settings
    from app.database import get_session_factory
    from app.models import AnalysisTask, ProviderOperation, PurchaseCandidate
    import app.task_worker as task_worker_module
    from app.task_worker import TaskWorker

    token = login(client)
    created = client.post(
        "/v2/tasks/purchase-analysis",
        headers={
            "Authorization": f"Bearer {token}",
            "Idempotency-Key": str(uuid4()),
        },
        json={"url": "https://shop.example.com/products/linen-shirt"},
    )
    assert created.status_code == 202
    task_id = created.json()["id"]

    async def fake_extract(url: str):
        from app.product_extraction import ExtractedProductImage

        return ExtractedProductImage(
            product_url=url,
            source_image_url="https://cdn.example.com/linen-shirt.jpg",
            image_bytes=_small_jpeg(),
            content_type="image/jpeg",
            title="Linen Shirt",
            domain="shop.example.com",
            price="199.00",
        )

    class SuccessfulAiService:
        def __init__(self, settings) -> None:
            pass

        async def analyze_garment(self, **kwargs):
            return AiAnalysis(
                category="top",
                colors=["white"],
                style="casual",
                material="linen",
                season=["summer"],
                fit="regular",
                tags=["shirt"],
                confidence=0.9,
                raw={"category": "top"},
                analysis_source="remote",
                provider="qwen",
                model_version="test-model",
                provider_usage={"prompt_tokens": 40, "completion_tokens": 10, "total_tokens": 50},
                reported_cost={"amount": "0.0012", "currency": "USD"},
            )

    monkeypatch.setattr(task_worker_module, "extract_product_image", fake_extract)
    monkeypatch.setattr(task_worker_module, "AiService", SuccessfulAiService)
    worker = TaskWorker(get_session_factory(), get_settings())

    processed = anyio.run(worker.run_once, backend="asyncio")

    assert processed is True
    with get_session_factory()() as db:
        task = db.get(AnalysisTask, task_id)
        candidates = db.query(PurchaseCandidate).filter_by(user_id=task.user_id).all()
        operations = db.query(ProviderOperation).filter_by(task_id=task_id).all()
        assert task.status == "succeeded"
        assert task.result_refs == [{"resource_type": "purchase_candidate", "id": candidates[0].id}]
        assert len(candidates) == 1
        assert candidates[0].analysis_source == "remote"
        assert len(operations) == 1
        assert operations[0].submission_status == "completed"

    assert anyio.run(worker.run_once, backend="asyncio") is False

    fetched = client.get(
        f"/v2/tasks/{task_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert fetched.status_code == 200
    operation = fetched.json()["operations"][0]
    assert operation["provider"] == "qwen"
    assert operation["submission_status"] == "completed"
    assert operation["duration_ms"] >= 0
    assert operation["usage"] == {"prompt_tokens": 40, "completion_tokens": 10, "total_tokens": 50}
    assert operation["reported_cost"] == {"amount": "0.0012", "currency": "USD"}
    assert "request_parameters" not in operation
    assert "image_bytes" not in operation
    assert {"validating", "downloading", "recognizing", "scoring", "persisting"}.issubset(
        set(fetched.json()["stage_durations_ms"])
    )


def test_worker_marks_ambiguous_provider_timeout_unknown_and_refuses_retry(monkeypatch, client: TestClient) -> None:
    from app.ai import AiAnalysisError
    from app.config import get_settings
    from app.database import get_session_factory
    from app.models import AnalysisTask, ProviderOperation
    import app.task_worker as task_worker_module
    from app.task_worker import TaskWorker

    token = login(client)
    created = client.post(
        "/v2/tasks/purchase-analysis",
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": str(uuid4())},
        json={"url": "https://shop.example.com/products/timeout"},
    )

    async def fake_extract(url: str):
        from app.product_extraction import ExtractedProductImage

        return ExtractedProductImage(
            product_url=url,
            source_image_url="https://cdn.example.com/timeout.jpg",
            image_bytes=_small_jpeg(),
            content_type="image/jpeg",
            title="Timeout",
            domain="shop.example.com",
        )

    class TimeoutAiService:
        def __init__(self, settings) -> None:
            pass

        async def analyze_garment(self, **kwargs):
            raise AiAnalysisError("provider_timeout", retryable=True)

    monkeypatch.setattr(task_worker_module, "extract_product_image", fake_extract)
    monkeypatch.setattr(task_worker_module, "AiService", TimeoutAiService)
    worker = TaskWorker(get_session_factory(), get_settings())

    assert anyio.run(worker.run_once, backend="asyncio") is True
    task_id = created.json()["id"]
    with get_session_factory()() as db:
        task = db.get(AnalysisTask, task_id)
        operation = db.query(ProviderOperation).filter_by(task_id=task_id).one()
        assert task.status == "running"
        assert task.stage == "awaiting_resolution"
        assert operation.submission_status == "unknown"

    retried = client.post(
        f"/v2/tasks/{task_id}/retry",
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": str(uuid4())},
        json={},
    )
    assert retried.status_code == 409
    assert retried.json()["detail"]["code"] == "provider_submission_unknown"


def test_worker_recovers_completed_provider_result_without_second_call(monkeypatch, client: TestClient) -> None:
    from app.config import get_settings
    from app.database import get_session_factory
    from app.models import AnalysisTask, ProviderOperation, PurchaseCandidate, utc_now
    import app.task_worker as task_worker_module
    from app.task_worker import TaskWorker
    from datetime import timedelta

    token = login(client)
    created = client.post(
        "/v2/tasks/purchase-analysis",
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": str(uuid4())},
        json={"url": "https://shop.example.com/products/recovered"},
    )
    task_id = created.json()["id"]
    with get_session_factory()() as db:
        task = db.get(AnalysisTask, task_id)
        task.status = "running"
        task.stage = "recognizing"
        task.attempt_count = 1
        task.lease_token = str(uuid4())
        task.lease_expires_at = utc_now() - timedelta(seconds=1)
        db.add(
            ProviderOperation(
                task_id=task_id,
                operation_key="tag:0",
                generation=1,
                provider="qwen",
                submission_status="completed",
                request_fingerprint="a" * 64,
                result_data={
                    "category": "top",
                    "colors": ["white"],
                    "style": "casual",
                    "material": "linen",
                    "season": ["summer"],
                    "fit": "regular",
                    "tags": ["shirt"],
                    "confidence": 0.9,
                    "raw": {"category": "top"},
                    "analysis_source": "remote",
                    "provider": "qwen",
                    "model_version": "test-model",
                },
            )
        )
        db.commit()

    async def fake_extract(url: str):
        from app.product_extraction import ExtractedProductImage

        return ExtractedProductImage(
            product_url=url,
            source_image_url="https://cdn.example.com/recovered.jpg",
            image_bytes=_small_jpeg(),
            content_type="image/jpeg",
            title="Recovered shirt",
            domain="shop.example.com",
        )

    class NeverAiService:
        def __init__(self, settings) -> None:
            pass

        async def analyze_garment(self, **kwargs):
            raise AssertionError("completed provider result must be reused")

    monkeypatch.setattr(task_worker_module, "extract_product_image", fake_extract)
    monkeypatch.setattr(task_worker_module, "AiService", NeverAiService)
    worker = TaskWorker(get_session_factory(), get_settings())

    assert anyio.run(worker.run_once, backend="asyncio") is True
    with get_session_factory()() as db:
        task = db.get(AnalysisTask, task_id)
        candidates = db.query(PurchaseCandidate).filter_by(user_id=task.user_id).all()
        operations = db.query(ProviderOperation).filter_by(task_id=task_id).all()
        assert task.status == "succeeded"
        assert len(candidates) == 1
        assert len(operations) == 1
        assert operations[0].submission_status == "completed"


def test_v2_garment_upload_queues_validated_image_as_private_asset(client: TestClient) -> None:
    from app.database import get_session_factory
    from app.models import AnalysisTask, StoredAsset

    token = login(client)
    image = BytesIO()
    Image.new("RGB", (24, 16), "white").save(image, format="JPEG")
    response = client.post(
        "/v2/tasks/garment-upload",
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": str(uuid4())},
        data={"mode": "single"},
        files={"file": ("shirt.jpg", image.getvalue(), "image/jpeg")},
    )

    assert response.status_code == 202
    assert response.json()["kind"] == "garment_single"
    with get_session_factory()() as db:
        task = db.get(AnalysisTask, response.json()["id"])
        asset = db.get(StoredAsset, task.input_refs["asset_id"])
        thumbnail = db.get(StoredAsset, asset.thumbnail_asset_id)
        assert asset.storage_key.startswith(f"{task.user_id}/")
        assert asset.storage_key != asset.thumbnail_asset_id
        assert thumbnail is not None
        assert asset.byte_size <= 10 * 1024 * 1024


def test_worker_turns_garment_upload_into_pending_review_asset(monkeypatch, client: TestClient) -> None:
    from app.ai import AiAnalysis
    from app.config import get_settings
    from app.database import get_session_factory
    from app.models import AnalysisTask, Garment
    import app.task_worker as task_worker_module
    from app.task_worker import TaskWorker

    token = login(client)
    response = client.post(
        "/v2/tasks/garment-upload",
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": str(uuid4())},
        data={"mode": "single"},
        files={"file": ("wool-coat.jpg", _small_jpeg(), "image/jpeg")},
    )
    task_id = response.json()["id"]

    class SuccessfulAiService:
        def __init__(self, settings) -> None:
            pass

        async def analyze_garment(self, **kwargs):
            return AiAnalysis(
                category="outerwear",
                colors=["gray"],
                style="classic",
                material="wool",
                season=["winter"],
                fit="regular",
                tags=["coat"],
                confidence=0.9,
                raw={"category": "outerwear"},
                analysis_source="remote",
                provider="qwen",
                model_version="test-model",
            )

    monkeypatch.setattr(task_worker_module, "AiService", SuccessfulAiService)
    worker = TaskWorker(get_session_factory(), get_settings())

    assert anyio.run(worker.run_once, backend="asyncio") is True
    with get_session_factory()() as db:
        task = db.get(AnalysisTask, task_id)
        garment = db.get(Garment, task.result_refs[0]["id"])
        assert task.status == "succeeded"
        assert garment.review_status == "pending_review"
        assert garment.analysis_source == "remote"
        assert garment.asset_id == task.input_refs["asset_id"]


def test_worker_splits_one_photo_into_separately_tracked_pending_garments(client: TestClient) -> None:
    from app.config import get_settings
    from app.database import get_session_factory
    from app.models import AnalysisTask, Garment, ProviderOperation
    from app.task_worker import TaskWorker

    token = login(client)
    image = BytesIO()
    Image.new("RGB", (1000, 1000), (225, 220, 215)).save(image, format="JPEG")
    created = client.post(
        "/v2/tasks/garment-upload",
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": str(uuid4())},
        data={"mode": "split"},
        files={"file": ("multi-look.jpg", image.getvalue(), "image/jpeg")},
    )
    assert created.status_code == 202

    worker = TaskWorker(get_session_factory(), get_settings())
    assert anyio.run(worker.run_once, backend="asyncio") is True

    with get_session_factory()() as db:
        task = db.get(AnalysisTask, created.json()["id"])
        garments = list(db.query(Garment).filter(Garment.id.in_([ref["id"] for ref in task.result_refs])).all())
        operations = list(db.query(ProviderOperation).filter(ProviderOperation.task_id == task.id).all())
        assert task.status == "succeeded", task.error_code
        assert len(garments) == 2
        assert {item.category for item in garments} == {"top", "bottom"}
        assert all(item.review_status == "pending_review" and item.asset_id for item in garments)
        assert {(item.operation_key, item.submission_status) for item in operations} == {
            ("split:0", "completed"), ("tag:1", "completed"), ("tag:2", "completed")
        }


def test_worker_analyzes_uploaded_purchase_image_without_owning_it(monkeypatch, client: TestClient) -> None:
    from app.ai import AiAnalysis
    from app.config import get_settings
    from app.database import get_session_factory
    from app.models import AnalysisTask, PurchaseCandidate
    import app.task_worker as task_worker_module
    from app.task_worker import TaskWorker

    token = login(client)
    response = client.post(
        "/v2/tasks/purchase-analysis",
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": str(uuid4())},
        data={"product_url": "https://shop.example.com/products/blue-shirt"},
        files={"file": ("blue-shirt.jpg", _small_jpeg(), "image/jpeg")},
    )
    assert response.status_code == 202

    class SuccessfulAiService:
        def __init__(self, settings) -> None:
            pass

        async def analyze_garment(self, **kwargs):
            return AiAnalysis(
                category="top",
                colors=["blue"],
                style="casual",
                material="cotton",
                season=["summer"],
                fit="regular",
                tags=["shirt"],
                confidence=0.9,
                raw={"category": "top"},
                analysis_source="remote",
                provider="qwen",
                model_version="test-model",
            )

    monkeypatch.setattr(task_worker_module, "AiService", SuccessfulAiService)
    worker = TaskWorker(get_session_factory(), get_settings())

    assert anyio.run(worker.run_once, backend="asyncio") is True
    with get_session_factory()() as db:
        task = db.get(AnalysisTask, response.json()["id"])
        candidate = db.get(PurchaseCandidate, task.result_refs[0]["id"])
        assert task.status == "succeeded"
        assert candidate.asset_id == task.input_refs["asset_id"]
        assert candidate.product_url == "https://shop.example.com/products/blue-shirt"
        assert candidate.analysis_source == "remote"
        assert candidate.ownership_state == "never_saved"
        assert db.query(PurchaseCandidate).count() == 1


def test_worker_does_not_claim_more_than_two_running_tasks_for_one_user(client: TestClient) -> None:
    from datetime import timedelta

    from app.config import get_settings
    from app.database import get_session_factory
    from app.models import AnalysisTask, utc_now
    from app.task_worker import TaskWorker

    token = login(client, "parallel@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    created = [
        client.post(
            "/v2/tasks/purchase-analysis",
            headers={**headers, "Idempotency-Key": str(uuid4())},
            json={"url": f"https://shop.example.com/items/{index}"},
        )
        for index in range(3)
    ]
    assert all(response.status_code == 202 for response in created)
    with get_session_factory()() as db:
        for response in created[:2]:
            task = db.get(AnalysisTask, response.json()["id"])
            task.status = "running"
            task.lease_token = str(uuid4())
            task.lease_expires_at = utc_now() + timedelta(seconds=60)
        db.commit()

    worker = TaskWorker(get_session_factory(), get_settings())
    assert worker._claim_next() is None


def test_provider_hourly_quota_defers_task_before_remote_submission(monkeypatch, client: TestClient) -> None:
    from app.config import get_settings
    from app.database import get_session_factory
    from app.models import AnalysisTask, RateLimitCounter, ProviderOperation, utc_now
    import app.task_worker as task_worker_module
    from app.task_worker import TaskWorker
    from datetime import timedelta

    token = login(client, "quota@example.com")
    created = client.post(
        "/v2/tasks/garment-upload",
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": str(uuid4())},
        data={"mode": "single"},
        files={"file": ("coat.jpg", _small_jpeg(), "image/jpeg")},
    )
    assert created.status_code == 202
    with get_session_factory()() as db:
        task = db.get(AnalysisTask, created.json()["id"])
        db.add(
            RateLimitCounter(
                scope="provider_submit",
                key=task.user_id,
                window_seconds=3600,
                count=20,
                window_started_at=utc_now(),
                reset_at=utc_now() + timedelta(minutes=30),
            )
        )
        db.commit()

    class NeverAiService:
        def __init__(self, settings) -> None:
            pass

        async def analyze_garment(self, **kwargs):
            raise AssertionError("quota exhaustion must defer before provider submission")

    monkeypatch.setattr(task_worker_module, "AiService", NeverAiService)
    settings = get_settings()
    settings.ai_demo_mode = False
    worker = TaskWorker(get_session_factory(), settings)
    assert anyio.run(worker.run_once, backend="asyncio") is True

    with get_session_factory()() as db:
        task = db.get(AnalysisTask, created.json()["id"])
        assert task.status == "queued"
        assert task.lease_token is None
        assert db.query(ProviderOperation).filter_by(task_id=task.id).count() == 0


def test_recommendation_item_analysis_runs_as_private_persisted_task(monkeypatch, client: TestClient) -> None:
    from types import SimpleNamespace

    from app.ai import AiAnalysis
    from app.config import get_settings
    from app.database import get_session_factory
    from app.models import (
        AnalysisTask,
        PurchaseCandidate,
        ShoppingRecommendationItem,
        ShoppingRecommendationRun,
        User,
    )
    import app.task_worker as task_worker_module
    from app.task_worker import TaskWorker

    token = login(client, "recommendation-worker@example.com")
    with get_session_factory()() as db:
        user = db.query(User).filter_by(email="recommendation-worker@example.com").one()
        run = ShoppingRecommendationRun(user_id=user.id, target="auto_gap", status="ready")
        db.add(run)
        db.flush()
        item = ShoppingRecommendationItem(
            run_id=run.id,
            user_id=user.id,
            platform="shop",
            platform_item_id="product-42",
            title="Blue linen shirt",
            image_url="https://cdn.example.com/products/linen-shirt.jpg",
            product_url="https://shop.example.com/products/linen-shirt",
            price="199.00",
        )
        db.add(item)
        db.commit()
        item_id = item.id

    queued = client.post(
        "/v2/tasks/recommendation-analysis",
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": str(uuid4())},
        json={"item_id": item_id},
    )
    assert queued.status_code == 202

    async def fake_download(url: str):
        assert url == "https://cdn.example.com/products/linen-shirt.jpg"
        return SimpleNamespace(image_url=url, image_bytes=_small_jpeg(), content_type="image/jpeg")

    class SuccessfulAiService:
        def __init__(self, settings) -> None:
            pass

        async def analyze_garment(self, **kwargs):
            return AiAnalysis(
                category="top",
                colors=["blue"],
                style="casual",
                material="linen",
                season=["summer"],
                fit="regular",
                tags=["shirt"],
                confidence=0.91,
                raw={"category": "top"},
                analysis_source="remote",
                provider="qwen",
                model_version="test-model",
            )

    monkeypatch.setattr(task_worker_module, "download_public_image", fake_download)
    monkeypatch.setattr(task_worker_module, "AiService", SuccessfulAiService)
    worker = TaskWorker(get_session_factory(), get_settings())
    assert anyio.run(worker.run_once, backend="asyncio") is True

    with get_session_factory()() as db:
        task = db.get(AnalysisTask, queued.json()["id"])
        item = db.get(ShoppingRecommendationItem, item_id)
        candidate = db.get(PurchaseCandidate, task.result_refs[0]["id"])
        assert task.status == "succeeded"
        assert item.analysis_status == "analyzed"
        assert item.purchase_candidate_id == candidate.id
        assert candidate.analysis_source == "remote"
        assert candidate.asset_id is not None


def test_recommendation_analysis_task_rejects_another_users_item(client: TestClient) -> None:
    from app.database import get_session_factory
    from app.models import ShoppingRecommendationItem, ShoppingRecommendationRun, User

    token = login(client, "recommendation-owner@example.com")
    with get_session_factory()() as db:
        user = db.query(User).filter_by(email="recommendation-owner@example.com").one()
        run = ShoppingRecommendationRun(user_id=user.id, target="auto_gap", status="ready")
        db.add(run)
        db.flush()
        item = ShoppingRecommendationItem(
            run_id=run.id,
            user_id=user.id,
            platform="shop",
            platform_item_id="private-product",
            title="Private",
            image_url="https://cdn.example.com/private.jpg",
            product_url="https://shop.example.com/private",
        )
        db.add(item)
        db.commit()
        item_id = item.id

    other_token = login(client, "recommendation-other@example.com")
    response = client.post(
        "/v2/tasks/recommendation-analysis",
        headers={"Authorization": f"Bearer {other_token}", "Idempotency-Key": str(uuid4())},
        json={"item_id": item_id},
    )
    assert response.status_code == 404


def test_asset_images_require_owner_issued_short_lived_access_token(client: TestClient) -> None:
    from app.database import get_session_factory
    from app.models import AnalysisTask

    owner_token = login(client, "asset-owner@example.com")
    other_token = login(client, "asset-other@example.com")
    image = BytesIO()
    Image.new("RGB", (32, 24), "red").save(image, format="JPEG")
    task = client.post(
        "/v2/tasks/garment-upload",
        headers={"Authorization": f"Bearer {owner_token}", "Idempotency-Key": str(uuid4())},
        data={"mode": "single"},
        files={"file": ("private.jpg", image.getvalue(), "image/jpeg")},
    )
    with get_session_factory()() as db:
        task_row = db.get(AnalysisTask, task.json()["id"])
        asset_id = task_row.input_refs["asset_id"]

    access = client.get(
        f"/v2/assets/{asset_id}/access?variant=original",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    other_access = client.get(
        f"/v2/assets/{asset_id}/access?variant=original",
        headers={"Authorization": f"Bearer {other_token}"},
    )
    image_response = client.get(access.json()["url"])
    unsigned = client.get(
        f"/v2/assets/{asset_id}/content?variant=original",
        headers={"Authorization": f"Bearer {owner_token}"},
    )

    assert access.status_code == 200
    assert other_access.status_code == 404
    assert image_response.status_code == 200
    assert image_response.headers["cache-control"] == "private, no-store"
    assert image_response.headers["content-type"] == "image/jpeg"
    assert unsigned.status_code == 422
