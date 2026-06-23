from fastapi.testclient import TestClient

from app.models import Garment
from tests.conftest import login


def test_auto_gap_keywords_prioritize_missing_categories() -> None:
    from app.shopping_recommendations import generate_recommendation_keywords

    garments = [
        Garment(
            user_id="user-1",
            image_url="/static/uploads/top.jpg",
            image_key="garments/top.jpg",
            category="top",
            colors=["white"],
            style="work",
            material="cotton",
            season=["summer"],
            status="ready",
        ),
        Garment(
            user_id="user-1",
            image_url="/static/uploads/bottom.jpg",
            image_key="garments/bottom.jpg",
            category="bottom",
            colors=["black"],
            style="work",
            material="cotton",
            season=["summer"],
            status="ready",
        ),
    ]

    keywords = generate_recommendation_keywords("auto_gap", garments)

    assert keywords
    assert len(keywords) <= 5
    assert any("shoe" in keyword or "bag" in keyword for keyword in keywords)


def test_explicit_target_keywords_are_stable() -> None:
    from app.shopping_recommendations import generate_recommendation_keywords

    keywords = generate_recommendation_keywords("summer", [])

    assert keywords[:2] == ["summer breathable top", "summer lightweight trousers"]


def test_demo_taobao_client_returns_normalized_products() -> None:
    from app.taobao_client import DemoTaobaoClient

    products = DemoTaobaoClient().search(["work skirt"])

    assert products[0].platform == "taobao"
    assert products[0].platform_item_id
    assert products[0].product_url.startswith("https://")
    assert products[0].raw["demo"] is True


def test_taobao_client_parses_material_search_response() -> None:
    from app.taobao_client import parse_taobao_products

    products = parse_taobao_products(
        {
            "tbk_dg_material_optional_response": {
                "result_list": {
                    "map_data": [
                        {
                            "num_iid": "123",
                            "title": "Black Work Skirt",
                            "pict_url": "//img.alicdn.com/skirt.jpg",
                            "zk_final_price": "129.00",
                            "shop_title": "Example Shop",
                            "item_url": "//item.taobao.com/item.htm?id=123",
                        }
                    ]
                }
            }
        }
    )

    assert products[0].platform == "taobao"
    assert products[0].platform_item_id == "123"
    assert products[0].image_url == "https://img.alicdn.com/skirt.jpg"
    assert products[0].product_url == "https://item.taobao.com/item.htm?id=123"


def test_demo_recommendations_create_run_and_items(client: TestClient) -> None:
    token = login(client)
    _upload_ready_garment(client, token)

    response = client.post(
        "/shopping/recommendations",
        headers={"Authorization": f"Bearer {token}"},
        json={"target": "auto_gap", "refresh": True},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["target"] == "auto_gap"
    assert body["status"] == "ready"
    assert body["cache_hit"] is False
    assert body["keywords"]
    assert body["rate_limit"]["remaining_refreshes"] == 2
    assert body["rate_limit"]["reset_at"]
    assert body["wardrobe_gaps"]
    assert body["avoid_categories"] is not None
    assert body["recommendation_groups"]
    assert body["recommendation_groups"][0]["title"]
    assert len(body["items"]) >= 3
    assert body["items"][0]["platform"] == "taobao"
    assert body["items"][0]["analysis_status"] == "analyzed"
    assert body["items"][0]["purchase_candidate_id"]


def test_recommendations_reuse_cache_when_refresh_false(client: TestClient) -> None:
    token = login(client)
    first = client.post(
        "/shopping/recommendations",
        headers={"Authorization": f"Bearer {token}"},
        json={"target": "work", "refresh": True},
    ).json()

    second = client.post(
        "/shopping/recommendations",
        headers={"Authorization": f"Bearer {token}"},
        json={"target": "work", "refresh": False},
    )

    assert second.status_code == 200
    body = second.json()
    assert body["id"] == first["id"]
    assert body["cache_hit"] is True


def test_recommendation_refresh_limit_returns_reset_time(client: TestClient) -> None:
    token = login(client)

    for _ in range(3):
        response = client.post(
            "/shopping/recommendations",
            headers={"Authorization": f"Bearer {token}"},
            json={"target": "date", "refresh": True},
        )
        assert response.status_code == 201

    limited = client.post(
        "/shopping/recommendations",
        headers={"Authorization": f"Bearer {token}"},
        json={"target": "date", "refresh": True},
    )

    assert limited.status_code == 429
    body = limited.json()
    assert body["detail"]["code"] == "recommendation_rate_limited"
    assert body["detail"]["reset_at"]


def test_analyze_item_creates_purchase_candidate(client: TestClient) -> None:
    token = login(client)
    run = client.post(
        "/shopping/recommendations",
        headers={"Authorization": f"Bearer {token}"},
        json={"target": "basics", "refresh": True},
    ).json()
    pending = next(item for item in run["items"] if item["analysis_status"] == "pending_analysis")

    analyzed = client.post(
        f"/shopping/recommendations/items/{pending['id']}/analyze",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert analyzed.status_code == 200
    body = analyzed.json()
    assert body["analysis_status"] == "analyzed"
    assert body["purchase_candidate_id"]
    assert body["score"] >= 0
    assert body["reason_summary"]


def test_failed_auto_analysis_keeps_recommendation_run_usable(client: TestClient, monkeypatch) -> None:
    from app import shopping_recommendations
    from app.taobao_client import TaobaoClientError

    def fail_image_download(*args, **kwargs):
        raise TaobaoClientError("image_download_failed")

    monkeypatch.setattr(shopping_recommendations, "_product_image_payload", fail_image_download)
    token = login(client)

    response = client.post(
        "/shopping/recommendations",
        headers={"Authorization": f"Bearer {token}"},
        json={"target": "auto_gap", "refresh": True},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "ready"
    assert len(body["items"]) >= 3
    assert body["items"][0]["analysis_status"] == "failed"
    assert body["items"][0]["purchase_candidate_id"] is None
    assert any(item["analysis_status"] == "pending_analysis" for item in body["items"])


def test_repeated_product_reuses_existing_analysis_candidate(client: TestClient) -> None:
    token = login(client)

    first = client.post(
        "/shopping/recommendations",
        headers={"Authorization": f"Bearer {token}"},
        json={"target": "basics", "refresh": True},
    ).json()
    second = client.post(
        "/shopping/recommendations",
        headers={"Authorization": f"Bearer {token}"},
        json={"target": "basics", "refresh": True},
    ).json()

    first_by_product = {item["platform_item_id"]: item for item in first["items"]}
    reused = next(item for item in second["items"] if item["platform_item_id"] in first_by_product)
    assert reused["analysis_status"] == "analyzed"
    assert reused["purchase_candidate_id"] == first_by_product[reused["platform_item_id"]]["purchase_candidate_id"]


def test_analyzed_recommendation_response_includes_similar_items(client: TestClient) -> None:
    token = login(client)
    _upload_ready_garment(client, token)

    response = client.post(
        "/shopping/recommendations",
        headers={"Authorization": f"Bearer {token}"},
        json={"target": "basics", "refresh": True},
    )

    assert response.status_code == 201
    analyzed_items = [item for item in response.json()["items"] if item["analysis_status"] == "analyzed"]
    assert any(item["similar_items"] for item in analyzed_items)


def _upload_ready_garment(client: TestClient, token: str) -> None:
    response = client.post(
        "/uploads/plain-garment",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("white-shirt.jpg", b"existing image", "image/jpeg")},
    )
    assert response.status_code == 201
