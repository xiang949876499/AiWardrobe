import pytest
from datetime import date
from uuid import uuid4

from fastapi.testclient import TestClient

from app.models import (
    Garment,
    Outfit,
    PurchaseCandidate,
    User,
    UserDecisionContext,
    UserPreference,
    WearEvent,
    utc_now,
)
from tests.conftest import login


def _candidate(db, user_id: str) -> PurchaseCandidate:
    candidate = PurchaseCandidate(
        user_id=user_id,
        product_url="https://shop.example.com/products/shirt",
        source_image_url="https://cdn.example.com/shirt.jpg",
        image_url="",
        image_key="",
        title="Blue shirt",
        domain="shop.example.com",
        category="top",
        colors=["blue"],
        style="casual",
        material="cotton",
        season=["summer"],
        fit="regular",
        tags=["shirt"],
        recommendation="consider",
        score=0,
        decision_score=None,
        reason_summary="资料不足",
        analysis={
            "score": None,
            "suggested_price": None,
            "coverage": {"status": "insufficient", "confirmed_garment_count": 0},
            "evidence": {"rule_version": "purchase-v1"},
        },
        analysis_source="remote",
        requested_snapshot_revision=1,
    )
    db.add(candidate)
    db.flush()
    from app.decision_context import create_purchase_snapshot

    create_purchase_snapshot(db, candidate)
    db.flush()
    return candidate


def test_candidate_list_detail_and_expected_revision_conflict(client: TestClient) -> None:
    token = login(client, "candidate-api@example.com")
    from app.database import get_session_factory

    with get_session_factory()() as db:
        user = db.query(User).filter_by(email="candidate-api@example.com").one()
        candidate = _candidate(db, user.id)
        candidate_id = candidate.id
        db.commit()

    headers = {"Authorization": f"Bearer {token}"}
    listed = client.get("/v2/purchase/candidates", headers=headers)
    detail = client.get(f"/v2/purchase/candidates/{candidate_id}", headers=headers)
    assert listed.status_code == 200
    assert listed.json()["items"][0]["score"] is None
    assert listed.json()["items"][0]["coverage"]["status"] == "insufficient"
    assert detail.json()["snapshots"][0]["snapshot_revision"] == 1

    updated = client.patch(
        f"/v2/purchase/candidates/{candidate_id}/decision",
        headers=headers,
        json={"decision_status": "skipped", "expected_revision": 1},
    )
    conflict = client.patch(
        f"/v2/purchase/candidates/{candidate_id}/decision",
        headers=headers,
        json={"decision_status": "considering", "expected_revision": 1},
    )
    assert updated.status_code == 200
    assert updated.json()["revision"] == 2
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "revision_conflict"


