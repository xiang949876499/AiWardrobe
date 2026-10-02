from dataclasses import replace
from datetime import timedelta
import pytest
from sqlalchemy import select
from app.database import get_session_factory
from app.models import WeddingAsset, WeddingProject, StoryVersion, StoryboardShot, ParticipantReview, WeddingParticipant, utc_now
from app.wedding_creation import process_next_creation_job
from app.wedding_media import build_provider_bundle, ProviderUnavailable
from tests.test_wedding_creation import prepare_frames, enqueue_frames, setup_project, submit
from tests.test_wedding_authoring import rendered_story
from tests.test_wedding_photo_flow import photo_flow, review
from app.wedding_worker import process_next_generation_job


def frames(client, monkeypatch):
    project, headers, _, photos, ids = prepare_frames(client, monkeypatch)
    assert enqueue_frames(client, project, headers, photos).status_code == 202
    assert process_next_creation_job().status == 'completed'
    board = client.get(f'/wedding-projects/{project}/storyboard', headers=headers).json()
    return project, headers, board, ids


@pytest.mark.parametrize('source', ['A', 'B', 'generated_person'])
def test_scene_rejects_known_portrait_public_assignment(client, monkeypatch, source):
    project, headers, board, ids = frames(client, monkeypatch)
    scene = next(s for s in board['shots'] if s['shot_type'] == 'scene_ai')
    portrait = ids.get(source) or next(s['frame_asset_id'] for s in board['shots'] if s['shot_type'] == 'person_ai')
    assignments = [{'shot_id':s['id'], 'asset_id':portrait if s['id']==scene['id'] else s['frame_asset_id']} for s in board['shots']]
    result = client.post(f'/wedding-projects/{project}/frames/approve', headers=headers, json={'version':1, 'frames':assignments})
    assert result.status_code == 422, result.text
    assert result.json()['detail'] == 'FRAME_ASSET_PERSON_SCOPE_INVALID'


@pytest.mark.parametrize('stage', ['generating', 'preview_review', 'ready_to_export', 'delivered'])
def test_delete_referenced_asset_from_every_stage(client, monkeypatch, stage):
    project, headers, _, ids = rendered_story(client, monkeypatch)
    with get_session_factory()() as db:
        db.get(WeddingProject, project).status = stage
        db.commit()
    result = client.delete(f'/wedding-projects/{project}/assets/{ids["A"]}', headers=headers)
    assert result.status_code == 204, result.text
    with get_session_factory()() as db:
        assert db.get(WeddingAsset, ids['A']) is None
        assert db.get(WeddingAsset, ids['B']) is not None


def test_unknown_creation_blocks_fresh_key(client, monkeypatch):
    project, headers = setup_project(client, monkeypatch)
    assert submit(client, project, headers).status_code == 202
    class Unknown:
        def structure(self, request):
            raise ProviderUnavailable('UNKNOWN', 'Unknown', submission_state='unknown')
    assert process_next_creation_job(providers=replace(build_provider_bundle(), story=Unknown())).status == 'needs_action'
    retry = submit(client, project, headers, key='fresh-browser-key')
    assert retry.status_code == 409, retry.text
    assert retry.json()['detail'] == 'CREATION_OUTCOME_UNRESOLVED'


def test_live_manual_all_photo_story_cannot_be_approved(client, monkeypatch):
    project, headers = setup_project(client, monkeypatch)
    body = {'version':1,'story_text':'A real story','facts':[{'source_field':'story','text':'A real story'}],
        'shots':[{'shot_index':i+1,'shot_type':'photo','duration_ms':5000,'person_keys':['A'],'fact_indexes':[0],
                  'description':'Original photograph','subtitle':'','creative_scene':False} for i in range(18)]}
    assert client.post(f'/wedding-projects/{project}/storyboard',headers=headers,json=body).status_code == 201
    monkeypatch.setenv('WEDDING_MEDIA_MODE','live')
    from app.config import get_settings
    get_settings.cache_clear()
    result = client.post(f'/wedding-projects/{project}/storyboard/approve',headers=headers,json={'version':1})
    assert result.status_code == 409, result.text
    assert result.json()['detail'] == 'STANDARD_COMPOSITION_INVALID'


def ready(client, monkeypatch):
    base, actors, ids, board, assignments = photo_flow(client, monkeypatch)
    for shot in board['shots']:
        for key in shot['person_keys']:
            assert review(client, actors[key], shot, shot['frame_asset_id']).status_code == 200
    return base, actors, ids, board, assignments


