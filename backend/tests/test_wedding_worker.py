from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from threading import Barrier, Event, Thread
import sys

from PIL import Image
import pytest
from fastapi import HTTPException
from sqlalchemy import event, select
from sqlalchemy.exc import OperationalError

from app.config import Settings, get_settings
from app.database import get_session_factory
from app.models import (
    BudgetLedger,
    BudgetLedgerStatus,
    CandidateReviewStatus,
    GenerationJob,
    RenderVersion,
    RenderVersionStatus,
    ShotCandidate,
    StoryVersion,
    StoryVersionStatus,
    StoryboardShot,
    StoryboardShotStatus,
    User,
    WeddingAsset,
    WeddingAssetType,
    WeddingJobStatus,
    WeddingParticipant,
    WeddingProject,
    WeddingProjectStatus,
    WeddingQuote,
    WeddingAuditEvent,
    utc_now,
)
from app.wedding_service import WeddingPrivateStorage, reserve_generation_budget, set_job_status
from app.wedding_worker import (
    ClaimedGenerationJob,
    MediaOutput,
    ProviderBundle,
    _finalize_job,
    _record_provider_output,
    _recover_stale_generation_jobs,
    claim_next_generation_job,
    claim_next_render_version,
    process_claimed_generation_job,
    process_claimed_render_version,
)
import app.wedding_worker as wedding_worker
from app.wedding_media import (
    FFmpegRenderer,
    ProviderUnavailable,
    build_provider_bundle,
)
from tests.conftest import login


@dataclass(frozen=True)
class ProjectWork:
    project_id: str
    story_id: str
    shot_id: str
    frame_asset_id: str
    job_id: str
    render_id: str | None = None


def _claim(factory, expected_job_id: str) -> ClaimedGenerationJob:
    claim = claim_next_generation_job(factory)
    assert claim is not None and claim.job_id == expected_job_id
    return claim


def _new_reservation_job(db, work: ProjectWork, key: str) -> GenerationJob:
    current = db.get(GenerationJob, work.job_id)
    assert current is not None
    job = GenerationJob(
        project_id=work.project_id,
        story_version_id=work.story_id,
        quote_id=current.quote_id,
        target_shot_id=work.shot_id,
        project_version=1,
        idempotency_key=key,
        request_fingerprint=(key + "f" * 64)[:64],
        status=WeddingJobStatus.QUEUED,
        progress_stage="queued",
        total_shots=1,
    )
    db.add(job)
    db.flush()
    return job


def _png() -> bytes:
    image = Image.new("RGB", (32, 32), color=(180, 80, 120))
    data = BytesIO()
    image.save(data, format="PNG")
    return data.getvalue()


def _project_with_job(client, *, with_render: bool = False) -> ProjectWork:
    token = login(client, "worker-owner@example.com")
    created = client.post(
        "/wedding-projects",
        headers={"Authorization": f"Bearer {token}"},
        json={"title": "Worker test", "owner_name": "A"},
    )
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]
    headers = {"Authorization": f"Bearer {token}"}
    owner_consent = client.post(
        f"/wedding-projects/{project_id}/self-consent",
        headers=headers,
        json={
            "terms_version": "worker-v1",
            "scope": ["portrait_processing"],
            "provider_list": ["local_demo"],
        },
    )
    assert owner_consent.status_code == 200, owner_consent.text
    invite = client.post(
        f"/wedding-projects/{project_id}/consent-invites",
        headers=headers,
        json={
            "person_key": "B",
            "display_name": "B",
            "provider_list": ["local_demo"],
            "terms_version": "worker-v1",
        },
    )
    assert invite.status_code == 201, invite.text
    partner = client.post(
        f"/consent-invites/{invite.json()['invite_token']}/confirm",
        json={"accepted": True, "display_name": "B", "terms_version": "worker-v1"},
    )
    assert partner.status_code == 200, partner.text

    storage = WeddingPrivateStorage(get_settings())
    with get_session_factory()() as db:
        project = db.get(WeddingProject, project_id)
        assert project is not None
        project.frame_approved_version = 1
        project.status = WeddingProjectStatus.GENERATING
        story = StoryVersion(
            project_id=project_id,
            version=1,
            story_text="User-authored story",
            style="warm_documentary",
            status=StoryVersionStatus.APPROVED,
            owner_approved_at=utc_now(),
        )
        db.add(story)
        db.flush()
        frame_key = storage.put(project_id, _png(), ".png", "image/png")
        frame = WeddingAsset(
            project_id=project_id,
            asset_type=WeddingAssetType.STORYBOARD_FRAME,
            object_key=frame_key,
            sha256="a" * 64,
            mime="image/png",
            size_bytes=len(_png()),
            generated=False,
        )
        db.add(frame)
        db.flush()
        shot = StoryboardShot(
            project_id=project_id,
            story_version_id=story.id,
            project_version=1,
            shot_index=0,
            shot_type="person_ai",
            duration_ms=5_000,
            person_keys=["A"],
            fact_ids=[],
            description="An approved shot description",
            subtitle="Confirmed subtitle",
            frame_asset_id=frame.id,
            status=StoryboardShotStatus.FRAME_APPROVED,
        )
        db.add(shot)
        quote = WeddingQuote(
            project_id=project_id,
            project_version=1,
            max_budget_fen=1_000,
            expires_at=utc_now() + timedelta(minutes=30),
        )
        db.add(quote)
        db.flush()
        job = GenerationJob(
            project_id=project_id,
            story_version_id=story.id,
            quote_id=quote.id,
            target_shot_id=shot.id,
            project_version=1,
            idempotency_key="worker-test-key",
            request_fingerprint="f" * 64,
            status=WeddingJobStatus.QUEUED,
            progress_stage="queued",
            total_shots=1,
        )
        db.add(job)
        db.flush()
        reserve_generation_budget(db, project, job, "generation:worker-test-key", 1_000)
        render_id = None
        if with_render:
            shot.status = StoryboardShotStatus.FALLBACK
            shot.selected_asset_id = frame.id
            render = RenderVersion(
                project_id=project_id,
                project_version=1,
                version=1,
                timeline_hash="b" * 64,
                input_asset_ids=[frame.id],
                status=RenderVersionStatus.QUEUED,
            )
            db.add(render)
            db.flush()
            render_id = render.id
        db.commit()
        return ProjectWork(project_id, story.id, shot.id, frame.id, job.id, render_id)


