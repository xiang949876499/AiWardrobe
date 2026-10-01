"""Durable per-shot video checkpoints, consent-fenced bounded lease callbacks."""
from dataclasses import replace
from decimal import ROUND_CEILING
from fastapi import HTTPException
from sqlalchemy import select, text

from app.config import get_settings
from app.models import WeddingBillingReceipt, WeddingProject, GenerationJob, StoryboardShot, utc_now
from app.wedding_creation import digest
from app.wedding_references import selected_references, consent_provenance
from app.wedding_media import ProviderUnavailable


def receipt_for(db, job_id, shot_id):
    return db.scalar(select(WeddingBillingReceipt).where(WeddingBillingReceipt.operation_key == f'{job_id}:{shot_id}'))


def video_configuration(shot):
    cfg = get_settings()
    person = shot.shot_type == 'person_ai'
    live = cfg.wedding_media_mode == 'live'
    return {'mode': cfg.wedding_media_mode,
            'provider': (cfg.wedding_character_video_provider if person else cfg.wedding_scene_video_provider) if live else 'local_demo',
            'model': (cfg.wedding_character_video_model if person else cfg.wedding_scene_video_model) if live else 'local-demo-v1',
            'region_endpoint': cfg.wedding_dashscope_base_url, 'price_version': cfg.wedding_price_version,
            'rate': str(cfg.wedding_r2v_fen_per_second if person else cfg.wedding_i2v_flash_fen_per_second)}


def validate_receipt_provenance(db, project, shot, receipt):
    configuration = receipt.provenance.get('configuration')
    if configuration and configuration != video_configuration(shot):
        raise HTTPException(409, 'PROVIDER_CONFIGURATION_CHANGED')
    if configuration and configuration['mode'] == 'live':
        consent = consent_provenance(db, project, configuration['provider'])
        if digest(consent) != receipt.provenance['consent_hash']:
            raise HTTPException(409, 'CONSENT_CHANGED')
        refs = selected_references(db, project, shot.person_keys if shot.shot_type == 'person_ai' else [], demo=False)
        if digest({'description': shot.description, 'frame': shot.frame_asset_id,
                   'references': [(r['asset_id'], r['sha256']) for r in refs]}) != receipt.provenance['input_hash']:
            raise HTTPException(409, 'PRIMARY_REFERENCE_CHANGED')


def prepare_receipt(db, project, job, shot):
    receipt = receipt_for(db, job.id, shot.id)
    if receipt:
        validate_receipt_provenance(db, project, shot, receipt)
        return receipt
    cfg = get_settings()
    person = shot.shot_type == 'person_ai'
    live = cfg.wedding_media_mode == 'live'
    provider = (cfg.wedding_character_video_provider if person else cfg.wedding_scene_video_provider) if live else 'local_demo'
    model = (cfg.wedding_character_video_model if person else cfg.wedding_scene_video_model) if live else 'local-demo-v1'
    consent = consent_provenance(db, project, provider) if live else []
    refs = selected_references(db, project, shot.person_keys if person else [], demo=not live)
    rate = cfg.wedding_r2v_fen_per_second if person else cfg.wedding_i2v_flash_fen_per_second
    receipt = WeddingBillingReceipt(operation_key=f'{job.id}:{shot.id}', owner_id=project.owner_id,
        project_id=project.id, job_id=job.id, provider=provider, model=model, state='dispatching',
        reserved_fen=int((rate * 5).to_integral_value(rounding=ROUND_CEILING)) if live else 0,
        provenance={'input_hash': digest({'description': shot.description, 'frame': shot.frame_asset_id,
                    'references': [(r['asset_id'],r['sha256']) for r in refs]}),
                    'consent_hash': digest(consent), 'consent': consent, 'price_version': cfg.wedding_price_version,
                    'region_endpoint': cfg.wedding_dashscope_base_url, 'configuration': video_configuration(shot)})
    db.add(receipt)
    db.flush()
    return receipt


