"""Parent-first fenced render leases, bounded recovery, immutable snapshot execution."""
from copy import deepcopy
from datetime import timedelta
import hashlib
from contextlib import contextmanager
from threading import Event, Thread
from uuid import uuid4
from sqlalchemy import select, update
from fastapi import HTTPException
from app.config import get_settings
from app.database import get_session_factory
from app.models import RenderVersion, WeddingAsset, WeddingProject, WeddingAuditEvent, WeddingObjectCleanup, utc_now
from app.wedding_service import _as_utc, validate_active_consents
from app.wedding_retention import lock_project, touch_activity, RETENTION
from app.wedding_cleanup import stage_output,attach_stage,abandon_stage,cleanup_objects
from app.wedding_media import ProviderUnavailable,RenderInput


class ClaimedRender(str):
    def __new__(cls,render_id,lease_token):
        obj=super().__new__(cls,render_id);obj.lease_token=lease_token;return obj


def lock_render(db,render_id):
    pid=db.scalar(select(RenderVersion.project_id).where(RenderVersion.id==str(render_id)))
    project=lock_project(db,pid) if pid else None
    render=db.scalar(select(RenderVersion).where(RenderVersion.id==str(render_id)).with_for_update().execution_options(populate_existing=True))
    return project,render


def recover_renders(factory):
    cutoff=utc_now()-timedelta(seconds=get_settings().wedding_render_lease_seconds)
    with factory() as db:
        ids=list(db.scalars(select(RenderVersion.id).where(RenderVersion.status=='processing',
            (RenderVersion.updated_at<cutoff)|(RenderVersion.updated_at.is_(None))).limit(20)))
    for rid in ids:
        with factory() as db:
            project,row=lock_render(db,rid)
            if row and row.status=='processing' and (row.updated_at is None or _as_utc(row.updated_at)<cutoff):
                row.status='failed' if row.attempts>=get_settings().wedding_render_max_attempts else 'queued'
                row.error_code='RENDER_RETRY_EXHAUSTED' if row.status=='failed' else 'RENDER_LEASE_EXPIRED'
                row.lease_token=None;db.commit()


def claim_next_render_version(session_factory=None):
    factory=session_factory or get_session_factory();recover_renders(factory)
    with factory() as db:
        ids=list(db.scalars(select(RenderVersion.id).where(RenderVersion.status=='queued').order_by(RenderVersion.created_at).limit(20)))
    for rid in ids:
        with factory() as db:
            project,row=lock_render(db,rid)
            if row is None or row.status!='queued':continue
            if project is None or project.status in ('cancelled','blocked') or row.invalidated_at:
                row.status='cancelled';db.commit();continue
            token=str(uuid4());row.lease_token=token;row.status='processing';row.attempts+=1
            row.started_at=row.updated_at=utc_now();row.error_code=None;db.commit()
            return ClaimedRender(rid,token)
    return None


def fence(db,project,row,token):
    if row is None or project is None:raise HTTPException(409,'RENDER_NOT_FOUND')
    if not token or row.lease_token!=token or row.status!='processing':raise HTTPException(409,'RENDER_LEASE_STALE')
    if project.status in ('cancelled','blocked') or row.invalidated_at:raise HTTPException(409,'RENDER_CANCELLED')
    if project.current_version!=row.project_version:raise HTTPException(409,'PROJECT_VERSION_STALE')
    validate_active_consents(db,project)
    if not row.timeline_snapshot:raise HTTPException(409,'RENDER_HISTORY_INVALID')
    from app.wedding_authoring import validate_timeline
    validate_timeline(db,project,row.timeline_snapshot)


def fail(factory,rid,token,code):
    from app.wedding_worker import WorkerResult
    with factory() as db:
        project,row=lock_render(db,rid)
        if row is None:return WorkerResult(str(rid),'missing','RENDER_NOT_FOUND')
        if row.lease_token!=token or row.status!='processing':return WorkerResult(row.id,str(row.status),'RENDER_LEASE_STALE')
        retryable=code in ('FFMPEG_TIMEOUT','OUTPUT_PERSISTENCE_FAILED','MEDIA_READ_FAILED')
        row.status='queued' if retryable and row.attempts<get_settings().wedding_render_max_attempts else 'failed'
        row.error_code=code;row.updated_at=utc_now();row.lease_token=None
        db.add(WeddingAuditEvent(project_id=row.project_id,actor_kind='worker',action='render.failed',target_type='render_version',target_id=row.id,project_version=row.project_version,metadata_json={'error_code':code}))
        db.commit();return WorkerResult(row.id,str(row.status),code)


