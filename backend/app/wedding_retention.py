"""Project media capacity, genuine activity retention and durable expiry transfers."""
from datetime import timedelta
from sqlalchemy import select, update, delete, func, or_
from fastapi import HTTPException
from app.models import (WeddingProject, WeddingAsset, WeddingObjectCleanup, WeddingParticipant,
    ParticipantReview, ShotCandidate, StoryboardShot, RenderVersion, utc_now)
from app.wedding_service import _as_utc
from app.config import get_settings

STORAGE_LIMIT = 2 * 1024 ** 3
RETENTION = timedelta(days=30)


def lock_project(db, project_id):
    # An actual write also serializes SQLite, whose SELECT FOR UPDATE is a no-op.
    db.execute(update(WeddingProject).where(WeddingProject.id==project_id).values(id=WeddingProject.id))
    return db.scalar(select(WeddingProject).where(WeddingProject.id==project_id).with_for_update().execution_options(populate_existing=True))


def check_storage_capacity(db, project_id, additional_bytes):
    used = db.scalar(select(func.coalesce(func.sum(WeddingAsset.size_bytes),0)).where(WeddingAsset.project_id==project_id))
    staged = db.scalar(select(func.coalesce(func.sum(WeddingObjectCleanup.size_bytes),0)).where(
        WeddingObjectCleanup.project_id==project_id,
        or_(WeddingObjectCleanup.state!='deleted', WeddingObjectCleanup.writer_finished_at.is_(None)),
        ~WeddingObjectCleanup.object_key.in_(select(WeddingAsset.object_key))))
    if additional_bytes < 0 or used+staged+additional_bytes > STORAGE_LIMIT:
        raise HTTPException(409,'PROJECT_STORAGE_LIMIT')


def retention_deadline(project):
    return _as_utc(project.delivered_at or project.last_activity_at or project.created_at) + RETENTION


def asset_expired(project, asset, *, now=None):
    current = now or utc_now()
    return retention_deadline(project) <= current or (asset.expires_at is not None and _as_utc(asset.expires_at) <= current)


def touch_activity(db, project, *, delivered=False):
    now=utc_now()
    previous_deadline=retention_deadline(project)
    if previous_deadline<=now:
        # Materialize the already-effective project expiry before a new activity
        # can advance its deadline; even inconsistent legacy rows cannot revive.
        db.execute(update(WeddingAsset).where(WeddingAsset.project_id==project.id,
            or_(WeddingAsset.expires_at.is_(None),WeddingAsset.expires_at>previous_deadline))
            .values(expires_at=previous_deadline).execution_options(synchronize_session="fetch"))
    project.last_activity_at=now
    if delivered: project.delivered_at=now
    deadline=retention_deadline(project)
    # Never resurrect already-expired files. Active retained inputs and outputs share the deadline.
    db.execute(update(WeddingAsset).where(WeddingAsset.project_id==project.id,
        WeddingAsset.invalidated_at.is_(None),
        or_(WeddingAsset.expires_at.is_(None),WeddingAsset.expires_at>now)).values(expires_at=deadline).execution_options(synchronize_session="fetch"))
    return deadline


def reserve_egress(db, project, asset):
    from app.models import WeddingAssetType
    if asset.asset_type == WeddingAssetType.FINAL_VIDEO:
        if project.final_download_count >= 10:
            raise HTTPException(429,'FINAL_DOWNLOAD_LIMIT')
        project.final_download_count += 1
    else:  # Originals, frames and candidates share preview egress; no alternate gateway bypass.
        if project.preview_bytes_served + asset.size_bytes > get_settings().wedding_preview_bandwidth_bytes:
            raise HTTPException(429,'PREVIEW_BANDWIDTH_LIMIT')
        project.preview_bytes_served += asset.size_bytes
    # Charge full bytes before I/O; failed/disconnected requests conservatively consume budget.


