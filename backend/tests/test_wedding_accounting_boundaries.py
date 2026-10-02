"""Operator/provider interleavings and legacy accounting identity boundaries."""
from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.database import get_session_factory
from app.models import (BudgetLedger, GenerationJob, ShotCandidate, StoryboardShot,
    WeddingAccountUsage, WeddingAsset, WeddingBillingReceipt, WeddingProject, utc_now)
from app.wedding_accounting import reconcile_rows
from app.wedding_media import build_provider_bundle
from app.wedding_video_operations import prepare_receipt
from tests.test_wedding_final_guards import ready, enqueue_video


@pytest.mark.parametrize('terminal,cost', [('succeeded', 25), ('not_submitted', 0)])
def test_authoritative_partial_import_wins_over_inflight_cancel_get(client, monkeypatch, terminal, cost):
    from app.wedding_reconciliation import reconcile_cancelled
    base, actors, _, board, _ = ready(client, monkeypatch)
    response = enqueue_video(client, base, actors['A'], 'cancel-authority-interleave').json()
    shots = [s for s in board['shots'] if s['shot_type'] == 'scene_ai'][:2]
    with get_session_factory()() as db:
        project = db.get(WeddingProject, response['project_id']); job = db.get(GenerationJob, response['id'])
        for i, shot in enumerate(shots):
            receipt = prepare_receipt(db, project, job, db.get(StoryboardShot, shot['id']))
            receipt.state = 'running'; receipt.task_id = f'accepted-{i}'
        job.status = 'processing'; db.commit()
    assert client.post(base + f"/generation-jobs/{response['id']}/cancel", headers=actors['A']).status_code == 200
    with get_session_factory()() as db:
        db.get(GenerationJob, response['id']).updated_at = utc_now() - timedelta(days=1); db.commit()
    row = dict(operation_key=response['id'] + ':' + shots[0]['id'], terminal_state=terminal,
               cost_fen=cost, evidence_id='authoritative-synthetic-invoice')
    calls = []
    class Provider:
        def generate(self, request):
            assert request.reconcile_only and request.existing_task_id
            calls.append(request.existing_task_id)
            if request.existing_task_id == 'accepted-0':
                assert reconcile_rows(get_session_factory(), [row]) == [{'status': 'applied'}]
            return replace(build_provider_bundle().scene_video.generate(request),
                task_id=request.existing_task_id, actual_cost_fen=100 if request.existing_task_id == 'accepted-0' else 50)
    assert reconcile_cancelled(get_session_factory(), replace(build_provider_bundle(), scene_video=Provider())).status == 'cancelled'
    assert calls == ['accepted-0', 'accepted-1']
    for _ in range(2):
        assert reconcile_rows(get_session_factory(), [row]) == [{'status': 'duplicate'}]
    with get_session_factory()() as db:
        project = db.get(WeddingProject, response['project_id']); account = db.get(WeddingAccountUsage, project.owner_id)
        ledger = db.scalar(select(BudgetLedger).where(BudgetLedger.generation_job_id == response['id']))
        assert project.budget_spent_fen == account.spent_fen == ledger.actual_fen == cost + 50
        assert project.budget_reserved_fen == account.reserved_fen == 0
        receipt = db.scalar(select(WeddingBillingReceipt).where(WeddingBillingReceipt.operation_key == row['operation_key']))
        assert receipt.state == terminal and receipt.billed_fen == cost and receipt.estimated_fen == cost
        assert receipt.price_metadata['reconciliation_evidence'] == row['evidence_id']
        assert db.get(StoryboardShot, shots[0]['id']).generation_attempts == (0 if terminal == 'not_submitted' else 1)
        assert db.get(StoryboardShot, shots[1]['id']).generation_attempts == 1