class FailingVideoProvider:
    def __init__(self) -> None:
        self.calls = 0

    def generate(self, request):
        self.calls += 1
        raise ProviderUnavailable("PROVIDER_UNAVAILABLE", "No configured provider")


class SuccessfulVideoProvider:
    def __init__(self, *, cost_fen: int = 37, mutate_version: bool = False) -> None:
        self.calls = 0
        self.idempotency_keys: list[str] = []
        self.cost_fen = cost_fen
        self.mutate_version = mutate_version

    def generate(self, request) -> MediaOutput:
        self.calls += 1
        self.idempotency_keys.append(request.idempotency_key)
        if self.mutate_version:
            with get_session_factory()() as db:
                project = db.get(WeddingProject, request.project_id)
                assert project is not None
                project.current_version += 1
                db.commit()
        return MediaOutput(
            data=b"provider-output-bytes",
            mime="video/mp4",
            provider="test-provider",
            model_version="test-model-v1",
            actual_cost_fen=self.cost_fen,
            request_id="test-request-1",
        )


class BlockingVideoProvider:
    def __init__(self) -> None:
        self.calls = 0
        self.entered = Event()
        self.release = Event()
        self.output = MediaOutput(
            data=b"late-provider-output",
            mime="video/mp4",
            provider="test-provider",
            model_version="test-model-v1",
            actual_cost_fen=19,
            request_id="late-request-1",
        )

    def generate(self, request) -> MediaOutput:
        self.calls += 1
        self.entered.set()
        if not self.release.wait(timeout=10):
            raise TimeoutError("test did not release the fake provider")
        return self.output


class PartialVideoProvider:
    def __init__(self) -> None:
        self.calls = 0

    def generate(self, request) -> MediaOutput:
        self.calls += 1
        if self.calls == 2:
            raise ProviderUnavailable("PROVIDER_TIMEOUT", "Provider did not return a second shot", actual_cost_fen=5)
        return MediaOutput(
            data=b"first-shot-bytes",
            mime="video/mp4",
            provider="test-provider",
            model_version="test-model-v1",
            actual_cost_fen=31,
        )


def test_default_provider_bundle_is_unavailable_and_never_dispatches_network(client, monkeypatch):
    monkeypatch.setenv("WEDDING_MEDIA_MODE", "disabled")
    settings = Settings(_env_file=None)
    bundle = build_provider_bundle(settings)
    assert bundle.character_video is not None
    with pytest.raises(ProviderUnavailable, match="PROVIDER_UNAVAILABLE"):
        bundle.character_video.generate(None)

    work = _project_with_job(client)
    claim = _claim(get_session_factory(), work.job_id)
    report = process_claimed_generation_job(
        get_session_factory(), claim, bundle, WeddingPrivateStorage(settings)
    )
    assert report.status == "failed"
    with get_session_factory()() as db:
        job = db.get(GenerationJob, work.job_id)
        ledger = db.execute(select(BudgetLedger).where(BudgetLedger.generation_job_id == work.job_id)).scalar_one()
        assert job is not None and job.error_code == "PROVIDER_UNAVAILABLE"
        assert job.progress_stage == "failed"
        assert job.provider_request_id is None
        assert job.completed_shots == 0
        assert ledger.status == BudgetLedgerStatus.RELEASED
        shot = db.get(StoryboardShot, work.shot_id)
        assert shot is not None and shot.status == StoryboardShotStatus.FAILED


