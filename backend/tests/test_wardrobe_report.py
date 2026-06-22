from fastapi.testclient import TestClient

from tests.conftest import login


def test_empty_wardrobe_report_has_onboarding_gaps(client: TestClient) -> None:
    token = login(client)

    response = client.get("/reports/wardrobe", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 0
    assert body["wardrobe_gaps"]
    assert body["avoid_categories"] == []
    assert body["summary"]


def test_wardrobe_report_summarizes_categories_colors_and_duplicates(client: TestClient) -> None:
    token = login(client)
    for name in ["white-shirt.jpg", "white-shirt-2.jpg", "black-pants.jpg"]:
        upload = client.post(
            "/uploads/plain-garment",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": (name, b"image", "image/jpeg")},
        )
        assert upload.status_code == 201

    response = client.get("/reports/wardrobe", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert body["category_distribution"]
    assert body["color_distribution"]
    assert "duplicate_risks" in body
    assert "suggested_categories" in body