def test_known_unsettled_overrun_holds_same_owner_public_paid_admission(client, monkeypatch):
    first, actors, _, board, _ = ready(client, monkeypatch)
    second, other_actors, _, other_board, _ = ready(client, monkeypatch)
    shot = next(s for s in board['shots'] if s['shot_type'] == 'scene_ai')
    job = enqueue_video(client, first, actors['A'], 'overrun-original', shot).json()
    with get_session_factory()() as db:
        project = db.get(WeddingProject, job['project_id']); current = db.get(GenerationJob, job['id'])
        assert db.get(WeddingProject, second.rsplit('/', 1)[1]).owner_id == project.owner_id
        receipt = prepare_receipt(db, project, current, db.get(StoryboardShot, shot['id']))
        receipt.state = 'unknown'; current.status = 'needs_action'; project.status = 'ready_to_generate'; db.commit()
    row = dict(operation_key=job['id'] + ':' + shot['id'], terminal_state='succeeded', cost_fen=16000, evidence_id='known-over-cap')
    assert reconcile_rows(get_session_factory(), [row, row]) == [{'status': 'applied'}, {'status': 'duplicate'}]
    for base, headers, shots in [(first, actors['A'], board['shots']), (second, other_actors['A'], other_board['shots'])]:
        retry = enqueue_video(client, base, headers, 'fresh-after-known-overrun', next(s for s in shots if s['shot_type'] == 'scene_ai'))
        assert retry.status_code == 409, retry.text
        assert retry.json()['detail'] in ('PROVIDER_RECONCILIATION_REQUIRED', 'ACCOUNT_RECONCILIATION_REQUIRED')
    # Story/frame admission uses the same owner reserve boundary.
    from app.models import StoryVersion
    with get_session_factory()() as db:
        story = db.get(StoryVersion, other_board['id']); story.owner_approved_at = None; story.status = 'draft'
        db.commit()
    from tests.test_wedding_creation import submit
    creation = submit(client, second.rsplit('/', 1)[1], other_actors['A'], key='story-after-overrun')
    assert creation.status_code == 409 and creation.json()['detail'] == 'ACCOUNT_RECONCILIATION_REQUIRED'
    with get_session_factory()() as db:
        receipt = db.scalar(select(WeddingBillingReceipt).where(WeddingBillingReceipt.operation_key == row['operation_key']))
        assert receipt.billed_fen == 16000 and not receipt.settled
        assert db.get(GenerationJob, job['id']).error_code == 'ACTUAL_COST_EXCEEDS_RESERVATION'
        assert db.get(WeddingProject, job['project_id']).budget_reserved_fen == job['reserved_amount_fen']
    assert client.delete(first, headers=actors['A']).status_code == 204
    after_delete = submit(client, second.rsplit('/', 1)[1], other_actors['A'], key='story-after-overrun-project-deleted')
    assert after_delete.status_code == 409 and after_delete.json()['detail'] == 'ACCOUNT_RECONCILIATION_REQUIRED'


@pytest.mark.parametrize('targeted', [False, True])
def test_legacy_equal_cardinality_does_not_return_another_shots_paid_attempt(client, monkeypatch, targeted):
    from app.models import StoryVersion
    base, actors, _, board, _ = ready(client, monkeypatch)
    scenes = [s for s in board['shots'] if s['shot_type'] == 'scene_ai'][:2]
    result = enqueue_video(client, base, actors['A'], 'legacy-ambiguous-original', scenes[0]).json()
    with get_session_factory()() as db:
        project = db.get(WeddingProject, result['project_id']); job = db.get(GenerationJob, result['id'])
        original = db.get(StoryboardShot, scenes[0]['id']); unrelated = db.get(StoryboardShot, scenes[1]['id'])
        receipt = prepare_receipt(db, project, job, original); receipt.state = 'unknown'
        envelope = db.scalar(select(WeddingBillingReceipt).where(WeddingBillingReceipt.operation_key == 'reservation:' + job.id))
        envelope.provenance = {'price_version': 'legacy'}
        job.target_shot_id = original.id if targeted else None
        job.total_shots = 1; job.status = 'needs_action'; original.generation_attempts = 2; unrelated.generation_attempts = 3
        descendant_story = StoryVersion(project_id=project.id, version=2, story_text='Historical unchanged revision', style=project.style)
        db.add(descendant_story); db.flush()
        descendant = StoryboardShot(project_id=project.id, story_version_id=descendant_story.id,
            project_version=2, shot_index=original.shot_index, shot_type=original.shot_type,
            description=original.description, person_keys=[], duration_ms=5000, source_shot_id=original.id,
            frame_asset_id=original.frame_asset_id, generation_attempts=2)
        db.add(descendant); db.flush(); descendant_id = descendant.id
        other_job = GenerationJob(project_id=project.id, story_version_id=job.story_version_id, quote_id=job.quote_id,
            project_version=1, idempotency_key='legacy-historical-other', request_fingerprint='historical', status='completed')
        db.add(other_job); db.flush()
        # Historical concurrent work supplied every current shot except B. Today's
        # pending set has the same size as the old reserved set, but another ID.
        for shot in db.scalars(select(StoryboardShot).where(StoryboardShot.story_version_id == job.story_version_id)):
            if shot.shot_type == 'photo':
                continue
            asset = WeddingAsset(project_id=project.id, object_key='synthetic/' + shot.id, sha256='0'*64,
                mime='image/png', size_bytes=1, asset_type='video_candidate', generated=True,
                expires_at=utc_now() - timedelta(days=1) if shot.id == unrelated.id else utc_now() + timedelta(days=1))
            db.add(asset); db.flush()
            db.add(ShotCandidate(project_id=project.id, shot_id=shot.id, generation_job_id=other_job.id,
                asset_id=asset.id, frame_asset_id=shot.frame_asset_id, provider='local_demo', model_version='local-demo-v1'))
        db.commit()
    row = dict(operation_key=result['id'] + ':' + scenes[0]['id'], terminal_state='not_submitted', cost_fen=0, evidence_id='legacy-provider-proof')
    assert reconcile_rows(get_session_factory(), [row]) == [{'status': 'applied'}]
    with get_session_factory()() as db:
        assert db.get(StoryboardShot, scenes[1]['id']).generation_attempts == 3
        assert db.get(StoryboardShot, scenes[0]['id']).generation_attempts == (1 if targeted else 2)
        assert db.get(StoryboardShot, descendant_id).generation_attempts == (1 if targeted else 2)
        envelope = db.scalar(select(WeddingBillingReceipt).where(WeddingBillingReceipt.operation_key == 'reservation:' + result['id']))
        assert scenes[1]['id'] not in envelope.provenance.get('returned_attempt_shot_ids', [])


