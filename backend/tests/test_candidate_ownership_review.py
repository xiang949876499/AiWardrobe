from uuid import uuid4

import pytest
from sqlalchemy import select

from app.database import get_session_factory
from app.models import Garment, PurchaseCandidate, User, utc_now
from tests.conftest import login
from tests.test_decision_api import _candidate


def setup_owned(client, *, state="legacy_unlinked", confirmed=True):
    token = login(client, "ownership-review@example.com")
    with get_session_factory()() as db:
        user = db.execute(select(User).where(User.email == "ownership-review@example.com")).scalar_one()
        candidate = _candidate(db, user.id)
        candidate.ownership_state = state
        garment = Garment(user_id=user.id, image_url="", image_key="", category="top",
                          status="ready", review_status="confirmed" if confirmed else "pending_review",
                          user_confirmed_at=utc_now() if confirmed else None, tags=["original"])
        db.add(garment)
        db.flush()
        ids = candidate.id, garment.id
        db.commit()
    return {"Authorization": f"Bearer {token}", "Idempotency-Key": str(uuid4())}, *ids


def link(client, headers, candidate_id, garment_id, revision=1):
    return client.post(f"/v2/purchase/candidates/{candidate_id}/link-owned-garment", headers=headers,
                       json={"expected_revision": revision, "garment_id": garment_id, "confirm_owned": True})


def save(client, headers, candidate_id, **changes):
    return client.post(f"/v2/purchase/candidates/{candidate_id}/save", headers=headers,
                       json={"expected_revision": 1, "confirm_owned": True, **changes})


def test_link_then_save_returns_original_garment_with_stale_revision(client):
    headers, candidate_id, garment_id = setup_owned(client)
    assert link(client, headers, candidate_id, garment_id).status_code == 200
    saved = save(client, headers, candidate_id, tags=["changed"])
    assert saved.status_code == 200
    assert saved.json()["garment_id"] == garment_id
    with get_session_factory()() as db:
        assert db.get(Garment, garment_id).source_candidate_id == candidate_id
        assert db.get(Garment, garment_id).tags == ["original"]
        assert db.query(Garment).count() == 1


def test_link_then_delete_establishes_tombstone_and_cannot_restore(client):
    headers, candidate_id, garment_id = setup_owned(client)
    assert link(client, headers, candidate_id, garment_id).status_code == 200
    assert client.delete(f"/garments/{garment_id}", headers=headers).status_code == 204
    with get_session_factory()() as db:
        candidate = db.get(PurchaseCandidate, candidate_id)
        assert candidate.ownership_state == "removed"
        assert candidate.owned_garment_id == garment_id
    assert save(client, headers, candidate_id).json()["detail"]["code"] == "owned_garment_removed"
    assert link(client, headers, candidate_id, garment_id).json()["detail"]["code"] == "owned_garment_removed"


def test_second_candidate_cannot_link_garment_already_bound(client):
    headers, candidate_id, garment_id = setup_owned(client)
    assert link(client, headers, candidate_id, garment_id).status_code == 200
    with get_session_factory()() as db:
        original = db.get(PurchaseCandidate, candidate_id)
        second = _candidate(db, original.user_id)
        second.ownership_state = "legacy_unlinked"
        second_id = second.id
        db.commit()
    result = link(client, {**headers, "Idempotency-Key": str(uuid4())}, second_id, garment_id)
    assert result.status_code == 409
    with get_session_factory()() as db:
        assert db.get(Garment, garment_id).source_candidate_id == candidate_id
        assert db.get(PurchaseCandidate, second_id).ownership_state == "legacy_unlinked"


def test_legacy_unlinked_cannot_save(client):
    headers, candidate_id, _ = setup_owned(client)
    result = save(client, headers, candidate_id)
    assert result.status_code == 409
    assert result.json()["detail"]["code"] == "ownership_link_required"


def test_link_replay_ignores_revision_but_conflicts_on_different_target(client):
    headers, candidate_id, garment_id = setup_owned(client)
    first = link(client, headers, candidate_id, garment_id)
    replay = link(client, headers, candidate_id, garment_id, revision=99)
    assert first.status_code == replay.status_code == 200
    assert first.json() == replay.json()
    with get_session_factory()() as db:
        assert db.get(PurchaseCandidate, candidate_id).revision == 2
    conflict = link(client, headers, candidate_id, str(uuid4()), revision=2)
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "idempotency_conflict"


def test_save_key_conflicts_when_tags_change(client):
    headers, candidate_id, _ = setup_owned(client, state="never_saved")
    assert save(client, headers, candidate_id, tags=["one"]).status_code == 201
    conflict = save(client, headers, candidate_id, tags=["two"])
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "idempotency_conflict"


@pytest.mark.parametrize("key", [None, "invalid"])
def test_link_requires_uuid_idempotency_key(client, key):
    headers, candidate_id, garment_id = setup_owned(client)
    headers.pop("Idempotency-Key")
    if key is not None:
        headers["Idempotency-Key"] = key
    assert link(client, headers, candidate_id, garment_id).status_code == 422