def enqueue_video(client, base, owner, key, shot=None):
    quote = client.get(base+'/quote',headers=owner,params={'shot_id':shot['id']} if shot else {})
    assert quote.status_code == 200, quote.text
    return client.post(base+(f'/shots/{shot["id"]}/regenerate' if shot else '/generation-jobs'),
        headers={**owner,'Idempotency-Key':key},json={'version':1,'quote_id':quote.json()['id']})


@pytest.mark.parametrize('fence', ['rejection','expiry'])
def test_queued_dispatch_rechecks_exact_frame(client, monkeypatch, fence):
    base, actors, _, board, _ = ready(client, monkeypatch)
    shot = next(s for s in board['shots'] if s['shot_type'] == ('person_ai' if fence=='rejection' else 'scene_ai'))
    assert enqueue_video(client,base,actors['A'],'dispatch-fence',shot).status_code == 202
    if fence == 'rejection':
        assert review(client,actors[shot['person_keys'][0]],shot,shot['frame_asset_id'],'rejected').status_code == 200
    else:
        with get_session_factory()() as db:
            db.get(WeddingAsset,shot['frame_asset_id']).expires_at = utc_now()-timedelta(seconds=1)
            db.commit()
    calls = []
    class Spy:
        def generate(self, request):
            calls.append(request)
            return build_provider_bundle().character_video.generate(request)
    process_next_generation_job(providers=replace(build_provider_bundle(),character_video=Spy(),scene_video=Spy()))
    assert calls == []


def test_participant_multilink_asset_listing_is_existential(client,monkeypatch):
    base, actors, ids, board, _ = photo_flow(client,monkeypatch)
    visible = client.get(base+'/assets',headers=actors['B'])
    assert visible.status_code == 200, visible.text
    assert ids['B'] in {a['id'] for a in visible.json()}
    assert ids['A'] not in {a['id'] for a in visible.json()}
    assert client.get('/wedding-assets/'+ids['A'],headers=actors['B']).status_code == 404


def test_fourth_generation_attempt_is_durable_after_candidate_deletion(client,monkeypatch):
    base, actors, _, board, _ = ready(client,monkeypatch)
    shot = next(s for s in board['shots'] if s['shot_type']=='scene_ai')
    for i in range(3):
        assert enqueue_video(client,base,actors['A'],f'limited-attempt-{i}',shot).status_code == 202
        assert process_next_generation_job().status == 'completed'
    candidates = client.get(base+'/candidates',headers=actors['A']).json()
    for candidate in candidates:
        assert client.delete(base+'/assets/'+candidate['asset_id'],headers=actors['A']).status_code == 204
    result = enqueue_video(client,base,actors['A'],'limited-attempt-4',shot)
    assert result.status_code == 409, result.text
    assert result.json()['detail'] == 'SHOT_ATTEMPT_LIMIT_REACHED'


def test_cancelled_known_task_resumes_only_get_after_restart(client,monkeypatch):
    from app.models import GenerationJob, WeddingBillingReceipt, ShotCandidate
    base, actors, _, board, _ = ready(client,monkeypatch)
    shot = next(s for s in board['shots'] if s['shot_type']=='scene_ai')
    job = enqueue_video(client,base,actors['A'],'cancelled-known-task',shot).json()
    calls = []
    class Running:
        def generate(self, request):
            calls.append(request.existing_task_id)
            if not request.existing_task_id:
                request.checkpoint('accepted-task')
                raise ProviderUnavailable('PENDING','Pending',task_id='accepted-task',submission_state='running',actual_cost_fen=100)
            assert request.reconcile_only
            return replace(build_provider_bundle().scene_video.generate(request),task_id='accepted-task',actual_cost_fen=100)
    bundle = replace(build_provider_bundle(),scene_video=Running())
    assert process_next_generation_job(providers=bundle).status == 'processing'
    assert client.post(base+f'/generation-jobs/{job["id"]}/cancel',headers=actors['A']).status_code == 200
    for _ in range(2):
        with get_session_factory()() as db:
            db.get(GenerationJob,job['id']).updated_at = utc_now()-timedelta(days=1)
            db.commit()
        process_next_generation_job(providers=bundle)
    assert calls == [None,'accepted-task']
    with get_session_factory()() as db:
        assert db.get(GenerationJob,job['id']).status == 'cancelled'
        assert db.get(WeddingProject,job['project_id']).budget_reserved_fen == 0
        assert db.scalar(select(ShotCandidate.id).where(ShotCandidate.generation_job_id==job['id'])) is None
        assert all(r.settled for r in db.scalars(select(WeddingBillingReceipt).where(WeddingBillingReceipt.job_id==job['id'])))


