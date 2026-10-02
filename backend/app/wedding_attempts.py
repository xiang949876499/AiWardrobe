"""Attempt reservations share the project's lock and durable budget envelope."""
from sqlalchemy import select

from app.models import StoryboardShot, WeddingBillingReceipt


def reserve_attempts(db, job, shots):
    envelope = db.scalar(select(WeddingBillingReceipt).where(
        WeddingBillingReceipt.operation_key == f'reservation:{job.id}'))
    envelope.provenance = {**envelope.provenance, 'attempt_shot_ids': [s.id for s in shots],
                           'returned_attempt_shot_ids': []}


def return_unsubmitted_attempts(db, job):
    """Called at terminal settlement, before operation states change; exactly once.

    Missing receipts prove no dispatch only after this job has been fenced from
    further execution. Submitted failures and ambiguous outcomes keep their slot.
    """
    rows = list(db.scalars(select(WeddingBillingReceipt).where(WeddingBillingReceipt.job_id == job.id)))
    envelope = next((r for r in rows if r.provider == 'budget_envelope'), None)
    if envelope is None:
        return
    ids = envelope.provenance.get('attempt_shot_ids')
    if ids is None:
        # Today's pending candidates cannot identify a historical whole-job set,
        # even when its cardinality matches. Only the immutable target is proof.
        shot = db.get(StoryboardShot, job.target_shot_id) if job.target_shot_id else None
        if shot is None or shot.project_id != job.project_id or shot.story_version_id != job.story_version_id:
            return
        ids = [shot.id]
    returned = set(envelope.provenance.get('returned_attempt_shot_ids', []))
    operations = {r.operation_key: r for r in rows if r is not envelope}
    from app.wedding_guards import source_ids
    shots = list(db.scalars(select(StoryboardShot).where(StoryboardShot.project_id == job.project_id)))
    for shot_id in set(ids) - returned:
        receipt = operations.get(f'{job.id}:{shot_id}')
        if receipt is not None and receipt.state != 'not_submitted':
            continue
        for shot in shots:
            if shot_id in source_ids(shot):
                shot.generation_attempts = max(0, shot.generation_attempts - 1)
        returned.add(shot_id)
    envelope.provenance = {**envelope.provenance, 'attempt_shot_ids': ids,
                           'returned_attempt_shot_ids': sorted(returned)}
    db.flush()
