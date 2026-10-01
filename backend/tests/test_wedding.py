from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from app.database import get_session_factory
from app.config import get_settings
from app.models import BudgetLedger, ConsentInvite, WeddingAsset, WeddingProject
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
    owner_asset = _image_upload(client, owner_token, project_id, "A")
    partner_asset = _image_upload(client, owner_token, project_id, "B")
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
        assert invite.token_hash != invite_token
        assert invite_token not in invite.token_hash

    confirmed = client.post(
        f"/consent-invites/{invite_token}/confirm",
        json={"accepted": True, "display_name": "小周", "terms_version": "portrait-v1"},
    )
    assert confirmed.status_code == 200, confirmed.text
    participant_token = confirmed.json()["access_token"]
    assert confirmed.json()["participant"]["person_key"] == "B"

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
        asset = db.get(WeddingAsset, partner_asset["id"])
        assert asset is not None
        assert asset.object_key not in own_asset.text
        assert client.get(f"/static/uploads/{asset.object_key}").status_code == 404

    revoked = client.post(
        f"/wedding-projects/{project_id}/consent/revoke",
        headers=_headers(participant_token),
    )
    assert revoked.status_code == 200
    assert client.get(f"/wedding-assets/{partner_asset['id']}", headers=_headers(participant_token)).status_code == 401


def test_generation_job_requires_consents_storyboard_static_frames_and_identity_reviews(client: TestClient) -> None:
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


def test_asset_upload_rejects_fake_image_and_mime_spoof(client: TestClient) -> None:
    owner_token = login(client)
    project_id = _create_project(client, owner_token)["id"]

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