def test_link_rejects_pending_and_cross_account_garments(client):
    headers, candidate_id, garment_id = setup_owned(client, confirmed=False)
    assert link(client, headers, candidate_id, garment_id).status_code == 404
    other = login(client, "ownership-other@example.com")
    assert link(client, {**headers, "Authorization": f"Bearer {other}"}, candidate_id, garment_id).status_code == 404
    with get_session_factory()() as db:
        candidate = db.get(PurchaseCandidate, candidate_id)
        db.get(Garment, garment_id).user_confirmed_at = utc_now()
        db.get(Garment, garment_id).review_status = "confirmed"
        other_user = db.execute(select(User).where(User.email == "ownership-other@example.com")).scalar_one()
        candidate.user_id = other_user.id
        db.commit()
    assert link(client, {**headers, "Authorization": f"Bearer {other}"}, candidate_id, garment_id).status_code == 404


def test_link_cannot_replace_candidates_existing_canonical_garment(client):
    headers, candidate_id, garment_id = setup_owned(client, state="never_saved")
    saved = save(client, headers, candidate_id)
    assert saved.status_code == 201
    result = link(client, headers, candidate_id, garment_id, revision=2)
    assert result.status_code == 409
    assert result.json()["detail"]["code"] == "candidate_already_linked"


def test_same_link_key_different_candidate_conflicts(client):
    headers, candidate_id, garment_id = setup_owned(client)
    assert link(client, headers, candidate_id, garment_id).status_code == 200
    with get_session_factory()() as db:
        original = db.get(PurchaseCandidate, candidate_id)
        second = _candidate(db, original.user_id)
        second.ownership_state = "legacy_unlinked"
        second_id = second.id
        db.commit()
    result = link(client, headers, second_id, garment_id)
    assert result.status_code == 409
    assert result.json()["detail"]["code"] == "idempotency_conflict"


def test_version_12_database_gets_durable_ownership_replay_table(tmp_path):
    from sqlalchemy import create_engine, inspect, text
    from app.migrations import upgrade_database
    engine = create_engine(f"sqlite:///{tmp_path / 'ownership-v12.db'}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, name VARCHAR(120), applied_at VARCHAR(40))"))
        connection.execute(text("CREATE TABLE users (id VARCHAR(36) PRIMARY KEY)"))
        connection.execute(text("INSERT INTO schema_migrations VALUES (12, 'previous', '2026-10-01')"))
    assert upgrade_database(engine) == 13
    assert "candidate_ownership_operations" in inspect(engine).get_table_names()


def test_explicit_link_repairs_preexisting_mirror_without_canonical_source(client):
    headers, candidate_id, garment_id = setup_owned(client)
    with get_session_factory()() as db:
        candidate = db.get(PurchaseCandidate, candidate_id)
        candidate.ownership_state = "linked"
        candidate.owned_garment_id = garment_id
        db.commit()
    assert save(client, headers, candidate_id).status_code == 409
    assert link(client, headers, candidate_id, garment_id).status_code == 200
    assert save(client, headers, candidate_id).json()["garment_id"] == garment_id


def test_concurrent_sqlite_saves_create_one_canonical_garment(client):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    headers, candidate_id, _ = setup_owned(client, state="never_saved")
    barrier = Barrier(2)
    def concurrent_save():
        barrier.wait()
        return save(client, headers, candidate_id)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: concurrent_save(), range(2)))
    assert sorted(response.status_code for response in results) == [200, 201]
    assert results[0].json()["garment_id"] == results[1].json()["garment_id"]
    with get_session_factory()() as db:
        assert db.query(Garment).filter_by(source_candidate_id=candidate_id).count() == 1


def test_concurrent_sqlite_links_bind_garment_to_only_one_candidate(client):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    headers, candidate_id, garment_id = setup_owned(client)
    with get_session_factory()() as db:
        original = db.get(PurchaseCandidate, candidate_id)
        second = _candidate(db, original.user_id)
        second.ownership_state = "legacy_unlinked"
        second_id = second.id
        db.commit()
    barrier = Barrier(2)
    def concurrent_link(identifier):
        barrier.wait()
        return link(client, {**headers, "Idempotency-Key": str(uuid4())}, identifier, garment_id)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(concurrent_link, [candidate_id, second_id]))
    assert sorted(response.status_code for response in results) == [200, 409]
    winner = next(response.json()["candidate_id"] for response in results if response.status_code == 200)
    with get_session_factory()() as db:
        assert db.get(Garment, garment_id).source_candidate_id == winner
        assert db.query(PurchaseCandidate).filter_by(ownership_state="linked").count() == 1


def test_delete_preexisting_explicit_mirror_preserves_tombstone(client):
    headers, candidate_id, garment_id = setup_owned(client)
    with get_session_factory()() as db:
        candidate = db.get(PurchaseCandidate, candidate_id)
        candidate.ownership_state = "linked"
        candidate.owned_garment_id = garment_id
        db.commit()
    assert client.delete(f"/garments/{garment_id}", headers=headers).status_code == 204
    with get_session_factory()() as db:
        candidate = db.get(PurchaseCandidate, candidate_id)
        assert candidate.ownership_state == "removed"
        assert candidate.owned_garment_id == garment_id
    assert save(client, headers, candidate_id).json()["detail"]["code"] == "owned_garment_removed"


def test_legacy_save_uses_linked_canonical_relationship(client):
    headers, candidate_id, garment_id = setup_owned(client)
    assert link(client, headers, candidate_id, garment_id).status_code == 200
    saved = client.post(f"/purchase/candidates/{candidate_id}/save", headers=headers)
    assert saved.status_code == 200
    assert saved.json()["id"] == garment_id
    assert client.delete(f"/garments/{garment_id}", headers=headers).status_code == 204
    assert client.post(f"/purchase/candidates/{candidate_id}/save", headers=headers).json()["detail"]["code"] == "owned_garment_removed"
