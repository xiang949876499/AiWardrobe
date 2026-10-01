"""Photo readiness, scoped references and immutable render authoring."""
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import unicodedata

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from sqlalchemy import select

from app.config import get_settings
from app.database import get_db
from app.models import (WeddingProject, WeddingParticipant, WeddingAsset, WeddingAssetType,
    StoryVersion, ParticipantReview, RenderVersion, GenerationJob, utc_now)
from app.wedding_service import get_wedding_actor, require_project_owner, validate_active_consents, _as_utc

router = APIRouter(tags=['wedding authoring'])


class MusicTrack(BaseModel):
    model_config = ConfigDict(extra='ignore')
    track_id: str = Field(min_length=1,max_length=120)
    title: str = Field(min_length=1,max_length=200)
    path: str = Field(min_length=1)
    license_id: str = Field(min_length=1,max_length=200)
    license_version: str = Field(min_length=1,max_length=120)
    valid_from: str
    valid_until: str
    perpetual_embedded_delivery: StrictBool
    allowed_uses: list[str]


def _catalog():
    path = get_settings().wedding_music_catalog_file
    if not path:
        return []
    try:
        rows = json.loads(Path(path).read_text(encoding='utf-8'))
        if not isinstance(rows, list) or len(rows)>1000 or len({r['track_id'] for r in rows}) != len(rows):
            raise ValueError()
        return [MusicTrack.model_validate(row).model_dump() for row in rows]
    except (OSError, ValueError, KeyError, TypeError):
        raise HTTPException(409, 'MUSIC_CATALOG_INVALID') from None


def music_catalog():
    fields = ('track_id','title','license_id','license_version','valid_from','valid_until',
              'perpetual_embedded_delivery','allowed_uses')
    return [{k:r.get(k) for k in fields} for r in _catalog()]


def select_music(track_id):
    row = next((r for r in _catalog() if r['track_id']==track_id),None)
    if row is None:
        raise HTTPException(409,'MUSIC_UNAVAILABLE')
    if not row.get('license_id') or not row.get('license_version') or row.get('perpetual_embedded_delivery') is not True or not {'customer_embedded_delivery','wedding_playback'} <= set(row.get('allowed_uses',[])):
        raise HTTPException(409,'MUSIC_RIGHTS_INSUFFICIENT')
    try:
        valid = _as_utc(datetime.fromisoformat(row['valid_from'])) <= utc_now() < _as_utc(datetime.fromisoformat(row['valid_until']))
    except (ValueError, KeyError, TypeError):
        raise HTTPException(409,'MUSIC_CATALOG_INVALID') from None
    if not valid:
        raise HTTPException(409,'MUSIC_LICENSE_EXPIRED')
    path = Path(row.get('path',''))
    if not path.is_file():
        raise HTTPException(409,'MUSIC_FILE_MISSING')
    row = {k: row[k] for k in ('track_id','title','path','license_id','license_version','valid_from','valid_until','perpetual_embedded_delivery','allowed_uses')}
    return {**deepcopy(row), 'sha256':hashlib.sha256(path.read_bytes()).hexdigest(), 'selected_at':utc_now().isoformat()}


def safe_music(snapshot):
    return {k:v for k,v in (snapshot or {}).items() if k in ('track_id','title','license_id','license_version','valid_from','valid_until','perpetual_embedded_delivery','allowed_uses','sha256','selected_at')}


def valid_asset(asset, project_id):
    return asset is not None and asset.project_id==project_id and not asset.invalidated_at and (not asset.expires_at or _as_utc(asset.expires_at)>utc_now())


def photo_readiness(db, project):
    demo = get_settings().wedding_media_mode=='demo'
    people=[]
    for person in validate_active_consents(db, project):
        assets=list(db.scalars(select(WeddingAsset).where(WeddingAsset.project_id==project.id,
            WeddingAsset.person_key==person.person_key, WeddingAsset.asset_type==WeddingAssetType.PHOTO,
            WeddingAsset.generated.is_(False))))
        usable=[a for a in assets if valid_asset(a,project.id) and a.quality.get('usable') and (demo or not a.quality.get('demo_bypass'))]
        warnings=[a for a in usable if a.quality.get('warnings') and not a.quality_confirmed_at]
        primary=next((a for a in usable if a.id==person.primary_photo_asset_id),None)
        people.append(dict(person_key=person.person_key, usable_count=len(usable), required_count=3,
            primary_photo_asset_id=person.primary_photo_asset_id,
            ready=demo or (len(usable)>=3 and primary is not None and not warnings),
            warnings_unconfirmed=[a.id for a in warnings],
            photos=[dict(asset_id=a.id,quality=a.quality,confirmed_at=a.quality_confirmed_at) for a in assets]))
    return dict(demo_bypass=demo, ready=all(p['ready'] for p in people), people=people)