@pytest.mark.parametrize('change',['fact','scene','subtitle'])
def test_revision_preserves_unrelated_selection_and_immutable_history(client,monkeypatch,change):
    base,actors,_,board,_ = ready(client,monkeypatch)
    assert enqueue_video(client,base,actors['A'],'revision-all-videos').status_code == 202
    assert process_next_generation_job().status == 'completed'
    candidates = client.get(base+'/candidates',headers=actors['A']).json()
    for candidate in candidates:
        result=client.post(base+f'/shots/{candidate["shot_id"]}/select-candidate',headers=actors['A'],
            json={'project_version':1,'candidate_id':candidate['id']})
        assert result.status_code == 200,result.text
    before=client.get(base+'/storyboard',headers=actors['A']).json()
    facts=before['facts']
    body={'version':1,'story_text':before['story_text'],
        'facts':[{'source_field':f['source_field'],'text':f['text']} for f in facts],
        'shots':[{**{k:s[k] for k in ('shot_index','shot_type','duration_ms','person_keys','description','subtitle','creative_scene')},
                  'fact_indexes':[i for i,f in enumerate(facts) if f['id'] in s['fact_ids']]} for s in before['shots']]}
    if change=='scene':
        body['shots'][0]['description']='A corrected scene with a quiet lake'
        affected={before['shots'][0]['id']}
    elif change=='subtitle':
        body['shots'][0]['subtitle']='New displayed subtitle'
        affected=set()
    else:
        body['facts'][0]['text']='We met in 2020, not 2019.'
        affected={s['id'] for s in before['shots'] if facts[0]['id'] in s['fact_ids']}
    result=client.post(base+'/storyboard/revisions',headers={**actors['A'],'If-Match':before['draft_fingerprint']},json=body)
    assert result.status_code==201,result.text
    revised=result.json()
    assert revised['version']==2 and revised['status']=='draft'
    for old,new in zip(before['shots'],revised['shots']):
        if old['id'] in affected:
            assert new['selected_candidate_id'] is None and new['frame_asset_id'] is None
        else:
            assert new['selected_candidate_id']==old['selected_candidate_id']
            assert new['frame_asset_id']==old['frame_asset_id']
            assert new['generation_attempts']==old['generation_attempts']
    with get_session_factory()() as db:
        old=db.get(StoryVersion,before['id'])
        assert old.status=='approved'
        assert old.facts[0].text==facts[0]['text']
        assert old.shots[0].description==before['shots'][0]['description']
    if change=='scene':
        approved=client.post(base+'/storyboard/approve',headers=actors['A'],json={'version':2})
        assert approved.status_code==200,approved.text
        queued=client.post(base+'/frame-jobs',headers={**actors['A'],'Idempotency-Key':'revision-one-frame'},json={'version':2,
            'photos':[{'shot_id':s['id'],'asset_id':s['frame_asset_id']} for s in revised['shots'] if s['shot_type']=='photo']})
        assert queued.status_code==202 and queued.json()['total_operations']==1,queued.text
        assert process_next_creation_job().status=='completed'
        framed=client.get(base+'/storyboard',headers=actors['A']).json()
        approval=client.post(base+'/frames/approve',headers=actors['A'],json={'version':2,'frames':[
            {'shot_id':s['id'],'asset_id':s['frame_asset_id']} for s in framed['shots']]})
        assert approval.status_code==200,approval.text
        quote=client.get(base+'/quote',headers=actors['A'])
        assert quote.status_code==200,quote.text
        queued=client.post(base+'/generation-jobs',headers={**actors['A'],'Idempotency-Key':'revision-one-video'},json={'version':2,'quote_id':quote.json()['id']})
        assert queued.status_code==202 and queued.json()['total_shots']==1,queued.text
        assert process_next_generation_job().status=='completed'
        after=client.get(base+'/storyboard',headers=actors['A']).json()
        for old,new in zip(before['shots'],after['shots']):
            if old['id'] not in affected:
                assert old['selected_candidate_id']==new['selected_candidate_id']
                assert old['frame_asset_id']==new['frame_asset_id']
                assert old['generation_attempts']==new['generation_attempts']



