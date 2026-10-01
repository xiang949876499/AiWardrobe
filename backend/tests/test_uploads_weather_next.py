import pytest
from fastapi.testclient import TestClient

from app.ai import AiAnalysis
from app.comfyui import ComfyUIOutput
from app.config import get_settings
from app.runninghub import RunningHubOutput
from tests.conftest import login


def _jpeg_bytes(width: int = 1000, height: int = 1000) -> bytes:
    from io import BytesIO

    from PIL import Image

    output = BytesIO()
    Image.new("RGB", (width, height), color=(240, 240, 240)).save(output, format="JPEG")
    return output.getvalue()


def _png_bytes() -> bytes:
    from io import BytesIO

    from PIL import Image

    output = BytesIO()
    Image.new("RGBA", (64, 48), (240, 240, 240, 180)).save(output, format="PNG")
    return output.getvalue()


def test_garment_photo_upload_creates_session_and_ready_items(client: TestClient) -> None:
    token = login(client)

    uploaded = client.post(
        "/uploads/garment-photo",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("multi-look.jpg", _jpeg_bytes(), "image/jpeg")},
    )

    assert uploaded.status_code == 201
    body = uploaded.json()
    assert body["status"] == "ready"
    assert body["original_image_url"].startswith("/v2/assets/")
    assert body["original_image_key"] == ""
    assert len(body["garments"]) >= 1
    assert body["garments"][0]["status"] == "ready"
    assert body["garments"][0]["review_status"] == "pending_review"
    assert body["garments"][0]["crop_box"]

    fetched = client.get(f"/uploads/{body['id']}", headers={"Authorization": f"Bearer {token}"})

    assert fetched.status_code == 200
    assert fetched.json()["id"] == body["id"]
    assert fetched.json()["garments"][0]["id"] == body["garments"][0]["id"]


def test_valid_uploaded_photo_writes_cropped_item_image(client: TestClient, tmp_path) -> None:
    token = login(client)

    uploaded = client.post(
        "/uploads/garment-photo",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("multi-look.jpg", _jpeg_bytes(), "image/jpeg")},
    )

    assert uploaded.status_code == 201
    first = uploaded.json()["garments"][0]
    downloaded = client.get(first["image_url"])
    assert downloaded.status_code == 200
    from PIL import Image
    from io import BytesIO

    with Image.open(BytesIO(downloaded.content)) as image:
        assert image.size == (first["crop_box"]["width"], first["crop_box"]["height"])


def test_single_item_upload_without_detection_box_keeps_full_image(client: TestClient, tmp_path) -> None:
    token = login(client)

    uploaded = client.post(
        "/uploads/garment-photo",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("shirt.jpg", _jpeg_bytes(width=1400, height=1200), "image/jpeg")},
    )

    assert uploaded.status_code == 201
    first = uploaded.json()["garments"][0]
    assert first["crop_box"] is None
    assert first["ai_result"]["crop_box"] is None

    from PIL import Image
    from io import BytesIO

    image_response = client.get(first["image_url"])
    assert image_response.status_code == 200
    with Image.open(BytesIO(image_response.content)) as image:
        assert image.size == (480, 411)
    original_response = client.get(uploaded.json()["original_image_url"])
    with Image.open(BytesIO(original_response.content)) as image:
        assert image.size == (1400, 1200)


def test_editing_tags_keeps_item_in_ready_wardrobe(client: TestClient) -> None:
    token = login(client)
    uploaded = client.post(
        "/uploads/garment-photo",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("shirt.jpg", _jpeg_bytes(), "image/jpeg")},
    ).json()
    garment_id = uploaded["garments"][0]["id"]

    confirmed = client.patch(
        f"/garments/{garment_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"tags": ["通勤", "常穿"]},
    )

    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "ready"
    assert confirmed.json()["review_status"] == "confirmed"
    assert confirmed.json()["tags"] == ["通勤", "常穿"]


def test_plain_upload_creates_ready_item_with_vl_tags(monkeypatch, client: TestClient) -> None:
    token = login(client)
    calls: list[tuple[str, str, bytes]] = []

    async def analyze(self, filename: str, content_type: str, image_bytes: bytes):
        calls.append((filename, content_type, image_bytes))
        return AiAnalysis(
            category="bag",
            colors=["黑色"],
            style="通勤",
            material="皮革",
            season=["四季通用"],
            fit="标准",
            tags=["托特包", "通勤", "皮革"],
            confidence=0.91,
            raw={"category": "包", "sub_category": "托特包", "confidence": 0.91},
        )

    import app.task_worker as uploads_module

    monkeypatch.setattr(uploads_module.AiService, "analyze_garment", analyze)
    uploaded = client.post(
        "/uploads/plain-garment",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("single-shirt.jpg", _jpeg_bytes(), "image/jpeg")},
    )

    assert uploaded.status_code == 201
    body = uploaded.json()
    assert calls[0][0:2] == ("single-shirt.jpg", "image/jpeg")
    assert calls[0][2].startswith(b"\xff\xd8")
    assert body["image_url"].startswith("/v2/assets/")
    assert body["category"] == "bag"
    assert body["colors"] == ["黑色"]
    assert body["tags"] == ["托特包", "通勤", "皮革"]
    assert body["ai_result"]["source"] == "plain_upload"
    assert body["ai_result"]["category"] == "包"
    assert body["ai_confidence"] == 0.91
    assert body["status"] == "ready"
    assert body["review_status"] == "pending_review"


