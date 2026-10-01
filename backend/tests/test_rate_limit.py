from datetime import timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session


def test_short_window_cleanup_does_not_erase_longer_window_events(tmp_path) -> None:
    from app.database import Base
    from app.models import RateLimitEvent, utc_now
    from app.rate_limit import check_rate_limit

    engine = create_engine(f"sqlite:///{tmp_path / 'limits.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(RateLimitEvent(scope="model_submit", key="user-1", created_at=utc_now() - timedelta(minutes=2)))
        db.commit()

        short_window = check_rate_limit(db, "login", "ip-1", limit=10, window_seconds=60)
        assert short_window.allowed
        long_window = check_rate_limit(db, "model_submit", "user-1", limit=1, window_seconds=600)

        assert long_window.allowed is False
