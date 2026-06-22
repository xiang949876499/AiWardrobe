from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.ai import AiAnalysis
from app.models import Garment
from app.schemas import PurchaseCandidateResponse
from tests.conftest import login


def _purchase_candidate_payload(analysis: dict[str, object], **overrides: object) -> dict[str, object]:
    created_at = datetime.now(timezone.utc)
    payload: dict[str, object] = {
        "id": "candidate-normalized",
        "product_url": "https://shop.example.com/products/normalized",
        "source_image_url": "https://shop.example.com/normalized.jpg",
        "image_url": "/static/uploads/purchase/normalized.jpg",
        "image_key": "purchase/normalized.jpg",
        "thumbnail_url": None,
        "title": "Normalized Candidate",
        "domain": "shop.example.com",
        "category": "top",
        "colors": ["black"],
        "style": "casual",
        "material": "cotton",
        "season": ["summer"],
        "fit": "regular",
        "tags": ["basic"],
        "ai_result": {},
        "ai_confidence": 0.9,
        "similar_items": [],
        "recommendation": "consider",
        "score": 72,
        "reason_summary": "Normalized deterministic summary",
        "analysis": analysis,
        "status": "ready",
        "created_at": created_at,
        "updated_at": created_at,
    }
    payload.update(overrides)
    return payload


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
    analysis = body["analysis"]
    assert analysis["conclusion"] in ["recommend", "consider", "skip"]
    assert analysis["score"] == body["score"]
    assert analysis["dimensions"]["duplicate_risk"] >= 0
    assert analysis["dimensions"]["outfit_potential"] >= 0
    assert analysis["idle_risk_detail"]["level"] in ["低", "中", "高"]
    assert analysis["suggested_price"]["ideal"] > 0
    assert "analyze_another" in analysis["next_actions"]

    listed = client.get("/garments", headers={"Authorization": f"Bearer {token}"})
    assert len(listed.json()["items"]) == 1


def test_purchase_analysis_exposes_score_breakdown() -> None:
    from app.purchase_analysis import analyze_purchase

    candidate = AiAnalysis(
        category="top",
        colors=["black"],
        style="casual",
        material="cotton",
        season=["summer"],
        fit="regular",
        tags=["basic"],
        confidence=0.9,
        raw={},
    )

    decision = analyze_purchase(candidate, [])

    assert decision.analysis["dimensions"]["gap_fill"] == decision.analysis["wardrobe_gap_score"]
    assert decision.analysis["score_breakdown"]["wardrobe_gap"] >= 0
    assert decision.analysis["match_scenes"]
    assert decision.analysis["pros"]
    assert decision.analysis["next_actions"] == ["save", "share", "analyze_another", "upload_wardrobe"]


def test_purchase_candidate_response_backfills_legacy_analysis_shape() -> None:
    created_at = datetime.now(timezone.utc)
    response = PurchaseCandidateResponse.model_validate(
        {
            "id": "candidate-legacy",
            "product_url": "https://shop.example.com/products/legacy",
            "source_image_url": "https://shop.example.com/legacy.jpg",
            "image_url": "/static/uploads/purchase/legacy.jpg",
            "image_key": "purchase/legacy.jpg",
            "thumbnail_url": None,
            "title": "Legacy Candidate",
            "domain": "shop.example.com",
            "category": "top",
            "colors": ["black"],
            "style": "casual",
            "material": "cotton",
            "season": ["summer"],
            "fit": "regular",
            "tags": ["basic"],
            "ai_result": {},
            "ai_confidence": 0.9,
            "similar_items": [],
            "recommendation": "consider",
            "score": 61,
            "reason_summary": "Legacy deterministic summary",
            "analysis": {
                "duplicate_score": 20,
                "wardrobe_gap_score": 70,
                "pairing_score": 55,
                "decision_factors": ["legacy factor"],
            },
            "status": "ready",
            "created_at": created_at,
            "updated_at": created_at,
        }
    )

    analysis = response.analysis
    assert analysis.conclusion == "consider"
    assert analysis.score == 61
    assert analysis.summary == "Legacy deterministic summary"
    assert analysis.dimensions.duplicate_risk == 20
    assert analysis.dimensions.gap_fill == 70
    assert analysis.dimensions.outfit_potential == 55
    assert analysis.score_breakdown.wardrobe_gap == 70
    assert analysis.duplicate_score == 20
    assert analysis.wardrobe_gap_score == 70
    assert analysis.pairing_score == 55
    assert analysis.decision_factors == ["legacy factor"]
    assert analysis.suggested_price.ideal > 0
    assert "analyze_another" in analysis.next_actions


