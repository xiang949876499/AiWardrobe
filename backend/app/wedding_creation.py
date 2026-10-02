"""Durable story/frame jobs; API enqueues only, workers perform provider calls."""
from __future__ import annotations

from datetime import timedelta
from decimal import ROUND_CEILING
import hashlib
import json
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update, func

from app.config import get_settings
from app.database import get_db, get_session_factory
from app.models import (WeddingProject, WeddingCreationJob, WeddingBillingReceipt, StoryVersion,
    StoryFact, StoryboardShot, WeddingAsset, WeddingAssetType, utc_now)
from app.wedding_accounting import reserve, settle, lock_account
from app.wedding_retention import retention_deadline
from app.wedding_media import build_provider_bundle, StoryStructureRequest, FrameGenerationRequest, ProviderUnavailable
from app.wedding_references import selected_references, consent_provenance
from app.wedding_service import (get_wedding_actor, require_project_owner, WeddingPrivateStorage,
                                 _as_utc, validate_active_consents)

router = APIRouter(tags=['wedding creation'])


class StoryJobRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: int = Field(ge=1)
    story_text: str = Field(min_length=1, max_length=12000)


class PhotoAssignment(BaseModel):
    shot_id: str
    asset_id: str


class FrameJobRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: int = Field(ge=1)
    photos: list[PhotoAssignment] = Field(default_factory=list, max_length=6)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def provider_configuration(kind):
    cfg = get_settings()
    return {key: str(value) for key, value in {
        'mode': cfg.wedding_media_mode, 'provider': getattr(cfg, f'wedding_{kind}_provider'),
        'model': getattr(cfg, f'wedding_{kind}_model'), 'region_endpoint': cfg.wedding_ark_base_url,
        'price_version': cfg.wedding_price_version, 'frame_rate': cfg.wedding_frame_fen_per_image,
        'story_input_rate': cfg.wedding_story_input_fen_per_million_tokens,
        'story_output_rate': cfg.wedding_story_output_fen_per_million_tokens}.items()}


def current_story(db, project):
    return db.scalar(select(StoryVersion).where(StoryVersion.project_id == project.id, StoryVersion.version == project.current_version))


def draft_fingerprint(db, project):
    story = current_story(db, project)
    if story is None:
        return digest(None)
    return digest({'id': story.id, 'text': story.story_text, 'style': story.style, 'status': str(story.status),
        'approved': str(story.owner_approved_at),
        'facts': sorted([(f.id, f.text, f.source_field, str(f.confirmed_at)) for f in story.facts]),
        'shots': [dict(id=s.id, index=s.shot_index, kind=s.shot_type, duration=s.duration_ms,
                    people=s.person_keys, facts=s.fact_ids, description=s.description, subtitle=s.subtitle,
                    creative=s.creative_scene) for s in sorted(story.shots, key=lambda s:s.shot_index)]})


def frame_fingerprint(db, project):
    story = current_story(db, project)
    return digest({'approved_version': project.frame_approved_version,
        'shots': [(s.id, s.frame_asset_id, str(s.status)) for s in sorted(story.shots, key=lambda s:s.shot_index)]})


def require_frame_build_stage(db, project):
    from app.models import GenerationJob, RenderVersion
    if project.frame_approved_version == project.current_version or project.status != 'frame_review':
        raise HTTPException(409, 'FRAME_REBUILD_REQUIRES_RESET')
    if db.scalar(select(GenerationJob.id).where(GenerationJob.project_id == project.id,
            GenerationJob.project_version == project.current_version,
            GenerationJob.status.in_(['queued', 'processing', 'cancel_requested']))) or db.scalar(
            select(RenderVersion.id).where(RenderVersion.project_id == project.id,
                RenderVersion.project_version == project.current_version,
                RenderVersion.status.in_(['queued', 'processing']))):
        raise HTTPException(409, 'FRAME_REBUILD_DOWNSTREAM_ACTIVE')


def response(job):
    return dict(id=job.id, project_id=job.project_id, kind=job.kind, version=job.project_version,
                status=job.status, error_code=job.error_code, completed_operations=job.completed_operations,
                total_operations=job.total_operations, demo=job.demo, result_story_id=job.result_story_id,
                created_at=job.created_at, updated_at=job.updated_at)


def locked_owner(db, actor, project_id):
    require_project_owner(db, actor, project_id)
    return db.scalar(select(WeddingProject).where(WeddingProject.id == project_id).with_for_update().execution_options(populate_existing=True))


