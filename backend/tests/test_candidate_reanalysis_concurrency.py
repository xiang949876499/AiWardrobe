from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

from sqlalchemy import event, select
from fastapi import Request

from app.database import get_db, get_engine, get_session_factory
from app.models import AnalysisTask, Garment, PurchaseCandidate, User
from app.routers import decision
from tests.conftest import login
from tests.test_decision_api import _candidate


def test_reanalysis_waits_for_concurrent_save_before_capturing_candidate_revision(client, monkeypatch):
    """Independent request transactions must capture the committed ownership revision."""
    token = login(client, "reanalysis-concurrency@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    with get_session_factory()() as db:
        user = db.scalar(select(User).where(User.email == "reanalysis-concurrency@example.com"))
        candidate = _candidate(db, user.id)
        candidate_id = candidate.id
        db.commit()

    save_locked_candidate = Event()
    reanalysis_contending = Event()
    save_completed = Event()
    original_owned_candidate = decision._owned_candidate

    def independent_request_session(request: Request):
        with get_session_factory()() as db:
            db.info["actor"] = request.headers.get("X-Test-Actor")
            db.connection().info["actor"] = db.info["actor"]
            yield db

    client.app.dependency_overrides[get_db] = independent_request_session

    # Pause the save after the real ownership helper has acquired its writer and
    # candidate locks. Reanalysis then starts against this uncommitted revision.
    from app import candidate_ownership
    original_candidate = candidate_ownership._candidate

    def hold_save(db, user_id, identifier):
        result = original_candidate(db, user_id, identifier)
        if db.info.get("actor") == "save":
            save_locked_candidate.set()
            assert reanalysis_contending.wait(10), "reanalysis never contended with save"
        return result

    def observe_reanalysis(db, user_id, identifier, *, lock=False):
        result = original_owned_candidate(db, user_id, identifier, lock=lock)
        if db.info.get("actor") == "reanalysis":
            reanalysis_contending.set()
            assert save_completed.wait(10), "save did not finish"
        return result

    def observe_writer_reservation(connection, cursor, statement, parameters, context, executemany):
        if connection.info.get("actor") == "reanalysis" and statement == "BEGIN IMMEDIATE":
            reanalysis_contending.set()

    monkeypatch.setattr(candidate_ownership, "_candidate", hold_save)
    monkeypatch.setattr(decision, "_owned_candidate", observe_reanalysis)
    engine = get_engine()
    event.listen(engine, "before_cursor_execute", observe_writer_reservation)

    def save():
        try:
            return client.post(f"/v2/purchase/candidates/{candidate_id}/save",
                headers={**headers, "Idempotency-Key": str(uuid4()), "X-Test-Actor": "save"},
                json={"expected_revision": 1, "confirm_owned": True})
        finally:
            save_completed.set()

    def reanalyze():
        assert save_locked_candidate.wait(10), "save did not acquire ownership locks"
        return client.post(f"/v2/purchase/candidates/{candidate_id}/reanalyze",
            headers={**headers, "Idempotency-Key": str(uuid4()), "X-Test-Actor": "reanalysis"})

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            save_result = pool.submit(save)
            reanalysis_result = pool.submit(reanalyze)
            saved = save_result.result(timeout=20)
            queued = reanalysis_result.result(timeout=20)
    finally:
        event.remove(engine, "before_cursor_execute", observe_writer_reservation)
        client.app.dependency_overrides.pop(get_db)

    assert saved.status_code == 201
    assert queued.status_code == 202
    with get_session_factory()() as db:
        candidate = db.get(PurchaseCandidate, candidate_id)
        task = db.get(AnalysisTask, queued.json()["id"])
        assert candidate.revision == 2
        assert task.input_parameters["candidate_revision"] == candidate.revision
        assert candidate.requested_snapshot_revision == task.input_parameters["snapshot_revision"] == 2
        assert db.scalar(select(Garment).where(Garment.source_candidate_id == candidate_id)).id == saved.json()["garment_id"]
