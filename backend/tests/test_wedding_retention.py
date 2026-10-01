from datetime import timedelta
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from app.config import get_settings
from app.database import get_session_factory
from app.models import WeddingAsset, WeddingObjectCleanup, WeddingProject, utc_now
from app.wedding_cleanup import stage_output, cleanup_objects
from app.wedding_service import WeddingPrivateStorage
from tests.test_wedding_worker import _project_with_job


def test_staged_and_expired_physical_bytes_are_in_capacity(client):
    from app.wedding_retention import check_storage_capacity, STORAGE_LIMIT
    work = _project_with_job(client)
    factory = get_session_factory()
    with factory() as db:
        asset = db.get(WeddingAsset, work.frame_asset_id)
        asset.size_bytes = STORAGE_LIMIT - 4
        asset.expires_at = utc_now() - timedelta(days=1)
        db.commit()
    storage = WeddingPrivateStorage(get_settings())
    stage_output(factory, storage, work.project_id, 'first', b'1234', '.png', 'image/png')
    with pytest.raises(HTTPException, match='PROJECT_STORAGE_LIMIT'):
        stage_output(factory, storage, work.project_id, 'second', b'5', '.png', 'image/png')


def test_finished_writer_tombstone_prunes_but_unknown_writer_keeps_capacity(client):
    work = _project_with_job(client)
    factory = get_session_factory(); storage = WeddingPrivateStorage(get_settings())
    key = stage_output(factory, storage, work.project_id, 'finished', b'123', '.png', 'image/png')
    from app.wedding_cleanup import abandon_stage
    abandon_stage(factory, key)
    cleanup_objects(factory, storage, now=utc_now()+timedelta(hours=2))
    with factory() as db:
        assert db.get(WeddingObjectCleanup, key) is None


def test_expiry_revokes_access_before_failed_delete_then_retries(client):
    from app.wedding_retention import expire_assets
    from tests.conftest import login
    work = _project_with_job(client); factory = get_session_factory()
    with factory() as db:
        db.get(WeddingAsset,work.frame_asset_id).expires_at=utc_now()-timedelta(days=1)
        db.commit()
    class Failed:
        def delete(self,key): raise OSError('private')
    assert expire_assets(factory, Failed()) == 1
    with factory() as db:
        assert db.get(WeddingAsset,work.frame_asset_id) is None
        assert db.scalar(select(WeddingObjectCleanup)).error_code == 'OBJECT_DELETE_FAILED'
    headers={'Authorization':'Bearer '+login(client,'worker-owner@example.com')}
    assert client.get('/wedding-assets/'+work.frame_asset_id,headers=headers).status_code==404
    cleanup_objects(factory,WeddingPrivateStorage(get_settings()),now=utc_now()+timedelta(minutes=1))
    with factory() as db: assert db.scalar(select(WeddingObjectCleanup)) is None

from concurrent.futures import ThreadPoolExecutor
from app.models import RenderVersion
from app.wedding_retention import lock_project,touch_activity,STORAGE_LIMIT
from tests.conftest import login


def ready_assets(client):
    work=_project_with_job(client,with_render=True)
    factory=get_session_factory();storage=WeddingPrivateStorage(get_settings())
    ids=[]
    with factory() as db:
        render=db.get(RenderVersion,work.render_id);render.status='approved';render.owner_approved_at=utc_now()
        for kind in ('final_video','preview_video'):
            key=storage.put(work.project_id,b'0123456789','.mp4','video/mp4')
            asset=WeddingAsset(project_id=work.project_id,asset_type=kind,object_key=key,sha256='a'*64,mime='video/mp4',size_bytes=10)
            db.add(asset);db.flush();ids.append(asset.id)
        render.export_asset_id,render.preview_asset_id=ids
        db.commit()
    return work,{'Authorization':'Bearer '+login(client,'worker-owner@example.com')},ids