def test_consent_is_rechecked_immediately_before_provider_dispatch(client, monkeypatch):
    work = _project_with_job(client)
    provider = FailingVideoProvider()
    bundle = ProviderBundle(character_video=provider)
    claim = _claim(get_session_factory(), work.job_id)
    with get_session_factory()() as db:
        partner = db.execute(
            select(WeddingParticipant).where(
                WeddingParticipant.project_id == work.project_id,
                WeddingParticipant.person_key == "B",
            )
        ).scalar_one()
        partner.consent_status = "revoked"
        partner.revoked_at = utc_now()
        db.commit()
    result = process_claimed_generation_job(
        get_session_factory(), claim, bundle, WeddingPrivateStorage(Settings(_env_file=None))
    )
    assert result.error_code == "CONSENT_REQUIRED"
    assert provider.calls == 0
    with get_session_factory()() as db:
        job = db.get(GenerationJob, work.job_id)
        assert job is not None and job.reserved_amount_fen == 0


def test_configured_production_provider_without_credentials_is_explicitly_unavailable():
    settings = Settings(
        _env_file=None,
        wedding_character_video_provider="aliyun_model_studio",
        wedding_character_video_api_key=None,
    )
    provider = build_provider_bundle(settings).character_video
    assert provider is not None
    with pytest.raises(ProviderUnavailable, match="PROVIDER_CREDENTIALS_MISSING"):
        provider.generate(None)


def test_success_settles_actual_cost_and_stores_provider_bytes_privately(client, tmp_path, monkeypatch):
    work = _project_with_job(client)
    provider = SuccessfulVideoProvider(cost_fen=37)
    settings = Settings(_env_file=None, local_upload_dir=str(tmp_path / "uploads"))
    storage = WeddingPrivateStorage(settings)
    claim = _claim(get_session_factory(), work.job_id)
    result = process_claimed_generation_job(
        get_session_factory(), claim, ProviderBundle(character_video=provider), storage
    )
    assert result.status == "completed"
    with get_session_factory()() as db:
        job = db.get(GenerationJob, work.job_id)
        ledger = db.execute(select(BudgetLedger).where(BudgetLedger.generation_job_id == work.job_id)).scalar_one()
        candidate = db.execute(select(ShotCandidate).where(ShotCandidate.generation_job_id == work.job_id)).scalar_one()
        asset = db.get(WeddingAsset, candidate.asset_id)
        project = db.get(WeddingProject, work.project_id)
        assert job is not None and job.actual_cost_fen == 37 and job.provider_request_id == "test-request-1"
        assert ledger.status == BudgetLedgerStatus.SETTLED
        assert ledger.actual_fen == 37 and ledger.released_fen == 963
        assert candidate.actual_cost_fen == 37
        assert asset is not None and asset.generated is True and asset.provider == "test-provider"
        assert project is not None and project.budget_spent_fen == 37 and project.budget_reserved_fen == 0
        assert project.status == WeddingProjectStatus.PREVIEW_REVIEW
        key = asset.object_key
    assert storage.read(key) == b"provider-output-bytes"


def test_partial_shot_failure_preserves_success_and_settles_known_cost_for_recovery(client):
    work = _project_with_job(client)
    provider = PartialVideoProvider()
    factory = get_session_factory()
    with factory() as db:
        first = db.get(StoryboardShot, work.shot_id)
        job = db.get(GenerationJob, work.job_id)
        assert first is not None and job is not None
        second = StoryboardShot(
            project_id=work.project_id,
            story_version_id=work.story_id,
            project_version=1,
            shot_index=1,
            shot_type="person_ai",
            duration_ms=5_000,
            person_keys=["A"],
            fact_ids=[],
            description="Second approved shot",
            subtitle="Second subtitle",
            frame_asset_id=work.frame_asset_id,
            status=StoryboardShotStatus.FRAME_APPROVED,
        )
        db.add(second)
        job.target_shot_id = None
        job.total_shots = 2
        db.commit()
    claim = _claim(factory, work.job_id)
    result = process_claimed_generation_job(
        factory, claim, ProviderBundle(character_video=provider), WeddingPrivateStorage(get_settings())
    )
    assert result.status == "needs_action"
    assert result.error_code == "PROVIDER_TIMEOUT"
    with factory() as db:
        job = db.get(GenerationJob, work.job_id)
        ledger = db.execute(select(BudgetLedger).where(BudgetLedger.generation_job_id == work.job_id)).scalar_one()
        candidates = list(db.execute(select(ShotCandidate).where(ShotCandidate.generation_job_id == work.job_id)).scalars())
        assert job is not None and job.completed_shots == 1 and job.actual_cost_fen == 36
        assert ledger.status == BudgetLedgerStatus.SETTLED and ledger.actual_fen == 36
        assert ledger.released_fen == 964
        assert len(candidates) == 1 and candidates[0].actual_cost_fen == 31


def test_duplicate_claim_cannot_process_the_same_queued_job_twice(client):
    work = _project_with_job(client)
    factory = get_session_factory()
    _claim(factory, work.job_id)
    assert claim_next_generation_job(factory) is None
    with factory() as db:
        job = db.get(GenerationJob, work.job_id)
        assert job is not None and job.status == WeddingJobStatus.PROCESSING


