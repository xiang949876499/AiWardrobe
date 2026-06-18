from datetime import datetime, timezone

from app.config import Settings
from app.season import (
    AUTUMN_BEGIN,
    SPRING_BEGIN,
    SUMMER_BEGIN,
    WINTER_BEGIN,
    current_season,
    season_for_datetime,
)


def test_season_for_datetime_uses_solar_term_moments() -> None:
    boundaries = {
        SPRING_BEGIN: datetime(2026, 2, 4, 4, 1, 51),
        SUMMER_BEGIN: datetime(2026, 5, 5, 19, 48, 17),
        AUTUMN_BEGIN: datetime(2026, 8, 7, 19, 42, 26),
        WINTER_BEGIN: datetime(2026, 11, 7, 17, 51, 46),
    }

    assert season_for_datetime(datetime(2026, 5, 5, 19, 48, 16), boundaries) == "spring"
    assert season_for_datetime(datetime(2026, 5, 5, 19, 48, 17), boundaries) == "summer"
    assert season_for_datetime(datetime(2026, 8, 7, 19, 42, 26), boundaries) == "autumn"
    assert season_for_datetime(datetime(2026, 11, 7, 17, 51, 46), boundaries) == "winter"
    assert season_for_datetime(datetime(2026, 1, 1, 0, 0, 0), boundaries) == "winter"


def test_current_season_normalizes_aware_datetime_to_hong_kong_time(monkeypatch) -> None:
    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        @property
        def text(self) -> str:
            return _fake_2026_hko_xml()

    class FakeClient:
        def __init__(self, timeout: int) -> None:
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback) -> None:
            return None

        def get(self, url: str) -> FakeResponse:
            return FakeResponse()

    import app.season as season_module

    season_module._SOLAR_TERM_CACHE.clear()
    monkeypatch.setattr(season_module.httpx, "Client", FakeClient)

    assert current_season(Settings(testing=True), datetime(2026, 5, 5, 11, 48, 59, tzinfo=timezone.utc)) == "spring"
    assert current_season(Settings(testing=True), datetime(2026, 5, 5, 11, 49, 0, tzinfo=timezone.utc)) == "summer"


def test_current_season_fetches_solar_terms_from_network_and_uses_cache(monkeypatch) -> None:
    calls: list[str] = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        @property
        def text(self) -> str:
            return _fake_2026_hko_xml()

    class FakeClient:
        def __init__(self, timeout: int) -> None:
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback) -> None:
            return None

        def get(self, url: str) -> FakeResponse:
            calls.append(url)
            return FakeResponse()

    import app.season as season_module

    season_module._SOLAR_TERM_CACHE.clear()
    monkeypatch.setattr(season_module.httpx, "Client", FakeClient)
    settings = Settings(testing=True)

    assert current_season(settings, datetime(2026, 5, 5, 19, 48, 59)) == "spring"
    assert current_season(settings, datetime(2026, 5, 5, 19, 49, 0)) == "summer"
    assert calls == ["https://www.hko.gov.hk/en/gts/astronomy/data/files/24SolarTerms_2026.xml"]


def test_current_season_falls_back_when_network_fails(monkeypatch) -> None:
    class FailingClient:
        def __init__(self, timeout: int) -> None:
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback) -> None:
            return None

        def get(self, url: str):
            raise RuntimeError("network down")

    import app.season as season_module

    season_module._SOLAR_TERM_CACHE.clear()
    monkeypatch.setattr(season_module.httpx, "Client", FailingClient)

    assert current_season(Settings(testing=True), datetime(2026, 5, 26, 10, 0, 0)) == "summer"


def _fake_2026_hko_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8"?>
<SolarTerms_2026>
<Data><M>01</M><D>05</D><hm>16:23</hm></Data><Data><M>01</M><D>20</D><hm>09:45</hm></Data>
<Data><M>02</M><D>04</D><hm>04:02</hm></Data><Data><M>02</M><D>18</D><hm>23:52</hm></Data>
<Data><M>03</M><D>05</D><hm>21:59</hm></Data><Data><M>03</M><D>20</D><hm>22:46</hm></Data>
<Data><M>04</M><D>05</D><hm>02:40</hm></Data><Data><M>04</M><D>20</D><hm>09:39</hm></Data>
<Data><M>05</M><D>05</D><hm>19:49</hm></Data><Data><M>05</M><D>21</D><hm>08:37</hm></Data>
<Data><M>06</M><D>05</D><hm>23:48</hm></Data><Data><M>06</M><D>21</D><hm>16:25</hm></Data>
<Data><M>07</M><D>07</D><hm>09:57</hm></Data><Data><M>07</M><D>23</D><hm>03:13</hm></Data>
<Data><M>08</M><D>07</D><hm>19:43</hm></Data><Data><M>08</M><D>23</D><hm>10:19</hm></Data>
<Data><M>09</M><D>07</D><hm>22:41</hm></Data><Data><M>09</M><D>23</D><hm>08:05</hm></Data>
<Data><M>10</M><D>08</D><hm>14:29</hm></Data><Data><M>10</M><D>23</D><hm>17:38</hm></Data>
<Data><M>11</M><D>07</D><hm>17:52</hm></Data><Data><M>11</M><D>22</D><hm>15:23</hm></Data>
<Data><M>12</M><D>07</D><hm>10:53</hm></Data><Data><M>12</M><D>22</D><hm>04:50</hm></Data>
</SolarTerms_2026>"""