def test_actual_get_downloads_atomic_project_cap_and_post_does_not_count(client):
    work,headers,ids=ready_assets(client)
    assert all(client.post(f'/wedding-projects/{work.project_id}/export',headers=headers).status_code==200 for _ in range(12))
    with ThreadPoolExecutor(max_workers=5) as pool:
        codes=list(pool.map(lambda _:client.get('/wedding-assets/'+ids[0],headers=headers).status_code,range(15)))
    assert codes.count(200)==10 and codes.count(429)==5
    with get_session_factory()() as db:assert db.get(WeddingProject,work.project_id).final_download_count==10
    # New output under another render never resets project lifetime downloads.
    with get_session_factory()() as db:
        render=db.get(RenderVersion,work.render_id);render.version+=1;db.commit()
    assert client.get('/wedding-assets/'+ids[0],headers=headers).json()['detail']=='FINAL_DOWNLOAD_LIMIT'


def test_preview_both_gateways_charge_same_budget_and_expiry_is_immediate(client,monkeypatch):
    work,headers,ids=ready_assets(client)
    monkeypatch.setenv('WEDDING_PREVIEW_BANDWIDTH_BYTES','20');get_settings.cache_clear()
    assert client.get('/wedding-assets/'+ids[1],headers=headers).status_code==200
    assert client.get(f'/wedding-projects/{work.project_id}/preview',headers=headers).status_code==200
    assert client.get('/wedding-assets/'+ids[1],headers=headers).json()['detail']=='PREVIEW_BANDWIDTH_LIMIT'
    with get_session_factory()() as db:
        db.get(WeddingAsset,ids[0]).expires_at=utc_now()-timedelta(seconds=1);db.commit()
    assert client.get('/wedding-assets/'+ids[0],headers=headers).status_code==404


def test_upload_io_unchecked_connection_and_revoke_during_read(client):
    from app.wedding_service import private_storage
    work,headers,ids=ready_assets(client);factory=get_session_factory();real=WeddingPrivateStorage(get_settings())
    counts=[]
    class Storage:
        def read(self,key):
            counts.append(factory.kw['bind'].pool.checkedout())
            with factory() as db:
                from app.models import WeddingParticipant
                person=db.scalar(select(WeddingParticipant).where(WeddingParticipant.project_id==work.project_id))
                person.revoked_at=utc_now();person.consent_status='revoked';db.commit()
            return real.read(key)
    client.app.dependency_overrides[private_storage]=lambda:Storage()
    assert client.get('/wedding-assets/'+ids[0],headers=headers).status_code==404
    assert counts==[0]


def test_storage_concurrent_last_capacity_slot_and_delete_transfer(client):
    from app.wedding_retention import remove_asset
    work=_project_with_job(client);factory=get_session_factory();real=WeddingPrivateStorage(get_settings())
    with factory() as db:
        asset=db.get(WeddingAsset,work.frame_asset_id);asset.size_bytes=STORAGE_LIMIT-1;db.commit()
    def write(_):
        try:return stage_output(factory,real,work.project_id,'race',b'x','.png','image/png')
        except HTTPException:return None
    with ThreadPoolExecutor(max_workers=2) as pool:keys=list(pool.map(write,range(2)))
    assert sum(k is not None for k in keys)==1
    with factory() as db:
        project=lock_project(db,work.project_id);remove_asset(db,project,db.get(WeddingAsset,work.frame_asset_id));db.commit()
    with pytest.raises(HTTPException,match='PROJECT_STORAGE_LIMIT'):write(3) if False else stage_output(factory,real,work.project_id,'blocked',b'x','.png','image/png')


def test_delivery_extends_active_inputs_but_does_not_resurrect_expired(client):
    work=_project_with_job(client);factory=get_session_factory()
    with factory() as db:
        project=lock_project(db,work.project_id);asset=db.get(WeddingAsset,work.frame_asset_id)
        asset.expires_at=utc_now()+timedelta(hours=1);db.flush()
        deadline=touch_activity(db,project,delivered=True);db.commit()
        from app.wedding_service import _as_utc
        assert _as_utc(asset.expires_at)==deadline
        assert project.delivered_at is not None
        asset.expires_at=utc_now()-timedelta(seconds=1);db.commit()
        touch_activity(db,project);db.commit();assert _as_utc(asset.expires_at)<utc_now()


