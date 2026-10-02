"""Authoritative subject, exact approval, composition and invalidation rules.

Callers serialize mutations with the project lock. This module performs no I/O.
"""
from fastapi import HTTPException
from sqlalchemy import select, delete, update
from app.config import get_settings
from app.models import (WeddingAsset, WeddingAssetType, WeddingParticipant, ParticipantReview,
    StoryboardShot, ShotCandidate, RenderVersion, GenerationJob, utc_now)


def require_asset(db, project, asset):
    from app.wedding_retention import asset_expired
    if asset is None or asset.project_id != project.id or asset.invalidated_at or asset_expired(project, asset):
        raise HTTPException(409, 'FRAME_ASSET_INVALID')
    return asset


def validate_frame(db, project, shot, asset, *, reviews=False):
    require_asset(db, project, asset)
    subjects = set(asset.provenance.get('person_keys', []))
    if asset.person_key:
        subjects.add(asset.person_key)
    # Legacy generated frames retain evidence via their original shot association.
    if asset.generated and not asset.provenance:
        for linked in db.scalars(select(StoryboardShot).where(StoryboardShot.frame_asset_id == asset.id)):
            subjects.update(linked.person_keys)
    if (shot.shot_type == 'scene_ai' and (subjects or shot.person_keys)
            or shot.shot_type == 'person_ai' and (not shot.person_keys or not subjects <= set(shot.person_keys))):
        raise HTTPException(422, 'FRAME_ASSET_PERSON_SCOPE_INVALID')
    source = asset.provenance.get('shot_id')
    if source and source not in (shot.id, shot.source_shot_id) and asset.provenance.get('person_keys') != shot.person_keys:
        raise HTTPException(422, 'FRAME_SHOT_PROVENANCE_INVALID')
    if reviews:
        exact_reviews(db, project, shot, asset.id)


def exact_reviews(db, project, shot, asset_id):
    result = []
    for key in shot.person_keys:
        person = db.scalar(select(WeddingParticipant).where(WeddingParticipant.project_id == project.id,
            WeddingParticipant.person_key == key, WeddingParticipant.consent_status == 'active'))
        review = db.scalar(select(ParticipantReview).where(ParticipantReview.participant_id == person.id,
            ParticipantReview.shot_id == shot.id, ParticipantReview.project_version == project.current_version,
            ParticipantReview.asset_id == asset_id, ParticipantReview.result == 'approved')) if person else None
        if review is None:
            raise HTTPException(409, 'IDENTITY_REVIEW_REQUIRED')
        from app.wedding_service import _as_utc
        result.append(review.id + ":" + _as_utc(review.confirmed_at).isoformat())
    return sorted(result)


def source_ids(shot):
    from sqlalchemy.orm import object_session
    db = object_session(shot)
    ids = {shot.id}
    current = shot
    while db and current.source_shot_id and current.source_shot_id not in ids:
        parent = db.get(StoryboardShot, current.source_shot_id)
        if parent is None or parent.project_id != shot.project_id:
            break
        ids.add(parent.id)
        current = parent
    return ids


def candidate_matches(shot, candidate):
    return (candidate is not None and candidate.shot_id in source_ids(shot)
        and candidate.review_result != 'rejected'
        and (candidate.frame_asset_id == shot.frame_asset_id
             or (candidate.frame_asset_id is None and get_settings().wedding_media_mode == 'demo')))


def validate_composition(db, project, shots, *, selected=False, items=None):
    if get_settings().wedding_media_mode != 'live':
        return
    if (len(shots) != 18 or any(s is None for s in shots) or sum(s.shot_type == 'person_ai' for s in shots) != 8
            or sum(s.shot_type == 'scene_ai' for s in shots) != 4
            or sum(s.shot_type == 'photo' for s in shots) != 6
            or any(s.duration_ms != 5000 for s in shots)):
        raise HTTPException(409, 'STANDARD_COMPOSITION_INVALID')
    if not selected:
        return
    motion = person_motion = fallback = 0
    snapshots = {i['shot_id']: i for i in items or []}
    for shot in shots:
        item = snapshots.get(shot.id)
        candidate_id = item.get('candidate_id') if item else shot.selected_candidate_id
        candidate = db.get(ShotCandidate, candidate_id) if candidate_id else None
        asset = db.get(WeddingAsset, item['asset_id'] if item else candidate.asset_id if candidate else shot.selected_asset_id)
        require_asset(db, project, asset)
        if shot.shot_type == 'photo':
            if asset.generated or asset.asset_type != WeddingAssetType.PHOTO:
                raise HTTPException(409, 'STANDARD_PHOTO_INVALID')
        elif candidate:
            expected = get_settings().wedding_character_video_model if shot.shot_type == 'person_ai' else get_settings().wedding_scene_video_model
            if (not candidate_matches(shot, candidate) or asset.mime != 'video/mp4'
                    or candidate.model_version != expected
                    or candidate.provider != ('wan_r2v' if shot.shot_type == 'person_ai' else 'wan_i2v_flash')
                    or asset.asset_type != WeddingAssetType.VIDEO_CANDIDATE or not asset.generated):
                raise HTTPException(409, 'STANDARD_MOTION_INVALID')
            motion += 1
            person_motion += shot.shot_type == 'person_ai'
        elif (item.get('fallback_accepted') if item else shot.status == 'fallback') and asset.asset_type == WeddingAssetType.PHOTO and not asset.generated:
            fallback += 1
        else:
            raise HTTPException(409, 'STANDARD_FALLBACK_ACCEPTANCE_REQUIRED')
    if fallback > 2 or person_motion < 6 or motion < 10:
        raise HTTPException(409, 'STANDARD_MOTION_FLOOR_REQUIRED')


def invalidate_shots(db, project, shots, *, clear_frame=True):
    """Invalidate derived product access, retaining original inputs and money receipts."""
    ids = {s.id for s in shots}
    if not ids:
        return
    fence_dependencies(db, project, ids)
    for shot in shots:
        for candidate in shot.candidates:
            candidate.review_result = 'rejected'
        review_ids = select(ParticipantReview.id).where(ParticipantReview.shot_id == shot.id)
        db.execute(update(ParticipantReview).where(ParticipantReview.carried_from_review_id.in_(review_ids)).values(carried_from_review_id=None))
        db.execute(delete(ParticipantReview).where(ParticipantReview.shot_id == shot.id))
        shot.selected_asset_id = shot.selected_candidate_id = None
        if clear_frame:
            shot.frame_asset_id = None
        shot.status = 'frame_pending'
    project.frame_approved_version = None
    if project.status not in ('cancelled','blocked','awaiting_consent'):
        project.status = 'frame_review'


def fence_dependencies(db, project, ids):
    from app.wedding_service import release_generation_budget
    for job in db.scalars(select(GenerationJob).where(GenerationJob.project_id == project.id,
            GenerationJob.status.in_(['queued','processing','cancel_requested'])).with_for_update()):
        if job.target_shot_id and job.target_shot_id not in ids:
            continue
        if job.status == 'queued' and not job.started_at:
            release_generation_budget(db, job)
            job.status = 'cancelled'
            job.finished_at = utc_now()
        else:
            job.status = 'cancel_requested'
            job.cancel_requested_at = utc_now()
    for render in db.scalars(select(RenderVersion).where(RenderVersion.project_id == project.id)):
        if any(i.get('shot_id') in ids for i in render.timeline_snapshot):
            render.invalidated_at = utc_now()
            render.owner_approved_at = None
            if render.status in ('queued','processing'):
                render.status = 'cancelled'
