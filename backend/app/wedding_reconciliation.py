"""Bounded GET-only accounting recovery for fenced accepted video operations."""
from datetime import timedelta
from sqlalchemy import select
from app.config import get_settings
from app.models import GenerationJob, WeddingBillingReceipt, utc_now
from app.wedding_media import VideoGenerationRequest, ProviderUnavailable
from app.wedding_service import _as_utc


def reconcile_cancelled(factory, providers):
    from app.wedding_worker import _lock_project_then_generation_job, WorkerResult
    cutoff = utc_now()-timedelta(seconds=get_settings().wedding_worker_stale_seconds)
    with factory() as db:
        job_id = db.scalar(select(GenerationJob.id).where(
            GenerationJob.status.in_(['cancel_requested','cancelled','failed']),
            GenerationJob.updated_at < cutoff,
            GenerationJob.id.in_(select(WeddingBillingReceipt.job_id).where(WeddingBillingReceipt.settled.is_(False)))
        ).order_by(GenerationJob.updated_at).limit(1))
    if not job_id:
        return None
    with factory() as db:
        _, project, job = _lock_project_then_generation_job(db, job_id)
        if not project or not job or job.updated_at and _as_utc(job.updated_at) >= cutoff:
            return None
        token = utc_now()
        job.updated_at = job.started_at = token
        rows = list(db.scalars(select(WeddingBillingReceipt).where(WeddingBillingReceipt.job_id == job.id,
            WeddingBillingReceipt.settled.is_(False),WeddingBillingReceipt.provider != 'budget_envelope')))
        work = [(r.id,r.task_id,r.operation_key,r.provenance,r.model) for r in rows if r.task_id and r.state in ('running','unknown','dispatching')]
        project_id = project.id
        db.commit()
    for receipt_id, task_id, operation, provenance, model in work:
        kind = provenance.get('shot_type','person_ai' if model == get_settings().wedding_character_video_model else 'scene_ai')
        from types import SimpleNamespace
        from app.wedding_video_operations import video_configuration
        configuration = provenance.get('configuration')
        if configuration and configuration != video_configuration(SimpleNamespace(shot_type=kind)):
            continue  # Never send a pinned task ID to a changed provider/region.
        provider = providers.character_video if kind == 'person_ai' else providers.scene_video
        request = VideoGenerationRequest(project_id,job_id,operation.split(':')[-1],kind,'','',b'','image/png',operation,
            existing_task_id=task_id,reconcile_only=True)
        try:
            output = provider.generate(request)
            state = 'succeeded'
        except ProviderUnavailable as exc:
            output, state = exc, exc.submission_state
        except Exception:
            continue
        with factory() as db:
            _, project, job = _lock_project_then_generation_job(db,job_id)
            if not job or _as_utc(job.started_at) != token:
                return None
            receipt = db.get(WeddingBillingReceipt, receipt_id)
            if receipt:
                from app.wedding_accounting import lock_account
                lock_account(db, receipt.owner_id)
                db.refresh(receipt, with_for_update=True)
            if receipt and not receipt.settled and receipt.billed_fen is None:
                receipt.state = state if state in ('succeeded','failed') else 'running'
                receipt.estimated_fen = max(0,int(output.actual_cost_fen))
                receipt.usage = output.usage
                receipt.request_id = output.request_id or receipt.request_id
                receipt.price_metadata = output.price_metadata
            db.commit()
    with factory() as db:
        _, project, job = _lock_project_then_generation_job(db,job_id)
        if not project or not job or _as_utc(job.started_at) != token:
            return None
        rows = list(db.scalars(select(WeddingBillingReceipt).where(WeddingBillingReceipt.job_id==job_id,
            WeddingBillingReceipt.provider!='budget_envelope')))
        if any(r.state in ('dispatching','unknown','running') for r in rows):
            job.error_code = 'PROVIDER_RECONCILIATION_PENDING'
        else:
            from app.wedding_service import settle_generation_budget
            actual = sum(r.billed_fen if r.billed_fen is not None else r.estimated_fen for r in rows)
            if actual <= job.reserved_amount_fen:
                settle_generation_budget(db,job,actual)
                job.status = 'cancelled'
                job.finished_at = utc_now()
                job.error_code = None
                from app.wedding_worker import _restore_project_workflow_state
                _restore_project_workflow_state(db, project)
            else:
                job.error_code = 'ACTUAL_COST_EXCEEDS_RESERVATION'
        db.commit()
        return WorkerResult(job.id,str(job.status),job.error_code)
