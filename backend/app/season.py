from datetime import datetime, timedelta, timezone
import xml.etree.ElementTree as ET

import httpx

from app.config import Settings

SEASON_TERMS = {
    "spring": 2,
    "summer": 8,
    "autumn": 14,
    "winter": 20,
}

SPRING_BEGIN = "spring_begin"
SUMMER_BEGIN = "summer_begin"
AUTUMN_BEGIN = "autumn_begin"
WINTER_BEGIN = "winter_begin"

_FALLBACK_BOUNDARY_DATES = {
    SPRING_BEGIN: (2, 4, 0, 0, 0),
    SUMMER_BEGIN: (5, 5, 0, 0, 0),
    AUTUMN_BEGIN: (8, 7, 0, 0, 0),
    WINTER_BEGIN: (11, 7, 0, 0, 0),
}

_SOLAR_TERM_CACHE: dict[tuple[str, int], dict[str, datetime]] = {}
HONG_KONG_TIME = timezone(timedelta(hours=8))


def current_season(settings: Settings, now: datetime | None = None) -> str:
    moment = _as_hong_kong_time(now or datetime.now(HONG_KONG_TIME))
    boundaries = solar_term_boundaries(settings, moment.year)
    return season_for_datetime(moment, boundaries)


def season_for_datetime(moment: datetime, boundaries: dict[str, datetime]) -> str:
    spring_begin = boundaries[SPRING_BEGIN]
    summer_begin = boundaries[SUMMER_BEGIN]
    autumn_begin = boundaries[AUTUMN_BEGIN]
    winter_begin = boundaries[WINTER_BEGIN]
    if spring_begin <= moment < summer_begin:
        return "spring"
    if summer_begin <= moment < autumn_begin:
        return "summer"
    if autumn_begin <= moment < winter_begin:
        return "autumn"
    return "winter"


def solar_term_boundaries(settings: Settings, year: int) -> dict[str, datetime]:
    cache_key = (settings.solar_terms_api_url, year)
    if cache_key not in _SOLAR_TERM_CACHE:
        try:
            _SOLAR_TERM_CACHE[cache_key] = _fetch_solar_term_boundaries(settings, year)
        except Exception:
            _SOLAR_TERM_CACHE[cache_key] = _fallback_boundaries(year)
    return _SOLAR_TERM_CACHE[cache_key]


def _fetch_solar_term_boundaries(settings: Settings, year: int) -> dict[str, datetime]:
    url = settings.solar_terms_api_url.format(year=year)
    with httpx.Client(timeout=8) as client:
        response = client.get(url)
        response.raise_for_status()
        return _parse_hko_xml(year, response.text)


def _fallback_boundaries(year: int) -> dict[str, datetime]:
    return {
        term: datetime(year, month, day, hour, minute, second)
        for term, (month, day, hour, minute, second) in _FALLBACK_BOUNDARY_DATES.items()
    }


def _parse_hko_xml(year: int, text: str) -> dict[str, datetime]:
    data = list(ET.fromstring(text).findall("Data"))
    if len(data) < 21:
        raise ValueError("Solar term XML did not include all 24 terms")
    terms = {
        SPRING_BEGIN: data[SEASON_TERMS["spring"]],
        SUMMER_BEGIN: data[SEASON_TERMS["summer"]],
        AUTUMN_BEGIN: data[SEASON_TERMS["autumn"]],
        WINTER_BEGIN: data[SEASON_TERMS["winter"]],
    }
    return {name: _parse_hko_term(year, node) for name, node in terms.items()}


def _parse_hko_term(year: int, node: ET.Element) -> datetime:
    month = int((node.findtext("M") or "").strip())
    day = int((node.findtext("D") or "").strip())
    hour_text, minute_text = (node.findtext("hm") or "").strip().split(":", maxsplit=1)
    return datetime(year, month, day, int(hour_text), int(minute_text), 0)


def _as_hong_kong_time(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment
    return moment.astimezone(HONG_KONG_TIME).replace(tzinfo=None)
