from dataclasses import replace
from datetime import timedelta
import json

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.database import get_session_factory, init_db
from app.models import WeddingProject, WeddingParticipant, StoryVersion, WeddingAsset, WeddingAssetType
from app.wedding_media import build_provider_bundle, StoryStructureRequest
from tests.conftest import login
from tests.test_wedding_worker import _project_with_job, _png


def setup_project(client, monkeypatch):
    monkeypatch.setenv('WEDDING_MEDIA_MODE', 'demo')
    get_settings.cache_clear()
    work = _project_with_job(client)
    headers = {'Authorization': 'Bearer ' + login(client, 'worker-owner@example.com')}
    with get_session_factory()() as db:
        project = db.get(WeddingProject, work.project_id)
        # Keep established consents, start from an editable empty creation project.
        for job in list(project.generation_jobs):
            from app.wedding_service import release_generation_budget
            release_generation_budget(db, job)
            db.delete(job)
        db.flush()
        for story in list(project.story_versions):
            db.delete(story)
        project.status = 'draft'
        project.frame_approved_version = None
        db.commit()
    return work.project_id, headers


def submit(client, project_id, headers, key='story-1', **kwargs):
    return client.post(f'/wedding-projects/{project_id}/story-jobs',
                       headers={**headers, 'Idempotency-Key': key},
                       json={'version': 1, 'story_text': 'We met in a library.', **kwargs})


def test_story_enqueue_is_async_idempotent_and_human_approved(client, monkeypatch):
    project, headers = setup_project(client, monkeypatch)
    response = submit(client, project, headers)
    assert response.status_code == 202, response.text
    assert response.json()['status'] == 'queued'
    assert submit(client, project, headers).json()['id'] == response.json()['id']
    assert submit(client, project, headers, story_text='Other text').status_code == 409
    from app.wedding_creation import process_next_creation_job
    result = process_next_creation_job()
    assert result.status == 'completed'
    story = client.get(f'/wedding-projects/{project}/storyboard', headers=headers).json()
    assert len(story['shots']) == 18
    assert story['status'] == 'draft' and story['owner_approved_at'] is None
    assert all(f['confirmed_at'] is None for f in story['facts'])
    jobs = client.get(f'/wedding-projects/{project}/creation-jobs', headers=headers).json()
    assert jobs[0]['demo'] is True


def test_demo_story_has_source_provenance_and_fixed_timeline(monkeypatch):
    monkeypatch.setenv('WEDDING_MEDIA_MODE', 'demo')
    get_settings.cache_clear()
    output = build_provider_bundle().story.structure(StoryStructureRequest('We met in a library.', (), 'key'))
    data = json.loads(output.data)
    assert len(data['shots']) == 18
    assert all(not fact['confirmed'] and fact['source_quote'] in 'We met in a library.' for fact in data['facts'])
    assert sum(s['duration_ms'] for s in data['shots']) == 90000


def test_account_budget_survives_project_deletion(client, monkeypatch):
    work = _project_with_job(client)
    headers = {'Authorization': 'Bearer ' + login(client, 'worker-owner@example.com')}
    from app.models import GenerationJob
    from app.wedding_service import settle_generation_budget
    with get_session_factory()() as db:
        job = db.get(GenerationJob, work.job_id)
        amount = job.reserved_amount_fen
        settle_generation_budget(db, job, amount)
        db.commit()
    assert client.delete(f'/wedding-projects/{work.project_id}', headers=headers).status_code == 204
    monkeypatch.setenv('WEDDING_ACCOUNT_BUDGET_LIMIT_FEN', str(amount))
    get_settings.cache_clear()
    # Same account cannot reset its cap by deleting the charged project.
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as error:
        _project_with_job(client)
    assert error.value.detail == 'ACCOUNT_BUDGET_LIMIT_EXCEEDED'


def test_story_does_not_overwrite_concurrent_draft(client, monkeypatch):
    project, headers = setup_project(client, monkeypatch)
    assert submit(client, project, headers).status_code == 202
    from app.wedding_creation import process_next_creation_job
    demo = build_provider_bundle()
    class EditingStory:
        def structure(self, request):
            with get_session_factory()() as db:
                db.add(StoryVersion(project_id=project, version=1, story_text='Manual newer draft', style='warm_documentary'))
                db.commit()
            return demo.story.structure(request)
    result = process_next_creation_job(providers=replace(demo, story=EditingStory()))
    assert result.error_code == 'DRAFT_CHANGED'
    with get_session_factory()() as db:
        assert db.scalar(select(StoryVersion).where(StoryVersion.project_id == project)).story_text == 'Manual newer draft'


@pytest.mark.parametrize('mutation', ['facts', 'total', 'duration', 'scope', 'subtitle', 'provenance'])
def test_creation_rejects_malformed_provider_output(client, monkeypatch, mutation):
    project, headers = setup_project(client, monkeypatch)
    assert submit(client, project, headers).status_code == 202
    from app.wedding_creation import process_next_creation_job
    demo = build_provider_bundle()
    class BadStory:
        def structure(self, request):
            output = demo.story.structure(request)
            body = json.loads(output.data)
            if mutation == 'facts': body['shots'][0]['fact_indexes'] = [999]
            if mutation == 'total': body['shots'].pop()
            if mutation == 'duration': body['shots'][0]['duration_ms'] = 4999
            if mutation == 'scope': body['shots'][0]['person_keys'] = ['A']
            if mutation == 'subtitle': body['shots'][0]['subtitle'] = 'a' * 81
            if mutation == 'provenance': body['facts'][0].pop('source')
            return replace(output, data=json.dumps(body).encode())
    result = process_next_creation_job(providers=replace(demo, story=BadStory()))
    assert result.error_code == 'PROVIDER_RESULT_INVALID'


