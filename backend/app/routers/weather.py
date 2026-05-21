from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

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

    weather = WeatherCache(
        user_id=current_user.id,
        date=date_key,
        lat_key=lat_key,
        lon_key=lon_key,
        city="当前位置",
        condition="Cloudy",
        temperature=_demo_temperature(lat),
        feels_like=_demo_temperature(lat),
        precipitation=0.0,
        wind_speed=8.0,
        expires_at=now + timedelta(days=1),
    )
    db.add(weather)
    db.commit()
    db.refresh(weather)
    return _weather_response(weather, cached=False)


def _demo_temperature(lat: float) -> int:
    return max(8, min(32, int(round(24 - abs(lat) * 0.05))))


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