@pytest.mark.parametrize('kind',['person_ai','scene_ai'])
def test_changed_frame_invalidates_only_derived_shot_and_render(client,monkeypatch,kind):
    base,actors,ids,board,assignments=ready(client,monkeypatch)
    assert enqueue_video(client,base,actors['A'],'frame-change-videos').status_code==202
    assert process_next_generation_job().status=='completed'
    candidates=client.get(base+'/candidates',headers=actors['A']).json()
    for candidate in candidates:
        assert client.post(base+f'/shots/{candidate["shot_id"]}/select-candidate',headers=actors['A'],
            json={'project_version':1,'candidate_id':candidate['id']}).status_code==200
        shot=next(s for s in board['shots'] if s['id']==candidate['shot_id'])
        for key in shot['person_keys']:
            assert review(client,actors[key],shot,candidate['asset_id']).status_code==200
    rendered=client.post(base+'/render',headers=actors['A'],json={'version':1})
    assert rendered.status_code==202,rendered.text
    before=client.get(base+'/storyboard',headers=actors['A']).json()
    same=client.post(base+'/frames/approve',headers=actors['A'],json={'version':1,'frames':assignments})
    assert same.status_code==200 and same.json()['shots']==before['shots']
    shot=next(s for s in before['shots'] if s['shot_type']==kind)
    replacement=ids['A'] if kind=='person_ai' else next(s['frame_asset_id'] for s in before['shots'] if s['shot_type']=='scene_ai' and s['id']!=shot['id'])
    changed=client.post(base+'/frames/approve',headers=actors['A'],json={'version':1,'frames':[
        {**a,'asset_id':replacement} if a['shot_id']==shot['id'] else a for a in assignments]})
    assert changed.status_code==200,changed.text
    for old,new in zip(before['shots'],changed.json()['shots']):
        if old['id']==shot['id']:
            assert new['selected_candidate_id'] is None and new['identity_reviews']==[]
        else:
            assert new==old
    assert client.get(base+'/renders/'+rendered.json()['id'],headers=actors['A']).json()['invalidated_at']
    old_candidate=next(c for c in candidates if c['shot_id']==shot['id'])
    assert client.post(base+f'/shots/{shot["id"]}/select-candidate',headers=actors['A'],
        json={'project_version':1,'candidate_id':old_candidate['id']}).status_code==409


def test_api_selected_pending_candidate_satisfies_cli_exact_review_contract(client,monkeypatch,tmp_path):
    from pathlib import Path
    import subprocess
    from scripts.wedding_acceptance import selected_video
    from app.wedding_media import MediaOutput
    tools = Path(__file__).resolve().parents[2]/'output/wedding-tools/ffmpeg/ffmpeg-9.0.2-essentials_build/bin'
    clip=tmp_path/'synthetic.mp4'
    result=subprocess.run([str(tools/'ffmpeg.exe'),'-v','error','-y','-f','lavfi','-i','color=c=navy:s=1280x720:r=30:d=5',
        '-c:v','libx264','-threads','2','-pix_fmt','yuv420p',str(clip)],capture_output=True)
    assert result.returncode==0
    monkeypatch.setenv('WEDDING_FFMPEG_PATH',str(tools/'ffmpeg.exe'))
    monkeypatch.setenv('WEDDING_FFPROBE_PATH',str(tools/'ffprobe.exe'))
    from app.config import get_settings
    get_settings.cache_clear()
    base,actors,_,board,_=ready(client,monkeypatch)
    shot=next(s for s in board['shots'] if s['shot_type']=='person_ai' and s['person_keys']==['A','B'])
    assert enqueue_video(client,base,actors['A'],'cli-public-selection',shot).status_code==202
    class Synthetic:
        def generate(self, request):
            return MediaOutput(clip.read_bytes(),'video/mp4','wan_r2v','wan2.7-r2v-2026-06-12',100)
    assert process_next_generation_job(providers=replace(build_provider_bundle(),character_video=Synthetic())).status=='completed'
    candidates=client.get(base+'/candidates',headers=actors['A']).json()
    candidate=candidates[0]
    assert candidate['review_result']=='pending'
    for key in shot['person_keys']:
        assert review(client,actors[key],shot,candidate['asset_id']).status_code==200
    selected=client.post(base+f'/shots/{shot["id"]}/select-candidate',headers=actors['A'],json={'project_version':1,'candidate_id':candidate['id']})
    assert selected.status_code==200,selected.text
    current=next(s for s in selected.json()['shots'] if s['id']==shot['id'])
    assert selected_video(current,candidates)
    assert review(client,actors['B'],shot,candidate['asset_id'],'rejected').status_code==200
    rejected=next(s for s in client.get(base+'/storyboard',headers=actors['A']).json()['shots'] if s['id']==shot['id'])
    assert not selected_video(rejected,candidates)


