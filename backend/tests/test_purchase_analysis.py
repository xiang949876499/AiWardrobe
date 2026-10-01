import pytest
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.ai import AiAnalysis
from app.models import Garment
from app.schemas import PurchaseCandidateResponse
from tests.conftest import jpeg_bytes, login


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


def test_product_extraction_reads_jd_script_image_and_price() -> None:
    from app.product_extraction import select_product_metadata

    metadata = select_product_metadata(
        """
        <html>
          <head><title>森马短袖T恤 京东自营</title></head>
          <body>
            <script>
              var pageConfig = {
                product: {
                  name: '森马短袖T恤',
                  imageList: ['//img14.360buyimg.com/n1/jfs/t1/123/456/main-shirt.jpg']
                },
                price: { jdPrice: '79.90' }
              };
            </script>
          </body>
        </html>
        """,
        "https://item.jd.com/10212037610954.html",
    )

    assert metadata.image_url == "https://img14.360buyimg.com/n1/jfs/t1/123/456/main-shirt.jpg"
    assert metadata.title == "森马短袖T恤 京东自营"
    assert metadata.price == "79.90"


def test_product_extraction_rejects_jd_homepage_redirect() -> None:
    from app.product_extraction import ProductExtractionError, select_product_metadata

    try:
        select_product_metadata(
            """
            <html>
              <head>
                <title>京东(JD.COM)-正品低价、品质保障、配送及时、轻松购物！</title>
                <meta property="og:image" content="//storage.360buyimg.com/retail-mall/logo.png" />
              </head>
              <body>网上购物，上京东</body>
            </html>
            """,
            "https://www.jd.com?from=pc_item_sd",
        )
    except ProductExtractionError as exc:
        assert exc.code == "product_page_blocked"
    else:
        raise AssertionError("Expected JD homepage redirect to be rejected")


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
    existing = client.post(
        "/garments/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("black-shirt.jpg", jpeg_bytes(), "image/jpeg")},
    ).json()
    confirmed = client.patch(
        f"/garments/{existing['id']}",
        headers={"Authorization": f"Bearer {token}"},
        json={"tags": existing["tags"]},
    )
    assert confirmed.status_code == 200

    async def fake_extract_product(url: str):
        from app.product_extraction import ExtractedProductImage

        return ExtractedProductImage(
            product_url=url,
            source_image_url="https://shop.example.com/black-shirt.jpg",
            image_bytes=jpeg_bytes(),
            content_type="image/jpeg",
            title="Black Shirt",
            domain="shop.example.com",
            price="129.00",
        )

    import app.task_worker as purchase_router

    monkeypatch.setattr(purchase_router, "extract_product_image", fake_extract_product)

    response = client.post(
        "/purchase/analyze",
        headers={"Authorization": f"Bearer {token}"},
        json={"url": "https://shop.example.com/products/black-shirt"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "evidence_insufficient"
    detail = client.get(f"/v2/purchase/candidates/{response.json()['detail']['candidate_id']}",
                        headers={"Authorization": f"Bearer {token}"})
    assert detail.status_code == 200
    body = detail.json()
    assert body["analysis_status"] == "ready"
    assert body["product_url"] == "https://shop.example.com/products/black-shirt"
    assert body["image_url"].startswith("/v2/assets/")
    assert body["recommendation"] in ["recommend", "consider", "skip"]
    assert body["similar_items"]
    analysis = body["analysis"]
    assert analysis["conclusion"] in ["recommend", "consider", "skip"]
    assert analysis["score"] == body["score"]
    assert body["actual_price"] == "129.00"
    assert body["currency"] == "CNY"
    assert analysis["dimensions"]["duplicate_risk"] >= 0
    # One confirmed top establishes duplicate comparison, not a complete outfit.
    assert analysis["dimensions"]["outfit_potential"] is None
    assert analysis["idle_risk_detail"] is None
    assert analysis["outfit_ideas"] == []
    assert body["score"] is None
    assert analysis["suggested_price"] is None
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

    assert decision.score is None
    assert decision.recommendation == "consider"
    assert decision.analysis["dimensions"]["gap_fill"] is None
    assert decision.analysis["score_breakdown"] is None
    assert decision.analysis["suggested_price"] is None
    assert decision.analysis["match_scenes"]
    assert decision.analysis["cons"]
    assert decision.analysis["next_actions"] == ["upload_wardrobe", "analyze_another"]


def test_purchase_hard_constraints_apply_without_wardrobe_and_unknown_price_stays_null() -> None:
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

    avoided = analyze_purchase(candidate, [], {"avoid_categories": ["top"]})
    over_budget = analyze_purchase(
        candidate,
        [],
        {"budget_max": 100},
        actual_price="129.00",
        currency="CNY",
    )
    unknown_price = analyze_purchase(candidate, [], {"budget_max": 100})

    assert avoided.recommendation == "skip"
    assert avoided.score is None
    assert "category_avoided" in avoided.evidence["hard_constraints"]
    assert over_budget.recommendation == "skip"
    assert "over_budget" in over_budget.evidence["hard_constraints"]
    assert unknown_price.recommendation == "consider"
    assert unknown_price.score is None
    assert unknown_price.analysis["suggested_price"] is None
    assert unknown_price.coverage["dimensions"]["budget_match"] is None


def test_purchase_analysis_reports_provider_failure_without_saving_candidate(
    monkeypatch,
    client: TestClient,
) -> None:
    from app.ai import AiAnalysisError
    from app.database import get_session_factory
    from app.models import PurchaseCandidate

    token = login(client)

    async def fake_extract_product(url: str):
        from app.product_extraction import ExtractedProductImage

        return ExtractedProductImage(
            product_url=url,
            source_image_url="https://shop.example.com/failure.jpg",
            image_bytes=jpeg_bytes(),
            content_type="image/jpeg",
            title="Failure Candidate",
            domain="shop.example.com",
        )

    class FailedAiService:
        def __init__(self, settings) -> None:
            pass

        async def analyze_garment(self, **kwargs):
            raise AiAnalysisError("provider_timeout", retryable=True)

    import app.task_worker as purchase_router

    monkeypatch.setattr(purchase_router, "extract_product_image", fake_extract_product)
    monkeypatch.setattr(purchase_router, "AiService", FailedAiService)
    response = client.post(
        "/purchase/analyze",
        headers={"Authorization": f"Bearer {token}"},
        json={"url": "https://shop.example.com/products/failure"},
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "analysis_pending"
    task = client.get(f"/v2/tasks/{response.json()['detail']['task_id']}", headers={"Authorization": f"Bearer {token}"}).json()
    assert task["error"]["code"] == "provider_timeout"
    assert task["submission_status"] == "unknown"
    with get_session_factory()() as db:
        assert db.query(PurchaseCandidate).count() == 0


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
    assert analysis.suggested_price is None
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
    existing = client.post(
        "/garments/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("owned-shirt.jpg", jpeg_bytes(), "image/jpeg")},
    ).json()
    confirmed = client.patch(
        f"/garments/{existing['id']}",
        headers={"Authorization": f"Bearer {token}"},
        json={"tags": existing["tags"]},
    )
    assert confirmed.status_code == 200

    async def fake_extract_product(url: str):
        from app.product_extraction import ExtractedProductImage

        return ExtractedProductImage(
            product_url=url,
            source_image_url="https://shop.example.com/blue-jeans.jpg",
            image_bytes=jpeg_bytes(),
            content_type="image/jpeg",
            title="Blue Jeans",
            domain="shop.example.com",
        )

    import app.task_worker as purchase_router

    monkeypatch.setattr(purchase_router, "extract_product_image", fake_extract_product)
    insufficient = client.post(
        "/purchase/analyze",
        headers={"Authorization": f"Bearer {token}"},
        json={"url": "https://shop.example.com/products/blue-jeans"},
    )
    assert insufficient.status_code == 409
    candidate = client.get(f"/v2/purchase/candidates/{insufficient.json()['detail']['candidate_id']}",
                           headers={"Authorization": f"Bearer {token}"}).json()

    saved = client.post(
        f"/purchase/candidates/{candidate['id']}/save",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert saved.status_code == 201
    garment = saved.json()
    assert garment["status"] == "ready"
    assert garment["image_url"].startswith("/v2/assets/")
    assert garment["image_key"] == ""
    assert garment["image_url"].split("/content")[0] == candidate["image_url"].split("/content")[0]

    listed = client.get("/garments", headers={"Authorization": f"Bearer {token}"})
    assert listed.json()["items"][0]["id"] == garment["id"]


pytestmark = pytest.mark.usefixtures("legacy_worker")