def test_cancellation_after_claim_stops_before_provider_dispatch_and_releases_reservation(client):
    work = _project_with_job(client)
    provider = SuccessfulVideoProvider()
    factory = get_session_factory()
    claim = _claim(factory, work.job_id)
    with factory() as db:
        job = db.get(GenerationJob, work.job_id)
        assert job is not None
        job.status = WeddingJobStatus.CANCEL_REQUESTED
        job.cancel_requested_at = utc_now()
        db.commit()
    result = process_claimed_generation_job(
        factory, claim, ProviderBundle(character_video=provider), WeddingPrivateStorage(Settings(_env_file=None))
    )
    assert result.status == "cancelled"
    assert provider.calls == 0
    with factory() as db:
        job = db.get(GenerationJob, work.job_id)
        ledger = db.execute(select(BudgetLedger).where(BudgetLedger.generation_job_id == work.job_id)).scalar_one()
        assert job is not None and job.reserved_amount_fen == 0
        assert ledger.status == BudgetLedgerStatus.RELEASED


def test_project_hard_cap_is_checked_before_dispatch_and_keeps_delivery_reserve(client):
    work = _project_with_job(client)
    provider = SuccessfulVideoProvider()
    factory = get_session_factory()
    claim = _claim(factory, work.job_id)
    with factory() as db:
        project = db.get(WeddingProject, work.project_id)
        assert project is not None
        project.budget_spent_fen = 13_001
        db.commit()
    result = process_claimed_generation_job(
        factory, claim, ProviderBundle(character_video=provider), WeddingPrivateStorage(get_settings())
    )
    assert result.error_code == "BUDGET_LIMIT_EXCEEDED"
    assert provider.calls == 0
    with factory() as db:
        project = db.get(WeddingProject, work.project_id)
        ledger = db.execute(select(BudgetLedger).where(BudgetLedger.generation_job_id == work.job_id)).scalar_one()
        assert project is not None and project.budget_delivery_reserve_fen == 2_000
        assert project.budget_reserved_fen == 0
        assert ledger.status == BudgetLedgerStatus.RELEASED


def test_owner_account_budget_cap_blocks_dispatch_across_two_valid_projects(client, monkeypatch):
    first = _project_with_job(client)
    second = _project_with_job(client)
    factory = get_session_factory()
    for work in (first, second):
        with factory() as db:
            project = db.get(WeddingProject, work.project_id)
            assert project is not None
            assert project.budget_reserved_fen == 1_000
            assert project.budget_spent_fen + project.budget_reserved_fen + project.budget_delivery_reserve_fen <= project.budget_limit_fen
    monkeypatch.setenv("WEDDING_ACCOUNT_BUDGET_LIMIT_FEN", "1500")
    get_settings.cache_clear()
    provider = SuccessfulVideoProvider()
    claim = _claim(factory, first.job_id)
    result = process_claimed_generation_job(
        factory, claim, ProviderBundle(character_video=provider), WeddingPrivateStorage(get_settings())
    )
    assert result.error_code == "ACCOUNT_BUDGET_LIMIT_EXCEEDED"
    assert provider.calls == 0
    with factory() as db:
        first_job = db.get(GenerationJob, first.job_id)
        first_ledger = db.execute(select(BudgetLedger).where(BudgetLedger.generation_job_id == first.job_id)).scalar_one()
        first_project = db.get(WeddingProject, first.project_id)
        second_project = db.get(WeddingProject, second.project_id)
        assert first_job is not None and first_job.actual_cost_fen == 0
        assert first_ledger.status == BudgetLedgerStatus.RELEASED
        assert first_project is not None and first_project.budget_reserved_fen == 0
        assert second_project is not None and second_project.budget_reserved_fen == 1_000


def test_reservation_enforces_owner_account_cap_across_projects_and_idempotent_replay(client, monkeypatch):
    first = _project_with_job(client)
    second = _project_with_job(client)
    monkeypatch.setenv("WEDDING_ACCOUNT_BUDGET_LIMIT_FEN", "2500")
    get_settings.cache_clear()
    factory = get_session_factory()

    with factory() as db:
        first_project = db.get(WeddingProject, first.project_id)
        assert first_project is not None
        first_job = _new_reservation_job(db, first, "account-reserve-first")
        ledger = reserve_generation_budget(db, first_project, first_job, "account-reserve-first", 300)
        first_job_id = first_job.id
        db.commit()
        ledger_id = ledger.id

    # Replaying the same reservation remains idempotent and does not count it twice.
    with factory() as db:
        first_project = db.get(WeddingProject, first.project_id)
        assert first_project is not None
        first_job = db.get(GenerationJob, first_job_id)
        assert first_job is not None
        replay = reserve_generation_budget(db, first_project, first_job, "account-reserve-first", 300)
        assert replay.id == ledger_id
        db.commit()

    with factory() as db:
        second_project = db.get(WeddingProject, second.project_id)
        assert second_project is not None
        second_job = _new_reservation_job(db, second, "account-reserve-second")
        with pytest.raises(HTTPException) as exc:
            reserve_generation_budget(db, second_project, second_job, "account-reserve-second", 300)
        assert exc.value.detail == "ACCOUNT_BUDGET_LIMIT_EXCEEDED"
        db.rollback()

    with factory() as db:
        owner_id = db.scalar(select(User.id).where(User.email == "worker-owner@example.com"))
        total = sum(
            db.execute(
                select(WeddingProject.budget_spent_fen + WeddingProject.budget_reserved_fen).where(
                    WeddingProject.owner_id == owner_id
                )
            ).scalars()
        )
        assert total == 2_300


