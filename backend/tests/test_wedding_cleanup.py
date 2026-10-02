from app.config import get_settings
from app.database import get_session_factory
from app.wedding_service import WeddingPrivateStorage, private_storage
from tests.test_wedding_creation import prepare_frames, enqueue_frames
from tests.test_wedding_worker import _project_with_job
from tests.conftest import login


def test_delete_remote_io_has_no_connection_and_failure_removes_access(client,monkeypatch):
    work=_project_with_job(client)
    headers={'Authorization':'Bearer '+login(client,'worker-owner@example.com')}
    factory=get_session_factory()
    counts=[]
    class FailingStorage:
        def delete(self,key):
            counts.append(factory.kw['bind'].pool.checkedout())
            raise OSError('synthetic S3 unavailable')
    client.app.dependency_overrides[private_storage]=lambda:FailingStorage()
    response=client.delete(f'/wedding-projects/{work.project_id}',headers=headers)
    assert response.status_code==204
    assert counts and all(n==0 for n in counts)
    assert client.get(f'/wedding-projects/{work.project_id}',headers=headers).status_code==404
    assert client.get(f'/wedding-assets/{work.frame_asset_id}',headers=headers).status_code==404
    from app.models import WeddingObjectCleanup
    from sqlalchemy import select
    with factory() as db:
        rows=list(db.scalars(select(WeddingObjectCleanup)))
        assert rows and all(r.error_code=="OBJECT_DELETE_FAILED" for r in rows)
        keys=[r.object_key for r in rows]
    from app.wedding_cleanup import cleanup_objects
    real=WeddingPrivateStorage(get_settings())
    assert cleanup_objects(factory,real,now=utc_now()+timedelta(minutes=1))==len(keys)
    for key in keys:
        with pytest.raises(FileNotFoundError):real.read(key)


import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import OperationalError
from app.models import WeddingAsset,WeddingObjectCleanup,utc_now
from datetime import timedelta


@pytest.mark.parametrize('failure',['insert','commit','writer_commit','connection'])
def test_frame_upload_db_failure_leaves_recoverable_ownership(client,monkeypatch,failure):
    from app.wedding_creation import process_next_creation_job
    project,headers,story,photos,ids=prepare_frames(client,monkeypatch)
    assert enqueue_frames(client,project,headers,photos).status_code==202
    factory=get_session_factory();engine=factory.kw['bind']
    real=WeddingPrivateStorage(get_settings())
    uploaded=[];armed=[False];raised=[False];attaching=[False]
    class Storage:
        def put(self,*args):
            key=real.put(*args);uploaded.append(key);armed[0]=True;return key
        def put_at(self,key,*args):
            real.put_at(key,*args);uploaded.append(key);armed[0]=True
        def read(self,key):return real.read(key)
        def delete(self,key):raise OSError('delete temporarily failed')
    def fail_sql(conn,cursor,statement,parameters,context,executemany):
        if armed[0] and statement.startswith('INSERT INTO wedding_assets'):
            attaching[0]=True
            if not raised[0] and failure=='insert':
                raised[0]=True;raise OperationalError('synthetic insert',{},Exception('DB unavailable'))
    def fail_commit(conn):
        target = (failure=='commit' and attaching[0]) or (failure=='writer_commit' and not attaching[0])
        if armed[0] and not raised[0] and target:
            raised[0]=True;raise OperationalError('synthetic commit',{},Exception('DB unavailable'))
    def fail_checkout(*args):
        if armed[0] and not raised[0] and failure=='connection':
            raised[0]=True;raise OperationalError('synthetic checkout',{},Exception('DB unavailable'))
    event.listen(engine,'before_cursor_execute',fail_sql);event.listen(engine,'commit',fail_commit);event.listen(engine,'checkout',fail_checkout)
    try:
        if failure in ('connection','writer_commit'):
            # Writer-finished checkpoint is now the first post-upload DB access.
            # Creation preserves the stage and reports needs_action when that fails.
            result=process_next_creation_job(storage=Storage())
            assert result.status=='needs_action' and result.error_code=='PROVIDER_RESULT_INVALID'
        else:
            with pytest.raises(OperationalError):process_next_creation_job(storage=Storage())
    finally:
        event.remove(engine,'before_cursor_execute',fail_sql);event.remove(engine,'commit',fail_commit);event.remove(engine,'checkout',fail_checkout)
    assert uploaded and raised[0]
    assert attaching[0] == (failure in ('insert','commit'))
    with factory() as db:
        assert db.scalar(select(WeddingAsset).where(WeddingAsset.object_key==uploaded[0])) is None
        stage=db.get(WeddingObjectCleanup,uploaded[0])
        assert stage is not None,'Uploaded object must have durable cleanup ownership after DB failure'
    from app.wedding_cleanup import cleanup_objects
    cleanup_objects(factory,real,now=utc_now()+timedelta(minutes=20))
    with pytest.raises(FileNotFoundError):real.read(uploaded[0])