def require_photo_readiness(db,project):
    if get_settings().wedding_media_mode != 'live':
        return
    if not photo_readiness(db,project)['ready']:
        raise HTTPException(409,'PHOTO_READINESS_REQUIRED')


def scoped_project(db,actor,project_id,person_key=None):
    project=db.scalar(select(WeddingProject).where(WeddingProject.id==project_id).with_for_update())
    if project is None or (actor.kind=='owner' and actor.owner_id!=project.owner_id) or (actor.kind=='participant' and actor.project_id!=project.id):
        raise HTTPException(404,'PROJECT_NOT_FOUND')
    if project.status in ('cancelled','blocked'):
        raise HTTPException(409,'PROJECT_NOT_EDITABLE')
    people=validate_active_consents(db,project)
    own='A' if actor.kind=='owner' else actor.person_key
    if person_key and own!=person_key:
        raise HTTPException(403,'SUBJECT_SCOPE_REQUIRED')
    person = db.scalar(select(WeddingParticipant).where(WeddingParticipant.project_id==project.id, WeddingParticipant.person_key==own).with_for_update().execution_options(populate_existing=True))
    return project,person


@router.get('/wedding-projects/{project_id}/photo-readiness')
def get_readiness(project_id:str,actor=Depends(get_wedding_actor),db=Depends(get_db)):
    project,person=scoped_project(db,actor,project_id)
    result=photo_readiness(db,project)
    if actor.kind=='participant':
        result['people']=[p for p in result['people'] if p['person_key']==person.person_key]
    return result


class QualityConfirmation(BaseModel):
    model_config=ConfigDict(extra='forbid')
    confirmed: bool


@router.post('/wedding-projects/{project_id}/photos/{asset_id}/confirm-quality')
def confirm_quality(project_id:str,asset_id:str,body:QualityConfirmation,actor=Depends(get_wedding_actor),db=Depends(get_db)):
    project,person=scoped_project(db,actor,project_id)
    asset=db.get(WeddingAsset,asset_id)
    if not valid_asset(asset,project.id) or asset.person_key!=person.person_key or asset.asset_type!=WeddingAssetType.PHOTO:
        raise HTTPException(404,'PHOTO_NOT_FOUND')
    if not asset.quality.get('usable'):
        raise HTTPException(409,'PHOTO_UNUSABLE')
    asset.quality_confirmed_at=utc_now() if body.confirmed else None
    db.commit()
    return dict(asset_id=asset.id,confirmed_at=asset.quality_confirmed_at)


class PrimaryRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    version:int=Field(ge=1)
    asset_id:str