def test_parallel_same_owner_reservations_never_exceed_account_cap(client, monkeypatch):
    first = _project_with_job(client)
    second = _project_with_job(client)
    monkeypatch.setenv("WEDDING_ACCOUNT_BUDGET_LIMIT_FEN", "2500")
    get_settings.cache_clear()
    factory = get_session_factory()
    job_ids = []
    for work, key in ((first, "parallel-first"), (second, "parallel-second")):
        with factory() as db:
            job_ids.append(_new_reservation_job(db, work, key).id)
            db.commit()

    start_together = Barrier(2)

    def reserve(work: ProjectWork, job_id: str, key: str) -> str:
        with factory() as db:
            project = db.get(WeddingProject, work.project_id)
            job = db.get(GenerationJob, job_id)
            assert project is not None and job is not None
            start_together.wait(timeout=5)
            try:
                reserve_generation_budget(db, project, job, key, 300)
                db.commit()
                return "reserved"
            except HTTPException as exc:
                db.rollback()
                return str(exc.detail)
            except OperationalError:
                # SQLite ignores FOR UPDATE and serializes concurrent writes coarsely;
                # a lock loser cannot create an over-cap reservation.
                db.rollback()
                return "database-write-serialized"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(reserve, (first, second), job_ids, ("parallel-first", "parallel-second")))
    with factory() as db:
        owner_id = db.scalar(select(User.id).where(User.email == "worker-owner@example.com"))
        # Sum across all projects (SQLite test DB may surface multiple scalar rows).
        total = sum(
            db.execute(
                select(WeddingProject.budget_spent_fen + WeddingProject.budget_reserved_fen).where(
                    WeddingProject.owner_id == owner_id
                )
            ).scalars()
        )
        assert total <= 2_500
        assert outcomes.count("reserved") <= 1
        assert any(outcome in {"reserved", "ACCOUNT_BUDGET_LIMIT_EXCEEDED", "database-write-serialized"} for outcome in outcomes)


def test_stale_dispatch_is_quarantined_and_late_cost_is_recorded_once_without_candidate(client, monkeypatch):
    work = _project_with_job(client)
    factory = get_session_factory()
    monkeypatch.setenv("WEDDING_WORKER_STALE_SECONDS", "120")
    get_settings.cache_clear()
    claim = _claim(factory, work.job_id)
    provider = BlockingVideoProvider()
    storage = WeddingPrivateStorage(get_settings())
    results = []
    worker = Thread(
        target=lambda: results.append(
            process_claimed_generation_job(factory, claim, ProviderBundle(character_video=provider), storage)
        ),
        daemon=True,
    )
    worker.start()
    assert provider.entered.wait(timeout=5)

    with factory() as db:
        job = db.get(GenerationJob, work.job_id)
        assert job is not None and job.progress_stage.startswith("dispatching_")
        job.updated_at = utc_now() - timedelta(hours=1)
        db.commit()
    assert claim_next_generation_job(factory) is None
    with factory() as db:
        job = db.get(GenerationJob, work.job_id)
        ledger = db.execute(select(BudgetLedger).where(BudgetLedger.generation_job_id == work.job_id)).scalar_one()
        assert job is not None and job.status == WeddingJobStatus.NEEDS_ACTION
        assert job.error_code == "PROVIDER_OUTCOME_UNKNOWN"
        assert job.progress_stage == "provider_outcome_unknown"
        assert ledger.status == BudgetLedgerStatus.RESERVED and job.reserved_amount_fen == 1_000

    provider.release.set()
    worker.join(timeout=10)
    assert not worker.is_alive()
    assert results and results[0].status == "needs_action"
    assert results[0].error_code == "PROVIDER_OUTCOME_UNKNOWN"
    _record_provider_output(factory, work.job_id, work.shot_id, provider.output, storage, claim.lease_token)
    with factory() as db:
        job = db.get(GenerationJob, work.job_id)
        candidates = list(db.execute(select(ShotCandidate).where(ShotCandidate.generation_job_id == work.job_id)).scalars())
        ledger = db.execute(select(BudgetLedger).where(BudgetLedger.generation_job_id == work.job_id)).scalar_one()
        assert job is not None and job.status == WeddingJobStatus.NEEDS_ACTION
        assert job.error_code == "PROVIDER_OUTCOME_UNKNOWN" and job.actual_cost_fen == 19
        assert not candidates
        assert ledger.status == BudgetLedgerStatus.RESERVED and job.reserved_amount_fen == 1_000