from app.wedding_retention import check_storage_capacity


def enqueue(db, actor, project_id, body, key, kind):
    if not key or len(key) > 128 or not key.strip():
        raise HTTPException(422, 'IDEMPOTENCY_KEY_REQUIRED')
    project = locked_owner(db, actor, project_id)
    fingerprint = digest({'kind': kind, **body.model_dump()})
    existing = db.scalar(select(WeddingCreationJob).where(WeddingCreationJob.project_id == project.id, WeddingCreationJob.idempotency_key == key))
    if existing:
        if existing.request_fingerprint != fingerprint:
            raise HTTPException(409, 'IDEMPOTENCY_KEY_REUSED')
        return existing
    if project.current_version != body.version:
        raise HTTPException(409, 'PROJECT_VERSION_STALE')
    if project.status in ('cancelled', 'blocked', 'delivered'):
        raise HTTPException(409, 'PROJECT_NOT_CREATABLE')
    if db.scalar(select(WeddingCreationJob.id).where(WeddingCreationJob.project_id == project.id, WeddingCreationJob.status.in_(['queued','processing']))):
        raise HTTPException(409, 'CREATION_ALREADY_ACTIVE')
    if db.scalar(select(WeddingBillingReceipt.id).where(WeddingBillingReceipt.project_id == project.id,
            WeddingBillingReceipt.settled.is_(False), WeddingBillingReceipt.state.in_(['dispatching','unknown','running']))):
        raise HTTPException(409, 'CREATION_OUTCOME_UNRESOLVED')
    config = get_settings()
    demo = config.wedding_media_mode == 'demo'
    from app.wedding_providers import provider_capabilities
    capability = provider_capabilities(config)[kind if kind == 'story' else 'frame']
    if not demo and not capability['available']:
        raise HTTPException(409, 'PROVIDER_UNAVAILABLE')
    provider = 'local_demo' if demo else capability['provider']
    model = 'local-demo-v1' if demo else capability['model']
    consent = consent_provenance(db, project, provider)
    story = current_story(db, project)
    snapshot = {'provider': provider, 'model': model, 'consent': consent, 'style': project.style,
                'configuration': provider_configuration(kind)}
    if kind == 'story':
        if story and story.owner_approved_at:
            raise HTTPException(409, 'APPROVED_STORYBOARD_IMMUTABLE')
        snapshot['story_text'] = body.story_text
        snapshot['roles'] = [(p.person_key, p.display_name) for p in validate_active_consents(db, project)]
        operations = [('story', 0 if demo else config.wedding_story_max_cost_fen, {})]
    else:
        if story is None or story.status != 'approved' or not story.owner_approved_at:
            raise HTTPException(409, 'STORYBOARD_APPROVAL_REQUIRED')
        require_frame_build_stage(db, project)
        snapshot['frame_fingerprint'] = frame_fingerprint(db, project)
        if len(story.shots) != 18 or sum(s.shot_type == 'person_ai' for s in story.shots) != 8 or sum(s.shot_type == 'scene_ai' for s in story.shots) != 4 or any(s.duration_ms != 5000 for s in story.shots):
            raise HTTPException(409, 'STORYBOARD_TEMPLATE_INVALID')
        if any((s.shot_type == 'scene_ai' and s.person_keys) or
               (s.shot_type == 'person_ai' and (not s.person_keys or len(set(s.person_keys)) != len(s.person_keys)))
               for s in story.shots):
            raise HTTPException(409, 'STORYBOARD_SCOPE_INVALID')
        from app.wedding_authoring import require_photo_readiness
        require_photo_readiness(db, project)
        pending = [s for s in story.shots if s.shot_type != 'photo' and not s.frame_asset_id]
        if not pending:
            raise HTTPException(409, 'NO_FRAMES_REQUIRE_GENERATION')
        check_storage_capacity(db, project.id, len(pending) * config.wedding_max_image_bytes)
        photos = {p.shot_id: p.asset_id for p in body.photos}
        if len(photos) != len(body.photos) or set(photos) != {s.id for s in story.shots if s.shot_type == 'photo'}:
            raise HTTPException(422, 'PHOTO_ASSIGNMENTS_REQUIRED')
        for shot in story.shots:
            if shot.shot_type == 'photo':
                asset = db.get(WeddingAsset, photos[shot.id])
                if asset is None or asset.project_id != project.id or asset.asset_type != WeddingAssetType.PHOTO or asset.generated or (asset.expires_at and _as_utc(asset.expires_at) <= utc_now()) or (shot.person_keys and asset.person_key not in shot.person_keys):
                    raise HTTPException(422, 'PHOTO_ASSET_INVALID')
        snapshot.update(story_id=story.id, photos=photos, shots=[dict(id=s.id, description=s.description,
            person_keys=s.person_keys, references=selected_references(db, project, s.person_keys, demo=demo))
            for s in sorted(story.shots, key=lambda s:s.shot_index) if s in pending])
        price = 0 if demo else int(config.wedding_frame_fen_per_image.to_integral_value(rounding=ROUND_CEILING))
        operations = [(s['id'], price, {'references': [{k:v for k,v in r.items() if k != 'object_key'} for r in s['references']]}) for s in snapshot['shots']]
    job = WeddingCreationJob(project_id=project.id, kind=kind, project_version=body.version,
        idempotency_key=key, request_fingerprint=fingerprint, draft_fingerprint=draft_fingerprint(db, project),
        snapshot=snapshot, demo=demo, total_operations=len(operations))
    db.add(job)
    db.flush()
    reserve(db, project, sum(amount for _, amount, _ in operations))
    for operation, amount, provenance in operations:
        db.add(WeddingBillingReceipt(operation_key=f'{job.id}:{operation}', owner_id=project.owner_id,
            project_id=project.id, job_id=job.id, provider=provider, model=model, reserved_fen=amount,
            provenance={**provenance, 'input_hash': fingerprint, 'consent_hash': digest(consent),
                        'price_version': config.wedding_price_version, 'draft_hash': job.draft_fingerprint,
                        'consent': consent, 'region_endpoint': config.wedding_ark_base_url}))
    # Only a newly accepted owner request renews retention. Replay returns above;
    # provider outcome and status polling do not manufacture further activity.
    from app.wedding_retention import touch_activity
    touch_activity(db, project)
    db.commit()
    return job


