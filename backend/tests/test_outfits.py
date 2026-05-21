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