def test_old_predispatch_lease_is_rejected_after_stale_reclaim(client, monkeypatch):
    work = _project_with_job(client)
    factory = get_session_factory()
    monkeypatch.setenv("WEDDING_WORKER_STALE_SECONDS", "120")
    get_settings.cache_clear()
    old_claim = _claim(factory, work.job_id)
    with factory() as db:
        job = db.get(GenerationJob, work.job_id)
        assert job is not None
        job.updated_at = utc_now() - timedelta(hours=1)
        job.progress_stage = "claimed"
        db.commit()
    fresh_claim = _claim(factory, work.job_id)
    assert fresh_claim.lease_token != old_claim.lease_token

    provider = SuccessfulVideoProvider()
    result = process_claimed_generation_job(
        factory, old_claim, ProviderBundle(character_video=provider), WeddingPrivateStorage(get_settings())
    )
    assert result.error_code == "WORKER_LEASE_STALE"
    assert provider.calls == 0
    with factory() as db:
        job = db.get(GenerationJob, work.job_id)
        assert job is not None and job.status == WeddingJobStatus.PROCESSING
        assert job.reserved_amount_fen == 1_000 and job.actual_cost_fen == 0


def test_worker_finalization_locks_project_before_job_and_releases_budget(client):
    work = _project_with_job(client)
    factory = get_session_factory()
    engine = factory.kw["bind"]
    claim = _claim(factory, work.job_id)
    locked_entities = []

    def capture_lock_order(conn, cursor, statement, parameters, context, executemany):
        compiled = getattr(context, "compiled", None)
        compiled_statement = getattr(compiled, "statement", None)
        if compiled_statement is None or getattr(compiled_statement, "_for_update_arg", None) is None:
            return
        entities = getattr(compiled_statement, "column_descriptions", ())
        if entities:
            locked_entities.append(entities[0].get("entity"))

    event.listen(engine, "before_cursor_execute", capture_lock_order)
    try:
        result = _finalize_job(
            factory,
            work.job_id,
            status=WeddingJobStatus.FAILED,
            error_code="TEST_FINALIZATION",
            lease_token=claim.lease_token,
        )
    finally:
        event.remove(engine, "before_cursor_execute", capture_lock_order)

    assert result.status == "failed"
    assert locked_entities == [WeddingProject, GenerationJob]
    with factory() as db:
        project = db.get(WeddingProject, work.project_id)
        ledger = db.execute(
            select(BudgetLedger).where(BudgetLedger.generation_job_id == work.job_id)
        ).scalar_one()
        job = db.get(GenerationJob, work.job_id)
        assert project is not None and project.status == WeddingProjectStatus.READY_TO_GENERATE
        assert project.budget_reserved_fen == 0
        assert ledger.status == BudgetLedgerStatus.RELEASED
        assert job is not None and job.status == WeddingJobStatus.FAILED


def test_stale_recovery_locks_project_before_job_and_rotates_lease(client, monkeypatch):
    work = _project_with_job(client)
    factory = get_session_factory()
    monkeypatch.setenv("WEDDING_WORKER_STALE_SECONDS", "120")
    get_settings.cache_clear()
    old_claim = _claim(factory, work.job_id)
    with factory() as db:
        job = db.get(GenerationJob, work.job_id)
        assert job is not None
        job.updated_at = utc_now() - timedelta(hours=1)
        job.progress_stage = "claimed"
        db.commit()

    engine = factory.kw["bind"]
    locked_entities = []

    def capture_lock_order(conn, cursor, statement, parameters, context, executemany):
        compiled = getattr(context, "compiled", None)
        compiled_statement = getattr(compiled, "statement", None)
        if compiled_statement is None or getattr(compiled_statement, "_for_update_arg", None) is None:
            return
        entities = getattr(compiled_statement, "column_descriptions", ())
        if entities:
            locked_entities.append(entities[0].get("entity"))

    event.listen(engine, "before_cursor_execute", capture_lock_order)
    try:
        recovered = _recover_stale_generation_jobs(factory)
    finally:
        event.remove(engine, "before_cursor_execute", capture_lock_order)

    assert recovered == [work.job_id]
    assert locked_entities == [WeddingProject, GenerationJob]
    with factory() as db:
        job = db.get(GenerationJob, work.job_id)
        assert job is not None and job.status == WeddingJobStatus.QUEUED
        assert job.started_at is not None
        assert job.started_at.isoformat() != old_claim.lease_token