@router.put('/wedding-projects/{project_id}/participants/{person_key}/primary-photo')
def primary_photo(project_id:str,person_key:str,body:PrimaryRequest,actor=Depends(get_wedding_actor),db=Depends(get_db)):
    project,person=scoped_project(db,actor,project_id,person_key)
    if project.current_version!=body.version:
        raise HTTPException(409,'PROJECT_VERSION_STALE')
    asset=db.get(WeddingAsset,body.asset_id)
    demo=get_settings().wedding_media_mode=='demo'
    if not valid_asset(asset,project.id) or asset.person_key!=person.person_key or asset.asset_type!=WeddingAssetType.PHOTO or asset.generated or not asset.quality.get('usable') or (not demo and asset.quality.get('demo_bypass')):
        raise HTTPException(409,'PRIMARY_PHOTO_INVALID')
    affected=[]
    if person.primary_photo_asset_id!=asset.id:
        from app.wedding_creation import release_unstarted, current_story
        from app.models import WeddingCreationJob
        from app.wedding_service import release_generation_budget
        for job in db.scalars(select(WeddingCreationJob).where(WeddingCreationJob.project_id==project.id, WeddingCreationJob.status.in_(['queued','processing'])).with_for_update()):
            if job.kind=='frame' and any(person_key in item['person_keys'] for item in job.snapshot.get('shots',[])):
                job.status,job.error_code='cancelled','PRIMARY_REFERENCE_CHANGED'
                release_unstarted(db,project,job)
        for job in db.scalars(select(GenerationJob).where(GenerationJob.project_id==project.id,GenerationJob.status.in_(['queued','processing','cancel_requested'])).with_for_update()):
            if job.target_shot_id:
                from app.models import StoryboardShot
                target=db.get(StoryboardShot,job.target_shot_id)
                if target and person_key not in target.person_keys:
                    continue
            if job.status=='queued':
                release_generation_budget(db,job)
                job.status='cancelled'
            else:
                job.status='cancel_requested'
                job.cancel_requested_at=utc_now()
        story=current_story(db,project)
        invalid=set()
        if story:
            for shot in story.shots:
                if person_key not in shot.person_keys or shot.shot_type=='photo':
                    continue
                affected.append(shot.id)
                invalid.update([shot.frame_asset_id,shot.selected_asset_id])
                invalid.update(c.asset_id for c in shot.candidates)
                for review in list(shot.reviews):
                    db.delete(review)
                shot.frame_asset_id=None
                shot.selected_asset_id=None
                shot.selected_candidate_id=None
                shot.status='frame_pending'
            for asset_id in invalid-{None}:
                old=db.get(WeddingAsset,asset_id)
                if old and old.asset_type != WeddingAssetType.PHOTO:
                    old.invalidated_at=utc_now()
            project.frame_approved_version=None
            if story.status=='approved':
                project.status='frame_review'
        for render in db.scalars(select(RenderVersion).where(RenderVersion.project_id==project.id)):
            if set(render.input_asset_ids)&invalid or any(person_key in item.get('person_keys',[]) for item in render.timeline_snapshot):
                render.invalidated_at=utc_now()
                if render.status in ('queued','processing'):
                    render.status='cancelled'
        person.primary_photo_asset_id=asset.id
    db.commit()
    return dict(person_key=person_key,primary_photo_asset_id=asset.id,version=project.current_version,
        affected_shot_ids=affected,regeneration_required=bool(affected),
        cost_notice='Affected frames and videos require regeneration; live provider calls incur new costs. Unaffected assets and reviews are reused.' if affected else None)


@router.get('/wedding-music')
def list_music(actor=Depends(get_wedding_actor)):
    tracks=music_catalog()
    return dict(available=bool(tracks),tracks=tracks)


class MusicRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    track_id:str


@router.put('/wedding-projects/{project_id}/music')
def project_music(project_id:str,body:MusicRequest,actor=Depends(get_wedding_actor),db=Depends(get_db)):
    require_project_owner(db,actor,project_id)
    project,_=scoped_project(db,actor,project_id)
    project.selected_music=select_music(body.track_id)
    db.commit()
    return safe_music(project.selected_music)


def validate_subtitle(text):
    if len(text.split('\n'))>2 or any(sum(2 if unicodedata.east_asian_width(c) in ('W','F') else 1 for c in line)>36 for line in text.split('\n')) or any(ord(c)<32 and c!='\n' for c in text):
        raise HTTPException(422,'SUBTITLE_TOO_LONG')
    return text


def timeline_snapshot(db,project,shots,asset_ids,subtitles):
    from app.models import ShotCandidate
    if set(subtitles)-{s.id for s in shots}:
        raise HTTPException(422,'SUBTITLE_SHOT_UNKNOWN')
    items=[]
    for shot,asset_id in zip(shots,asset_ids,strict=True):
        asset=db.get(WeddingAsset,asset_id)
        if not valid_asset(asset,project.id):
            raise HTTPException(409,'TIMELINE_ASSET_INVALID')
        items.append(dict(shot_id=shot.id,shot_index=shot.shot_index,shot_type=shot.shot_type,
            person_keys=shot.person_keys,asset_id=asset_id,asset_sha256=asset.sha256,
            candidate_id=shot.selected_candidate_id,frame_asset_id=shot.frame_asset_id,
            subtitle=validate_subtitle(subtitles.get(shot.id,shot.subtitle)),duration_ms=shot.duration_ms,
            consent={p.person_key: {'participant_id':p.id,'confirmed_at':_as_utc(p.confirmed_at).isoformat(),'terms_version':p.terms_version} for p in project.participants},
            primary_references={p.person_key:p.primary_photo_asset_id for p in project.participants if p.person_key in shot.person_keys and shot.shot_type!='photo'}))
    return items