@router.post('/wedding-projects/{project_id}/story-jobs', status_code=202)
def create_story_job(project_id: str, body: StoryJobRequest, idempotency_key: str | None = Header(default=None),
                     actor=Depends(get_wedding_actor), db=Depends(get_db)):
    return response(enqueue(db, actor, project_id, body, idempotency_key, 'story'))


@router.post('/wedding-projects/{project_id}/frame-jobs', status_code=202)
def create_frame_job(project_id: str, body: FrameJobRequest, idempotency_key: str | None = Header(default=None),
                     actor=Depends(get_wedding_actor), db=Depends(get_db)):
    return response(enqueue(db, actor, project_id, body, idempotency_key, 'frame'))


@router.get('/wedding-projects/{project_id}/creation-jobs')
def list_creation_jobs(project_id: str, actor=Depends(get_wedding_actor), db=Depends(get_db)):
    require_project_owner(db, actor, project_id)
    return [response(j) for j in db.scalars(select(WeddingCreationJob).where(WeddingCreationJob.project_id == project_id).order_by(WeddingCreationJob.created_at.desc()).limit(100))]


def lock_job(db, job_id):
    project_id = db.scalar(select(WeddingCreationJob.project_id).where(WeddingCreationJob.id == job_id))
    project = db.scalar(select(WeddingProject).where(WeddingProject.id == project_id).with_for_update()) if project_id else None
    job = db.scalar(select(WeddingCreationJob).where(WeddingCreationJob.id == job_id).with_for_update())
    return project, job


def receipts(db, job):
    return list(db.scalars(select(WeddingBillingReceipt).where(WeddingBillingReceipt.job_id == job.id).order_by(WeddingBillingReceipt.operation_key)))


def release_unstarted(db, project, job):
    for receipt in receipts(db, job):
        if receipt.state == 'reserved' and not receipt.settled:
            settle(db, project, receipt.reserved_fen, 0)
            receipt.state = 'not_submitted'
            receipt.settled = True