@contextmanager
def heartbeat(factory,rid,token):
    stop=Event();errors=[]
    def beat():
        with factory() as db:
            project,row=lock_render(db,rid)
            if row is None or row.lease_token!=token or row.status!='processing' or row.invalidated_at or project is None or project.status in ('blocked','cancelled'):
                raise ProviderUnavailable('RENDER_LEASE_STALE','Render no longer owns its lease')
            validate_active_consents(db,project)
            row.updated_at=utc_now()
            from app.wedding_cleanup import UPLOAD_LEASE
            db.execute(update(WeddingObjectCleanup).where(
                WeddingObjectCleanup.operation_id=='render:'+rid+':'+token,
                WeddingObjectCleanup.state=='uploading',
                WeddingObjectCleanup.discard_requested.is_(False)
            ).values(cleanup_after=utc_now()+UPLOAD_LEASE))
            db.commit()
    def loop():
        while not stop.wait(max(1,get_settings().wedding_render_lease_seconds/4)):
            try:beat()
            except Exception as exc:errors.append(exc);return
    thread=Thread(target=loop,daemon=True);thread.start()
    def check():
        if errors:raise ProviderUnavailable('RENDER_LEASE_STALE','Render lease lost')
    try:yield check
    finally:stop.set();thread.join(timeout=5)


def process_claimed_render_version(factory,claimed,storage,renderer):
    from app.wedding_worker import WorkerResult
    rid=str(claimed);token=getattr(claimed,'lease_token',None);keys=[]
    if not token:return WorkerResult(rid,'failed','RENDER_LEASE_STALE')
    try:
        with factory() as db:
            project,row=lock_render(db,rid);fence(db,project,row,token)
            timeline=deepcopy(row.timeline_snapshot);music=deepcopy(row.music_snapshot)
            pid=project.id
            from app.wedding_render_media import MAX_INPUT
            if sum(db.get(WeddingAsset,i['asset_id']).size_bytes for i in {i['asset_id']:i for i in timeline}.values())>MAX_INPUT:
                raise ProviderUnavailable('RENDER_INPUT_LIMIT','Render inputs exceed memory limit')
            specs=[(db.get(WeddingAsset,i['asset_id']).object_key,db.get(WeddingAsset,i['asset_id']).mime,i) for i in timeline]
            demo=get_settings().wedding_media_mode=='demo' or any(db.get(WeddingAsset,i['asset_id']).provider=='local_demo' for i in timeline)
        with heartbeat(factory,rid,token) as check:
            inputs=[];cache={}
            for key,mime,item in specs:
                check()
                if key not in cache: cache[key]=storage.read(key)
                data=cache[key]
                if sum(len(value) for value in cache.values())>MAX_INPUT:raise ProviderUnavailable("RENDER_INPUT_LIMIT","Render inputs exceed memory limit")
                if hashlib.sha256(data).hexdigest()!=item['asset_sha256']:raise ProviderUnavailable('TIMELINE_ASSET_INVALID','Input bytes changed')
                inputs.append(RenderInput(data,mime,item['subtitle'],item['duration_ms']))
            # Historical embedded rights remain valid after catalog expiration. File I/O is outside DB.
            from app.wedding_authoring import historical_music
            historical_music(music)
            outputs=[]
            for resolution in ((1280,720),(1920,1080)):
                result=renderer.render(inputs,demo=demo,resolution=resolution,music_snapshot=music,heartbeat=check)
                check()
                key=stage_output(factory,storage,pid,'render:'+rid+':'+token,result.data,'.mp4','video/mp4')
                keys.append(key);outputs.append(result)
            with factory() as db:
                project,row=lock_render(db,rid);fence(db,project,row,token)
                # Successful work records activity only after every source passes the final retention fence.
                deadline=touch_activity(db,project)
                assets=[]
                for key,result,kind in zip(keys,outputs,('preview_video','final_video'),strict=True):
                    attach_stage(db,key)
                    asset=WeddingAsset(project_id=pid,asset_type=kind,object_key=key,sha256=hashlib.sha256(result.data).hexdigest(),mime='video/mp4',size_bytes=len(result.data),generated=True,provider='local_demo' if demo else 'ffmpeg',expires_at=deadline)
                    db.add(asset);assets.append(asset)
                db.flush();row.preview_asset_id=assets[0].id;row.export_asset_id=assets[1].id
                row.output_duration_ms=sum(i['duration_ms'] for i in timeline);row.status='ready';row.error_code=None;row.lease_token=None;row.updated_at=utc_now()
                db.commit();return WorkerResult(rid,'ready')
    except HTTPException as exc:return fail(factory,rid,token,str(exc.detail))
    except ProviderUnavailable as exc:return fail(factory,rid,token,exc.code)
    except OSError:return fail(factory,rid,token,'MEDIA_READ_FAILED')
    except Exception:return fail(factory,rid,token,'OUTPUT_PERSISTENCE_FAILED')
    finally:
        for key in keys:abandon_stage(factory,key)
        if keys:cleanup_objects(factory,storage,keys=keys)