def test_candidate_save_is_idempotent_and_removed_garment_is_a_tombstone(client: TestClient) -> None:
    token = login(client, "candidate-save@example.com")
    from app.database import get_session_factory

    with get_session_factory()() as db:
        user = db.query(User).filter_by(email="candidate-save@example.com").one()
        candidate = _candidate(db, user.id)
        candidate_id = candidate.id
        db.commit()
    headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": str(uuid4())}
    body = {"expected_revision": 1, "confirm_owned": True, "tags": ["blue", "shirt"]}
    first = client.post(f"/v2/purchase/candidates/{candidate_id}/save", headers=headers, json=body)
    replay = client.post(f"/v2/purchase/candidates/{candidate_id}/save", headers=headers, json=body)
    assert first.status_code == 201
    assert replay.status_code == 200
    assert first.json()["garment_id"] == replay.json()["garment_id"]
    deleted = client.delete(
        f"/garments/{first.json()['garment_id']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert deleted.status_code == 204
    replay_after_delete = client.post(f"/v2/purchase/candidates/{candidate_id}/save", headers=headers, json=body)
    assert replay_after_delete.status_code == 409
    assert replay_after_delete.json()["detail"]["code"] == "owned_garment_removed"


def test_reanalysis_task_replay_keeps_snapshot_sequence_and_worker_reuses_visual_tags(client: TestClient) -> None:
    from app.config import get_settings
    from app.database import get_session_factory
    from app.models import AnalysisTask, PurchaseAnalysisSnapshot
    from app.task_worker import TaskWorker
    import anyio

    token = login(client, "reanalyze@example.com")
    with get_session_factory()() as db:
        user = db.query(User).filter_by(email="reanalyze@example.com").one()
        candidate = _candidate(db, user.id)
        candidate_id = candidate.id
        db.commit()
    headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": str(uuid4())}
    first = client.post(f"/v2/purchase/candidates/{candidate_id}/reanalyze", headers=headers, json={})
    replay = client.post(f"/v2/purchase/candidates/{candidate_id}/reanalyze", headers=headers, json={})
    assert first.status_code == replay.status_code == 202
    assert first.json()["id"] == replay.json()["id"]
    worker = TaskWorker(get_session_factory(), get_settings())
    assert anyio.run(worker.run_once, backend="asyncio") is True
    with get_session_factory()() as db:
        candidate = db.get(PurchaseCandidate, candidate_id)
        task = db.get(AnalysisTask, first.json()["id"])
        snapshots = db.query(PurchaseAnalysisSnapshot).filter_by(candidate_id=candidate_id).order_by(PurchaseAnalysisSnapshot.snapshot_revision).all()
        assert task.status == "succeeded"
        assert candidate.requested_snapshot_revision == 2
        assert [snapshot.snapshot_revision for snapshot in snapshots] == [1, 2]
        assert snapshots[-1].decision["score"] is None


def test_preferences_revision_changes_only_for_real_changes(client: TestClient) -> None:
    token = login(client, "preference-api@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    payload = {
        "primary_goal": "通勤",
        "scenes": ["通勤"],
        "styles": ["casual"],
        "avoid_types": [],
        "budget_range": "",
        "budget_min": "100.00",
        "budget_max": "800.00",
        "currency": "CNY",
        "avoid_categories": ["top"],
    }
    first = client.put("/v2/preferences/me", headers=headers, json=payload)
    replay = client.put("/v2/preferences/me", headers=headers, json=payload)
    assert first.status_code == replay.status_code == 200
    assert first.json()["revision"] == replay.json()["revision"] == 1


def test_feedback_is_idempotent_account_scoped_and_reversible(client: TestClient) -> None:
    token = login(client, "feedback-owner@example.com")
    other_token = login(client, "feedback-other@example.com")
    from app.database import get_session_factory

    with get_session_factory()() as db:
        user = db.query(User).filter_by(email="feedback-owner@example.com").one()
        candidate = _candidate(db, user.id)
        candidate_id = candidate.id
        db.commit()
    payload = {
        "target_type": "candidate",
        "target_id": candidate_id,
        "snapshot_revision": 1,
        "value": "not_helpful",
        "reason_code": "duplicate",
        "garment_ids": [],
        "exclude_next_time": False,
    }
    key = str(uuid4())
    headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": key}
    first = client.post("/v2/feedback", headers=headers, json=payload)
    replay = client.post("/v2/feedback", headers=headers, json=payload)
    leaked = client.post(
        "/v2/feedback",
        headers={"Authorization": f"Bearer {other_token}", "Idempotency-Key": str(uuid4())},
        json=payload,
    )
    assert first.status_code == replay.status_code == 201
    assert first.json()["id"] == replay.json()["id"]
    assert leaked.status_code == 404
    revoked = client.delete(f"/v2/feedback/{first.json()['id']}", headers={"Authorization": f"Bearer {token}"})
    assert revoked.status_code == 204
    with get_session_factory()() as db:
        user = db.query(User).filter_by(email="feedback-owner@example.com").one()
        assert db.get(UserDecisionContext, user.id).feedback_revision == 2


def test_wear_event_records_members_and_revoke_is_soft(client: TestClient) -> None:
    token = login(client, "wear-api@example.com")
    from app.database import get_session_factory

    with get_session_factory()() as db:
        user = db.query(User).filter_by(email="wear-api@example.com").one()
        garment = Garment(
            user_id=user.id,
            image_url="",
            image_key="",
            category="top",
            colors=["blue"],
            style="casual",
            material="cotton",
            season=["summer"],
            fit="regular",
            tags=["shirt"],
            status="ready",
            review_status="confirmed",
            user_confirmed_at=utc_now(),
            analysis_source="manual",
        )
        db.add(garment)
        db.flush()
        outfit = Outfit(
            user_id=user.id,
            occasion="work",
            items=[{"garment_id": garment.id, "category": garment.category}],
        )
        db.add(outfit)
        db.commit()
        outfit_id = outfit.id
        garment_id = garment.id

    headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": str(uuid4())}
    response = client.post(
        "/v2/wear-events",
        headers=headers,
        json={"local_date": date.today().isoformat(), "timezone": "Asia/Shanghai", "outfit_id": outfit_id},
    )
    assert response.status_code == 201
    assert response.json()["items"][0]["garment_id"] == garment_id
    deleted = client.delete(f"/v2/wear-events/{response.json()['id']}", headers={"Authorization": f"Bearer {token}"})
    assert deleted.status_code == 204
    with get_session_factory()() as db:
        event = db.get(WearEvent, response.json()["id"])
        assert event.revoked_at is not None
        assert db.query(UserDecisionContext).filter_by(user_id=event.user_id).one().wear_tracking_started_at is not None


def test_v2_garment_pages_are_stable_and_cursors_bind_to_filters(client: TestClient) -> None:
    token = login(client, "garment-pages@example.com")
    from app.database import get_session_factory

    with get_session_factory()() as db:
        user = db.query(User).filter_by(email="garment-pages@example.com").one()
        for index in range(3):
            db.add(Garment(
                user_id=user.id,
                image_url="",
                image_key="",
                category="top",
                colors=["blue"],
                style=f"casual-{index}",
                material="cotton",
                season=["summer"],
                fit="regular",
                tags=["basic", f"shirt-{index}"],
                status="ready",
                review_status="confirmed",
                user_confirmed_at=utc_now(),
                analysis_source="manual",
            ))
        db.commit()
    headers = {"Authorization": f"Bearer {token}"}
    first = client.get("/v2/garments?tag=basic&limit=2", headers=headers)
    second = client.get(f"/v2/garments?tag=basic&limit=2&cursor={first.json()['next_cursor']}", headers=headers)
    changed_filter = client.get(f"/v2/garments?tag=shirt-0&limit=2&cursor={first.json()['next_cursor']}", headers=headers)
    assert first.status_code == 200
    assert second.status_code == 200, second.json()
    assert first.json()["has_more"] is True
    assert len(second.json()["items"]) == 1
    assert {item["id"] for item in first.json()["items"]}.isdisjoint({item["id"] for item in second.json()["items"]})
    assert changed_filter.status_code == 422


pytestmark = pytest.mark.usefixtures("legacy_worker")