def test_stale_processing_job_recovers_and_resumes_only_unfinished_shots(client, monkeypatch):
    work = _project_with_job(client)
    factory = get_session_factory()
    storage = WeddingPrivateStorage(get_settings())
    with factory() as db:
        first_shot = db.get(StoryboardShot, work.shot_id)
        job = db.get(GenerationJob, work.job_id)
        assert first_shot is not None and job is not None
        second_shot = StoryboardShot(
            project_id=work.project_id,
            story_version_id=work.story_id,
            project_version=1,
            shot_index=1,
            shot_type="person_ai",
            duration_ms=5_000,
            person_keys=["A"],
            fact_ids=[],
            description="Second approved shot",
            subtitle="Second subtitle",
            frame_asset_id=work.frame_asset_id,
            status=StoryboardShotStatus.FRAME_APPROVED,
        )
        db.add(second_shot)
        db.flush()
        second_shot_id = second_shot.id
        output_key = storage.put(work.project_id, b"persisted-first-candidate", ".mp4", "video/mp4")
        output_asset = WeddingAsset(
            project_id=work.project_id,
            asset_type=WeddingAssetType.VIDEO_CANDIDATE,
            object_key=output_key,
            sha256="c" * 64,
            mime="video/mp4",
            size_bytes=len(b"persisted-first-candidate"),
            generated=True,
            provider="test-provider",
        )
        db.add(output_asset)
        db.flush()
        db.add(
            ShotCandidate(
                project_id=work.project_id,
                shot_id=first_shot.id,
                generation_job_id=job.id,
                asset_id=output_asset.id,
                provider="test-provider",
                model_version="test-model-v1",
                review_result=CandidateReviewStatus.PENDING,
                actual_cost_fen=31,
            )
        )
        set_job_status(job, WeddingJobStatus.PROCESSING)
        job.target_shot_id = None
        job.total_shots = 2
        job.completed_shots = 1
        job.actual_cost_fen = 31
        job.provider_name = "test-provider"
        job.model_version = "test-model-v1"
        job.progress_stage = "candidate_review"
        job.started_at = utc_now() - timedelta(hours=1)
        job.updated_at = utc_now() - timedelta(hours=1)
        first_shot.status = StoryboardShotStatus.CANDIDATE_REVIEW
        db.commit()
        assert db.execute(
            select(ShotCandidate).where(
                ShotCandidate.generation_job_id == work.job_id,
                ShotCandidate.shot_id == work.shot_id,
            )
        ).scalars().first() is not None

    monkeypatch.setenv("WEDDING_WORKER_STALE_SECONDS", "120")
    get_settings.cache_clear()
    claim = _claim(factory, work.job_id)
    with factory() as db:
        recovered = db.get(GenerationJob, work.job_id)
        assert recovered is not None
        assert recovered.status == WeddingJobStatus.PROCESSING
        assert recovered.progress_stage == "recovered"
        assert recovered.actual_cost_fen == 31 and recovered.reserved_amount_fen == 1_000
        resumed_candidate = db.execute(
            select(ShotCandidate).where(
                ShotCandidate.generation_job_id == work.job_id,
                ShotCandidate.shot_id == work.shot_id,
            )
        ).scalars().first()
        assert resumed_candidate is not None
        assert recovered.target_shot_id is None

    provider = SuccessfulVideoProvider(cost_fen=19)
    result = process_claimed_generation_job(
        factory, claim, ProviderBundle(character_video=provider), storage
    )
    assert result.status == "completed"
    assert provider.calls == 1
    assert provider.idempotency_keys == [f"{work.job_id}:{second_shot_id}"], (
        f"work shot={work.shot_id}, second shot={second_shot_id}, keys={provider.idempotency_keys}"
    )
    with factory() as db:
        job = db.get(GenerationJob, work.job_id)
        ledger = db.execute(select(BudgetLedger).where(BudgetLedger.generation_job_id == work.job_id)).scalar_one()
        candidates = list(db.execute(select(ShotCandidate).where(ShotCandidate.generation_job_id == work.job_id)).scalars())
        assert job is not None and job.completed_shots == 2 and job.actual_cost_fen == 50
        assert ledger.status == BudgetLedgerStatus.SETTLED and ledger.actual_fen == 50
        assert ledger.released_fen == 950 and ledger.reserved_fen == 1_000
        assert len(candidates) == 2
        assert len({candidate.shot_id for candidate in candidates}) == 2


def test_provider_result_after_project_version_changes_is_discarded_but_cost_is_settled(client, tmp_path):
    work = _project_with_job(client)
    provider = SuccessfulVideoProvider(cost_fen=21, mutate_version=True)
    factory = get_session_factory()
    settings = Settings(_env_file=None, local_upload_dir=str(tmp_path / "uploads"))
    claim = _claim(factory, work.job_id)
    result = process_claimed_generation_job(
        factory, claim, ProviderBundle(character_video=provider), WeddingPrivateStorage(settings)
    )
    assert result.status == "failed"
    assert result.error_code == "PROJECT_VERSION_STALE"
    with factory() as db:
        job = db.get(GenerationJob, work.job_id)
        ledger = db.execute(select(BudgetLedger).where(BudgetLedger.generation_job_id == work.job_id)).scalar_one()
        assert db.execute(select(ShotCandidate).where(ShotCandidate.generation_job_id == work.job_id)).first() is None
        assert db.execute(select(WeddingAsset).where(WeddingAsset.project_id == work.project_id, WeddingAsset.generated.is_(True))).first() is None
        assert job is not None and job.actual_cost_fen == 21
        assert ledger.status == BudgetLedgerStatus.SETTLED and ledger.actual_fen == 21


