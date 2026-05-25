from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_db
from app.models import User, WeatherCache
from app.schemas import WeatherResponse
from app.security import get_current_user

router = APIRouter(prefix="/weather", tags=["weather"])


@router.get("/today", response_model=WeatherResponse)
def get_today_weather(
    lat: float = Query(ge=-90, le=90),
    lon: float = Query(ge=-180, le=180),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> WeatherResponse:
    now = datetime.now(timezone.utc)
    date_key = now.date().isoformat()
    lat_key = f"{lat:.2f}"
    lon_key = f"{lon:.2f}"
    cached = db.execute(
        select(WeatherCache).where(
            WeatherCache.user_id == current_user.id,
            WeatherCache.date == date_key,
            WeatherCache.lat_key == lat_key,
            WeatherCache.lon_key == lon_key,
            WeatherCache.expires_at > now,
        )
    ).scalar_one_or_none()
    if cached is not None:
        return _weather_response(cached, cached=True)

    current = _fetch_open_meteo_current(settings, lat, lon)
    weather = WeatherCache(
        user_id=current_user.id,
        date=date_key,
        lat_key=lat_key,
        lon_key=lon_key,
        city="当前位置",
        condition=str(current["condition"]),
        temperature=int(current["temperature"]),
        feels_like=int(current["feels_like"]),
        precipitation=float(current["precipitation"]),
        wind_speed=float(current["wind_speed"]),
        expires_at=now + timedelta(days=1),
    )
    db.add(weather)
    db.commit()
    db.refresh(weather)
    return _weather_response(weather, cached=False)


def _fetch_open_meteo_current(settings: Settings, lat: float, lon: float) -> dict[str, object]:
    if settings.weather_provider != "open_meteo":
        return _demo_current(lat)
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,apparent_temperature,precipitation,weather_code,wind_speed_10m",
        "timezone": "auto",
    }
    try:
        with httpx.Client(timeout=10) as client:
            response = client.get(f"{settings.open_meteo_base_url.rstrip('/')}/v1/forecast", params=params)
            response.raise_for_status()
        current = response.json().get("current") or {}
        return {
            "condition": _weather_code_label(int(current.get("weather_code", 0))),
            "temperature": round(float(current.get("temperature_2m", _demo_temperature(lat)))),
            "feels_like": round(float(current.get("apparent_temperature", _demo_temperature(lat)))),
            "precipitation": float(current.get("precipitation", 0.0)),
            "wind_speed": float(current.get("wind_speed_10m", 0.0)),
        }
    except Exception:
        return _demo_current(lat)


def _demo_current(lat: float) -> dict[str, object]:
    temperature = _demo_temperature(lat)
    return {
        "condition": "Cloudy",
        "temperature": temperature,
        "feels_like": temperature,
        "precipitation": 0.0,
        "wind_speed": 8.0,
    }


def _demo_temperature(lat: float) -> int:
    return max(8, min(32, int(round(24 - abs(lat) * 0.05))))


def _weather_code_label(code: int) -> str:
    if code == 0:
        return "Clear"
    if code in {1, 2}:
        return "Partly Cloudy"
    if code == 3:
        return "Cloudy"
    if code in {45, 48}:
        return "Fog"
    if code in {51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82}:
        return "Rain"
    if code in {71, 73, 75, 77, 85, 86}:
        return "Snow"
    if code in {95, 96, 99}:
        return "Thunderstorm"
    return "Unknown"


def _weather_response(weather: WeatherCache, cached: bool) -> WeatherResponse:
    return WeatherResponse(
        date=weather.date,
        lat_key=weather.lat_key,
        lon_key=weather.lon_key,
        city=weather.city,
        condition=weather.condition,
        temperature=weather.temperature,
        feels_like=weather.feels_like,
        precipitation=weather.precipitation,
        wind_speed=weather.wind_speed,
        cached=cached,
    )
