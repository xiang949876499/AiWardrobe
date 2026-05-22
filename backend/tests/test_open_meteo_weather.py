from fastapi.testclient import TestClient

import app.routers.weather as weather_module
from tests.conftest import login


class FakeResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return {
            "current": {
                "temperature_2m": 18.6,
                "apparent_temperature": 17.2,
                "precipitation": 0.4,
                "weather_code": 61,
                "wind_speed_10m": 12.8,
            }
        }


class FakeClient:
    calls: list[dict[str, object]] = []

    def __init__(self, timeout: int) -> None:
        self.timeout = timeout

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def get(self, url: str, params: dict[str, object]) -> FakeResponse:
        self.calls.append({"url": url, "params": params})
        return FakeResponse()


def test_today_weather_fetches_current_conditions_from_open_meteo(client: TestClient, monkeypatch) -> None:
    token = login(client)
    FakeClient.calls.clear()
    monkeypatch.setattr(weather_module.httpx, "Client", FakeClient)

    response = client.get("/weather/today?lat=31.2304&lon=121.4737", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    body = response.json()
    call = FakeClient.calls[0]
    assert call["url"] == "https://api.open-meteo.com/v1/forecast"
    assert call["params"]["latitude"] == 31.2304
    assert call["params"]["longitude"] == 121.4737
    assert call["params"]["current"] == "temperature_2m,apparent_temperature,precipitation,weather_code,wind_speed_10m"
    assert call["params"]["timezone"] == "auto"
    assert body["condition"] == "Rain"
    assert body["temperature"] == 19
    assert body["feels_like"] == 17
    assert body["precipitation"] == 0.4
    assert body["wind_speed"] == 12.8


def test_today_weather_cache_skips_second_open_meteo_call(client: TestClient, monkeypatch) -> None:
    token = login(client)
    FakeClient.calls.clear()
    monkeypatch.setattr(weather_module.httpx, "Client", FakeClient)

    first = client.get("/weather/today?lat=31.2304&lon=121.4737", headers={"Authorization": f"Bearer {token}"})
    second = client.get("/weather/today?lat=31.2311&lon=121.4740", headers={"Authorization": f"Bearer {token}"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["cached"] is False
    assert second.json()["cached"] is True
    assert len(FakeClient.calls) == 1