def test_known_video_task_is_checkpointed_and_recovered_without_resubmit(client, monkeypatch):
    from app.wedding_worker import claim_next_generation_job, process_claimed_generation_job, _recover_stale_generation_jobs
    from app.wedding_media import ProviderBundle, ProviderUnavailable, _DemoProvider
    from app.wedding_service import WeddingPrivateStorage
    from app.models import GenerationJob, utc_now
    work = _project_with_job(client)
    factory = get_session_factory()
    class Video:
        def generate(self, request):
            assert request.checkpoint is not None
            if request.existing_task_id is None:
                request.checkpoint('accepted-task-123')
                raise ProviderUnavailable('TIMEOUT', 'Still running', task_id='accepted-task-123', submission_state='running', actual_cost_fen=100)
            assert request.existing_task_id == 'accepted-task-123'
            return replace(_DemoProvider().generate(request), task_id=request.existing_task_id)
    bundle = ProviderBundle(character_video=Video(), scene_video=Video())
    claim = claim_next_generation_job(factory)
    result = process_claimed_generation_job(factory, claim, bundle, WeddingPrivateStorage(get_settings()))
    assert result.status == 'processing'
    with factory() as db:
        db.get(GenerationJob, work.job_id).updated_at = utc_now() - timedelta(days=1)
        db.commit()
    assert work.job_id in _recover_stale_generation_jobs(factory)
    next_claim = claim_next_generation_job(factory)
    assert next_claim.lease_token != claim.lease_token
    result = process_claimed_generation_job(factory, next_claim, bundle, WeddingPrivateStorage(get_settings()))
    assert result.status == 'completed'


def test_unknown_video_dispatch_holds_reservation(client):
    from app.wedding_worker import claim_next_generation_job, process_claimed_generation_job
    from app.wedding_media import ProviderBundle, ProviderUnavailable
    from app.wedding_service import WeddingPrivateStorage
    from app.models import WeddingAccountUsage
    work = _project_with_job(client)
    factory = get_session_factory()
    class Video:
        def generate(self, request):
            raise ProviderUnavailable('UNKNOWN', 'Unknown', submission_state='unknown', actual_cost_fen=100)
    result = process_claimed_generation_job(factory, claim_next_generation_job(factory), ProviderBundle(character_video=Video()), WeddingPrivateStorage(get_settings()))
    assert result.status == 'needs_action'
    with factory() as db:
        project = db.get(WeddingProject, work.project_id)
        assert project.budget_reserved_fen > 0
        assert db.get(WeddingAccountUsage, project.owner_id).reserved_fen == project.budget_reserved_fen


def prepare_frames(client, monkeypatch):
    project, headers = setup_project(client, monkeypatch)
    from app.wedding_creation import process_next_creation_job
    assert submit(client, project, headers).status_code == 202
    assert process_next_creation_job().status == 'completed'
    story = client.post(f'/wedding-projects/{project}/storyboard/approve', headers=headers, json={'version':1}).json()
    from app.wedding_service import WeddingPrivateStorage
    storage = WeddingPrivateStorage(get_settings())
    with get_session_factory()() as db:
        ids = {}
        for key in ('A','B'):
            asset = WeddingAsset(project_id=project, person_key=key, asset_type=WeddingAssetType.PHOTO,
                object_key=storage.put(project, _png(), '.png', 'image/png'), sha256=key*64,
                mime='image/png', size_bytes=len(_png()), generated=False)
            db.add(asset); db.flush()
            ids[key] = asset.id
            participant = db.scalar(select(WeddingParticipant).where(WeddingParticipant.project_id == project, WeddingParticipant.person_key == key))
            participant.primary_photo_asset_id = asset.id
        db.commit()
    photos = [{'shot_id':s['id'], 'asset_id':ids['A']} for s in story['shots'] if s['shot_type']=='photo']
    return project, headers, story, photos, ids


def test_frames_generate_twelve_scoped_outputs_with_original_photos(client, monkeypatch):
    project, headers, story, photos, ids = prepare_frames(client, monkeypatch)
    from app.wedding_creation import process_next_creation_job
    demo = build_provider_bundle()
    calls = []
    by_id = {s['id']:s for s in story['shots']}
    class Frames:
        def generate(self, request):
            calls.append(request)
            assert [r[0] for r in request.references] == by_id[request.shot_id]['person_keys']
            return demo.frame.generate(request)
    response = client.post(f'/wedding-projects/{project}/frame-jobs', headers={**headers, 'Idempotency-Key':'frames'}, json={'version':1,'photos':photos})
    assert response.status_code == 202, response.text
    result = process_next_creation_job(providers=replace(demo, frame=Frames()))
    assert result.status == 'completed', result
    assert len(calls)==12
    current = client.get(f'/wedding-projects/{project}/storyboard',headers=headers).json()
    assert all(s['frame_asset_id'] for s in current['shots'])
    assert all(s['frame_asset_id']==ids['A'] for s in current['shots'] if s['shot_type']=='photo')
    assert current['shots'][0]['status']=='frame_pending'  # Human frame approval still required.


