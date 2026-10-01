from datetime import timedelta
from threading import Event,Thread
import time
import pytest
from sqlalchemy import select,event
from app.config import get_settings
from app.database import get_session_factory
from app.models import RenderVersion,WeddingAsset,WeddingObjectCleanup,WeddingProject,WeddingParticipant,utc_now
from app.wedding_service import WeddingPrivateStorage
from app.wedding_media import RenderedVideo,ProviderUnavailable
from app.wedding_render_worker import claim_next_render_version,process_claimed_render_version,recover_renders,heartbeat
from tests.test_wedding_authoring import rendered_story


class UnitRenderer:
    """Orchestration test dependency only; real production renderer validates actual media."""
    def render(self,*args,**kwargs):return RenderedVideo(b'unit-render-result',90000,True)


def queued(client,monkeypatch):
    project,headers,_,_=rendered_story(client,monkeypatch)
    row=client.post(f'/wedding-projects/{project}/render',headers=headers,json={'version':1}).json()
    return project,headers,row['id']


def test_stale_attempt_cannot_complete_newer_claim_and_recovery_is_bounded(client,monkeypatch):
    pid,headers,rid=queued(client,monkeypatch);factory=get_session_factory();storage=WeddingPrivateStorage(get_settings())
    first=claim_next_render_version(factory)
    for attempt in range(3):
        with factory() as db:
            row=db.get(RenderVersion,rid);row.updated_at=utc_now()-timedelta(days=1);db.commit()
        recover_renders(factory)
        if attempt<2:
            current=claim_next_render_version(factory)
            assert current.lease_token!=first.lease_token
            result=process_claimed_render_version(factory,first,storage,UnitRenderer())
            assert result.error_code=='RENDER_LEASE_STALE'
    with factory() as db:
        row=db.get(RenderVersion,rid);assert row.status=='failed' and row.attempts==3 and row.error_code=='RENDER_RETRY_EXHAUSTED'
        assert row.preview_asset_id is None


@pytest.mark.parametrize("already_delivered", [False, True])
def test_render_io_outside_transactions_ready_and_delivery_retention(client,monkeypatch,already_delivered):
    pid,headers,rid=queued(client,monkeypatch);factory=get_session_factory();real=WeddingPrivateStorage(get_settings());counts=[]
    delivered=utc_now()-timedelta(days=29) if already_delivered else None
    with factory() as db:
        db.get(WeddingProject,pid).delivered_at=delivered;db.commit()
    class Storage:
        def read(self,key):counts.append(factory.kw['bind'].pool.checkedout());return real.read(key)
        def put_at(self,*args):counts.append(factory.kw['bind'].pool.checkedout());return real.put_at(*args)
        def delete(self,key):counts.append(factory.kw['bind'].pool.checkedout());return real.delete(key)
    class Renderer(UnitRenderer):
        def render(self,*args,**kwargs):counts.append(factory.kw['bind'].pool.checkedout());return super().render(*args,**kwargs)
    result=process_claimed_render_version(factory,claim_next_render_version(factory),Storage(),Renderer())
    assert result.status=='ready',result
    assert counts and all(c==0 for c in counts)
    with factory() as db:
        row=db.get(RenderVersion,rid);assert row.lease_token is None and row.attempts==1
        from app.wedding_service import _as_utc
        project=db.get(WeddingProject,pid)
        assert (_as_utc(project.delivered_at) if project.delivered_at else None)==delivered
        if delivered:assert _as_utc(db.get(WeddingAsset,row.preview_asset_id).expires_at)==delivered+timedelta(days=30)
        assert db.get(WeddingAsset,row.preview_asset_id).expires_at==db.get(WeddingAsset,row.export_asset_id).expires_at
        assert db.scalar(select(WeddingObjectCleanup)) is None


@pytest.mark.parametrize('change',['version','revoke','invalidate','delete','expire'])
def test_late_render_result_fenced_after_project_change(client,monkeypatch,change):
    pid,headers,rid=queued(client,monkeypatch);factory=get_session_factory();real=WeddingPrivateStorage(get_settings())
    class Renderer(UnitRenderer):
        def render(self,*args,**kwargs):
            if change=='delete':client.delete('/wedding-projects/'+pid,headers=headers)
            else:
                with factory() as db:
                    project=db.get(WeddingProject,pid);row=db.get(RenderVersion,rid)
                    if change=='version':project.current_version+=1
                    if change=='revoke':
                        person=db.scalar(select(WeddingParticipant).where(WeddingParticipant.project_id==pid));person.consent_status='revoked';person.revoked_at=utc_now()
                    if change=='invalidate':row.invalidated_at=utc_now()
                    if change=='expire':db.get(WeddingAsset,row.input_asset_ids[0]).expires_at=utc_now()-timedelta(seconds=1)
                    db.commit()
            return super().render(*args,**kwargs)
    result=process_claimed_render_version(factory,claim_next_render_version(factory),real,Renderer())
    assert result.status!='ready'
    with factory() as db:
        assert not list(db.scalars(select(WeddingAsset).where(WeddingAsset.project_id==pid,WeddingAsset.asset_type.in_(['preview_video','final_video']))))


