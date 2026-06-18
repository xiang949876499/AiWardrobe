from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Garment, Outfit, UploadSession, User


def test_migrates_legacy_sqlite_into_target_database_and_merges_users_by_email(tmp_path) -> None:
    source_url = f"sqlite:///{tmp_path / 'legacy.db'}"
    target_url = f"sqlite:///{tmp_path / 'target.db'}"
    source_engine = create_engine(source_url)
    target_engine = create_engine(target_url)
    Base.metadata.create_all(source_engine)
    Base.metadata.create_all(target_engine)

    with Session(source_engine) as source:
        legacy_user = User(id="legacy-user", email="stylist@example.com", password_hash="old")
        upload = UploadSession(
            id="legacy-upload",
            user_id="legacy-user",
            original_image_url="/static/uploads/original.jpg",
            original_image_key="garments/original.jpg",
            status="ready",
        )
        garment = Garment(
            id="legacy-garment",
            user_id="legacy-user",
            source_upload_id="legacy-upload",
            image_url="/static/uploads/garment.jpg",
            image_key="garments/garment.jpg",
            thumbnail_url="/static/uploads/garment.jpg",
            category="top",
            colors=["白色"],
            style="通勤",
            material="棉",
            season=["夏"],
            fit="标准",
            tags=["衬衫", "通勤"],
            ai_result={"source": "legacy"},
            ai_confidence=0.88,
            status="ready",
            review_status="confirmed",
        )
        outfit = Outfit(
            id="legacy-outfit",
            user_id="legacy-user",
            name="旧通勤",
            occasion="work",
            season="summer",
            temperature=31,
            items=[{"garment_id": "legacy-garment"}],
            explanation="旧库固定搭配",
            source="manual",
            is_favorite=True,
            is_fixed=True,
        )
        source.add_all([legacy_user, upload, garment, outfit])
        source.commit()

    with Session(target_engine) as target:
        target.add(User(id="target-user", email="stylist@example.com", password_hash="new"))
        target.commit()

    from app.sqlite_migration import migrate_sqlite_to_database

    result = migrate_sqlite_to_database(source_url, target_url)

    assert result.users_created == 0
    assert result.users_merged == 1
    assert result.upload_sessions == 1
    assert result.garments == 1
    assert result.outfits == 1

    with Session(target_engine) as target:
        users = target.execute(select(User)).scalars().all()
        garment = target.get(Garment, "legacy-garment")
        outfit = target.get(Outfit, "legacy-outfit")
        upload = target.get(UploadSession, "legacy-upload")

    assert len(users) == 1
    assert garment is not None
    assert garment.user_id == "target-user"
    assert garment.source_upload_id == "legacy-upload"
    assert garment.tags == ["衬衫", "通勤"]
    assert upload is not None
    assert upload.user_id == "target-user"
    assert outfit is not None
    assert outfit.user_id == "target-user"
    assert outfit.is_fixed is True

    second = migrate_sqlite_to_database(source_url, target_url)

    assert second.upload_sessions == 0
    assert second.garments == 0
    assert second.outfits == 0


def test_rejects_sqlite_database_url_outside_tests(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./aiwardrobe.db")
    monkeypatch.setenv("TESTING", "false")

    from app.config import get_settings

    get_settings.cache_clear()

    try:
        get_settings()
    except ValueError as exc:
        assert "SQLite is only allowed when TESTING=true" in str(exc)
    else:
        raise AssertionError("Expected non-test SQLite DATABASE_URL to be rejected")
