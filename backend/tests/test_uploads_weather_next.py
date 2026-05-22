from fastapi.testclient import TestClient

from tests.conftest import login


def _jpeg_bytes(width: int = 1000, height: int = 1000) -> bytes:
    from io import BytesIO

    from PIL import Image

    output = BytesIO()
    Image.new("RGB", (width, height), color=(240, 240, 240)).save(output, format="JPEG")
    return output.getvalue()


def test_garment_photo_upload_creates_session_and_pending_review_items(client: TestClient) -> None:
    token = login(client)

    uploaded = client.post(
        "/uploads/garment-photo",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("multi-look.jpg", b"fake image bytes", "image/jpeg")},
    )

    assert uploaded.status_code == 201
    body = uploaded.json()
    assert body["status"] == "pending_review"
    assert body["original_image_url"].startswith("/static/uploads/")
    assert len(body["garments"]) >= 1
    assert body["garments"][0]["status"] == "pending_review"
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
    crop_path = tmp_path / "uploads" / first["image_key"]
    assert crop_path.exists()

    from PIL import Image

    with Image.open(crop_path) as image:
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
    item_path = tmp_path / "uploads" / first["image_key"]
    assert item_path.exists()
    assert first["crop_box"] is None
    assert first["ai_result"]["crop_box"] is None

    from PIL import Image

    with Image.open(item_path) as image:
        assert image.size == (1400, 1200)


def test_confirming_ai_tags_moves_item_into_ready_wardrobe(client: TestClient) -> None:
    token = login(client)
    uploaded = client.post(
        "/uploads/garment-photo",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("shirt.jpg", b"fake image bytes", "image/jpeg")},
    ).json()
    garment_id = uploaded["garments"][0]["id"]

    confirmed = client.patch(
        f"/garments/{garment_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"category": "top", "tags": ["通勤"], "style": "通勤", "review_status": "confirmed"},
    )

    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "ready"
    assert confirmed.json()["review_status"] == "confirmed"


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
            files={"file": (filename, b"fake image bytes", "image/jpeg")},
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
            files={"file": (filename, b"fake image bytes", "image/jpeg")},
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