def fence(db, project, job):
    if provider_configuration(job.kind) != job.snapshot['configuration']:
        raise HTTPException(409, 'PROVIDER_CONFIGURATION_CHANGED')
    if project is None or project.status in ('cancelled', 'blocked') or job.status == 'cancelled':
        raise HTTPException(409, 'CREATION_CANCELLED')
    if project.current_version != job.project_version:
        raise HTTPException(409, 'PROJECT_VERSION_STALE')
    consent = consent_provenance(db, project, job.snapshot['provider'])
    if digest(consent) != digest(job.snapshot['consent']):
        raise HTTPException(409, 'CONSENT_CHANGED')
    if draft_fingerprint(db, project) != job.draft_fingerprint:
        raise HTTPException(409, 'DRAFT_CHANGED')
    if job.kind == 'frame':
        from app.wedding_authoring import require_photo_readiness
        require_photo_readiness(db, project)
        if frame_fingerprint(db, project) != job.snapshot.get('frame_fingerprint'):
            raise HTTPException(409, 'FRAMES_CHANGED')
        require_frame_build_stage(db, project)
        for shot_id, asset_id in job.snapshot['photos'].items():
            photo = db.get(WeddingAsset, asset_id)
            if photo is None or photo.project_id != project.id or (photo.expires_at and _as_utc(photo.expires_at) <= utc_now()):
                raise HTTPException(409, 'PHOTO_ASSET_INVALID')
        for shot in job.snapshot['shots']:
            if selected_references(db, project, shot['person_keys'], demo=job.demo) != shot['references']:
                raise HTTPException(409, 'PRIMARY_REFERENCE_CHANGED')


def claim_creation_job(factory):
    stale = utc_now() - timedelta(seconds=get_settings().wedding_worker_stale_seconds)
    with factory() as db:
        ids = list(db.scalars(select(WeddingCreationJob.id).where(WeddingCreationJob.status.in_(['queued','processing'])).order_by(WeddingCreationJob.created_at)))
    for job_id in ids:
        with factory() as db:
            project, job = lock_job(db, job_id)
            if not project or not job:
                continue
            if job.status == 'processing':
                if _as_utc(job.updated_at) >= stale:
                    continue
                # Sync APIs have no queryable task ID: never repeat ambiguous POSTs.
                if any(r.state in ('dispatching','unknown') for r in receipts(db, job)):
                    job.status, job.error_code = 'needs_action', 'PROVIDER_OUTCOME_UNKNOWN'
                    release_unstarted(db, project, job)
                    db.commit()
                    continue
                job.status = 'queued'
                db.flush()
            if job.status != 'queued':
                continue
            token = str(uuid4())
            result = db.execute(update(WeddingCreationJob).where(WeddingCreationJob.id == job.id, WeddingCreationJob.status == 'queued').values(status='processing', lease_token=token, updated_at=utc_now()).execution_options(synchronize_session=False))
            if result.rowcount != 1:
                db.rollback()
                continue
            db.commit()
            return job_id, token
    return None


def validate_story(output, source):
    from app.wedding_providers import _Story, SHOT_TYPES
    raw = json.loads(output.data)
    if any(not {'source','source_quote','confirmed'} <= set(f) for f in raw.get('facts', [])):
        raise ValueError('Missing fact provenance')
    story = _Story.model_validate(raw, strict=True)
    if tuple(s.shot_type for s in story.shots) != SHOT_TYPES or [s.shot_index for s in story.shots] != list(range(1,19)):
        raise ValueError('Invalid template')
    for fact in story.facts:
        if fact.source != 'story_quote' or fact.text != fact.source_quote or not fact.source_quote or fact.source_quote not in source:
            raise ValueError('Invalid source provenance')
    for shot in story.shots:
        expected = ['A'] if shot.shot_index == 5 else ['B'] if shot.shot_index == 6 else ['A','B']
        if shot.duration_ms != 5000 or any(type(i) is not int or i < 0 or i >= len(story.facts) for i in shot.fact_indexes) or len(set(shot.person_keys)) != len(shot.person_keys):
            raise ValueError('Invalid shot')
        if shot.shot_type == 'person_ai' and shot.person_keys != expected or shot.shot_type == 'scene_ai' and shot.person_keys:
            raise ValueError('Invalid scope')
    return story


def apply_story(db, project, job, data):
    story = current_story(db, project)
    if story:
        story.shots.clear()
        story.facts.clear()
        db.flush()
    else:
        story = StoryVersion(project_id=project.id, version=project.current_version, story_text=data.story_text, style=project.style)
        db.add(story)
        db.flush()
    story.story_text = data.story_text
    story.facts = [StoryFact(text=f.text, source_field=f.source) for f in data.facts]
    db.flush()
    story.shots = [StoryboardShot(project_id=project.id, project_version=project.current_version,
        shot_index=s.shot_index, shot_type=s.shot_type, duration_ms=s.duration_ms, person_keys=s.person_keys,
        fact_ids=[story.facts[i].id for i in s.fact_indexes], description=s.description,
        subtitle=s.subtitle, creative_scene=s.creative_scene) for s in data.shots]
    project.status = 'story_review'
    job.result_story_id = story.id


