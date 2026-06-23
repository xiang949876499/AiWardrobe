from fastapi.testclient import TestClient

from tests.conftest import login


def test_get_preferences_returns_empty_defaults(client: TestClient) -> None:
    token = login(client)

    response = client.get("/preferences/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    body = response.json()
    assert body["primary_goal"] == ""
    assert body["scenes"] == []
    assert body["styles"] == []
    assert body["avoid_types"] == []
    assert body["budget_range"] == ""


def test_put_preferences_upserts_current_user_preferences(client: TestClient) -> None:
    token = login(client)
    payload = {
        "primary_goal": "少买闲置",
        "scenes": ["通勤", "周末"],
        "styles": ["简约", "休闲"],
        "avoid_types": ["低质感针织"],
        "budget_range": "100-300",
    }

    saved = client.put("/preferences/me", headers={"Authorization": f"Bearer {token}"}, json=payload)

    assert saved.status_code == 200
    assert saved.json()["primary_goal"] == "少买闲置"
    assert saved.json()["scenes"] == ["通勤", "周末"]

    fetched = client.get("/preferences/me", headers={"Authorization": f"Bearer {token}"})

    assert fetched.status_code == 200
    assert fetched.json()["styles"] == ["简约", "休闲"]
    assert fetched.json()["budget_range"] == "100-300"