def test_auto_recognition_records_persistent_provider_operations(client: TestClient) -> None:
    from sqlalchemy import select
    from app.database import get_session_factory
    from app.models import ProviderOperation
    token = login(client)
    uploaded = client.post(
        "/uploads/garment-photo", headers={"Authorization": f"Bearer {token}"},
        files={"file": ("shirt.jpg", _jpeg_bytes(), "image/jpeg")},
    )
    assert uploaded.status_code == 201
    with get_session_factory()() as db:
        operations = list(db.scalars(select(ProviderOperation)))
        assert {operation.operation_key for operation in operations} == {"split:0", "tag:1"}
        assert all(operation.submission_status == "completed" for operation in operations)
    assert len(uploaded.json()["garments"]) == 1


def test_auto_recognition_can_use_comfyui_outputs(monkeypatch, client: TestClient, tmp_path) -> None:
    token = login(client)
    monkeypatch.setenv("WORKFLOW_PROVIDER", "comfyui")
    get_settings.cache_clear()

    import app.task_worker as uploads_module

    class FakeComfyUIClient:
        def __init__(self, settings) -> None:
            self.settings = settings

        async def submit_garment_recognition(self, filename: str, content_type: str, image_bytes: bytes):
            return "accepted-test-workflow"

        async def collect_garment_recognition(self, remote_id: str):
            return [
                ComfyUIOutput("top", "top.png", "", "output", _png_bytes()),
                ComfyUIOutput("bottom", "pants.png", "", "output", _png_bytes()),
                ComfyUIOutput("shoes", "shoes.png", "", "output", _png_bytes()),
            ]

    monkeypatch.setattr(uploads_module, "ComfyUIClient", FakeComfyUIClient)
    uploaded = client.post(
        "/uploads/garment-photo",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("look.jpg", _jpeg_bytes(), "image/jpeg")},
    )

    assert uploaded.status_code == 201
    body = uploaded.json()
    assert [garment["category"] for garment in body["garments"]] == ["top", "bottom", "shoes"]
    assert body["garments"][0]["ai_result"]["workflow_provider"] == "comfyui"
    assert body["garments"][0]["ai_result"]["comfyui_filename"] == "top.png"
    assert all(client.get(item["image_url"]).status_code == 200 for item in body["garments"])
    assert all(item["image_key"] == "" for item in body["garments"])


def test_auto_recognition_can_use_runninghub_outputs(monkeypatch, client: TestClient, tmp_path) -> None:
    token = login(client)
    monkeypatch.setenv("WORKFLOW_PROVIDER", "runninghub")
    monkeypatch.setenv("RUNNINGHUB_API_KEY", "rh-key")
    get_settings.cache_clear()

    import app.task_worker as uploads_module

    class FakeRunningHubClient:
        def __init__(self, settings) -> None:
            self.settings = settings

        async def submit_garment_recognition(self, filename: str, content_type: str, image_bytes: bytes):
            return "accepted-test-workflow"

        async def collect_garment_recognition(self, remote_id: str):
            return [
                RunningHubOutput("top.png", "image/png", _png_bytes(), file_url="https://example.com/top.png", node_id="139"),
                RunningHubOutput("pants.png", "image/png", _png_bytes(), file_url="https://example.com/pants.png", node_id="151"),
                RunningHubOutput("shoes.png", "image/png", _png_bytes(), file_url="https://example.com/shoes.png", node_id="156"),
            ]

    monkeypatch.setattr(uploads_module, "RunningHubClient", FakeRunningHubClient)
    uploaded = client.post(
        "/uploads/garment-photo",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("look.jpg", _jpeg_bytes(), "image/jpeg")},
    )

    assert uploaded.status_code == 201
    body = uploaded.json()
    assert len(body["garments"]) == 3
    assert body["garments"][0]["ai_result"]["workflow_provider"] == "runninghub"
    assert body["garments"][0]["ai_result"]["runninghub_filename"] == "top.png"
    assert body["garments"][0]["ai_result"]["runninghub_file_url"] == "https://example.com/top.png"
    assert body["garments"][0]["ai_result"]["runninghub_node_id"] == "139"
    assert all(client.get(item["image_url"]).status_code == 200 for item in body["garments"])
    assert all(item["image_key"] == "" for item in body["garments"])


