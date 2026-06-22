from fastapi.testclient import TestClient

from app.models import Garment
from app.wardrobe_report import build_wardrobe_report
from tests.conftest import login


def _garment(
    garment_id: str,
    category: str = "top",
    colors: list[str] | None = None,
    status: str = "ready",
) -> Garment:
    return Garment(
        id=garment_id,
        user_id="user-1",
        image_url=f"/static/uploads/{garment_id}.jpg",
        image_key=f"garments/{garment_id}.jpg",
        thumbnail_url=f"/static/uploads/{garment_id}.jpg",
        category=category,
        colors=colors or ["white"],
        style="casual",
        material="cotton",
        season=["spring"],
        fit="regular",
        tags=["casual"],
        status=status,
        review_status="confirmed",
    )


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


def test_wardrobe_report_detects_multicolor_duplicates_in_different_orders() -> None:
    report = build_wardrobe_report(
        [
            _garment("top-1", colors=[" Ivory ", "black", "ivory"]),
            _garment("top-2", colors=["black", "ivory"]),
            _garment("top-3", colors=["black"]),
        ]
    )

    risk = report["duplicate_risks"][0]
    assert risk["category"] == "top"
    assert risk["colors"] == ["black", "ivory"]
    assert risk["count"] == 2
    assert risk["garment_ids"] == ["top-1", "top-2"]


def test_color_distribution_counts_each_color_once_per_garment() -> None:
    report = build_wardrobe_report(
        [
            _garment("multi-1", colors=["black", "white", "black"]),
            _garment("multi-2", colors=["white", "black"]),
            _garment("single-1", colors=["black"]),
        ]
    )

    by_key = {item["key"]: item for item in report["color_distribution"]}
    assert by_key["black"]["count"] == 3
    assert by_key["black"]["ratio"] == 1.0
    assert by_key["white"]["count"] == 2
    assert by_key["white"]["ratio"] == 0.667


def test_wardrobe_report_summary_uses_ready_total_for_mixed_status_items() -> None:
    report = build_wardrobe_report(
        [
            _garment("ready-1", status="ready"),
            _garment("processing-1", status="processing"),
        ]
    )

    assert report["total"] == 2
    assert report["ready_total"] == 1
    assert "已入库" in str(report["summary"])
    assert "1 件" in str(report["summary"])


def test_wardrobe_report_summary_distinguishes_uploaded_but_not_ready_items() -> None:
    report = build_wardrobe_report(
        [
            _garment("processing-1", status="processing"),
            _garment("uploaded-1", status="uploaded"),
        ]
    )

    assert report["total"] == 2
    assert report["ready_total"] == 0
    assert "已有 2 件上传记录" in str(report["summary"])
    assert "待入库完成后" in str(report["summary"])