def callbacks(factory, job_id, shot_id, token):
    def pulse(task_id=None):
        from app.wedding_worker import _lock_project_then_generation_job, _lease_matches
        with factory() as db:
            # Connection/pool acquisition is separately bounded by database.py.
            if db.get_bind().dialect.name == 'postgresql':
                db.execute(text("SET LOCAL lock_timeout = '1000ms'"))
                db.execute(text("SET LOCAL statement_timeout = '2000ms'"))
            else:
                db.execute(text('PRAGMA busy_timeout = 1000'))
            _, project, job = _lock_project_then_generation_job(db, job_id)
            receipt = receipt_for(db, job_id, shot_id)
            if receipt is None:
                raise RuntimeError('Receipt missing')
            if task_id:
                if receipt.task_id and receipt.task_id != task_id:
                    raise RuntimeError('Task identity changed')
                receipt.task_id, receipt.state = task_id, 'running'
                db.flush()
            error = None
            if not job or not project or not _lease_matches(job, token) or job.status != 'processing':
                error = 'WORKER_LEASE_STALE'
            elif job.cancel_requested_at or project.current_version != job.project_version or project.status in ('cancelled','blocked'):
                error = 'DISPATCH_FENCED'
            else:
                try:
                    if get_settings().wedding_media_mode == 'live':
                        validate_receipt_provenance(db, project, db.get(StoryboardShot, shot_id), receipt)
                        consent = consent_provenance(db, project, receipt.provenance['configuration']['provider'])
                        if digest(consent) != receipt.provenance['consent_hash']:
                            error = 'CONSENT_CHANGED'
                    else:
                        from app.wedding_service import validate_active_consents
                        validate_active_consents(db, project)
                except HTTPException:
                    error = 'CONSENT_REQUIRED'
            if not error:
                job.updated_at = utc_now()
            db.commit()  # Accepted identity survives even when cancellation just won.
            if error:
                raise RuntimeError(error)
    return lambda task_id: pulse(task_id), lambda: pulse()


def attach_request(factory, request, token, storage):
    with factory() as db:
        project = db.get(WeddingProject, request.project_id)
        shot = db.get(StoryboardShot, request.shot_id)
        receipt = receipt_for(db, request.job_id, request.shot_id)
        if receipt:
            validate_receipt_provenance(db, project, shot, receipt)
        task_id = receipt.task_id if receipt else None
        if receipt and receipt.state in ('unknown','failed','succeeded','not_submitted'):
            raise ProviderUnavailable('PROVIDER_OUTCOME_ALREADY_RECORDED', 'Operation cannot be submitted again', submission_state='unknown', task_id=task_id)
        refs = selected_references(db, project, shot.person_keys if shot.shot_type == 'person_ai' else [], demo=get_settings().wedding_media_mode != 'live')
    portraits = tuple((r['person_key'], storage.read(r['object_key']), r['mime']) for r in refs)
    checkpoint, heartbeat = callbacks(factory, request.job_id, request.shot_id, token)
    return replace(request, primary_references=portraits, existing_task_id=task_id, checkpoint=checkpoint, heartbeat=heartbeat)


def record_result(factory, job_id, shot_id, output, state, token=None):
    from app.wedding_worker import _lock_project_then_generation_job
    with factory() as db:
        _, project, job = _lock_project_then_generation_job(db, job_id)
        receipt = receipt_for(db, job_id, shot_id)
        if receipt is None:
            return
        from app.wedding_worker import _lease_matches
        if job and token and not _lease_matches(job, token):
            return
        if receipt.settled:
            return
        receipt.task_id = output.task_id or receipt.task_id
        receipt.request_id = output.request_id or receipt.request_id
        receipt.state = state
        receipt.estimated_fen = max(0, int(output.actual_cost_fen))
        receipt.usage = output.usage
        receipt.price_metadata = {**output.price_metadata, 'price_version': output.price_version}
        if hasattr(output, 'provider'):
            receipt.provider, receipt.model = output.provider, output.model_version
        if project is None:
            from app.wedding_accounting import settle_deleted_video
            settle_deleted_video(db, receipt)
        if job and state in ('unknown','running'):
            job.progress_stage = 'awaiting_provider_task' if state == 'running' and receipt.task_id else 'provider_outcome_unknown'
            if not receipt.task_id or state == 'unknown':
                job.status = 'needs_action'
            job.error_code = 'PROVIDER_PENDING' if state == 'running' and receipt.task_id else 'PROVIDER_OUTCOME_UNKNOWN'
            job.updated_at = utc_now()
        db.commit()