def test_finished_during_delete_requires_another_delete_before_capacity_release(client):
    work=_project_with_job(client);factory=get_session_factory();real=WeddingPrivateStorage(get_settings())
    key=f'private-wedding/{work.project_id}/late.png'
    with factory() as db:
        db.add(WeddingObjectCleanup(object_key=key,project_id=work.project_id,state='uploading',size_bytes=10));db.commit()
    class Storage:
        def delete(self,key):
            real.delete(key)
            real.put_at(key,b'late bytes','image/png')
            from app.wedding_cleanup import finish_writer
            finish_writer(factory,key)
    cleanup_objects(factory,Storage())
    with factory() as db:assert db.get(WeddingObjectCleanup,key).state=='pending'
    cleanup_objects(factory,real,now=utc_now()+timedelta(hours=2))
    with factory() as db:assert db.get(WeddingObjectCleanup,key) is None


def test_expired_reference_and_output_invalidate_render_history(client):
    from app.wedding_retention import expire_assets
    work,headers,ids=ready_assets(client);factory=get_session_factory();real=WeddingPrivateStorage(get_settings())
    with factory() as db:
        row=db.get(RenderVersion,work.render_id)
        row.input_asset_ids=[];row.timeline_snapshot=[{'primary_references':{'A':work.frame_asset_id}}]
        db.get(WeddingAsset,work.frame_asset_id).expires_at=utc_now()-timedelta(seconds=1)
        db.get(WeddingAsset,ids[1]).expires_at=utc_now()-timedelta(seconds=1)
        reserved=db.get(WeddingProject,work.project_id).budget_reserved_fen
        db.commit()
    assert expire_assets(factory,real)==2
    with factory() as db:
        row=db.get(RenderVersion,work.render_id)
        assert row.invalidated_at is not None and row.preview_asset_id is None
        assert db.get(WeddingProject,work.project_id).budget_reserved_fen==reserved


def test_upload_changed_consent_during_put_never_attaches(client):
    from app.wedding_service import private_storage
    from app.models import WeddingParticipant
    from PIL import Image
    from io import BytesIO
    work=_project_with_job(client);factory=get_session_factory();real=WeddingPrivateStorage(get_settings())
    headers={'Authorization':'Bearer '+login(client,'worker-owner@example.com')}
    class Storage:
        def put_at(self,*args):
            real.put_at(*args)
            with factory() as db:
                person=db.scalar(select(WeddingParticipant).where(WeddingParticipant.project_id==work.project_id,WeddingParticipant.person_key=='A'))
                person.confirmed_at=utc_now()+timedelta(seconds=1);db.commit()
        def delete(self,key):return real.delete(key)
    client.app.dependency_overrides[private_storage]=lambda:Storage()
    image=BytesIO();Image.new('RGB',(1024,1024),'pink').save(image,format='PNG')
    response=client.post('/wedding-projects/'+work.project_id+'/assets',headers=headers,data={'person_key':'A'},files={'file':('test.png',image.getvalue(),'image/png')})
    assert response.status_code==409 and response.json()['detail']=='CONSENT_CHANGED'
    with factory() as db:assert len(list(db.scalars(select(WeddingAsset).where(WeddingAsset.project_id==work.project_id))))==1


def test_candidate_or_source_gateway_cannot_bypass_preview_budget(client,monkeypatch):
    work=_project_with_job(client);headers={'Authorization':'Bearer '+login(client,'worker-owner@example.com')}
    monkeypatch.setenv('WEDDING_PREVIEW_BANDWIDTH_BYTES','1');get_settings.cache_clear()
    assert client.get('/wedding-assets/'+work.frame_asset_id,headers=headers).status_code==429


