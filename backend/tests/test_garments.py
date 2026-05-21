from fastapi.testclient import TestClient

from tests.conftest import login


def test_upload_analyzes_garment_and_lists_it(client: TestClient) -> None:
    token = login(client)

    uploaded = client.post(
        "/garments/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("white-shirt.jpg", b"fake image bytes", "image/jpeg")},
    )

    assert uploaded.status_code == 201
    garment = uploaded.json()
    assert garment["category"] in ["top", "bottom", "outerwear", "shoes", "accessory"]
    assert garment["status"] == "ready"
    assert garment["ai_confidence"] > 0
    assert garment["image_url"].startswith("/static/uploads/")
    assert "ai_result" in garment

    listed = client.get("/garments", headers={"Authorization": f"Bearer {token}"})

    assert listed.status_code == 200
    assert listed.json()["items"][0]["id"] == garment["id"]


def test_user_correction_overrides_ai_attributes(client: TestClient) -> None:
    token = login(client)
    uploaded = client.post(
        "/garments/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("jacket.jpg", b"fake image bytes", "image/jpeg")},
    ).json()

    patched = client.patch(
        f"/garments/{uploaded['id']}",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "category": "outerwear",
            "colors": ["black"],
            "style": "通勤",
            "material": "羊毛",
            "season": ["autumn", "winter"],
            "fit": "regular",
            "tags": ["上班", "正式"],
        },
    )

    assert patched.status_code == 200
    body = patched.json()
    assert body["category"] == "outerwear"
    assert body["colors"] == ["black"]
    assert body["tags"] == ["上班", "正式"]
    assert body["ai_result"]["category"] != ""


def test_filters_garments_by_category_and_tag(client: TestClient) -> None:
    token = login(client)
    first = client.post(
        "/garments/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("sneakers.jpg", b"fake image bytes", "image/jpeg")},
    ).json()
    client.patch(
        f"/garments/{first['id']}",
        headers={"Authorization": f"Bearer {token}"},
        json={"category": "shoes", "tags": ["运动"]},
    )

    listed = client.get(
        "/garments?category=shoes&tag=运动",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert listed.status_code == 200
    assert len(listed.json()["items"]) == 1
    assert listed.json()["items"][0]["category"] == "shoes"
