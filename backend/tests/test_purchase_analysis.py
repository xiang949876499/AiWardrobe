from fastapi.testclient import TestClient

from app.ai import AiAnalysis
from app.models import Garment
from tests.conftest import login


def test_product_image_extraction_prefers_og_image_and_resolves_relative_url() -> None:
    from app.product_extraction import select_product_metadata

    metadata = select_product_metadata(
        """
        <html>
          <head>
            <title>Black Linen Shirt</title>
            <meta property="og:image" content="/images/hero-shirt.jpg" />
            <meta name="twitter:image" content="https://cdn.example.com/fallback.jpg" />
          </head>
          <body><img src="https://cdn.example.com/body.jpg" width="1200" height="1400" /></body>
        </html>
        """,
        "https://shop.example.com/products/shirt",
    )

    assert metadata.image_url == "https://shop.example.com/images/hero-shirt.jpg"
    assert metadata.title == "Black Linen Shirt"
    assert metadata.domain == "shop.example.com"


def test_product_image_extraction_returns_recoverable_error_when_no_image() -> None:
    from app.product_extraction import ProductExtractionError, select_product_metadata

    try:
        select_product_metadata("<html><head><title>No image</title></head><body></body></html>", "https://example.com/item")
    except ProductExtractionError as exc:
        assert exc.code == "product_image_not_found"
    else:
        raise AssertionError("Expected product_image_not_found")


def test_similarity_scoring_identifies_near_duplicates() -> None:
    from app.purchase_analysis import score_similarity

    candidate = AiAnalysis(
        category="top",
        colors=["black"],
        style="casual",
        material="cotton",
        season=["summer"],
        fit="regular",
        tags=["t-shirt", "basic"],
        confidence=0.9,
        raw={},
    )
    existing = Garment(
        user_id="user-1",
        image_url="/static/uploads/black-tee.jpg",
        image_key="garments/black-tee.jpg",
        thumbnail_url="/static/uploads/black-tee.jpg",
        category="top",
        colors=["black"],
        style="casual",
        material="cotton",
        season=["summer"],
        fit="regular",
        tags=["t-shirt"],
        status="ready",
    )

    result = score_similarity(candidate, existing)

    assert result.similarity >= 80
    assert "same category" in result.matched_reasons
    assert "similar color" in result.matched_reasons


def test_purchase_analysis_creates_candidate_without_creating_garment(monkeypatch, client: TestClient) -> None:
    token = login(client)
    client.post(
        "/garments/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("black-shirt.jpg", b"existing image", "image/jpeg")},
    )

    async def fake_extract_product(url: str):
        from app.product_extraction import ExtractedProductImage

        return ExtractedProductImage(
            product_url=url,
            source_image_url="https://shop.example.com/black-shirt.jpg",
            image_bytes=b"candidate image",
            content_type="image/jpeg",
            title="Black Shirt",
            domain="shop.example.com",
        )

    import app.routers.purchase as purchase_router

    monkeypatch.setattr(purchase_router, "extract_product_image", fake_extract_product)

    response = client.post(
        "/purchase/analyze",
        headers={"Authorization": f"Bearer {token}"},
        json={"url": "https://shop.example.com/products/black-shirt"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "ready"
    assert body["product_url"] == "https://shop.example.com/products/black-shirt"
    assert body["source_image_url"] == "https://shop.example.com/black-shirt.jpg"
    assert body["image_url"].startswith("/static/uploads/purchase/")
    assert body["recommendation"] in ["recommend", "consider", "skip"]
    assert body["similar_items"]

    listed = client.get("/garments", headers={"Authorization": f"Bearer {token}"})
    assert len(listed.json()["items"]) == 1


def test_save_purchase_candidate_creates_ready_garment(monkeypatch, client: TestClient) -> None:
    token = login(client)

    async def fake_extract_product(url: str):
        from app.product_extraction import ExtractedProductImage

        return ExtractedProductImage(
            product_url=url,
            source_image_url="https://shop.example.com/blue-jeans.jpg",
            image_bytes=b"candidate image",
            content_type="image/jpeg",
            title="Blue Jeans",
            domain="shop.example.com",
        )

    import app.routers.purchase as purchase_router

    monkeypatch.setattr(purchase_router, "extract_product_image", fake_extract_product)
    candidate = client.post(
        "/purchase/analyze",
        headers={"Authorization": f"Bearer {token}"},
        json={"url": "https://shop.example.com/products/blue-jeans"},
    ).json()

    saved = client.post(
        f"/purchase/candidates/{candidate['id']}/save",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert saved.status_code == 201
    garment = saved.json()
    assert garment["status"] == "ready"
    assert garment["image_url"] == candidate["image_url"]
    assert garment["image_key"] == candidate["image_key"]

    listed = client.get("/garments", headers={"Authorization": f"Bearer {token}"})
    assert listed.json()["items"][0]["id"] == garment["id"]