def test_unknown_creation_requires_operator_terminal_reconciliation_before_retry(client,monkeypatch):
    from app.models import WeddingBillingReceipt
    from app.wedding_accounting import reconcile_rows
    project,headers=setup_project(client,monkeypatch)
    assert submit(client,project,headers).status_code==202
    class Unknown:
        def structure(self,request):
            raise ProviderUnavailable('UNKNOWN','Unknown',submission_state='unknown')
    process_next_creation_job(providers=replace(build_provider_bundle(),story=Unknown()))
    with get_session_factory()() as db:
        operation=db.scalar(select(WeddingBillingReceipt.operation_key).where(WeddingBillingReceipt.project_id==project,WeddingBillingReceipt.state=='unknown'))
    assert reconcile_rows(get_session_factory(),[{'operation_key':operation,'cost_fen':0,'terminal_state':'not_submitted','evidence_id':'local-provider-audit-1'}])[0]['status']=='applied'
    assert submit(client,project,headers,key='verified-fresh-attempt').status_code==202


def test_concurrent_last_attempt_reserves_only_one_slot(client,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from app.models import GenerationJob
    base,actors,_,board,_=ready(client,monkeypatch)
    shot=next(s for s in board['shots'] if s['shot_type']=='scene_ai')
    for i in range(2):
        assert enqueue_video(client,base,actors['A'],f'concurrent-prior-{i}',shot).status_code==202
        assert process_next_generation_job().status=='completed'
    quotes=[client.get(base+'/quote',headers=actors['A'],params={'shot_id':shot['id']}).json()['id'] for _ in range(2)]
    def enqueue(i):
        return client.post(base+f'/shots/{shot["id"]}/regenerate',headers={**actors['A'],'Idempotency-Key':f'concurrent-final-{i}'},
            json={'version':1,'quote_id':quotes[i]}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses=list(pool.map(enqueue,range(2)))
    assert sorted(statuses)==[202,409]
    with get_session_factory()() as db:
        assert db.get(StoryboardShot,shot['id']).generation_attempts==3


@pytest.mark.parametrize('fallback_count,expected',[ (2,202),(3,409)])
def test_live_manual_motion_floor_uses_public_selected_media(client,monkeypatch,tmp_path,fallback_count,expected):
    import subprocess
    from pathlib import Path
    from app.wedding_media import MediaOutput
    from app.config import get_settings
    from tests.test_wedding_photo_flow import _png
    project,headers=setup_project(client,monkeypatch)
    base=f'/wedding-projects/{project}'
    photo=client.post(base+'/assets',headers=headers,data={'person_key':'A'},files={'file':('a.png',_png(),'image/png')}).json()['id']
    assert client.put(base+'/participants/A/primary-photo',headers=headers,json={'version':1,'asset_id':photo}).status_code==200
    body={'version':1,'story_text':'A confirmed memory','facts':[{'source_field':'story','text':'A confirmed memory'}],
        'shots':[{'shot_index':i+1,'shot_type':'person_ai' if i<8 else 'scene_ai' if i<12 else 'photo',
            'duration_ms':5000,'person_keys':['A'] if i<8 else [],'fact_indexes':[0],
            'description':'Quiet memory','subtitle':'','creative_scene':False} for i in range(18)]}
    assert client.post(base+'/storyboard',headers=headers,json=body).status_code==201
    board=client.post(base+'/storyboard/approve',headers=headers,json={'version':1}).json()
    assert client.post(base+'/frame-jobs',headers={**headers,'Idempotency-Key':'floor-frames'},json={'version':1,
        'photos':[{'shot_id':s['id'],'asset_id':photo} for s in board['shots'] if s['shot_type']=='photo']}).status_code==202
    assert process_next_creation_job().status=='completed'
    board=client.get(base+'/storyboard',headers=headers).json()
    board=client.post(base+'/frames/approve',headers=headers,json={'version':1,'frames':[
        {'shot_id':s['id'],'asset_id':s['frame_asset_id']} for s in board['shots']]}).json()
    for shot in board['shots']:
        for _ in shot['person_keys']:
            assert review(client,headers,shot,shot['frame_asset_id']).status_code==200
    tools=Path(__file__).resolve().parents[2]/'output/wedding-tools/ffmpeg/ffmpeg-9.0.2-essentials_build/bin'
    clip=tmp_path/'floor.mp4'
    assert subprocess.run([str(tools/'ffmpeg.exe'),'-v','error','-y','-f','lavfi','-i','color=c=navy:s=1280x720:r=30:d=5',
        '-c:v','libx264','-threads','2','-pix_fmt','yuv420p',str(clip)],capture_output=True).returncode==0
    monkeypatch.setenv('WEDDING_FFMPEG_PATH',str(tools/'ffmpeg.exe'));monkeypatch.setenv('WEDDING_FFPROBE_PATH',str(tools/'ffprobe.exe'));get_settings.cache_clear()
    class Synthetic:
        def generate(self,request):
            person=request.shot_type=='person_ai'
            return MediaOutput(clip.read_bytes(),'video/mp4','wan_r2v' if person else 'wan_i2v_flash',
                'wan2.7-r2v-2026-06-12' if person else 'wan2.6-i2v-flash',0)
    assert enqueue_video(client,base,headers,'floor-videos').status_code==202
    assert process_next_generation_job(providers=replace(build_provider_bundle(),character_video=Synthetic(),scene_video=Synthetic())).status=='completed'
    for candidate in client.get(base+'/candidates',headers=headers).json():
        shot=next(s for s in board['shots'] if s['id']==candidate['shot_id'])
        assert client.post(base+f'/shots/{shot["id"]}/select-candidate',headers=headers,json={'project_version':1,'candidate_id':candidate['id']}).status_code==200
        for _ in shot['person_keys']:
            assert review(client,headers,shot,candidate['asset_id']).status_code==200
    for shot in board['shots'][:fallback_count]:
        assert client.post(base+f'/shots/{shot["id"]}/accept-fallback',headers=headers,json={'project_version':1,'asset_id':photo,'accepted':True}).status_code==200
        assert review(client,headers,shot,photo).status_code==200
    monkeypatch.setenv('WEDDING_MEDIA_MODE','live');get_settings.cache_clear()
    # Synthetic materials/music isolate the production composition gate, not portrait/music acceptance.
    monkeypatch.setattr('app.wedding_authoring.require_photo_readiness',lambda db,project:None)
    monkeypatch.setattr('app.wedding_authoring.render_music',lambda project,track=None:{})
    result=client.post(base+'/render',headers=headers,json={'version':1})
    assert result.status_code==expected,result.text
    if expected==409:
        assert result.json()['detail']=='STANDARD_MOTION_FLOOR_REQUIRED'


def test_cancelled_before_dispatch_has_no_unsettled_accounting(client,monkeypatch):
    from app.models import GenerationJob, WeddingBillingReceipt
    base,actors,_,board,_=ready(client,monkeypatch)
    job=enqueue_video(client,base,actors['A'],'cancel-before-dispatch').json()
    assert client.post(base+f'/generation-jobs/{job["id"]}/cancel',headers=actors['A']).status_code==200
    with get_session_factory()() as db:
        db.get(GenerationJob,job['id']).updated_at=utc_now()-timedelta(days=1)
        db.commit()
        assert all(r.settled for r in db.scalars(select(WeddingBillingReceipt).where(WeddingBillingReceipt.job_id==job['id'])))
    assert process_next_generation_job() is None


def test_cosmetic_title_cannot_reset_shot_revision_attempts(client,monkeypatch):
    base,actors,_,board,_=ready(client,monkeypatch)
    result=client.patch(base,headers=actors['A'],json={'version':1,'title':'A new display title'})
    assert result.status_code==200,result.text
    assert result.json()['version']==1
    assert client.get(base+'/storyboard',headers=actors['A']).json()['id']==board['id']


def test_unknown_frame_creation_blocks_fresh_key(client,monkeypatch):
    project,headers,_,photos,_=prepare_frames(client,monkeypatch)
    assert enqueue_frames(client,project,headers,photos).status_code==202
    class Unknown:
        def generate(self,request):
            raise ProviderUnavailable('UNKNOWN','Unknown',submission_state='unknown')
    assert process_next_creation_job(providers=replace(build_provider_bundle(),frame=Unknown())).status=='needs_action'
    response=client.post(f'/wedding-projects/{project}/frame-jobs',headers={**headers,'Idempotency-Key':'fresh-frame-browser'},json={'version':1,'photos':photos})
    assert response.status_code==409 and response.json()['detail']=='CREATION_OUTCOME_UNRESOLVED'
