from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session


def test_database_migrations_create_a_version_record_and_are_repeatable(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    from app.migrations import current_version, upgrade_database

    upgrade_database(engine)
    first_version = current_version(engine)
    first_table_count = len(inspect(engine).get_table_names())

    upgrade_database(engine)

    assert first_version == 13
    assert current_version(engine) == first_version
    assert len(inspect(engine).get_table_names()) == first_table_count
    assert {"analysis_tasks", "provider_operations", "stored_assets", "candidate_ownership_operations"}.issubset(
        set(inspect(engine).get_table_names())
    )
    with engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM schema_migrations")).scalar_one() == 13


def test_legacy_saved_candidates_are_backfilled_without_inventing_a_garment_link(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy-candidates.db'}")
    from app.database import Base
    from app.models import Garment, PurchaseCandidate, User
    from app.migrations import upgrade_database

    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, name VARCHAR(120) NOT NULL, applied_at VARCHAR(40) NOT NULL)"))
        connection.execute(text("INSERT INTO schema_migrations VALUES (10, 'previous', '2026-10-01T00:00:00Z')"))
    with Session(engine) as db:
        user = User(email="legacy-candidate@example.com", password_hash="hash")
        db.add(user)
        db.flush()
        linked = PurchaseCandidate(
            user_id=user.id, product_url="https://shop.example/item/1", source_image_url="",
            image_url="", image_key="", category="top", status="saved", recommendation="consider",
            score=0, decision_score=None, reason_summary="", analysis={},
        )
        unlinked = PurchaseCandidate(
            user_id=user.id, product_url="https://shop.example/item/2", source_image_url="",
            image_url="", image_key="", category="top", status="saved", recommendation="consider",
            score=0, decision_score=None, reason_summary="", analysis={},
        )
        db.add_all([linked, unlinked])
        db.flush()
        db.add(Garment(user_id=user.id, source_candidate_id=linked.id, image_url="", image_key="", category="top"))
        linked_id, unlinked_id = linked.id, unlinked.id
        db.commit()

    assert upgrade_database(engine) == 13
    with Session(engine) as db:
        linked = db.get(PurchaseCandidate, linked_id)
        unlinked = db.get(PurchaseCandidate, unlinked_id)
        assert linked is not None and linked.decision_status == "bought"
        assert linked.ownership_state == "linked" and linked.owned_garment_id is not None
        assert unlinked is not None and unlinked.decision_status == "bought"
        assert unlinked.ownership_state == "legacy_unlinked" and unlinked.owned_garment_id is None