def test_render_worker_reports_missing_ffmpeg_without_ready_asset(client, monkeypatch):
    work = _project_with_job(client, with_render=True)
    factory = get_session_factory()
    assert claim_next_render_version(factory) == work.render_id
    monkeypatch.setattr("app.wedding_media.shutil.which", lambda _: None)
    renderer = FFmpegRenderer(Settings(_env_file=None, wedding_music_file="", wedding_music_license_id=""), ffmpeg_path=None)
    result = process_claimed_render_version(factory, work.render_id, WeddingPrivateStorage(Settings(_env_file=None)), renderer)
    assert result.status == "failed"
    assert result.error_code == "FFMPEG_UNAVAILABLE"
    with factory() as db:
        render = db.get(RenderVersion, work.render_id)
        audit = db.execute(
            select(WeddingAuditEvent).where(
                WeddingAuditEvent.target_id == work.render_id,
                WeddingAuditEvent.action == "render.failed",
            )
        ).scalar_one()
        assert render is not None and render.status == RenderVersionStatus.FAILED
        assert render.preview_asset_id is None and render.export_asset_id is None
        assert audit.metadata_json == {"error_code": "FFMPEG_UNAVAILABLE"}


def test_render_worker_persists_missing_music_license_blocker(client):
    work = _project_with_job(client, with_render=True)
    factory = get_session_factory()
    assert claim_next_render_version(factory) == work.render_id
    renderer = FFmpegRenderer(
        Settings(_env_file=None, wedding_music_file="", wedding_music_license_id=""),
        ffmpeg_path="ffmpeg",
    )
    result = process_claimed_render_version(
        factory, work.render_id, WeddingPrivateStorage(get_settings()), renderer
    )
    assert result.error_code == "MUSIC_LICENSE_REQUIRED"
    with factory() as db:
        render = db.get(RenderVersion, work.render_id)
        assert render is not None and render.status == RenderVersionStatus.FAILED
        assert render.preview_asset_id is None and render.export_asset_id is None
        audit = db.execute(
            select(WeddingAuditEvent).where(
                WeddingAuditEvent.target_id == work.render_id,
                WeddingAuditEvent.action == "render.failed",
            )
        ).scalar_one()
        assert audit.metadata_json == {"error_code": "MUSIC_LICENSE_REQUIRED"}


def test_renderer_blocks_when_music_license_is_missing_even_if_ffmpeg_exists(tmp_path):
    renderer = FFmpegRenderer(
        Settings(_env_file=None, wedding_music_file="", wedding_music_license_id=""),
        ffmpeg_path="ffmpeg",
    )
    with pytest.raises(ProviderUnavailable, match="MUSIC_LICENSE_REQUIRED"):
        renderer.validate_music_license(demo=False)


def test_local_demo_output_is_repeatable_and_visibly_labels_static_demo(client, tmp_path):
    settings = get_settings().model_copy(update={"wedding_media_mode": "demo"})
    bundle = build_provider_bundle(settings)
    work = _project_with_job(client)
    storage = WeddingPrivateStorage(settings)
    with get_session_factory()() as db:
        shot = db.get(StoryboardShot, work.shot_id)
        frame = db.get(WeddingAsset, work.frame_asset_id)
        assert shot is not None and frame is not None
        from app.wedding_worker import VideoGenerationRequest
        request = VideoGenerationRequest(
            project_id=work.project_id,
            job_id=work.job_id,
            shot_id=work.shot_id,
            shot_type=shot.shot_type,
            description=shot.description,
            subtitle=shot.subtitle,
            reference_image=storage.read(frame.object_key),
            reference_mime=frame.mime,
            idempotency_key=work.job_id + ":" + work.shot_id,
        )
    first = bundle.character_video.generate(request)
    second = bundle.character_video.generate(request)
    assert first.data == second.data
    assert first.provider == "local_demo" and first.actual_cost_fen == 0
    assert b"DEMO ONLY" in first.data and b"NO AI MOTION" in first.data


def test_local_demo_persists_provider_label_and_zero_cost_candidate(client):
    settings = get_settings().model_copy(update={"wedding_media_mode": "demo"})
    work = _project_with_job(client)
    factory = get_session_factory()
    claim = _claim(factory, work.job_id)
    result = process_claimed_generation_job(
        factory,
        claim,
        build_provider_bundle(settings),
        WeddingPrivateStorage(settings),
    )
    assert result.status == "completed"
    with factory() as db:
        candidate = db.execute(select(ShotCandidate).where(ShotCandidate.generation_job_id == work.job_id)).scalar_one()
        asset = db.get(WeddingAsset, candidate.asset_id)
        ledger = db.execute(select(BudgetLedger).where(BudgetLedger.generation_job_id == work.job_id)).scalar_one()
        assert asset is not None and asset.provider == "local_demo" and asset.mime == "image/png"
        assert WeddingPrivateStorage(settings).read(asset.object_key).find(b"DEMO ONLY") >= 0
        assert candidate.provider == "local_demo" and candidate.actual_cost_fen == 0
        assert ledger.status == BudgetLedgerStatus.SETTLED and ledger.actual_fen == 0


def test_worker_initializes_database_before_polling(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(sys, "argv", ["wedding_worker", "--once"])
    monkeypatch.setattr(wedding_worker, "init_db", lambda: calls.append("init"))
    monkeypatch.setattr(wedding_worker, "run_worker_once", lambda: calls.append("poll") or [])

    wedding_worker.main()

    assert calls == ["init", "poll"]
