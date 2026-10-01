from io import BytesIO
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock

from fastapi.testclient import TestClient
from PIL import Image
import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as SQLAlchemySession

from app.database import get_session_factory
from app.config import get_settings
from app.models import (
    BudgetLedger,
    ConsentInvite,
    GenerationJob,
    WeddingAsset,
    WeddingConsent,
    WeddingParticipant,
    WeddingProject,
)
from app.wedding_service import WeddingPrivateStorage
from tests.conftest import login


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _image_upload(
    client: TestClient,
    token: str,
    project_id: str,
    person_key: str,
    asset_type: str = "photo",
) -> dict[str, object]:
    image = Image.new("RGB", (1024, 1024), color=(180, 80, 120))
    data = BytesIO()
    image.save(data, format="PNG")
    response = client.post(
        f"/wedding-projects/{project_id}/assets",
        headers=_headers(token),
        data={"person_key": person_key, "asset_type": asset_type},
        files={"file": ("portrait.png", data.getvalue(), "image/png")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _create_project(client: TestClient, token: str) -> dict[str, object]:
    response = client.post(
        "/wedding-projects",
        headers=_headers(token),
        json={"title": "小林与小周", "owner_name": "小林"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _confirm_partner(client: TestClient, owner_token: str, project_id: str) -> str:
    invite = client.post(
        f"/wedding-projects/{project_id}/consent-invites",
        headers=_headers(owner_token),
        json={
            "person_key": "B",
            "display_name": "小周",
            "provider_list": ["volcengine_ark", "aliyun_model_studio"],
            "terms_version": "portrait-v1",
        },
    )
    assert invite.status_code == 201, invite.text
    confirmed = client.post(
        f"/consent-invites/{invite.json()['invite_token']}/confirm",
        json={"accepted": True, "display_name": "小周", "terms_version": "portrait-v1"},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["project_id"] == project_id
    return confirmed.json()["access_token"]


def test_project_crud_is_owner_scoped_and_uses_optimistic_versioning(client: TestClient) -> None:
    owner_token = login(client, "owner@example.com")
    other_token = login(client, "other@example.com")
    project = _create_project(client, owner_token)
    project_id = project["id"]

    hidden = client.get(f"/wedding-projects/{project_id}", headers=_headers(other_token))
    assert hidden.status_code == 404

    updated = client.patch(
        f"/wedding-projects/{project_id}",
        headers=_headers(owner_token),
        json={"version": 1, "title": "小林与小周的故事", "style": "romantic_film"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["version"] == 2
    assert updated.json()["title"] == "小林与小周的故事"

    stale = client.patch(
        f"/wedding-projects/{project_id}",
        headers=_headers(owner_token),
        json={"version": 1, "title": "旧版本"},
    )
    assert stale.status_code == 409

    consent = client.post(
        f"/wedding-projects/{project_id}/self-consent",
        headers=_headers(owner_token),
        json={
            "terms_version": "portrait-v1",
            "scope": ["portrait_processing"],
            "provider_list": ["volcengine_ark"],
        },
    )
    assert consent.status_code == 200
    _confirm_partner(client, owner_token, project_id)
    private_asset = _image_upload(client, owner_token, project_id, "A")
    with get_session_factory()() as db:
        asset_row = db.get(WeddingAsset, private_asset["id"])
        assert asset_row is not None
        object_key = asset_row.object_key
    upload_root = Path(get_settings().local_upload_dir).resolve()
    private_root = upload_root.parent / f"{upload_root.name}-private-wedding"
    private_path = private_root / Path(*object_key.split("/")[1:])
    assert private_path.is_file()

    deleted = client.delete(f"/wedding-projects/{project_id}", headers=_headers(owner_token))
    assert deleted.status_code == 204
    assert client.get(f"/wedding-projects/{project_id}", headers=_headers(owner_token)).status_code == 404
    with get_session_factory()() as db:
        assert db.get(WeddingAsset, private_asset["id"]) is None
        assert db.get(WeddingProject, project_id) is None
    assert not private_path.exists()


def test_invite_is_hashed_single_use_and_participant_asset_access_is_scoped(client: TestClient) -> None:
    owner_token = login(client, "owner@example.com")
    project_id = _create_project(client, owner_token)["id"]
    client.post(
        f"/wedding-projects/{project_id}/self-consent",
        headers=_headers(owner_token),
        json={
            "terms_version": "portrait-v1",
            "scope": ["portrait_processing"],
            "provider_list": ["volcengine_ark"],
        },
    )
    invite_response = client.post(
        f"/wedding-projects/{project_id}/consent-invites",
        headers=_headers(owner_token),
        json={
            "person_key": "B",
            "display_name": "小周",
            "provider_list": ["volcengine_ark"],
            "terms_version": "portrait-v1",
        },
    )
    assert invite_response.status_code == 201, invite_response.text
    invite_token = invite_response.json()["invite_token"]

    with get_session_factory()() as db:
        invite = db.query(ConsentInvite).one()
        assert invite.status == "active"
        assert invite.token_hash != invite_token
        assert invite_token not in invite.token_hash

    confirmed = client.post(
        f"/consent-invites/{invite_token}/confirm",
        json={"accepted": True, "display_name": "小周", "terms_version": "portrait-v1"},
    )
    assert confirmed.status_code == 200, confirmed.text
    participant_token = confirmed.json()["access_token"]
    assert confirmed.json()["participant"]["person_key"] == "B"
    owner_asset = _image_upload(client, owner_token, project_id, "A")
    partner_asset = _image_upload(client, participant_token, project_id, "B")

    replay = client.post(
        f"/consent-invites/{invite_token}/confirm",
        json={"accepted": True, "display_name": "小周", "terms_version": "portrait-v1"},
    )
    assert replay.status_code == 409

    own_asset = client.get(f"/wedding-assets/{partner_asset['id']}", headers=_headers(participant_token))
    assert own_asset.status_code == 200
    assert own_asset.headers["content-type"] == "image/png"
    assert own_asset.content.startswith(b"\x89PNG")

    other_asset = client.get(f"/wedding-assets/{owner_asset['id']}", headers=_headers(participant_token))
    assert other_asset.status_code == 404

    with get_session_factory()() as db:
        invite = db.query(ConsentInvite).one()
        participant = db.query(WeddingParticipant).filter_by(person_key="B").one()
        assert invite.status == "consumed"
        assert participant.consent_status == "active"
        asset = db.get(WeddingAsset, partner_asset["id"])
        assert asset is not None
        assert asset.object_key not in own_asset.text
        assert client.get(f"/static/uploads/{asset.object_key}").status_code == 404

    revoked = client.post(
        f"/wedding-projects/{project_id}/consent/revoke",
        headers=_headers(participant_token),
    )
    assert revoked.status_code == 200
    with get_session_factory()() as db:
        participant = db.query(WeddingParticipant).filter_by(person_key="B").one()
        assert participant.consent_status == "revoked"
        assert db.query(WeddingConsent).filter_by(participant_id=participant.id, revoked_at=None).count() == 0
    assert client.get(f"/wedding-assets/{partner_asset['id']}", headers=_headers(participant_token)).status_code == 401


def test_wedding_read_models_restore_owner_and_participant_scopes(client: TestClient) -> None:
    from datetime import timedelta

    from app.models import (
        GenerationJob,
        RenderVersion,
        ShotCandidate,
        ParticipantReview,
        StoryboardShot,
        StoryFact,
        StoryVersion,
        WeddingAssetType,
        WeddingQuote,
        utc_now,
    )

    owner_token = login(client, "restore-owner@example.com")
    outsider_token = login(client, "restore-outsider@example.com")
    project_id = _create_project(client, owner_token)["id"]
    client.post(
        f"/wedding-projects/{project_id}/self-consent",
        headers=_headers(owner_token),
        json={"terms_version": "portrait-v1", "scope": ["portrait_processing"], "provider_list": ["local_demo"]},
    )
    participant_token = _confirm_partner(client, owner_token, project_id)
    owner_photo = _image_upload(client, owner_token, project_id, "A")
    partner_photo = _image_upload(client, participant_token, project_id, "B")

    submitted = client.post(
        f"/wedding-projects/{project_id}/storyboard",
        headers=_headers(owner_token),
        json={
            "version": 1,
            "story_text": "我们在春天相遇，一起走向新的生活。",
            "facts": [
                {"source_field": "相识", "text": "A 的相识事实"},
                {"source_field": "共同生活", "text": "B 的共同生活事实"},
            ],
            "shots": [
                {"shot_index": 1, "shot_type": "person_ai", "duration_ms": 5000, "person_keys": ["A"], "fact_indexes": [0], "description": "A 的镜头", "subtitle": "A 字幕"},
                {"shot_index": 2, "shot_type": "person_ai", "duration_ms": 5000, "person_keys": ["B"], "fact_indexes": [1], "description": "B 的镜头", "subtitle": "B 字幕"},
            ],
        },
    )
    assert submitted.status_code == 201, submitted.text
    approved = client.post(
        f"/wedding-projects/{project_id}/storyboard/approve",
        headers=_headers(owner_token),
        json={"version": 1},
    )
    assert approved.status_code == 200, approved.text
    shot_a, shot_b = submitted.json()["shots"]
    photo_frames = client.post(
        f"/wedding-projects/{project_id}/frames/approve",
        headers=_headers(owner_token),
        json={"version": 1, "frames": [
            {"shot_id": shot_a["id"], "asset_id": owner_photo["id"]},
            {"shot_id": shot_b["id"], "asset_id": partner_photo["id"]},
        ]},
    )
    assert photo_frames.status_code == 200, photo_frames.text

    with get_session_factory()() as db:
        now = utc_now()
        story = db.query(StoryVersion).filter_by(project_id=project_id).one()
        quote = WeddingQuote(
            project_id=project_id,
            project_version=1,
            max_budget_fen=1000,
            expires_at=now + timedelta(minutes=30),
        )
        db.add(quote)
        db.flush()
        job_a = GenerationJob(
            project_id=project_id,
            story_version_id=story.id,
            quote_id=quote.id,
            target_shot_id=shot_a["id"],
            project_version=1,
            idempotency_key="restore-job-a",
            request_fingerprint="a" * 64,
            status="completed",
            progress_stage="completed",
            total_shots=1,
            completed_shots=1,
        )
        job_b = GenerationJob(
            project_id=project_id,
            story_version_id=story.id,
            quote_id=quote.id,
            target_shot_id=shot_b["id"],
            project_version=1,
            idempotency_key="restore-job-b",
            request_fingerprint="b" * 64,
            status="failed",
            progress_stage="failed",
            total_shots=1,
            error_code="PROVIDER_UNAVAILABLE",
        )
        db.add_all([job_a, job_b])
        db.flush()
        job_a_id = job_a.id
        job_b_id = job_b.id

        candidate_a_asset = WeddingAsset(
            project_id=project_id,
            person_key=None,
            asset_type=WeddingAssetType.GENERATED_FRAME,
            object_key=f"{project_id}/candidate-a.png",
            sha256="a" * 64,
            mime="image/png",
            size_bytes=8,
            generated=True,
        )
        candidate_b_asset = WeddingAsset(
            project_id=project_id,
            person_key=None,
            asset_type=WeddingAssetType.GENERATED_FRAME,
            object_key=f"{project_id}/candidate-b.png",
            sha256="b" * 64,
            mime="image/png",
            size_bytes=8,
            generated=True,
        )
        export_asset = WeddingAsset(
            project_id=project_id,
            person_key=None,
            asset_type=WeddingAssetType.FINAL_VIDEO,
            object_key=f"{project_id}/export.mp4",
            sha256="e" * 64,
            mime="video/mp4",
            size_bytes=20,
            generated=True,
        )
        preview = WeddingAsset(
            project_id=project_id,
            person_key=None,
            asset_type=WeddingAssetType.PREVIEW_VIDEO,
            object_key=f"{project_id}/preview.mp4",
            sha256="c" * 64,
            mime="video/mp4",
            size_bytes=16,
            generated=True,
        )
        db.add_all([candidate_a_asset, candidate_b_asset, preview, export_asset])
        db.flush()
        candidate_a_asset_id = candidate_a_asset.id
        candidate_b_asset_id = candidate_b_asset.id
        preview_asset_id = preview.id
        export_asset_id = export_asset.id
        render = RenderVersion(
            project_id=project_id,
            project_version=1,
            version=1,
            status="ready",
            timeline_hash="d" * 64,
            input_asset_ids=[owner_photo["id"], partner_photo["id"]],
            preview_asset_id=preview.id,
            export_asset_id=export_asset.id,
            output_duration_ms=90000,
        )
        db.add_all(
            [
                ShotCandidate(
                    project_id=project_id,
                    shot_id=shot_a["id"],
                    generation_job_id=job_a.id,
                    asset_id=candidate_a_asset.id,
                    provider="local_demo",
                    model_version="local-demo-v1",
                ),
                ShotCandidate(
                    project_id=project_id,
                    shot_id=shot_b["id"],
                    generation_job_id=job_b.id,
                    asset_id=candidate_b_asset.id,
                    provider="local_demo",
                    model_version="local-demo-v1",
                ),
                render,
            ]
        )
        db.flush()
        render_id = render.id
        db.commit()

    owner_headers = _headers(owner_token)
    partner_headers = _headers(participant_token)
    outsider_headers = _headers(outsider_token)
    review_a = client.post(
        f"/participant-reviews/{shot_a['id']}",
        headers=owner_headers,
        json={"asset_id": candidate_a_asset_id, "result": "approved", "project_version": 1},
    )
    review_b = client.post(
        f"/participant-reviews/{shot_b['id']}",
        headers=partner_headers,
        json={"asset_id": candidate_b_asset_id, "result": "approved", "project_version": 1},
    )
    assert review_a.status_code == 200, review_a.text
    assert review_b.status_code == 200, review_b.text
    render_review_a = client.post(
        f"/wedding-projects/{project_id}/render-reviews",
        headers=owner_headers,
        params={"render_version_id": render_id},
        json={"asset_id": preview_asset_id, "result": "approved", "project_version": 1},
    )
    render_review_b = client.post(
        f"/wedding-projects/{project_id}/render-reviews",
        headers=partner_headers,
        params={"render_version_id": render_id},
        json={"asset_id": preview_asset_id, "result": "approved", "project_version": 1},
    )
    assert render_review_a.status_code == 200, render_review_a.text
    assert render_review_b.status_code == 200, render_review_b.text
    owner_story = client.get(f"/wedding-projects/{project_id}/storyboard", headers=owner_headers)
    partner_story = client.get(f"/wedding-projects/{project_id}/storyboard", headers=partner_headers)
    assert owner_story.status_code == 200, owner_story.text
    assert len(owner_story.json()["shots"]) == 2
    assert owner_story.json()["shots"][0]["selected_asset_id"] is None
    assert owner_story.json()["shots"][0]["selected_candidate_id"] is None
    assert partner_story.status_code == 200, partner_story.text
    assert [shot["id"] for shot in partner_story.json()["shots"]] == [shot_b["id"]]
    assert partner_story.json()["facts"][0]["text"] == "B 的共同生活事实"
    assert partner_story.json()["story_text"] == ""
    assert [item["person_key"] for item in owner_story.json()["shots"][1]["identity_reviews"]] == ["B"]
    assert [item["person_key"] for item in partner_story.json()["shots"][0]["identity_reviews"]] == ["B"]

    owner_assets = client.get(f"/wedding-projects/{project_id}/assets", headers=owner_headers)
    partner_assets = client.get(f"/wedding-projects/{project_id}/assets", headers=partner_headers)
    assert owner_assets.status_code == 200
    assert {item["id"] for item in owner_assets.json()} >= {owner_photo["id"], partner_photo["id"]}
    assert partner_assets.status_code == 200
    assert {item["id"] for item in partner_assets.json()} == {partner_photo["id"], candidate_b_asset_id, preview_asset_id}
    assert export_asset_id not in {item["id"] for item in partner_assets.json()}
    assert client.get(f"/wedding-assets/{export_asset_id}", headers=partner_headers).status_code == 404

    owner_jobs = client.get(f"/wedding-projects/{project_id}/generation-jobs", headers=owner_headers)
    partner_jobs = client.get(f"/wedding-projects/{project_id}/generation-jobs", headers=partner_headers)
    assert owner_jobs.status_code == 200 and len(owner_jobs.json()) == 2
    assert partner_jobs.status_code == 200 and {item["id"] for item in partner_jobs.json()} == {job_b_id}

    owner_candidates = client.get(f"/wedding-projects/{project_id}/candidates", headers=owner_headers)
    partner_candidates = client.get(f"/wedding-projects/{project_id}/candidates", headers=partner_headers)
    assert owner_candidates.status_code == 200 and len(owner_candidates.json()) == 2
    assert partner_candidates.status_code == 200
    assert [item["shot_id"] for item in partner_candidates.json()] == [shot_b["id"]]

    owner_renders = client.get(f"/wedding-projects/{project_id}/renders", headers=owner_headers)
    partner_renders = client.get(f"/wedding-projects/{project_id}/renders", headers=partner_headers)
    assert owner_renders.status_code == 200 and owner_renders.json()[0]["preview_asset_id"] == preview_asset_id
    assert owner_renders.json()[0]["export_asset_id"] == export_asset_id
    assert partner_renders.status_code == 200
    assert partner_renders.json()[0]["preview_asset_id"] == preview_asset_id
    assert partner_renders.json()[0]["export_asset_id"] is None
    assert [item["person_key"] for item in owner_renders.json()[0]["identity_reviews"]] == ["A", "B"]
    assert [item["person_key"] for item in partner_renders.json()[0]["identity_reviews"]] == ["B"]

    for path in (
        f"/wedding-projects/{project_id}/storyboard",
        f"/wedding-projects/{project_id}/assets",
        f"/wedding-projects/{project_id}/generation-jobs",
        f"/wedding-projects/{project_id}/candidates",
        f"/wedding-projects/{project_id}/renders",
    ):
        assert client.get(path, headers=outsider_headers).status_code == 404


def test_wedding_capabilities_report_demo_and_unavailable_without_credentials(client: TestClient, monkeypatch) -> None:
    monkeypatch.setenv("WEDDING_MEDIA_MODE", "disabled")
    monkeypatch.setenv("WEDDING_STORY_PROVIDER", "ark_seed")
    monkeypatch.setenv("WEDDING_STORY_API_KEY", "must-not-be-returned")
    from app.config import get_settings

    get_settings.cache_clear()
    assert client.get("/wedding-capabilities").status_code == 401
    token = login(client, "capability-reader@example.com")
    response = client.get("/wedding-capabilities", headers=_headers(token))
    assert response.status_code == 200
    data = response.json()
    assert data["demo_only"] is False
    assert data["providers"]["story"]["available"] is False
    assert data["providers"]["story"]["reason"] == "LIVE_ADAPTER_UNAVAILABLE"
    assert "credentials_present" not in data["providers"]["story"]
    assert "must-not-be-returned" not in response.text


def test_storyboard_draft_can_be_updated_but_approved_story_is_immutable(client: TestClient) -> None:
    token = login(client, "story-editor@example.com")
    project_id = _create_project(client, token)["id"]
    headers = _headers(token)

    def payload(story_text: str, subtitle: str) -> dict[str, object]:
        return {
            "version": 1,
            "story_text": story_text,
            "facts": [{"source_field": "相识", "text": "确认过的事实"}],
            "shots": [
                {
                    "shot_index": 1,
                    "shot_type": "photo",
                    "duration_ms": 5000,
                    "person_keys": ["A"],
                    "fact_indexes": [0],
                    "description": "用户确认的画面",
                    "subtitle": subtitle,
                }
            ],
        }

    first = client.post(f"/wedding-projects/{project_id}/storyboard", headers=headers, json=payload("初稿", "初稿字幕"))
    assert first.status_code == 201, first.text
    updated = client.put(f"/wedding-projects/{project_id}/storyboard", headers=headers, json=payload("修订稿", "修订后的字幕"))
    assert updated.status_code == 200, updated.text
    assert updated.json()["id"] == first.json()["id"]
    assert updated.json()["shots"][0]["id"] != first.json()["shots"][0]["id"]
    assert updated.json()["story_text"] == "修订稿"
    assert updated.json()["shots"][0]["subtitle"] == "修订后的字幕"

    approved = client.post(
        f"/wedding-projects/{project_id}/storyboard/approve",
        headers=headers,
        json={"version": 1},
    )
    assert approved.status_code == 200
    immutable = client.put(f"/wedding-projects/{project_id}/storyboard", headers=headers, json=payload("不能覆盖", "不应生效"))
    assert immutable.status_code == 409
    assert immutable.json()["detail"] == "APPROVED_STORYBOARD_IMMUTABLE"


def test_concurrent_invite_redemption_returns_one_participant_session(client: TestClient, monkeypatch) -> None:
    owner_token = login(client, "owner@example.com")
    project_id = _create_project(client, owner_token)["id"]
    invite_response = client.post(
        f"/wedding-projects/{project_id}/consent-invites",
        headers=_headers(owner_token),
        json={
            "display_name": "小周",
            "provider_list": ["volcengine_ark"],
            "terms_version": "portrait-v1",
        },
    )
    assert invite_response.status_code == 201, invite_response.text
    invite_token = invite_response.json()["invite_token"]

    barrier = Barrier(2)
    lock = Lock()
    waiting_requests = 0
    original_execute = SQLAlchemySession.execute

    def synchronize_invite_reads(session, statement, *args, **kwargs):
        nonlocal waiting_requests
        result = original_execute(session, statement, *args, **kwargs)
        sql = str(statement)
        if "wedding_consent_invites" in sql and "token_hash" in sql:
            with lock:
                should_wait = waiting_requests < 2
                waiting_requests += 1
            if should_wait:
                barrier.wait(timeout=10)
        return result

    monkeypatch.setattr(SQLAlchemySession, "execute", synchronize_invite_reads)

    def redeem():
        try:
            return client.post(
                f"/consent-invites/{invite_token}/confirm",
                json={"accepted": True, "display_name": "小周", "terms_version": "portrait-v1"},
            )
        except Exception as exc:  # Keep SQLite write races observable as a failed response assertion.
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: redeem(), range(2)))

    statuses = sorted(getattr(response, "status_code", 500) for response in responses)
    assert statuses == [200, 409]
    successful = next(response for response in responses if getattr(response, "status_code", None) == 200)
    assert successful.json()["access_token"]
    with get_session_factory()() as db:
        participant = db.query(WeddingParticipant).filter_by(project_id=project_id, person_key="B").one()
        assert participant.session_token_hash is not None
        assert db.query(WeddingConsent).filter_by(participant_id=participant.id, revoked_at=None).count() == 1


def test_generation_job_requires_consents_storyboard_static_frames_and_identity_reviews(
    client: TestClient,
    monkeypatch,
) -> None:
    owner_token = login(client, "owner@example.com")
    project_id = _create_project(client, owner_token)["id"]

    premature_quote = client.get(f"/wedding-projects/{project_id}/quote", headers=_headers(owner_token))
    assert premature_quote.status_code == 409

    own_consent = client.post(
        f"/wedding-projects/{project_id}/self-consent",
        headers=_headers(owner_token),
        json={
            "terms_version": "portrait-v1",
            "scope": ["portrait_processing"],
            "provider_list": ["volcengine_ark"],
        },
    )
    assert own_consent.status_code == 200, own_consent.text
    missing_partner_quote = client.get(f"/wedding-projects/{project_id}/quote", headers=_headers(owner_token))
    assert missing_partner_quote.status_code == 409
    participant_token = _confirm_partner(client, owner_token, project_id)

    storyboard = client.post(
        f"/wedding-projects/{project_id}/storyboard",
        headers=_headers(owner_token),
        json={
            "version": 1,
            "story_text": "我們在大學的社團活動中相識，之後一起旅行。",
            "facts": [
                {"source_field": "meet_story", "text": "兩人在大學社團活動中相識。"}
            ],
            "shots": [
                {
                    "shot_index": 1,
                    "shot_type": "person_ai",
                    "duration_ms": 5000,
                    "person_keys": ["A", "B"],
                    "fact_indexes": [0],
                    "description": "兩人並肩走在校園小路。",
                    "subtitle": "故事從一次社團活動開始。",
                }
            ],
        },
    )
    assert storyboard.status_code == 201, storyboard.text
    shot_id = storyboard.json()["shots"][0]["id"]

    approved_story = client.post(
        f"/wedding-projects/{project_id}/storyboard/approve",
        headers=_headers(owner_token),
        json={"version": 1},
    )
    assert approved_story.status_code == 200, approved_story.text

    frame = _image_upload(client, owner_token, project_id, "A", "storyboard_frame")
    frame_approval = client.post(
        f"/wedding-projects/{project_id}/frames/approve",
        headers=_headers(owner_token),
        json={"version": 1, "frames": [{"shot_id": shot_id, "asset_id": frame["id"]}]},
    )
    assert frame_approval.status_code == 200, frame_approval.text
    assert client.get(f"/wedding-assets/{frame['id']}", headers=_headers(participant_token)).status_code == 200

    missing_reviews = client.get(f"/wedding-projects/{project_id}/quote", headers=_headers(owner_token))
    assert missing_reviews.status_code == 409

    review_owner = client.post(
        f"/participant-reviews/{shot_id}",
        headers=_headers(owner_token),
        json={"asset_id": frame["id"], "result": "approved", "project_version": 1},
    )
    assert review_owner.status_code == 200, review_owner.text
    review_partner = client.post(
        f"/participant-reviews/{shot_id}",
        headers=_headers(participant_token),
        json={"asset_id": frame["id"], "result": "approved", "project_version": 1},
    )
    assert review_partner.status_code == 200, review_partner.text

    quote = client.get(f"/wedding-projects/{project_id}/quote", headers=_headers(owner_token))
    assert quote.status_code == 200, quote.text
    quote_data = quote.json()
    job_body = {"quote_id": quote_data["id"], "version": 1}
    headers = {**_headers(owner_token), "Idempotency-Key": "wedding-job-1"}

    original_flush = SQLAlchemySession.flush
    injected = {"done": False}

    def fail_job_flush(session: SQLAlchemySession, *args, **kwargs) -> None:
        if not injected["done"] and any(isinstance(item, GenerationJob) for item in session.new):
            injected["done"] = True
            raise IntegrityError(None, None, RuntimeError("simulated unique-key race"))
        original_flush(session, *args, **kwargs)

    with monkeypatch.context() as scoped_patch:
        scoped_patch.setattr(SQLAlchemySession, "flush", fail_job_flush)
        failed_flush = client.post(
            f"/wedding-projects/{project_id}/generation-jobs",
            headers=headers,
            json=job_body,
        )
    assert failed_flush.status_code == 409
    assert failed_flush.json()["detail"] == "IDEMPOTENCY_KEY_REUSED"
    with get_session_factory()() as db:
        before_retry = db.get(WeddingProject, project_id)
        assert before_retry is not None
        assert before_retry.budget_reserved_fen == 0
        assert db.query(BudgetLedger).filter_by(project_id=project_id).count() == 0

    created = client.post(
        f"/wedding-projects/{project_id}/generation-jobs",
        headers=headers,
        json=job_body,
    )
    assert created.status_code == 202, created.text
    job = created.json()
    assert job["status"] == "queued"
    assert job["completed_shots"] == 0
    assert job["total_shots"] == 1
    assert "output_url" not in job

    duplicate = client.post(
        f"/wedding-projects/{project_id}/generation-jobs",
        headers=headers,
        json=job_body,
    )
    assert duplicate.status_code == 202
    assert duplicate.json()["id"] == job["id"]

    progress = client.get(f"/wedding-generation-jobs/{job['id']}", headers=_headers(owner_token))
    assert progress.status_code == 200
    assert progress.json()["status"] == "queued"
    assert progress.json()["completed_shots"] == 0

    with get_session_factory()() as db:
        persisted_project = db.get(WeddingProject, project_id)
        assert persisted_project is not None
        assert persisted_project.budget_reserved_fen == quote_data["max_budget_fen"]
        assert db.query(BudgetLedger).filter_by(project_id=project_id).count() == 1

    cancelled = client.post(
        f"/wedding-projects/{project_id}/generation-jobs/{job['id']}/cancel",
        headers=_headers(owner_token),
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    with get_session_factory()() as db:
        persisted_project = db.get(WeddingProject, project_id)
        ledger = db.query(BudgetLedger).filter_by(project_id=project_id).one()
        assert persisted_project is not None
        assert persisted_project.status == "ready_to_generate"
        assert persisted_project.budget_reserved_fen == 0
        assert ledger.status == "released"

    second_quote = client.get(f"/wedding-projects/{project_id}/quote", headers=_headers(owner_token)).json()
    second_job = client.post(
        f"/wedding-projects/{project_id}/generation-jobs",
        headers={**_headers(owner_token), "Idempotency-Key": "wedding-job-2"},
        json={"quote_id": second_quote["id"], "version": 1},
    )
    assert second_job.status_code == 202, second_job.text
    with get_session_factory()() as db:
        persisted_job = db.get(GenerationJob, second_job.json()["id"])
        assert persisted_job is not None
        persisted_job.status = "processing"
        db.commit()

    edited = client.patch(
        f"/wedding-projects/{project_id}",
        headers=_headers(owner_token),
        json={"version": 1, "title": "Version changed during provider work"},
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["version"] == 2
    with get_session_factory()() as db:
        persisted_job = db.get(GenerationJob, second_job.json()["id"])
        persisted_project = db.get(WeddingProject, project_id)
        assert persisted_job is not None
        assert persisted_project is not None
        assert persisted_job.status == "cancel_requested"
        assert persisted_job.cancel_requested_at is not None
        assert persisted_project.status == "story_review"
        assert persisted_project.budget_reserved_fen == second_quote["max_budget_fen"]


def test_asset_upload_rejects_fake_image_and_mime_spoof(client: TestClient) -> None:
    owner_token = login(client)
    project_id = _create_project(client, owner_token)["id"]
    consent = client.post(
        f"/wedding-projects/{project_id}/self-consent",
        headers=_headers(owner_token),
        json={
            "terms_version": "portrait-v1",
            "scope": ["portrait_processing"],
            "provider_list": ["volcengine_ark"],
        },
    )
    assert consent.status_code == 200
    _confirm_partner(client, owner_token, project_id)

    fake_image = client.post(
        f"/wedding-projects/{project_id}/assets",
        headers=_headers(owner_token),
        data={"person_key": "A", "asset_type": "photo"},
        files={"file": ("photo.png", b"not an image", "image/png")},
    )
    assert fake_image.status_code == 415

    image = Image.new("RGB", (1024, 1024), color=(10, 20, 30))
    data = BytesIO()
    image.save(data, format="PNG")
    spoofed_mime = client.post(
        f"/wedding-projects/{project_id}/assets",
        headers=_headers(owner_token),
        data={"person_key": "A", "asset_type": "photo"},
        files={"file": ("photo.png", data.getvalue(), "image/jpeg")},
    )
    assert spoofed_mime.status_code == 415


def test_portrait_upload_requires_both_participants_before_storage(client: TestClient, monkeypatch) -> None:
    owner_token = login(client, "owner@example.com")
    image = Image.new("RGB", (1024, 1024), color=(60, 90, 120))
    data = BytesIO()
    image.save(data, format="PNG")

    stored_payloads: list[bytes] = []
    original_put = WeddingPrivateStorage.put

    def tracked_put(storage, project_id, payload, suffix, mime):
        stored_payloads.append(payload)
        return original_put(storage, project_id, payload, suffix, mime)

    monkeypatch.setattr(WeddingPrivateStorage, "put", tracked_put)

    def upload(token: str, project_id: str, key: str):
        return client.post(
            f"/wedding-projects/{project_id}/assets",
            headers=_headers(token),
            data={"person_key": key, "asset_type": "photo"},
            files={"file": ("portrait.png", data.getvalue(), "image/png")},
        )

    owner_only_project = _create_project(client, owner_token)["id"]
    self_consent = client.post(
        f"/wedding-projects/{owner_only_project}/self-consent",
        headers=_headers(owner_token),
        json={
            "terms_version": "portrait-v1",
            "scope": ["portrait_processing"],
            "provider_list": ["volcengine_ark"],
        },
    )
    assert self_consent.status_code == 200
    owner_before_partner = upload(owner_token, owner_only_project, "A")
    assert owner_before_partner.status_code == 409
    assert owner_before_partner.json()["detail"] == "CONSENT_REQUIRED"

    with get_session_factory()() as db:
        assert db.query(WeddingAsset).filter_by(project_id=owner_only_project).count() == 0
    assert stored_payloads == []

    invite = client.post(
        f"/wedding-projects/{owner_only_project}/consent-invites",
        headers=_headers(owner_token),
        json={"display_name": "Chen", "provider_list": ["volcengine_ark"], "terms_version": "portrait-v1"},
    )
    assert invite.status_code == 201
    confirmed = client.post(
        f"/consent-invites/{invite.json()['invite_token']}/confirm",
        json={"accepted": True, "display_name": "Chen", "terms_version": "portrait-v1"},
    )
    assert confirmed.status_code == 200
    partner_token = confirmed.json()["access_token"]

    owner_b_upload = upload(owner_token, owner_only_project, "B")
    assert owner_b_upload.status_code == 403
    owner_a_upload = upload(owner_token, owner_only_project, "A")
    assert owner_a_upload.status_code == 201
    partner_b_upload = upload(partner_token, owner_only_project, "B")
    assert partner_b_upload.status_code == 201
    partner_a_upload = upload(partner_token, owner_only_project, "A")
    assert partner_a_upload.status_code == 403

    partner_only_project = _create_project(client, owner_token)["id"]
    partner_invite = client.post(
        f"/wedding-projects/{partner_only_project}/consent-invites",
        headers=_headers(owner_token),
        json={"display_name": "Chen", "provider_list": ["volcengine_ark"], "terms_version": "portrait-v1"},
    )
    assert partner_invite.status_code == 201
    partner_confirmed = client.post(
        f"/consent-invites/{partner_invite.json()['invite_token']}/confirm",
        json={"accepted": True, "display_name": "Chen", "terms_version": "portrait-v1"},
    )
    assert partner_confirmed.status_code == 200
    partner_only_token = partner_confirmed.json()["access_token"]
    partner_before_owner = upload(partner_only_token, partner_only_project, "B")
    assert partner_before_owner.status_code == 409
    assert partner_before_owner.json()["detail"] == "CONSENT_REQUIRED"

    with get_session_factory()() as db:
        assert db.query(WeddingAsset).filter_by(project_id=partner_only_project).count() == 0
    assert len(stored_payloads) == 2

    owner_consent = client.post(
        f"/wedding-projects/{partner_only_project}/self-consent",
        headers=_headers(owner_token),
        json={
            "terms_version": "portrait-v1",
            "scope": ["portrait_processing"],
            "provider_list": ["volcengine_ark"],
        },
    )
    assert owner_consent.status_code == 200
    partner_after_owner = upload(partner_only_token, partner_only_project, "B")
    owner_after_partner = upload(owner_token, partner_only_project, "A")
    assert partner_after_owner.status_code == 201
    assert owner_after_partner.status_code == 201
    assert len(stored_payloads) == 4


def test_shot_regeneration_rejects_a_shot_from_an_old_story_version(client: TestClient) -> None:
    owner_token = login(client)
    project_id = _create_project(client, owner_token)["id"]

    def submit_story(version: int) -> dict[str, object]:
        result = client.post(
            f"/wedding-projects/{project_id}/storyboard",
            headers=_headers(owner_token),
            json={
                "version": version,
                "story_text": f"Story version {version}",
                "facts": [{"source_field": "meet_story", "text": f"Fact {version}"}],
                "shots": [
                    {
                        "shot_index": 1,
                        "shot_type": "person_ai",
                        "person_keys": ["A"],
                        "fact_indexes": [0],
                        "description": f"Shot in version {version}",
                    }
                ],
            },
        )
        assert result.status_code == 201, result.text
        return result.json()

    old_story = submit_story(1)
    old_shot_id = old_story["shots"][0]["id"]
    updated = client.patch(
        f"/wedding-projects/{project_id}",
        headers=_headers(owner_token),
        json={"version": 1, "title": "New version"},
    )
    assert updated.status_code == 200
    submit_story(2)

    redo = client.post(
        f"/wedding-projects/{project_id}/shots/{old_shot_id}/regenerate",
        headers={**_headers(owner_token), "Idempotency-Key": "stale-shot-redo"},
        json={"quote_id": "not-a-current-quote", "version": 2},
    )
    assert redo.status_code == 409
    assert redo.json()["detail"] == "SHOT_VERSION_STALE"


def test_domain_status_transitions_reject_illegal_edges() -> None:
    from app.models import (
        BudgetLedgerStatus,
        ParticipantReviewStatus,
        RenderVersionStatus,
        StoryVersionStatus,
        StoryboardShotStatus,
        WeddingConsentStatus,
        WeddingInviteStatus,
    )
    from app.models import (
        ConsentInvite,
        ParticipantReview,
        RenderVersion,
        StoryVersion,
        StoryboardShot,
        WeddingParticipant,
    )
    from app.wedding_service import (
        set_budget_status,
        set_consent_status,
        set_invite_status,
        set_render_status,
        set_review_status,
        set_shot_status,
        set_story_status,
    )

    participant = WeddingParticipant(project_id="p", person_key="B", consent_status=WeddingConsentStatus.ACTIVE)
    with pytest.raises(ValueError):
        set_consent_status(participant, WeddingConsentStatus.PENDING)

    story = StoryVersion(project_id="p", version=1, story_text="text", style="warm_documentary", status=StoryVersionStatus.APPROVED)
    with pytest.raises(ValueError):
        set_story_status(story, StoryVersionStatus.DRAFT)

    shot = StoryboardShot(project_id="p", story_version_id="s", project_version=1, shot_index=1, shot_type="photo", duration_ms=5000, status=StoryboardShotStatus.FRAME_APPROVED)
    with pytest.raises(ValueError):
        set_shot_status(shot, StoryboardShotStatus.DRAFT)

    review = ParticipantReview(participant_id="p", project_id="p", person_key="B", project_version=1, asset_id="a", result=ParticipantReviewStatus.APPROVED, confirmed_at=__import__("datetime").datetime.now())
    with pytest.raises(ValueError):
        set_review_status(review, "pending")

    render = RenderVersion(project_id="p", project_version=1, version=1, status=RenderVersionStatus.APPROVED, timeline_hash="x")
    with pytest.raises(ValueError):
        set_render_status(render, RenderVersionStatus.PROCESSING)

    ledger = BudgetLedger(project_id="p", generation_job_id="j", reservation_key="r", status=BudgetLedgerStatus.SETTLED, reserved_fen=100)
    with pytest.raises(ValueError):
        set_budget_status(ledger, BudgetLedgerStatus.RELEASED)

    invite = ConsentInvite(project_id="p", participant_id="p", token_hash="x", terms_version="v1", expires_at=__import__("datetime").datetime.now(), status=WeddingInviteStatus.CONSUMED)
    with pytest.raises(ValueError):
        set_invite_status(invite, WeddingInviteStatus.ACTIVE)