def test_render_heartbeat_refreshes_lease_without_renderer_connection(client,monkeypatch):
    pid,headers,rid=queued(client,monkeypatch);factory=get_session_factory()
    monkeypatch.setenv('WEDDING_RENDER_LEASE_SECONDS','10');get_settings.cache_clear()
    claim=claim_next_render_version(factory)
    with factory() as db:before=db.get(RenderVersion,rid).updated_at
    with heartbeat(factory,rid,claim.lease_token) as check:
        time.sleep(2.7);check()
        with factory() as db:assert db.get(RenderVersion,rid).updated_at>before
    with factory() as db:
        db.get(RenderVersion,rid).status='cancelled';db.commit()
    with heartbeat(factory,rid,claim.lease_token) as check:
        time.sleep(2.7)
        with pytest.raises(ProviderUnavailable,match='RENDER_LEASE_STALE'):check()


def test_renderer_transient_failures_retry_then_stop(client,monkeypatch):
    pid,headers,rid=queued(client,monkeypatch);factory=get_session_factory()
    class Failed:
        def render(self,*args,**kwargs):raise ProviderUnavailable('FFMPEG_TIMEOUT','test timeout')
    for expected in ('queued','queued','failed'):
        result=process_claimed_render_version(factory,claim_next_render_version(factory),WeddingPrivateStorage(get_settings()),Failed())
        assert result.status==expected
    assert claim_next_render_version(factory) is None


def test_render_hash_mutation_fail_closed(client,monkeypatch):
    pid,headers,rid=queued(client,monkeypatch);factory=get_session_factory();storage=WeddingPrivateStorage(get_settings())
    with factory() as db:
        row=db.get(RenderVersion,rid);key=db.get(WeddingAsset,row.input_asset_ids[0]).object_key
    storage.put_at(key,b'changed bytes','image/png')
    result=process_claimed_render_version(factory,claim_next_render_version(factory),storage,UnitRenderer())
    assert result.error_code=='TIMELINE_ASSET_INVALID'


def test_render_insert_failure_keeps_all_staged_ownership_until_delete_retry(client,monkeypatch):
    from sqlalchemy.exc import OperationalError
    from app.wedding_cleanup import cleanup_objects
    pid,headers,rid=queued(client,monkeypatch);factory=get_session_factory();real=WeddingPrivateStorage(get_settings())
    engine=factory.kw['bind'];armed=[True]
    class Storage:
        def read(self,key):return real.read(key)
        def put_at(self,*args):return real.put_at(*args)
        def delete(self,key):raise OSError('transient object failure')
    def fail_insert(conn,cursor,sql,params,context,executemany):
        if armed[0] and sql.startswith('INSERT INTO wedding_assets'):
            armed[0]=False;raise OperationalError('synthetic',{},Exception('db failure'))
    event.listen(engine,'before_cursor_execute',fail_insert)
    try:result=process_claimed_render_version(factory,claim_next_render_version(factory),Storage(),UnitRenderer())
    finally:event.remove(engine,'before_cursor_execute',fail_insert)
    assert result.status=='queued' and result.error_code=='OUTPUT_PERSISTENCE_FAILED'
    with factory() as db:
        rows=list(db.scalars(select(WeddingObjectCleanup).where(WeddingObjectCleanup.project_id==pid)))
        assert len(rows)==2 and all(r.size_bytes==len(b'unit-render-result') for r in rows)
        assert db.get(RenderVersion,rid).preview_asset_id is None
    cleanup_objects(factory,real,now=utc_now()+timedelta(minutes=1))
    with factory() as db:assert db.scalar(select(WeddingObjectCleanup)) is None


def test_render_heartbeat_protects_preview_stage_during_second_encode(client,monkeypatch):
    from app.wedding_cleanup import stage_output
    pid,headers,rid=queued(client,monkeypatch);factory=get_session_factory()
    monkeypatch.setenv('WEDDING_RENDER_LEASE_SECONDS','10');get_settings.cache_clear()
    claim=claim_next_render_version(factory)
    key=stage_output(factory,WeddingPrivateStorage(get_settings()),pid,'render:'+rid+':'+claim.lease_token,b'preview','.mp4','video/mp4')
    with factory() as db:db.get(WeddingObjectCleanup,key).cleanup_after=utc_now()+timedelta(seconds=5);db.commit()
    with heartbeat(factory,rid,claim.lease_token):time.sleep(2.7)
    from app.wedding_service import _as_utc
    with factory() as db:assert _as_utc(db.get(WeddingObjectCleanup,key).cleanup_after)>utc_now()+timedelta(minutes=14)