def test_purchase_candidate_response_derives_aliases_from_structured_analysis() -> None:
    created_at = datetime.now(timezone.utc)
    response = PurchaseCandidateResponse.model_validate(
        {
            "id": "candidate-structured",
            "product_url": "https://shop.example.com/products/structured",
            "source_image_url": "https://shop.example.com/structured.jpg",
            "image_url": "/static/uploads/purchase/structured.jpg",
            "image_key": "purchase/structured.jpg",
            "thumbnail_url": None,
            "title": "Structured Candidate",
            "domain": "shop.example.com",
            "category": "top",
            "colors": ["black"],
            "style": "casual",
            "material": "cotton",
            "season": ["summer"],
            "fit": "regular",
            "tags": ["basic"],
            "ai_result": {},
            "ai_confidence": 0.9,
            "similar_items": [],
            "recommendation": "recommend",
            "score": 82,
            "reason_summary": "Structured deterministic summary",
            "analysis": {
                "conclusion": "recommend",
                "score": 82,
                "summary": "Structured deterministic summary",
                "dimensions": {
                    "outfit_potential": 71,
                    "scene_match": 79,
                    "gap_fill": 64,
                    "duplicate_risk": 23,
                    "idle_risk": 38,
                },
                "duplicate_risk": 23,
                "idle_risk": 38,
                "outfit_potential": 71,
                "match_scenes": ["日常", "通勤"],
                "suggested_price": {"min": 80, "ideal": 138, "max": 220},
                "score_breakdown": {
                    "duplicate_risk": 23,
                    "wardrobe_gap": 64,
                    "outfit_potential": 71,
                    "scene_match": 79,
                    "idle_risk": 38,
                },
                "pros": ["补足衣橱缺口"],
                "cons": ["暂无明显风险"],
                "outfit_ideas": [],
                "idle_risk_detail": {"level": "低", "reason": "结构化风险说明"},
                "next_actions": ["save", "share", "analyze_another", "upload_wardrobe"],
                "decision_factors": ["structured factor"],
            },
            "status": "ready",
            "created_at": created_at,
            "updated_at": created_at,
        }
    )

    analysis = response.analysis
    assert analysis.duplicate_score == 23
    assert analysis.wardrobe_gap_score == 64
    assert analysis.pairing_score == 71
    assert analysis.dimensions.duplicate_risk == 23
    assert analysis.dimensions.gap_fill == 64
    assert analysis.dimensions.outfit_potential == 71
    assert analysis.dimensions.scene_match == 79
    assert analysis.score_breakdown.wardrobe_gap == 64
    assert analysis.idle_risk == 38
    assert analysis.idle_risk_detail == {"level": "低", "reason": "结构化风险说明"}


def test_purchase_candidate_response_prefers_nested_scores_over_zero_alias_placeholders() -> None:
    response = PurchaseCandidateResponse.model_validate(
        _purchase_candidate_payload(
            {
                "conclusion": "consider",
                "score": 72,
                "summary": "Mixed alias payload",
                "dimensions": {
                    "outfit_potential": 66,
                    "scene_match": 78,
                    "gap_fill": 58,
                    "duplicate_risk": 21,
                    "idle_risk": 36,
                },
                "duplicate_risk": 0,
                "idle_risk": 0,
                "outfit_potential": 0,
                "match_scenes": ["daily"],
                "suggested_price": {"min": 80, "ideal": 130, "max": 200},
                "score_breakdown": {
                    "duplicate_risk": 21,
                    "wardrobe_gap": 58,
                    "outfit_potential": 66,
                    "scene_match": 78,
                    "idle_risk": 36,
                },
                "pros": ["structured pro"],
                "cons": ["structured con"],
                "outfit_ideas": [],
                "idle_risk_detail": {"level": "low", "reason": "structured risk"},
                "next_actions": ["save", "share", "analyze_another", "upload_wardrobe"],
                "duplicate_score": 0,
                "wardrobe_gap_score": 0,
                "pairing_score": 0,
                "decision_factors": ["mixed factor"],
            }
        )
    )

    analysis = response.analysis
    assert analysis.duplicate_score == 21
    assert analysis.wardrobe_gap_score == 58
    assert analysis.pairing_score == 66
    assert analysis.duplicate_risk == 21
    assert analysis.idle_risk == 36
    assert analysis.outfit_potential == 66
    assert analysis.dimensions.duplicate_risk == 21
    assert analysis.dimensions.gap_fill == 58
    assert analysis.dimensions.outfit_potential == 66
    assert analysis.score_breakdown.wardrobe_gap == 58
    assert analysis.idle_risk_detail["level"] == "低"


def test_purchase_candidate_response_prefers_nested_scores_over_zero_top_level_placeholders() -> None:
    response = PurchaseCandidateResponse.model_validate(
        _purchase_candidate_payload(
            {
                "conclusion": "consider",
                "score": 72,
                "summary": "Mixed top-level payload",
                "dimensions": {
                    "outfit_potential": 73,
                    "scene_match": 81,
                    "gap_fill": 62,
                    "duplicate_risk": 18,
                    "idle_risk": 34,
                },
                "duplicate_risk": 0,
                "idle_risk": 0,
                "outfit_potential": 0,
                "match_scenes": ["daily"],
                "suggested_price": {"min": 80, "ideal": 130, "max": 200},
                "score_breakdown": {
                    "duplicate_risk": 18,
                    "wardrobe_gap": 62,
                    "outfit_potential": 73,
                    "scene_match": 81,
                    "idle_risk": 34,
                },
                "pros": ["structured pro"],
                "cons": ["structured con"],
                "outfit_ideas": [],
                "idle_risk_detail": {"level": "low", "reason": "structured risk"},
                "next_actions": ["save", "share", "analyze_another", "upload_wardrobe"],
                "decision_factors": ["mixed factor"],
            }
        )
    )

    analysis = response.analysis
    assert analysis.duplicate_score == 18
    assert analysis.wardrobe_gap_score == 62
    assert analysis.pairing_score == 73
    assert analysis.duplicate_risk == 18
    assert analysis.idle_risk == 34
    assert analysis.outfit_potential == 73
    assert analysis.dimensions.duplicate_risk == 18
    assert analysis.dimensions.scene_match == 81
    assert analysis.score_breakdown.outfit_potential == 73
    assert analysis.idle_risk_detail["level"] == "低"


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