def remove_asset(db, project, asset):
    """Caller owns parent lock. Transfer physical ownership before removing every FK."""
    from app.wedding_guards import invalidate_shots
    affected = list(db.scalars(select(StoryboardShot).where(StoryboardShot.project_id == project.id,
        StoryboardShot.project_version == project.current_version,
        or_(StoryboardShot.frame_asset_id == asset.id, StoryboardShot.selected_asset_id == asset.id))))
    primary_people = list(db.scalars(select(WeddingParticipant.person_key).where(WeddingParticipant.project_id==project.id,
        WeddingParticipant.primary_photo_asset_id==asset.id)))
    candidate_ids = list(db.scalars(select(ShotCandidate.id).where(ShotCandidate.asset_id==asset.id)))
    for shot in db.scalars(select(StoryboardShot).where(StoryboardShot.project_id==project.id,StoryboardShot.project_version==project.current_version)):
        if (shot.selected_candidate_id in candidate_ids or (shot.shot_type!='photo' and set(shot.person_keys)&set(primary_people))) and shot not in affected:
            affected.append(shot)
    invalidate_shots(db, project, affected)
    if db.get(WeddingObjectCleanup,asset.object_key) is None:
        db.add(WeddingObjectCleanup(object_key=asset.object_key, project_id=project.id,
            size_bytes=asset.size_bytes, state='pending', discard_requested=True,writer_finished_at=utc_now()))
    review_ids=select(ParticipantReview.id).where(ParticipantReview.asset_id==asset.id)
    db.execute(update(ParticipantReview).where(ParticipantReview.carried_from_review_id.in_(review_ids)).values(carried_from_review_id=None))
    db.execute(delete(ParticipantReview).where(ParticipantReview.asset_id==asset.id))
    candidate_ids=list(db.scalars(select(ShotCandidate.id).where(ShotCandidate.asset_id==asset.id)))
    if candidate_ids:
        db.execute(update(StoryboardShot).where(StoryboardShot.selected_candidate_id.in_(candidate_ids)).values(selected_candidate_id=None))
        db.execute(delete(ShotCandidate).where(ShotCandidate.id.in_(candidate_ids)))
    db.execute(update(WeddingParticipant).where(WeddingParticipant.primary_photo_asset_id==asset.id).values(primary_photo_asset_id=None))
    for attr in ('frame_asset_id','selected_asset_id'):
        db.execute(update(StoryboardShot).where(getattr(StoryboardShot,attr)==asset.id).values(**{attr:None}))
    for render in db.scalars(select(RenderVersion).where(RenderVersion.project_id==project.id).with_for_update()):
        referenced = any(asset.id == item.get('frame_asset_id') or asset.id in item.get('primary_references', {}).values() for item in (render.timeline_snapshot or []))
        if referenced or asset.id in (render.input_asset_ids or []) or asset.id in (render.preview_asset_id,render.export_asset_id):
            render.invalidated_at=utc_now(); render.owner_approved_at=None
            if render.status in ('queued','processing'): render.status='cancelled'
            if render.preview_asset_id==asset.id: render.preview_asset_id=None
            if render.export_asset_id==asset.id: render.export_asset_id=None
    db.delete(asset)


def expire_assets(factory, storage, *, now=None, limit=20):
    current=now or utc_now()
    with factory() as db:
        ids=list(db.execute(select(WeddingAsset.id,WeddingAsset.project_id).join(WeddingProject).where(or_(
            WeddingAsset.expires_at<=current, WeddingProject.delivered_at<=current-RETENTION,
            (WeddingProject.delivered_at.is_(None) & (func.coalesce(WeddingProject.last_activity_at,WeddingProject.created_at)<=current-RETENTION))))
            .order_by(WeddingAsset.expires_at).limit(min(limit,100))))
    count=0
    for asset_id,project_id in ids:
        with factory() as db:
            project=lock_project(db,project_id)
            asset=db.get(WeddingAsset,asset_id)
            if project is None or asset is None or not asset_expired(project,asset,now=current): continue
            remove_asset(db,project,asset);db.commit();count+=1
    from app.wedding_cleanup import cleanup_objects
    cleanup_objects(factory,storage,now=max(current,utc_now()),limit=limit)
    return count