def test_revocation_cancels_queued_creation_and_releases_safe_reservation(client, monkeypatch):
    project, headers = setup_project(client, monkeypatch)
    response = submit(client,project,headers)
    assert response.status_code==202
    assert client.post(f'/wedding-projects/{project}/consent/revoke', headers=headers).status_code==200
    jobs=client.get(f'/wedding-projects/{project}/creation-jobs',headers=headers).json()
    assert jobs[0]['status']=='cancelled'


def test_bill_import_is_idempotent_and_survives_project_deletion(client, monkeypatch):
    from app.models import WeddingBillingReceipt, WeddingAccountUsage
    from app.wedding_accounting import reconcile_rows
    project, headers=setup_project(client, monkeypatch)
    with get_session_factory()() as db:
        p=db.get(WeddingProject,project)
        p.budget_spent_fen=20
        account=db.get(WeddingAccountUsage,p.owner_id)
        account.spent_fen=20
        owner=p.owner_id
        db.add(WeddingBillingReceipt(operation_key='bill-test', owner_id=owner, project_id=project, job_id='test-job', provider='ark', model='test', task_id='bill-task', state='succeeded', settled=True, estimated_fen=20, reserved_fen=100))
        db.commit()
    assert client.delete(f'/wedding-projects/{project}',headers=headers).status_code==204
    result=reconcile_rows(get_session_factory(),[{'task_id':'bill-task','cost_fen':31},{'task_id':'bill-task','cost_fen':31},{'task_id':'missing','cost_fen':1}])
    assert [r['status'] for r in result]==['applied','duplicate','unmatched']
    with get_session_factory()() as db:
        assert db.get(WeddingAccountUsage,owner).spent_fen==31


def test_migration_backfills_once_without_resetting_deleted_usage(client):
    from app.models import WeddingAccountUsage
    from app.database import get_engine
    from app.wedding_migrations import migrate_creation
    work=_project_with_job(client)
    with get_session_factory()() as db:
        project=db.get(WeddingProject,work.project_id)
        owner=project.owner_id
        db.delete(db.get(WeddingAccountUsage,owner)); db.commit()
    migrate_creation(get_engine()); migrate_creation(get_engine())
    with get_session_factory()() as db:
        account=db.get(WeddingAccountUsage,owner)
        assert account.reserved_fen>0
        account.spent_fen=123; db.commit()
    migrate_creation(get_engine())
    with get_session_factory()() as db:
        assert db.get(WeddingAccountUsage,owner).spent_fen==123


def test_deleted_video_result_settles_durable_account_once(client):
    from app.wedding_worker import claim_next_generation_job, process_claimed_generation_job
    from app.wedding_media import ProviderBundle, _DemoProvider
    from app.wedding_service import WeddingPrivateStorage
    from app.models import WeddingAccountUsage, WeddingBillingReceipt
    work=_project_with_job(client)
    headers={'Authorization':'Bearer '+login(client,'worker-owner@example.com')}
    with get_session_factory()() as db:
        owner=db.get(WeddingProject,work.project_id).owner_id
    class DeletingVideo:
        def generate(self, request):
            request.checkpoint('deleted-task')
            assert client.delete(f'/wedding-projects/{work.project_id}',headers=headers).status_code==204
            return replace(_DemoProvider().generate(request),actual_cost_fen=31,task_id='deleted-task')
    factory=get_session_factory()
    result=process_claimed_generation_job(factory,claim_next_generation_job(factory),ProviderBundle(character_video=DeletingVideo()),WeddingPrivateStorage(get_settings()))
    assert result.status=='missing'
    with factory() as db:
        account=db.get(WeddingAccountUsage,owner)
        assert account.reserved_fen==0
        assert account.spent_fen==31
        receipt=db.scalar(select(WeddingBillingReceipt).where(WeddingBillingReceipt.task_id=='deleted-task'))
        assert receipt.settled


def test_live_capabilities_report_adapter_availability(client,monkeypatch):
    headers={'Authorization':'Bearer '+login(client)}
    monkeypatch.setenv('WEDDING_MEDIA_MODE','live')
    monkeypatch.setenv('WEDDING_STORY_PROVIDER','ark')
    monkeypatch.setenv('WEDDING_STORY_API_KEY','mock-key')
    monkeypatch.setenv('WEDDING_STORY_INPUT_FEN_PER_MILLION_TOKENS','80')
    monkeypatch.setenv('WEDDING_STORY_OUTPUT_FEN_PER_MILLION_TOKENS','200')
    get_settings.cache_clear()
    response=client.get('/wedding-capabilities',headers=headers)
    assert response.status_code==200
    assert response.json()['mode']=='live'
    assert response.json()['providers']['story']['available']


