from fastapi.testclient import TestClient

from tests.conftest import login


def _create_garment(client: TestClient, token: str, filename: str, category: str) -> str:
    created = client.post(
        "/garments/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": (filename, b"fake image bytes", "image/jpeg")},
    ).json()
    patched = client.patch(
        f"/garments/{created['id']}",
        headers={"Authorization": f"Bearer {token}"},
        json={"category": category, "tags": ["通勤"]},
    )
    assert patched.status_code == 200
    return created["id"]


def test_generates_outfit_and_saves_history(client: TestClient) -> None:
    token = login(client)
    _create_garment(client, token, "shirt.jpg", "top")
    _create_garment(client, token, "trousers.jpg", "bottom")
    _create_garment(client, token, "loafers.jpg", "shoes")

    generated = client.post(
        "/outfits/generate",
        headers={"Authorization": f"Bearer {token}"},
        json={"occasion": "work", "season": "spring", "temperature": 22},
    )

    assert generated.status_code == 201
    outfit = generated.json()
    assert outfit["occasion"] == "work"
    assert len(outfit["items"]) >= 3
    assert "上班" in outfit["explanation"] or "work" in outfit["explanation"].lower()

    history = client.get("/outfits/history", headers={"Authorization": f"Bearer {token}"})

    assert history.status_code == 200
    assert history.json()["items"][0]["id"] == outfit["id"]


def test_generated_outfit_uses_database_image_urls_even_when_ai_hallucinates(monkeypatch, client: TestClient) -> None:
    token = login(client)
    top_id = _create_garment(client, token, "shirt.jpg", "top")
    _create_garment(client, token, "trousers.jpg", "bottom")
    _create_garment(client, token, "loafers.jpg", "shoes")

    async def fake_generate_outfit(self, garments, occasion, season, temperature, weather=None):
        return [
            {
                "garment_id": top_id,
                "category": "top",
                "image_url": "http://localhost:9100/aiwardrobe/missing.png",
                "reason": "AI returned a stale URL",
            }
        ], "AI 搭配"

    import app.routers.outfits as outfits_module

    monkeypatch.setattr(outfits_module.AiService, "generate_outfit", fake_generate_outfit)

    generated = client.post(
        "/outfits/generate",
        headers={"Authorization": f"Bearer {token}"},
        json={"occasion": "work", "season": "spring", "temperature": 22},
    )

    assert generated.status_code == 201
    item = generated.json()["items"][0]
    assert item["garment_id"] == top_id
    assert item["image_url"] != "http://localhost:9100/aiwardrobe/missing.png"
    assert item["image_url"].startswith("/static/uploads/garments/")


def test_generated_outfit_includes_target_garment_even_when_ai_omits_it(monkeypatch, client: TestClient) -> None:
    token = login(client)
    _create_garment(client, token, "shirt.jpg", "top")
    target_id = _create_garment(client, token, "trousers.jpg", "bottom")
    shoes_id = _create_garment(client, token, "loafers.jpg", "shoes")

    async def fake_generate_outfit(self, garments, occasion, season, temperature, weather=None):
        return [
            {
                "garment_id": shoes_id,
                "category": "shoes",
                "reason": "AI omitted the requested garment",
            }
        ], "AI outfit"

    import app.routers.outfits as outfits_module

    monkeypatch.setattr(outfits_module.AiService, "generate_outfit", fake_generate_outfit)

    generated = client.post(
        "/outfits/generate",
        headers={"Authorization": f"Bearer {token}"},
        json={"occasion": "work", "season": "spring", "temperature": 22, "garment_id": target_id},
    )

    assert generated.status_code == 201
    item_ids = [item["garment_id"] for item in generated.json()["items"]]
    assert target_id in item_ids
    assert item_ids[0] == target_id


def test_generating_outfit_derives_season_when_client_omits_it(monkeypatch, client: TestClient) -> None:
    token = login(client)
    _create_garment(client, token, "shirt.jpg", "top")
    _create_garment(client, token, "trousers.jpg", "bottom")
    _create_garment(client, token, "loafers.jpg", "shoes")
    seen: dict[str, str] = {}

    async def fake_generate_outfit(self, garments, occasion, season, temperature, weather=None):
        seen["season"] = season
        return [
            {
                "garment_id": garments[0].id,
                "category": garments[0].category,
                "reason": "ok",
            }
        ], "AI 搭配"

    import app.routers.outfits as outfits_module

    monkeypatch.setattr(outfits_module, "current_season", lambda settings: "summer")
    monkeypatch.setattr(outfits_module.AiService, "generate_outfit", fake_generate_outfit)

    generated = client.post(
        "/outfits/generate",
        headers={"Authorization": f"Bearer {token}"},
        json={"occasion": "work", "temperature": 31},
    )

    assert generated.status_code == 201
    assert generated.json()["season"] == "summer"
    assert seen["season"] == "summer"


def test_generating_outfit_requires_enough_ready_garments(client: TestClient) -> None:
    token = login(client)

    generated = client.post(
        "/outfits/generate",
        headers={"Authorization": f"Bearer {token}"},
        json={"occasion": "work", "season": "spring", "temperature": 22},
    )

    assert generated.status_code == 400
    assert generated.json()["detail"] == "Not enough ready garments to generate an outfit"


def test_favorites_outfit(client: TestClient) -> None:
    token = login(client)
    _create_garment(client, token, "shirt.jpg", "top")
    _create_garment(client, token, "trousers.jpg", "bottom")
    _create_garment(client, token, "loafers.jpg", "shoes")
    outfit = client.post(
        "/outfits/generate",
        headers={"Authorization": f"Bearer {token}"},
        json={"occasion": "work", "season": "spring", "temperature": 22},
    ).json()

    favorited = client.patch(
        f"/outfits/{outfit['id']}/favorite",
        headers={"Authorization": f"Bearer {token}"},
        json={"is_favorite": True},
    )

    assert favorited.status_code == 200
    assert favorited.json()["is_favorite"] is True

    favorites = client.get(
        "/outfits/history?favorite=true",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert len(favorites.json()["items"]) == 1


def test_deletes_outfit_from_history_and_favorites(client: TestClient) -> None:
    token = login(client)
    _create_garment(client, token, "shirt.jpg", "top")
    _create_garment(client, token, "trousers.jpg", "bottom")
    _create_garment(client, token, "loafers.jpg", "shoes")
    outfit = client.post(
        "/outfits/generate",
        headers={"Authorization": f"Bearer {token}"},
        json={"occasion": "work", "season": "spring", "temperature": 22},
    ).json()
    client.patch(
        f"/outfits/{outfit['id']}/favorite",
        headers={"Authorization": f"Bearer {token}"},
        json={"is_favorite": True},
    )

    deleted = client.delete(
        f"/outfits/{outfit['id']}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert deleted.status_code == 204
    history = client.get("/outfits/history", headers={"Authorization": f"Bearer {token}"})
    favorites = client.get("/outfits/history?favorite=true", headers={"Authorization": f"Bearer {token}"})
    assert history.json()["items"] == []
    assert favorites.json()["items"] == []