def test_runninghub_auto_recognition_reports_client_error(monkeypatch, client: TestClient) -> None:
    token = login(client)
    monkeypatch.setenv("WORKFLOW_PROVIDER", "runninghub")
    monkeypatch.setenv("RUNNINGHUB_API_KEY", "rh-key")
    from app.config import get_settings

    get_settings.cache_clear()

    import app.task_worker as uploads_module

    class FakeRunningHubClient:
        def __init__(self, settings) -> None:
            self.settings = settings

        async def submit_garment_recognition(self, filename: str, content_type: str, image_bytes: bytes):
            return "accepted-test-workflow"

        async def collect_garment_recognition(self, remote_id: str):
            raise RuntimeError("workflow app rejected request")

    monkeypatch.setattr(uploads_module, "RunningHubClient", FakeRunningHubClient)
    uploaded = client.post(
        "/uploads/garment-photo",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("look.jpg", _jpeg_bytes(), "image/jpeg")},
    )

    assert uploaded.status_code == 503
    assert uploaded.json()["detail"]["code"] == "analysis_pending"
    assert uploaded.json()["detail"]["task_id"]
    # An accepted remote workflow whose polling fails must remain recoverable.
    assert client.get("/garments", headers={"Authorization": f"Bearer {token}"}).json()["items"] == []


def test_comfyui_auto_recognition_failure_does_not_fallback_to_original(monkeypatch, client: TestClient) -> None:
    token = login(client)
    monkeypatch.setenv("WORKFLOW_PROVIDER", "comfyui")
    get_settings.cache_clear()

    import app.task_worker as uploads_module

    class FakeComfyUIClient:
        def __init__(self, settings) -> None:
            self.settings = settings

        async def submit_garment_recognition(self, filename: str, content_type: str, image_bytes: bytes):
            return "accepted-test-workflow"

        async def collect_garment_recognition(self, remote_id: str):
            return []

    monkeypatch.setattr(uploads_module, "ComfyUIClient", FakeComfyUIClient)
    uploaded = client.post(
        "/uploads/garment-photo",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("look.jpg", _jpeg_bytes(), "image/jpeg")},
    )

    assert uploaded.status_code == 502
    assert uploaded.json()["detail"]["code"] == "no_garments_detected"


def test_today_weather_is_cached_for_same_location(client: TestClient) -> None:
    token = login(client)

    first = client.get("/weather/today?lat=31.2304&lon=121.4737", headers={"Authorization": f"Bearer {token}"})
    second = client.get("/weather/today?lat=31.2311&lon=121.4740", headers={"Authorization": f"Bearer {token}"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["cached"] is False
    assert second.json()["cached"] is True
    assert second.json()["temperature"] == first.json()["temperature"]


def test_ai_outfit_uses_ready_items_and_weather_snapshot(client: TestClient) -> None:
    token = login(client)
    ready_ids: list[str] = []
    for filename, category in [("shirt.jpg", "top"), ("pants.jpg", "bottom"), ("shoes.jpg", "shoes")]:
        uploaded = client.post(
            "/uploads/garment-photo",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": (filename, _jpeg_bytes(), "image/jpeg")},
        ).json()
        garment_id = uploaded["garments"][0]["id"]
        client.patch(
            f"/garments/{garment_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"category": category, "review_status": "confirmed"},
        )
        ready_ids.append(garment_id)

    generated = client.post(
        "/outfits/generate",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "occasion": "work",
            "season": "spring",
            "temperature": 22,
            "weather": {"condition": "Cloudy", "temperature": 22, "city": "Shanghai"},
        },
    )

    assert generated.status_code == 201
    body = generated.json()
    assert body["source"] == "ai"
    assert body["weather_snapshot"]["condition"] == "Cloudy"
    assert {item["garment_id"] for item in body["items"]}.issubset(set(ready_ids))


def test_manual_outfit_can_be_saved_as_fixed(client: TestClient) -> None:
    token = login(client)
    garment_ids: list[str] = []
    for filename, category in [("shirt.jpg", "top"), ("pants.jpg", "bottom")]:
        uploaded = client.post(
            "/garments/upload",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": (filename, _jpeg_bytes(), "image/jpeg")},
        ).json()
        patched = client.patch(
            f"/garments/{uploaded['id']}",
            headers={"Authorization": f"Bearer {token}"},
            json={"category": category, "review_status": "confirmed"},
        )
        garment_ids.append(patched.json()["id"])

    manual = client.post(
        "/outfits/manual",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "周一通勤", "garment_ids": garment_ids, "occasion": "work", "is_fixed": True},
    )

    assert manual.status_code == 201
    assert manual.json()["source"] == "manual"
    assert manual.json()["is_fixed"] is True
    assert manual.json()["name"] == "周一通勤"

    unfixed = client.patch(
        f"/outfits/{manual.json()['id']}/fixed",
        headers={"Authorization": f"Bearer {token}"},
        json={"is_fixed": False},
    )
    assert unfixed.status_code == 200
    assert unfixed.json()["is_fixed"] is False


pytestmark = pytest.mark.usefixtures("legacy_worker")