@pytest.mark.parametrize('kind', ['video', 'story'])
def test_known_overrun_fences_previously_queued_provider_dispatch(client, monkeypatch, kind):
    from app.models import StoryVersion
    from app.wedding_worker import process_next_generation_job
    from app.wedding_creation import process_next_creation_job
    from tests.test_wedding_creation import submit
    first, actors, _, board, _ = ready(client, monkeypatch)
    second, other_actors, _, other_board, _ = ready(client, monkeypatch)
    shot = next(s for s in board['shots'] if s['shot_type'] == 'scene_ai')
    job = enqueue_video(client, first, actors['A'], 'overrun-before-other-dispatch', shot).json()
    if kind == 'video':
        assert enqueue_video(client, second, other_actors['A'], 'queued-before-invoice',
            next(s for s in other_board['shots'] if s['shot_type'] == 'scene_ai')).status_code == 202
    else:
        with get_session_factory()() as db:
            story = db.get(StoryVersion, other_board['id']); story.owner_approved_at = None; story.status = 'draft'; db.commit()
        assert submit(client, second.rsplit('/', 1)[1], other_actors['A'], key='queued-story-before-invoice').status_code == 202
    with get_session_factory()() as db:
        project = db.get(WeddingProject, job['project_id']); current = db.get(GenerationJob, job['id'])
        receipt = prepare_receipt(db, project, current, db.get(StoryboardShot, shot['id']))
        receipt.state = 'unknown'; current.status = 'needs_action'; project.status = 'ready_to_generate'; db.commit()
    assert reconcile_rows(get_session_factory(), [dict(operation_key=job['id'] + ':' + shot['id'],
        terminal_state='succeeded', cost_fen=16000, evidence_id='invoice-before-dispatch')]) == [{'status': 'applied'}]
    calls = []
    class Spy:
        def generate(self, request):
            calls.append(request)
            return build_provider_bundle().scene_video.generate(request)
        def structure(self, request):
            calls.append(request)
            return build_provider_bundle().story.structure(request)
    providers = replace(build_provider_bundle(), scene_video=Spy(), story=Spy())
    result = process_next_generation_job(providers=providers) if kind == 'video' else process_next_creation_job(providers=providers)
    assert result.error_code == 'ACCOUNT_RECONCILIATION_REQUIRED'
    assert calls == []


def test_authoritative_evidence_can_close_known_terminal_unsettled_receipt(client, monkeypatch):
    base, actors, _, board, _ = ready(client, monkeypatch)
    shot = next(s for s in board['shots'] if s['shot_type'] == 'scene_ai')
    job = enqueue_video(client, base, actors['A'], 'terminal-awaiting-settlement', shot).json()
    with get_session_factory()() as db:
        project = db.get(WeddingProject, job['project_id']); current = db.get(GenerationJob, job['id'])
        receipt = prepare_receipt(db, project, current, db.get(StoryboardShot, shot['id']))
        receipt.state = 'succeeded'; receipt.estimated_fen = 20; current.status = 'needs_action'; db.commit()
    row = dict(operation_key=job['id'] + ':' + shot['id'], terminal_state='succeeded', cost_fen=25, evidence_id='settlement-proof')
    assert reconcile_rows(get_session_factory(), [row, row]) == [{'status': 'applied'}, {'status': 'duplicate'}]
    assert enqueue_video(client, base, actors['A'], 'after-terminal-settlement', shot).status_code == 202