def settle_receipt(db, receipt, output, state):
    project = db.scalar(select(WeddingProject).where(WeddingProject.id == receipt.project_id).with_for_update())
    # Account serialization also protects tombstones after the parent is deleted.
    account = lock_account(db, receipt.owner_id)
    db.refresh(receipt, with_for_update=True)
    if receipt.settled or receipt.billed_fen is not None:
        return
    actual = max(0, int(output.actual_cost_fen))
    receipt.state = state
    receipt.estimated_fen = actual
    receipt.task_id = output.task_id or output.request_id or receipt.task_id
    receipt.request_id = output.request_id
    receipt.usage = output.usage
    receipt.price_metadata = {**output.price_metadata, 'price_version': output.price_version}
    if state in ('unknown','running'):
        return
    if project:
        settle(db, project, receipt.reserved_fen, actual)
    else:
        if account.reserved_fen < receipt.reserved_fen:
            raise ValueError("Accounting reservation inconsistent")
        account.reserved_fen -= receipt.reserved_fen
        account.spent_fen += actual
    receipt.settled = True


def process_next_creation_job(session_factory=None, providers=None, storage=None):
    from app.wedding_worker import WorkerResult
    factory = session_factory or get_session_factory()
    claim = claim_creation_job(factory)
    if not claim:
        return None
    job_id, token = claim
    bundle = providers or build_provider_bundle()
    storage = storage or WeddingPrivateStorage(get_settings())
    with factory() as db:
        job = db.get(WeddingCreationJob, job_id)
        operation_ids = [r.id for r in receipts(db, job)]
    for receipt_id in operation_ids:
        with factory() as db:
            project, job = lock_job(db, job_id)
            if not job or job.lease_token != token or job.status != 'processing':
                return WorkerResult(job_id, 'needs_action', 'WORKER_LEASE_STALE')
            receipt = db.get(WeddingBillingReceipt, receipt_id)
            if receipt.settled:
                continue
            try:
                fence(db, project, job)
                if job.kind == 'frame':
                    check_storage_capacity(db, project.id, get_settings().wedding_max_image_bytes)
            except HTTPException as exc:
                job.status, job.error_code = 'needs_action', str(exc.detail)
                release_unstarted(db, project, job)
                db.commit()
                return WorkerResult(job_id, job.status, job.error_code)
            snapshot, kind = job.snapshot, job.kind
            operation_key, receipt_project_id = receipt.operation_key, receipt.project_id
            operation = operation_key.split(':',1)[1]
            if kind == 'story':
                request = StoryStructureRequest(snapshot['story_text'], (), receipt.operation_key, tuple(tuple(r) for r in snapshot['roles']), snapshot['style'])
                provider = bundle.story
                refs = []
            else:
                shot = next(s for s in snapshot['shots'] if s['id'] == operation)
                refs = shot['references']
                provider = bundle.frame
            # Store ambiguous-dispatch marker BEFORE calling any remote provider.
            receipt.state = 'dispatching'
            job.updated_at = utc_now()
            db.commit()
        output = None
        state = 'succeeded'
        error = None
        try:
            if provider is None:
                raise ProviderUnavailable('PROVIDER_UNAVAILABLE', 'Provider unavailable')
            if kind == 'frame':
                request = FrameGenerationRequest(operation, shot['description'], tuple((r['person_key'], storage.read(r['object_key']), r['mime']) for r in refs), operation_key)
            output = provider.structure(request) if kind == 'story' else provider.generate(request)
        except ProviderUnavailable as exc:
            output, state, error = exc, exc.submission_state, exc.code
        except Exception:
            output = ProviderUnavailable('PROVIDER_ERROR', 'Provider outcome unknown', submission_state='unknown')
            state, error = 'unknown', 'PROVIDER_OUTCOME_UNKNOWN'
        data, object_key = None, None
        if not error:
            try:
                if output.provider != snapshot['provider'] or output.model_version != snapshot['model'] or type(output.actual_cost_fen) is not int or output.actual_cost_fen < 0:
                    raise ValueError('Missing output provenance')
                if kind == 'story':
                    data = validate_story(output, snapshot['story_text'])
                else:
                    from app.routers.wedding import _validate_image
                    mime, suffix, width, height = _validate_image(output.data, output.mime, 'storyboard_frame')
                    from app.wedding_cleanup import stage_output
                    object_key = stage_output(factory, storage, receipt_project_id, operation_key, output.data, suffix, mime)
            except HTTPException as exc:
                error = str(exc.detail)
            except Exception:
                error = 'PROVIDER_RESULT_INVALID'
        try:
            with factory() as db:
                project, job = lock_job(db, job_id)
                receipt = db.get(WeddingBillingReceipt, receipt_id)
                settle_receipt(db, receipt, output, state)
                if not job or job.lease_token != token or job.status != 'processing':
                    db.commit()
                    return WorkerResult(job_id, 'needs_action', 'WORKER_LEASE_STALE')
                try:
                    fence(db, project, job)
                    if kind == 'frame' and not error:
                        check_storage_capacity(db, project.id, 0)
                except HTTPException as exc:
                    error = str(exc.detail)
                if output.actual_cost_fen > receipt.reserved_fen:
                    error = 'ACTUAL_COST_EXCEEDS_RESERVATION'
                if kind == 'frame' and not error:
                    from app.wedding_cleanup import attach_stage
                    try:
                        attach_stage(db, object_key)
                    except HTTPException as exc:
                        error = str(exc.detail)
                if error:
                    job.status, job.error_code = 'needs_action', error
                    release_unstarted(db, project, job)
                    db.commit()
                    return WorkerResult(job_id, job.status, job.error_code)
                if kind == 'story':
                    apply_story(db, project, job, data)
                else:
                    asset = WeddingAsset(project_id=project.id, asset_type=WeddingAssetType.GENERATED_FRAME,
                        object_key=object_key, sha256=hashlib.sha256(output.data).hexdigest(), mime=mime,
                        size_bytes=len(output.data), width=width, height=height, generated=True,
                        provider=output.provider, expires_at=retention_deadline(project),
                        provenance={'shot_id': operation, 'person_keys': shot['person_keys'], 'project_version': job.project_version})
                    db.add(asset)
                    db.flush()
                    db.get(StoryboardShot, operation).frame_asset_id = asset.id
                    db.get(StoryboardShot, operation).status = 'frame_pending'
                    project.frame_approved_version = None
                job.completed_operations += 1
                job.updated_at = utc_now()
                if job.completed_operations == job.total_operations:
                    if kind == 'frame':
                        for shot_id, asset_id in snapshot['photos'].items():
                            db.get(StoryboardShot, shot_id).frame_asset_id = asset_id
                    job.status = 'completed'
                if kind == 'frame':
                    db.flush()
                    job.snapshot = {**job.snapshot, 'frame_fingerprint': frame_fingerprint(db, project)}
                db.commit()
        finally:
            if object_key:
                from app.wedding_cleanup import abandon_stage, cleanup_objects
                abandon_stage(factory, object_key)
                try:
                    cleanup_objects(factory, storage, keys=[object_key])
                except Exception:
                    # Durable stage remains available after a temporary DB outage.
                    pass
    return WorkerResult(job_id, 'completed')


@router.post('/wedding-projects/{project_id}/creation-jobs/{job_id}/cancel')
def cancel_creation_job(project_id: str, job_id: str, actor=Depends(get_wedding_actor), db=Depends(get_db)):
    project = locked_owner(db, actor, project_id)
    job = db.scalar(select(WeddingCreationJob).where(WeddingCreationJob.id == job_id, WeddingCreationJob.project_id == project_id).with_for_update())
    if not job:
        raise HTTPException(404, 'CREATION_JOB_NOT_FOUND')
    if job.status in ('queued','processing'):
        job.status, job.error_code = 'cancelled', 'CREATION_CANCELLED'
        release_unstarted(db, project, job)
        db.commit()
    return response(job)


def cancel_project_creation(db, project, reason):
    for job in db.scalars(select(WeddingCreationJob).where(WeddingCreationJob.project_id == project.id,
                          WeddingCreationJob.status.in_(['queued', 'processing'])).with_for_update()):
        job.status, job.error_code = 'cancelled', reason
        release_unstarted(db, project, job)