def test_worker_connection_timeouts_preserve_isolated_database_options():
    from app.database import _connect_args
    args=_connect_args('postgresql+psycopg://test@localhost/test?options=-csearch_path%3DweddingScope')
    assert 'search_path=weddingScope' in args['options']
    assert args['connect_timeout'] <= 3


def test_late_story_after_revocation_is_charged_without_product_output(client,monkeypatch):
    project, headers=setup_project(client,monkeypatch)
    from app.models import WeddingAccountUsage, WeddingBillingReceipt
    from app.wedding_creation import process_next_creation_job
    assert submit(client,project,headers).status_code==202
    demo=build_provider_bundle()
    class RevokingStory:
        def structure(self,request):
            assert client.post(f'/wedding-projects/{project}/consent/revoke',headers=headers).status_code==200
            return replace(demo.story.structure(request),actual_cost_fen=7,task_id='late-story',usage={'tokens':1})
    result=process_next_creation_job(providers=replace(demo,story=RevokingStory()))
    assert result.status=='needs_action'
    with get_session_factory()() as db:
        assert db.scalar(select(StoryVersion).where(StoryVersion.project_id==project)) is None
        receipt=db.scalar(select(WeddingBillingReceipt).where(WeddingBillingReceipt.task_id=='late-story'))
        assert receipt.settled and receipt.estimated_fen==7
        assert db.get(WeddingAccountUsage,receipt.owner_id).spent_fen==7