def validate_timeline(db,project,items):
    from app.models import StoryboardShot, ShotCandidate
    validate_active_consents(db,project)
    require_photo_readiness(db,project)
    if not items or sum(i['duration_ms'] for i in items)!=project.target_duration_ms or len({i['shot_id'] for i in items})!=len(items):
        raise HTTPException(409,'TIMELINE_DURATION_INVALID')
    people={p.person_key:p for p in project.participants}
    story_id=db.scalar(select(StoryVersion.id).where(StoryVersion.project_id==project.id,
        StoryVersion.version==project.current_version))
    for item in items:
        expected={p.person_key: {'participant_id':p.id,'confirmed_at':_as_utc(p.confirmed_at).isoformat(),'terms_version':p.terms_version} for p in project.participants}
        if item.get('consent') != expected:
            raise HTTPException(409,'CONSENT_CHANGED')
        asset=db.get(WeddingAsset,item['asset_id'])
        shot=db.get(StoryboardShot,item['shot_id'])
        if not valid_asset(asset,project.id) or asset.sha256!=item['asset_sha256'] or shot is None or shot.project_id!=project.id:
            raise HTTPException(409,'TIMELINE_ASSET_INVALID')
        if shot.project_version!=project.current_version or shot.story_version_id!=story_id:
            raise HTTPException(409,'TIMELINE_SHOT_VERSION_STALE')
        if any(people[k].primary_photo_asset_id!=v for k,v in item['primary_references'].items()):
            raise HTTPException(409,'PRIMARY_REFERENCE_CHANGED')
        if item.get('candidate_id'):
            candidate=db.get(ShotCandidate,item['candidate_id'])
            if candidate is None or candidate.shot_id!=shot.id or candidate.asset_id!=asset.id or candidate.review_result=='rejected':
                raise HTTPException(409,'TIMELINE_CANDIDATE_INVALID')
        for key in item['person_keys']:
            review=db.scalar(select(ParticipantReview).where(ParticipantReview.participant_id==people[key].id,
                ParticipantReview.shot_id==shot.id,ParticipantReview.project_version==project.current_version,
                ParticipantReview.asset_id==asset.id,ParticipantReview.result=='approved'))
            if review is None:
                raise HTTPException(409,'IDENTITY_REVIEW_REQUIRED')


def render_music(project,track_id=None):
    if track_id:
        project.selected_music=select_music(track_id)
        return project.selected_music
    if project.selected_music:
        # Existing project's perpetual embedded grant survives later catalog expiry/change.
        return historical_music(project.selected_music)
    if get_settings().wedding_media_mode=='demo':
        return {}
    raise HTTPException(409,'MUSIC_LICENSE_REQUIRED')


def historical_music(snapshot):
    if not snapshot:
        if get_settings().wedding_media_mode=='demo':
            return {}
        raise HTTPException(409,'MUSIC_LICENSE_REQUIRED')
    if snapshot.get('perpetual_embedded_delivery') is not True or not {'customer_embedded_delivery','wedding_playback'}<=set(snapshot.get('allowed_uses',[])):
        raise HTTPException(409,'MUSIC_RIGHTS_INSUFFICIENT')
    path=Path(snapshot.get('path',''))
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=snapshot.get('sha256'):
        raise HTTPException(409,'MUSIC_FILE_CHANGED')
    return deepcopy(snapshot)


def new_render(db,project,items,music,restored_from=None):
    prior=db.scalar(select(RenderVersion).where(RenderVersion.project_id==project.id).order_by(RenderVersion.version.desc()))
    render=RenderVersion(project_id=project.id,project_version=project.current_version,version=prior.version+1 if prior else 1,
        timeline_snapshot=deepcopy(items),music_snapshot=deepcopy(music),restored_from_id=restored_from,
        timeline_hash=hashlib.sha256(json.dumps([items,music],sort_keys=True).encode()).hexdigest(),
        input_asset_ids=[i['asset_id'] for i in items],status='queued')
    db.add(render)
    # Approval belongs to one render; every edit starts unapproved.
    project.status='preview_review'
    db.flush()
    return render


class RestoreRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    version:int=Field(ge=1)


@router.post('/wedding-projects/{project_id}/renders/{render_id}/restore',status_code=202)
def restore_render(project_id:str,render_id:str,body:RestoreRequest,actor=Depends(get_wedding_actor),db=Depends(get_db)):
    require_project_owner(db,actor,project_id)
    project,_=scoped_project(db,actor,project_id)
    if project.current_version!=body.version:
        raise HTTPException(409,'PROJECT_VERSION_STALE')
    render=render_from_history(db,project,render_id)
    db.commit()
    from app.routers.wedding import _render_response
    return _render_response(render)


def pending_video_shots(db,story,job_id=None):
    from app.models import ShotCandidate
    result=[]
    for shot in sorted(story.shots,key=lambda s:s.shot_index):
        if shot.shot_type=='photo':
            continue
        candidates=list(db.scalars(select(ShotCandidate).where(ShotCandidate.shot_id==shot.id,
            ShotCandidate.review_result!='rejected')))
        if candidates and any(c.generation_job_id==job_id for c in candidates):
            result.append(shot)
            continue
        if any(valid_asset(db.get(WeddingAsset,c.asset_id),shot.project_id) for c in candidates):
            continue
        result.append(shot)
    return result


def video_reservation(shots):
    from decimal import ROUND_CEILING, Decimal
    cfg=get_settings()
    return sum(int(((cfg.wedding_r2v_fen_per_second if s.shot_type=='person_ai' else cfg.wedding_i2v_flash_fen_per_second)*Decimal(s.duration_ms)/1000).to_integral_value(rounding=ROUND_CEILING)) for s in shots if s.shot_type!='photo')


@router.post('/wedding-projects/{project_id}/photos/{asset_id}/analyze-quality')
def analyze_existing_photo(project_id:str,asset_id:str,actor=Depends(get_wedding_actor),db=Depends(get_db)):
    from app.wedding_service import private_storage
    from app.wedding_photo_quality import analyze_photo
    project,person=scoped_project(db,actor,project_id)
    asset=db.get(WeddingAsset,asset_id)
    if not valid_asset(asset,project.id) or asset.person_key!=person.person_key or asset.asset_type!=WeddingAssetType.PHOTO or asset.generated:
        raise HTTPException(404,'PHOTO_NOT_FOUND')
    key,sha=asset.object_key,asset.sha256
    db.commit()
    try:
        data=private_storage().read(key)
        quality=analyze_photo(data,demo=get_settings().wedding_media_mode=='demo')
    except (OSError, RuntimeError):
        raise HTTPException(409,'PHOTO_ANALYSIS_UNAVAILABLE') from None
    project,person=scoped_project(db,actor,project_id)
    db.refresh(asset)
    if not valid_asset(asset,project.id) or asset.object_key!=key or asset.sha256!=sha:
        raise HTTPException(409,'PHOTO_CHANGED')
    asset.quality=quality
    asset.quality_confirmed_at=None
    db.commit()
    return dict(asset_id=asset.id,quality=quality,confirmed_at=None)


def render_from_history(db,project,source_id,subtitles=None,music_track_id=None):
    source=db.get(RenderVersion,source_id)
    if source is None or source.project_id!=project.id:
        raise HTTPException(404,'RENDER_NOT_FOUND')
    if source.project_version!=project.current_version:
        raise HTTPException(409,'RENDER_SOURCE_VERSION_STALE')
    if source.invalidated_at or not source.timeline_snapshot:
        raise HTTPException(409,'RENDER_HISTORY_INVALID')
    validate_timeline(db,project,source.timeline_snapshot)
    items=deepcopy(source.timeline_snapshot)
    subtitles=subtitles or {}
    if set(subtitles)-{i['shot_id'] for i in items}:
        raise HTTPException(422,'SUBTITLE_SHOT_UNKNOWN')
    for item in items:
        if item['shot_id'] in subtitles:
            item['subtitle']=validate_subtitle(subtitles[item['shot_id']])
    music=render_music(project,music_track_id) if music_track_id else historical_music(source.music_snapshot)
    return new_render(db,project,items,music,source.id)