def test_successful_frames_transfer_stage_ownership_atomically(client,monkeypatch):
    from app.wedding_creation import process_next_creation_job
    project,headers,story,photos,ids=prepare_frames(client,monkeypatch)
    assert enqueue_frames(client,project,headers,photos).status_code==202
    assert process_next_creation_job().status=='completed'
    with get_session_factory()() as db:
        assert not list(db.scalars(select(WeddingObjectCleanup)))
        assert len(list(db.scalars(select(WeddingAsset).where(WeddingAsset.generated==True))))==12


def test_delete_during_upload_fences_attach_and_late_write_is_recoverable(client,monkeypatch):
    from app.wedding_cleanup import stage_output,attach_stage,cleanup_objects
    from fastapi import HTTPException
    work=_project_with_job(client)
    headers={'Authorization':'Bearer '+login(client,'worker-owner@example.com')}
    factory=get_session_factory();real=WeddingPrivateStorage(get_settings())
    class Storage:
        def put_at(self,key,data,mime):
            assert factory.kw['bind'].pool.checkedout()==0
            assert client.delete(f'/wedding-projects/{work.project_id}',headers=headers).status_code==204
            real.put_at(key,data,mime)
    key=stage_output(factory,Storage(),work.project_id,'late-upload',b'delayed bytes','.png','image/png')
    with factory() as db:
        row=db.get(WeddingObjectCleanup,key)
        assert row.discard_requested
        with pytest.raises(HTTPException,match='OUTPUT_STAGE_EXPIRED'):attach_stage(db,key)
    future=utc_now()+timedelta(minutes=20)
    assert cleanup_objects(factory,real,now=future)>=1
    with pytest.raises(FileNotFoundError):real.read(key)
    # A separate unknown writer (no acknowledged completion) must retain its tombstone.
    with factory() as db:
        db.add(WeddingObjectCleanup(object_key=key,project_id=work.project_id,state='deleted',size_bytes=15,cleanup_after=future))
        db.commit()
    # A writer that outlives even the upload lease remains tracked by tombstone.
    real.put_at(key,b'extremely delayed','image/png')
    cleanup_objects(factory,real,now=future+timedelta(hours=2))
    with pytest.raises(FileNotFoundError):real.read(key)


def test_failed_upload_keeps_cleanup_record_when_abandon_database_is_down(client,monkeypatch):
    from app.wedding_cleanup import stage_output,cleanup_objects
    work=_project_with_job(client);factory=get_session_factory();real=WeddingPrivateStorage(get_settings())
    uploaded=[]
    class Storage:
        def put_at(self,key,data,mime):
            real.put_at(key,data,mime);uploaded.append(key)
            raise OSError('ambiguous S3 write failure')
    calls=[0]
    def flaky_factory():
        calls[0]+=1
        if calls[0]>1:raise RuntimeError('synthetic DB unavailable')
        return factory()
    with pytest.raises(OSError):stage_output(flaky_factory,Storage(),work.project_id,'ambiguous',b'bytes','.png','image/png')
    with factory() as db:assert db.get(WeddingObjectCleanup,uploaded[0]).state=='uploading'
    cleanup_objects(factory,real,now=utc_now()+timedelta(minutes=20))
    with pytest.raises(FileNotFoundError):real.read(uploaded[0])


def test_cleanup_never_deletes_asset_owned_object(client):
    from app.wedding_cleanup import cleanup_objects
    work=_project_with_job(client);factory=get_session_factory();real=WeddingPrivateStorage(get_settings())
    with factory() as db:
        asset=db.get(WeddingAsset,work.frame_asset_id);key=asset.object_key
        db.add(WeddingObjectCleanup(object_key=key,project_id=work.project_id,state='pending'))
        db.commit()
    before=real.read(key)
    assert cleanup_objects(factory,real)==0
    assert real.read(key)==before
    with factory() as db:assert db.get(WeddingObjectCleanup,key) is None


def test_expired_stage_fences_worker_without_losing_charge_or_cleanup(client,monkeypatch):
    from app.wedding_creation import process_next_creation_job
    from app.models import WeddingCreationJob
    project,headers,story,photos,ids=prepare_frames(client,monkeypatch)
    job=enqueue_frames(client,project,headers,photos).json()
    factory=get_session_factory();real=WeddingPrivateStorage(get_settings())
    class Storage:
        def read(self,key):return real.read(key)
        def put_at(self,key,*args):
            real.put_at(key,*args)
            with factory() as db:
                db.get(WeddingObjectCleanup,key).cleanup_after=utc_now()-timedelta(seconds=1)
                db.commit()
        def delete(self,key):real.delete(key)
    result=process_next_creation_job(storage=Storage())
    assert result.status=='needs_action' and result.error_code=='OUTPUT_STAGE_EXPIRED'
    with factory() as db:assert db.get(WeddingCreationJob,job['id']).status=='needs_action'