def test_creation_lease_claim_is_exclusive_and_stale_unknown_never_repeats(client,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from app.wedding_creation import claim_creation_job
    from app.models import WeddingCreationJob, WeddingBillingReceipt,utc_now
    project,headers=setup_project(client,monkeypatch)
    job_id=submit(client,project,headers).json()['id']
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims=list(pool.map(lambda _:claim_creation_job(get_session_factory()),range(2)))
    assert len([c for c in claims if c])==1
    with get_session_factory()() as db:
        db.get(WeddingCreationJob,job_id).updated_at=utc_now()-timedelta(days=1)
        db.scalar(select(WeddingBillingReceipt).where(WeddingBillingReceipt.job_id==job_id)).state='dispatching'
        db.commit()
    assert claim_creation_job(get_session_factory()) is None
    with get_session_factory()() as db:
        assert db.get(WeddingCreationJob,job_id).status=='needs_action'


def enable_mock_live(monkeypatch):
    for key,value in {'WEDDING_MEDIA_MODE':'live','WEDDING_STORY_PROVIDER':'ark','WEDDING_STORY_API_KEY':'mock-key','WEDDING_STORY_INPUT_FEN_PER_MILLION_TOKENS':'80','WEDDING_STORY_OUTPUT_FEN_PER_MILLION_TOKENS':'200','WEDDING_FRAME_PROVIDER':'seedream','WEDDING_FRAME_API_KEY':'mock-key'}.items():
        monkeypatch.setenv(key,value)
    get_settings.cache_clear()


def test_mock_live_story_reserves_and_settles_usage_provenance(client,monkeypatch):
    from app.models import WeddingAccountUsage,WeddingBillingReceipt
    from app.wedding_creation import process_next_creation_job
    project,headers=setup_project(client,monkeypatch)
    demo=build_provider_bundle()
    enable_mock_live(monkeypatch)
    assert submit(client,project,headers).json()['detail']=='PROVIDER_CONSENT_REQUIRED'
    with get_session_factory()() as db:
        for p in db.scalars(select(WeddingParticipant).where(WeddingParticipant.project_id==project)):
            p.provider_list=['ark','seedream']
        db.commit()
    response=submit(client,project,headers)
    assert response.status_code==202,response.text
    with get_session_factory()() as db:
        p=db.get(WeddingProject,project)
        assert p.budget_reserved_fen==100
    class Story:
        def structure(self,request):
            return replace(demo.story.structure(request),provider='ark', model_version=get_settings().wedding_story_model, demo=False,actual_cost_fen=17,request_id='mock-request',usage={'prompt_tokens':50,'completion_tokens':20},price_version='test-price')
    assert process_next_creation_job(providers=replace(demo,story=Story())).status=='completed'
    with get_session_factory()() as db:
        p=db.get(WeddingProject,project)
        assert p.budget_reserved_fen==0 and p.budget_spent_fen==17
        r=db.scalar(select(WeddingBillingReceipt).where(WeddingBillingReceipt.job_id==response.json()['id']))
        assert r.usage['prompt_tokens']==50 and r.request_id=='mock-request'
        assert r.provenance['consent'] and r.provenance['input_hash']
        assert r.price_metadata['price_version']=='test-price'


@pytest.mark.parametrize('case', ['account','project','missing_key','version','disabled','owner'])
def test_creation_request_boundaries(client,monkeypatch,case):
    project,headers=setup_project(client,monkeypatch)
    if case=='account':
        enable_mock_live(monkeypatch)
        monkeypatch.setenv('WEDDING_ACCOUNT_BUDGET_LIMIT_FEN','1');get_settings.cache_clear()
    if case=='project':
        enable_mock_live(monkeypatch)
        with get_session_factory()() as db:
            db.get(WeddingProject,project).budget_limit_fen=2001;db.commit()
    if case in ('account','project'):
        with get_session_factory()() as db:
            for p in db.scalars(select(WeddingParticipant).where(WeddingParticipant.project_id==project)):
                p.provider_list=['ark']
            db.commit()
    if case=='disabled':
        monkeypatch.setenv('WEDDING_MEDIA_MODE','disabled');get_settings.cache_clear()
    if case=='owner': headers={'Authorization':'Bearer '+login(client,'other@example.com')}
    response=submit(client,project,headers,key='' if case=='missing_key' else 'key',**({'version':2} if case=='version' else {}))
    assert response.status_code==({'missing_key':422,'owner':404}.get(case,409))


@pytest.mark.parametrize('state', ['not_submitted','unknown','failed'])
def test_creation_failure_accounting(client,monkeypatch,state):
    from app.wedding_media import ProviderUnavailable
    from app.wedding_creation import process_next_creation_job
    from app.models import WeddingBillingReceipt
    project,headers=setup_project(client,monkeypatch)
    demo=build_provider_bundle()
    response=submit(client,project,headers)
    class Bad:
        def structure(self,request):
            raise ProviderUnavailable('TEST_ERROR','sanitized',submission_state=state)
    assert process_next_creation_job(providers=replace(demo,story=Bad())).error_code=='TEST_ERROR'
    with get_session_factory()() as db:
        receipt=db.scalar(select(WeddingBillingReceipt).where(WeddingBillingReceipt.job_id==response.json()['id']))
        assert receipt.settled is (state!='unknown')
    assert process_next_creation_job() is None


def test_primary_loader_requires_explicit_owned_person_and_omits_unrelated_people(client,monkeypatch):
    from app.wedding_references import selected_references
    from fastapi import HTTPException
    project,headers,story,photos,ids=prepare_frames(client,monkeypatch)
    with get_session_factory()() as db:
        p=db.get(WeddingProject,project)
        refs=selected_references(db,p,['A'],demo=False)
        assert len(refs)==1 and refs[0]['asset_id']==ids['A']
        assert selected_references(db,p,[],demo=False)==[]
        participant=db.scalar(select(WeddingParticipant).where(WeddingParticipant.project_id==project,WeddingParticipant.person_key=='A'))
        participant.primary_photo_asset_id=None;db.flush()
        with pytest.raises(HTTPException,match='PRIMARY_PHOTO_REQUIRED'):
            selected_references(db,p,['A'],demo=False)
        participant.primary_photo_asset_id=ids['B'];db.flush()
        with pytest.raises(HTTPException,match='PRIMARY_PHOTO_INVALID'):
            selected_references(db,p,['A'],demo=False)


def test_bill_mismatch_conflict_and_over_cap_reported_without_provider_calls(client,monkeypatch):
    from app.wedding_accounting import reconcile_rows
    from app.models import WeddingBillingReceipt
    project,headers=setup_project(client,monkeypatch)
    with get_session_factory()() as db:
        p=db.get(WeddingProject,project)
        db.add(WeddingBillingReceipt(operation_key='cap-bill',owner_id=p.owner_id,project_id=project,job_id='cap',provider='ark',model='m',task_id='cap-task',state='succeeded',settled=True,estimated_fen=0))
        db.commit()
    rows=[{'task_id':'cap-task','cost_fen':99999},{'task_id':'cap-task','cost_fen':1},{'task_id':'cap-task','cost_fen':True}]
    results=reconcile_rows(get_session_factory(),rows)
    assert results[0]['over_cap'] and results[0]['mismatch']
    assert results[1]['status']=='conflict' and results[2]['status']=='invalid'


def test_frame_creation_respects_project_file_cap_before_dispatch(client,monkeypatch):
    project,headers,story,photos,ids=prepare_frames(client,monkeypatch)
    with get_session_factory()() as db:
        db.get(WeddingAsset,ids['A']).size_bytes=2*1024**3
        db.commit()
    response=client.post(f'/wedding-projects/{project}/frame-jobs',headers={**headers,'Idempotency-Key':'cap'},json={'version':1,'photos':photos})
    assert response.status_code==409
    assert response.json()['detail']=='PROJECT_STORAGE_LIMIT'


def test_video_storage_io_does_not_hold_database_transaction(client):
    from app.wedding_worker import claim_next_generation_job,process_claimed_generation_job
    from app.wedding_service import WeddingPrivateStorage
    from app.wedding_media import ProviderBundle,_DemoProvider
    work=_project_with_job(client)
    factory=get_session_factory()
    real=WeddingPrivateStorage(get_settings())
    counts=[]
    class Storage:
        def read(self,*args):
            counts.append(factory.kw['bind'].pool.checkedout())
            return real.read(*args)
        def put(self,*args):
            counts.append(factory.kw['bind'].pool.checkedout())
            return real.put(*args)
        def delete(self,*args):
            return real.delete(*args)
    result=process_claimed_generation_job(factory,claim_next_generation_job(factory),ProviderBundle(character_video=_DemoProvider()),Storage())
    assert result.status=='completed'
    assert counts and all(count==0 for count in counts)


def test_queued_creation_provider_switch_cannot_reuse_old_consent(client,monkeypatch):
    from app.wedding_creation import process_next_creation_job
    project,headers=setup_project(client,monkeypatch)
    assert submit(client,project,headers).status_code==202
    demo=build_provider_bundle()
    enable_mock_live(monkeypatch)
    result=process_next_creation_job(providers=demo)
    assert result.error_code=='PROVIDER_CONFIGURATION_CHANGED'


def test_delete_before_checkpoint_preserves_task_for_authoritative_bill(client):
    from app.wedding_worker import claim_next_generation_job,process_claimed_generation_job
    from app.wedding_service import WeddingPrivateStorage
    from app.wedding_media import ProviderBundle,ProviderUnavailable
    from app.wedding_accounting import reconcile_rows
    from app.models import WeddingAccountUsage,WeddingBillingReceipt
    work=_project_with_job(client)
    headers={'Authorization':'Bearer '+login(client,'worker-owner@example.com')}
    class DeletingVideo:
        def generate(self,request):
            assert client.delete(f'/wedding-projects/{work.project_id}',headers=headers).status_code==204
            with pytest.raises(RuntimeError):
                request.checkpoint('accepted-after-delete')
            raise ProviderUnavailable('PROVIDER_CHECKPOINT_FAILED','Deleted',task_id='accepted-after-delete',submission_state='running')
    factory=get_session_factory()
    process_claimed_generation_job(factory,claim_next_generation_job(factory),ProviderBundle(character_video=DeletingVideo()),WeddingPrivateStorage(get_settings()))
    with factory() as db:
        receipt=db.scalar(select(WeddingBillingReceipt).where(WeddingBillingReceipt.task_id=='accepted-after-delete'))
        owner=receipt.owner_id
        assert receipt.state=='running' and not receipt.settled
        assert db.get(WeddingAccountUsage,owner).reserved_fen==1000
    result=reconcile_rows(factory,[{'task_id':'accepted-after-delete','cost_fen':45}])
    assert result[0]['status']=='applied'
    with factory() as db:
        account=db.get(WeddingAccountUsage,owner)
        assert account.reserved_fen==0 and account.spent_fen==45
        assert db.get(WeddingProject,work.project_id) is None



def test_frame_job_rejects_scene_portrait_scope_in_approved_manual_story(client,monkeypatch):
    from app.models import StoryboardShot
    project,headers,story,photos,ids=prepare_frames(client,monkeypatch)
    with get_session_factory()() as db:
        db.get(StoryboardShot,story['shots'][0]['id']).person_keys=['A']
        db.commit()
    response=client.post(f'/wedding-projects/{project}/frame-jobs',headers={**headers,'Idempotency-Key':'scope'},json={'version':1,'photos':photos})
    assert response.status_code==409 and response.json()['detail']=='STORYBOARD_SCOPE_INVALID'


def test_frame_provider_failure_retains_explicit_needs_action(client,monkeypatch):
    from app.wedding_creation import process_next_creation_job
    from app.wedding_media import ProviderUnavailable
    project,headers,story,photos,ids=prepare_frames(client,monkeypatch)
    response=client.post(f'/wedding-projects/{project}/frame-jobs',headers={**headers,'Idempotency-Key':'fail'},json={'version':1,'photos':photos})
    assert response.status_code==202
    demo=build_provider_bundle()
    class BadFrame:
        def generate(self,request):
            raise ProviderUnavailable('FRAME_FAILED','Safe',submission_state='failed')
    result=process_next_creation_job(providers=replace(demo,frame=BadFrame()))
    assert result.status=='needs_action' and result.error_code=='FRAME_FAILED'


def test_worker_database_retry_logs_no_private_exception(client,monkeypatch,capsys):
    import app.wedding_worker as worker
    from sqlalchemy.exc import OperationalError
    monkeypatch.setattr('sys.argv',['wedding-worker'])
    monkeypatch.setattr(worker,'init_db',lambda: (_ for _ in ()).throw(OperationalError('INSERT PRIVATE_STORY',{'story':'secret-story'},Exception('secret-detail'))))
    monkeypatch.setattr(worker.time,'sleep',lambda _: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt): worker.main()
    captured=capsys.readouterr()
    assert 'secret-story' not in captured.out and 'PRIVATE_STORY' not in captured.out and 'secret-detail' not in captured.out


def test_video_late_provider_consent_change_fences_output(client,monkeypatch):
    from app.wedding_worker import claim_next_generation_job,process_claimed_generation_job
    from app.wedding_service import WeddingPrivateStorage
    from app.wedding_media import ProviderBundle,_DemoProvider
    from app.models import ShotCandidate
    work=_project_with_job(client)
    monkeypatch.setenv('WEDDING_MEDIA_MODE','live')
    monkeypatch.setenv('WEDDING_CHARACTER_VIDEO_PROVIDER','wan_r2v')
    get_settings.cache_clear()
    storage=WeddingPrivateStorage(get_settings())
    with get_session_factory()() as db:
        for p in db.scalars(select(WeddingParticipant).where(WeddingParticipant.project_id==work.project_id)):
            p.provider_list=['wan_r2v']
            asset=WeddingAsset(project_id=work.project_id,person_key=p.person_key,asset_type=WeddingAssetType.PHOTO,object_key=storage.put(work.project_id,_png(),'.png','image/png'),mime='image/png',sha256='a'*64,size_bytes=len(_png()))
            db.add(asset);db.flush();p.primary_photo_asset_id=asset.id
        db.commit()
    class Video:
        def generate(self,request):
            assert [r[0] for r in request.primary_references]==['A']
            with get_session_factory()() as db:
                p=db.scalar(select(WeddingParticipant).where(WeddingParticipant.project_id==work.project_id,WeddingParticipant.person_key=='A'))
                p.provider_list=['another-provider'];db.commit()
            return replace(_DemoProvider().generate(request),actual_cost_fen=25)
    factory=get_session_factory()
    result=process_claimed_generation_job(factory,claim_next_generation_job(factory),ProviderBundle(character_video=Video()),storage)
    with factory() as db:
        assert db.scalar(select(ShotCandidate.id).where(ShotCandidate.generation_job_id==work.job_id)) is None
        assert db.get(WeddingProject,work.project_id).budget_spent_fen==25
    assert result.status=='cancelled'


def test_bill_cli_import_and_duplicate_are_end_to_end_local(client,monkeypatch,tmp_path):
    import subprocess,sys
    from app.models import WeddingBillingReceipt,WeddingAccountUsage
    project,headers=setup_project(client,monkeypatch)
    with get_session_factory()() as db:
        owner=db.get(WeddingProject,project).owner_id
        db.add(WeddingBillingReceipt(operation_key='cli',owner_id=owner,project_id=project,job_id='cli',provider='ark',model='m',task_id='cli-task',state='succeeded',settled=True,estimated_fen=0))
        db.commit()
    path=tmp_path/'bills.json'
    path.write_text(json.dumps([{'task_id':'cli-task','cost_fen':31}]),encoding='utf-8')
    outputs=[]
    for _ in range(2):
        result=subprocess.run([sys.executable,'-m','app.wedding_accounting',str(path)],capture_output=True,text=True,timeout=15)
        assert result.returncode==0,result.stderr
        outputs.append(json.loads(result.stdout)[0]['status'])
    assert outputs==['applied','duplicate']
    with get_session_factory()() as db:
        assert db.get(WeddingAccountUsage,owner).spent_fen==31


def enqueue_frames(client,project,headers,photos,key='repair-frames'):
    return client.post(f'/wedding-projects/{project}/frame-jobs',headers={**headers,'Idempotency-Key':key},json={'version':1,'photos':photos})


def test_frame_rebuild_after_approval_is_rejected_without_reusing_approval(client,monkeypatch):
    from app.wedding_creation import process_next_creation_job
    project,headers,story,photos,ids=prepare_frames(client,monkeypatch)
    assert enqueue_frames(client,project,headers,photos).status_code==202
    assert process_next_creation_job().status=='completed'
    story=client.get(f'/wedding-projects/{project}/storyboard',headers=headers).json()
    assignments=[{'shot_id':s['id'],'asset_id':s['frame_asset_id']} for s in story['shots']]
    assert client.post(f'/wedding-projects/{project}/frames/approve',headers=headers,json={'version':1,'frames':assignments}).status_code==200
    response=enqueue_frames(client,project,headers,photos,key='rebuild')
    assert response.status_code==409 and response.json()['detail']=='FRAME_REBUILD_REQUIRES_RESET'


@pytest.mark.parametrize('during_call',[False,True])
def test_frame_job_fences_manual_frame_replacement(client,monkeypatch,during_call):
    from app.wedding_creation import process_next_creation_job
    from app.models import StoryboardShot
    project,headers,story,photos,ids=prepare_frames(client,monkeypatch)
    assert enqueue_frames(client,project,headers,photos).status_code==202
    demo=build_provider_bundle()
    def edit():
        with get_session_factory()() as db:
            db.get(StoryboardShot,story['shots'][0]['id']).frame_asset_id=ids['A']
            db.commit()
    class Frame:
        def generate(self,request):
            edit()
            return demo.frame.generate(request)
    if not during_call:edit()
    result=process_next_creation_job(providers=replace(demo,frame=Frame()))
    assert result.status=='needs_action' and result.error_code=='FRAMES_CHANGED'
    with get_session_factory()() as db:
        assert db.get(StoryboardShot,story['shots'][0]['id']).frame_asset_id==ids['A']


@pytest.mark.parametrize('processing',[False,True])
def test_manual_frame_approval_blocked_while_creation_is_queued(client,monkeypatch,processing):
    project,headers,story,photos,ids=prepare_frames(client,monkeypatch)
    assert enqueue_frames(client,project,headers,photos).status_code==202
    if processing:
        from app.wedding_creation import claim_creation_job
        assert claim_creation_job(get_session_factory())
    assignments=[{'shot_id':s['id'],'asset_id':ids['B'] if s['person_keys']==['B'] else ids['A']} for s in story['shots']]
    response=client.post(f'/wedding-projects/{project}/frames/approve',headers=headers,json={'version':1,'frames':assignments})
    assert response.status_code==409 and response.json()['detail']=='FRAME_CREATION_ACTIVE'


@pytest.mark.parametrize('kind',['story','frame'])
def test_deleted_creation_callback_refreshes_receipt_after_account_lock(client,monkeypatch,kind):
    import app.wedding_creation as creation
    from app.wedding_accounting import reconcile_rows
    from app.models import WeddingBillingReceipt,WeddingAccountUsage
    from app.wedding_media import StructuredOutput
    project,headers=setup_project(client,monkeypatch)
    factory=get_session_factory()
    with factory() as db:
        p=db.get(WeddingProject,project);owner=p.owner_id
        db.get(WeddingAccountUsage,owner).reserved_fen=100
        receipt=WeddingBillingReceipt(operation_key='race:'+kind,owner_id=owner,project_id=project,job_id=kind,provider='ark',model='m',task_id='race-'+kind,state='running',reserved_fen=100)
        db.add(receipt);db.commit();receipt_id=receipt.id
    assert client.delete(f'/wedding-projects/{project}',headers=headers).status_code==204
    original=creation.lock_account
    def bill_wins(db,owner_id):
        assert reconcile_rows(factory,[{'task_id':'race-'+kind,'cost_fen':31}])[0]['status']=='applied'
        return original(db,owner_id)
    monkeypatch.setattr(creation,'lock_account',bill_wins)
    with factory() as db:
        stale=db.get(WeddingBillingReceipt,receipt_id)
        creation.settle_receipt(db,stale,StructuredOutput(b'{}','ark','m',17,task_id='race-'+kind),'succeeded')
        db.commit()
    with factory() as db:
        account=db.get(WeddingAccountUsage,owner)
        assert account.reserved_fen==0 and account.spent_fen==31
        assert db.get(WeddingBillingReceipt,receipt_id).billed_fen==31

@pytest.mark.parametrize('callback',['result','checkpoint','envelope'])
def test_deleted_video_callback_preserves_bill_winning_account_lock(client,monkeypatch,callback):
    import app.wedding_accounting as accounting
    from app.wedding_video_operations import record_result,callbacks
    from app.models import WeddingBillingReceipt,WeddingAccountUsage
    from app.wedding_media import StructuredOutput
    project,headers=setup_project(client,monkeypatch)
    factory=get_session_factory()
    with factory() as db:
        owner=db.get(WeddingProject,project).owner_id
        db.get(WeddingAccountUsage,owner).reserved_fen=100
        db.add_all([
            WeddingBillingReceipt(operation_key='reservation:video-race',owner_id=owner,project_id=project,job_id='video-race',provider='budget_envelope',model='m',reserved_fen=100),
            WeddingBillingReceipt(operation_key='video-race:shot',owner_id=owner,project_id=project,job_id='video-race',provider='wan',model='m',task_id='video-race-task',state='running')])
        db.commit()
    assert client.delete(f'/wedding-projects/{project}',headers=headers).status_code==204
    original=accounting.lock_account
    entered=[]
    def bill_wins(db,owner_id):
        if not entered:
            entered.append(True)
            assert accounting.reconcile_rows(factory,[{'task_id':'video-race-task','cost_fen':31}])[0]['status']=='applied'
        return original(db,owner_id)
    monkeypatch.setattr(accounting,'lock_account',bill_wins)
    if callback=='result':
        record_result(factory,'video-race','shot',StructuredOutput(b'{}','wan','m',17,task_id='video-race-task'),'succeeded')
    elif callback=='checkpoint':
        with pytest.raises(RuntimeError,match='WORKER_LEASE_STALE'):
            callbacks(factory,'video-race','shot','old-token')[0]('video-race-task')
    else:
        with factory() as db:
            stale=db.scalar(select(WeddingBillingReceipt).where(WeddingBillingReceipt.operation_key=='video-race:shot'))
            accounting.settle_deleted_video(db,stale)
            db.commit()
    with factory() as db:
        account=db.get(WeddingAccountUsage,owner)
        assert (account.reserved_fen,account.spent_fen)==(0,31)
        receipt=db.scalar(select(WeddingBillingReceipt).where(WeddingBillingReceipt.operation_key=='video-race:shot'))
        assert receipt.state=='billed' and receipt.billed_fen==31 and receipt.settled


@pytest.mark.parametrize('project_version,status,expected',[(0,'queued',202),(1,'ready',202),(1,'queued',409)])
def test_frame_rebuild_considers_only_active_current_version_render(client,monkeypatch,project_version,status,expected):
    from app.models import RenderVersion
    project,headers,story,photos,ids=prepare_frames(client,monkeypatch)
    with get_session_factory()() as db:
        db.add(RenderVersion(project_id=project,project_version=project_version,version=1,status=status,timeline_hash='synthetic'))
        db.commit()
    response=enqueue_frames(client,project,headers,photos)
    assert response.status_code==expected,response.text
    if expected==409:assert response.json()['detail']=='FRAME_REBUILD_DOWNSTREAM_ACTIVE'


def test_pre_migration_frame_job_without_fingerprint_is_safely_fenced(client,monkeypatch):
    from app.models import WeddingCreationJob
    from app.wedding_creation import process_next_creation_job
    project,headers,story,photos,ids=prepare_frames(client,monkeypatch)
    job=enqueue_frames(client,project,headers,photos).json()
    with get_session_factory()() as db:
        row=db.get(WeddingCreationJob,job['id'])
        row.snapshot={k:v for k,v in row.snapshot.items() if k!='frame_fingerprint'}
        db.commit()
    result=process_next_creation_job()
    assert result.status=='needs_action' and result.error_code=='FRAMES_CHANGED'
