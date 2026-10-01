"""Durable private-object staging and resumable deletion; network I/O never holds a DB connection.

A staging row is committed before put_at. Asset insertion and staging-row deletion
transfer ownership in one transaction. Failed/ambiguous transactions leave that row
for cleanup. Project deletion queues all known keys in the same transaction that
removes product access. Cleanup has its own leases and retains deletion tombstones
so a delayed writer cannot make an object permanently untracked.
"""
from datetime import timedelta
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from app.models import WeddingObjectCleanup, WeddingAsset, WeddingProject, utc_now
from app.wedding_service import _as_utc

UPLOAD_LEASE = timedelta(minutes=15)
DELETE_LEASE = timedelta(minutes=2)


def stage_output(factory, storage, project_id, operation_id, data, suffix, mime):
    key = f'private-wedding/{project_id}/{uuid4().hex}{suffix}'
    with factory() as db:
        from app.wedding_retention import lock_project, check_storage_capacity
        project = lock_project(db, project_id)
        if project is None or project.status in ('cancelled', 'blocked'):
            raise HTTPException(409, 'OUTPUT_PROJECT_UNAVAILABLE')
        check_storage_capacity(db, project_id, len(data))
        db.add(WeddingObjectCleanup(object_key=key, project_id=project_id,
            operation_id=operation_id, state='uploading', size_bytes=len(data), cleanup_after=utc_now()+UPLOAD_LEASE))
        db.commit()
    try:
        storage.put_at(key, data, mime)
    except BaseException:
        abandon_stage(factory, key)
        raise
    finish_writer(factory, key)
    return key


def finish_writer(factory, key):
    # Durable completion is the only proof a remote writer cannot resume later.
    # If the DB is unavailable, preserve the unknown writer tombstone indefinitely.
    with factory() as db:
        row=db.scalar(select(WeddingObjectCleanup).where(WeddingObjectCleanup.object_key==key).with_for_update())
        if row:
            row.writer_finished_at=utc_now()
            if row.state=='deleted': row.state='pending'; row.cleanup_after=utc_now()
            db.commit()


def attach_stage(db, key):
    row = db.scalar(select(WeddingObjectCleanup).where(WeddingObjectCleanup.object_key == key).with_for_update())
    if row is None or row.state != 'uploading' or row.discard_requested or _as_utc(row.cleanup_after) <= utc_now():
        raise HTTPException(409, 'OUTPUT_STAGE_EXPIRED')
    # The caller inserts WeddingAsset in this same transaction. A failed commit
    # leaves the stage; an acknowledged or ambiguous successful commit leaves asset.
    db.delete(row)


def abandon_stage(factory, key):
    try:
        with factory() as db:
            row = db.scalar(select(WeddingObjectCleanup).where(WeddingObjectCleanup.object_key == key).with_for_update())
            if row is None:
                return  # Committed asset owns the key; never delete it here.
            if db.scalar(select(WeddingAsset.id).where(WeddingAsset.object_key == key)):
                db.delete(row)
            elif row.state != 'cleaning':
                row.state, row.cleanup_after, row.discard_requested = 'pending', utc_now(), True
            db.commit()
    except Exception:
        # Original durable uploading row is the recovery record during a DB outage.
        # Its lease will expire; no raw exception containing private keys is logged.
        return


def queue_project_objects(db, project):
    for row in db.scalars(select(WeddingObjectCleanup).where(WeddingObjectCleanup.project_id == project.id).with_for_update()):
        row.discard_requested = True
        if row.state not in ('uploading','cleaning'):
            row.state, row.cleanup_after = 'pending', utc_now()
    for asset in list(project.assets):
        row = db.get(WeddingObjectCleanup, asset.object_key)
        if row is None:
            db.add(WeddingObjectCleanup(object_key=asset.object_key, project_id=project.id,
                size_bytes=asset.size_bytes, state='pending', discard_requested=True, writer_finished_at=utc_now()))


def cleanup_objects(factory, storage, *, now=None, limit=20, keys=None):
    current = now or utc_now()
    with factory() as db:
        statement = select(WeddingObjectCleanup.object_key).where(WeddingObjectCleanup.cleanup_after <= current)
        if keys is not None:
            statement = statement.where(WeddingObjectCleanup.object_key.in_(keys))
        candidates = list(db.scalars(statement.order_by(WeddingObjectCleanup.cleanup_after).limit(limit)))
    completed = 0
    for key in candidates:
        with factory() as db:
            row = db.scalar(select(WeddingObjectCleanup).where(WeddingObjectCleanup.object_key == key).with_for_update())
            if row is None or _as_utc(row.cleanup_after) > current:
                continue
            if db.scalar(select(WeddingAsset.id).where(WeddingAsset.object_key == key)):
                db.delete(row); db.commit()
                continue
            finished_before_delete = row.writer_finished_at is not None
            token = str(uuid4())
            row.state, row.lease_token, row.cleanup_after = 'cleaning', token, current+DELETE_LEASE
            db.commit()
        error = None
        try:
            storage.delete(key)
        except Exception:
            error = 'OBJECT_DELETE_FAILED'
        with factory() as db:
            row = db.scalar(select(WeddingObjectCleanup).where(WeddingObjectCleanup.object_key == key).with_for_update())
            if row is None or row.lease_token != token:
                continue
            row.attempts += 1
            row.error_code = error
            row.state = 'pending' if error or not finished_before_delete else 'deleted'
            if not error and finished_before_delete:
                db.delete(row)
            # Unknown writers retain ownership/capacity and are deleted again;
            # a confirmed writer is pruned only after a later successful delete.
            row.cleanup_after = current + (timedelta(seconds=30) if error else timedelta(hours=1))
            db.commit()
            completed += not bool(error)
    return completed