def test_delivered_deadline_stays_fixed_on_day29_reads_and_mutations(client,monkeypatch):
    from app.wedding_service import _as_utc
    from app.routers.wedding import _project_response
    work,headers,ids=ready_assets(client);factory=get_session_factory()
    delivered=utc_now()-timedelta(days=29);deadline=delivered+timedelta(days=30)
    with factory() as db:
        project=db.get(WeddingProject,work.project_id);project.delivered_at=delivered
        for asset in db.scalars(select(WeddingAsset).where(WeddingAsset.project_id==project.id)):asset.expires_at=deadline
        db.commit()
    assert client.get('/wedding-assets/'+ids[1],headers=headers).status_code==200
    from PIL import Image
    from io import BytesIO
    image=BytesIO();Image.new('RGB',(1024,1024),'pink').save(image,format='PNG')
    uploaded=client.post('/wedding-projects/'+work.project_id+'/assets',headers=headers,data={'person_key':'A'},files={'file':('test.png',image.getvalue(),'image/png')})
    assert uploaded.status_code==201,uploaded.text
    with factory() as db:
        assert _as_utc(db.get(WeddingAsset,uploaded.json()['id']).expires_at)==deadline
        project=lock_project(db,work.project_id);touch_activity(db,project);db.commit()
        assert _as_utc(db.get(WeddingAsset,ids[1]).expires_at)==deadline
        assert _as_utc(_project_response(project).retention_expires_at)==deadline
        project.delivered_at=utc_now()-timedelta(days=31)
        db.get(WeddingAsset,ids[1]).expires_at=utc_now()+timedelta(days=30);db.commit()
    assert client.get('/wedding-assets/'+ids[1],headers=headers).status_code==404


def test_undelivered_activity_refreshes_only_unexpired_assets(client):
    from app.wedding_service import _as_utc
    work=_project_with_job(client);factory=get_session_factory()
    with factory() as db:
        project=lock_project(db,work.project_id);asset=db.get(WeddingAsset,work.frame_asset_id)
        asset.expires_at=utc_now()+timedelta(hours=1);db.flush()
        deadline=touch_activity(db,project);db.commit()
        assert project.delivered_at is None and deadline>utc_now()+timedelta(days=29)
        assert _as_utc(asset.expires_at)==deadline
        asset.expires_at=utc_now()-timedelta(days=1);db.commit()
        old=asset.expires_at;touch_activity(db,project);db.commit();assert asset.expires_at==old


@pytest.mark.parametrize("expired", [False, True])
def test_owner_approval_records_delivery_and_project_deadline_drives_cleanup(client,expired):
    from app.models import WeddingParticipant,ParticipantReview
    from app.wedding_service import _as_utc
    from app.wedding_retention import expire_assets
    work,headers,ids=ready_assets(client);factory=get_session_factory()
    with factory() as db:
        row=db.get(RenderVersion,work.render_id);row.status='ready';row.owner_approved_at=None
        db.get(WeddingProject,work.project_id).status='preview_review'
        for person in db.scalars(select(WeddingParticipant).where(WeddingParticipant.project_id==work.project_id)):
            db.add(ParticipantReview(participant_id=person.id,project_id=work.project_id,person_key=person.person_key,
                project_version=1,render_version_id=row.id,asset_id=ids[1],result='approved',confirmed_at=utc_now()))
        assert db.get(WeddingProject,work.project_id).delivered_at is None
        if expired:db.get(WeddingAsset,ids[1]).expires_at=utc_now()-timedelta(seconds=1)
        db.commit()
    response=client.post(f'/wedding-projects/{work.project_id}/approve',headers=headers,params={'render_version_id':work.render_id})
    if expired:
        assert response.status_code==409 and response.json()['detail']=='RENDER_NOT_READY'
        with factory() as db:assert db.get(WeddingProject,work.project_id).delivered_at is None
        return
    assert response.status_code==200,response.text
    with factory() as db:
        project=db.get(WeddingProject,work.project_id)
        assert project.delivered_at is not None
        assert _as_utc(db.get(WeddingAsset,ids[0]).expires_at)==_as_utc(project.delivered_at)+timedelta(days=30)
        project.delivered_at=utc_now()-timedelta(days=31);db.commit()
    assert expire_assets(factory,WeddingPrivateStorage(get_settings()))==3


def test_activity_cannot_resurrect_project_expired_legacy_asset(client):
    from app.wedding_service import _as_utc
    work=_project_with_job(client);factory=get_session_factory()
    with factory() as db:
        project=lock_project(db,work.project_id);asset=db.get(WeddingAsset,work.frame_asset_id)
        project.last_activity_at=utc_now()-timedelta(days=31)
        asset.expires_at=utc_now()+timedelta(days=30);db.commit()
        touch_activity(db,project);db.commit()
        assert _as_utc(asset.expires_at)<utc_now()
