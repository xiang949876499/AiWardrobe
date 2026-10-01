import json
from datetime import timedelta
import pytest
from fastapi import HTTPException
from app.config import get_settings
from app.models import utc_now
from tests.test_wedding_worker import _png
from tests.test_wedding_creation import setup_project


def test_quality_decode_and_controlled_detector():
    from app.wedding_photo_quality import analyze_photo
    assert analyze_photo(b'corrupt')['usable'] is False
    multiple = analyze_photo(_png(), detector=lambda image: ([(0,0,20,20),(30,0,20,20)], 4))
    assert multiple['usable'] is False and 'multiple_faces' in multiple['warnings']
    single = analyze_photo(_png(), detector=lambda image: ([(0,0,20,20)], 0))
    assert single['usable'] is True
    assert {'blur', 'possible_occlusion'} <= set(single['warnings'])
    assert analyze_photo(_png(), demo=True)['demo_bypass'] is True


def catalog(monkeypatch, tmp_path, **changes):
    music = tmp_path / 'test.wav'
    music.write_bytes(b'test only')
    row = dict(track_id='test', title='Test only', path=str(music), license_id='contract',
        license_version='v1', valid_from=(utc_now()-timedelta(days=1)).isoformat(),
        valid_until=(utc_now()+timedelta(days=1)).isoformat(), perpetual_embedded_delivery=True,
        allowed_uses=['customer_embedded_delivery','wedding_playback'])
    row.update(changes)
    path = tmp_path/'catalog.json'
    path.write_text(json.dumps([row]))
    monkeypatch.setenv('WEDDING_MUSIC_CATALOG_FILE', str(path))
    get_settings.cache_clear()
    return row


def test_catalog_rights_expiry_and_safe_metadata(monkeypatch, tmp_path):
    from app.wedding_authoring import music_catalog, select_music
    catalog(monkeypatch,tmp_path)
    assert 'path' not in music_catalog()[0]
    assert select_music('test')['license_version']=='v1'
    catalog(monkeypatch,tmp_path,perpetual_embedded_delivery=False)
    with pytest.raises(HTTPException,match='MUSIC_RIGHTS_INSUFFICIENT'):
        select_music('test')
    catalog(monkeypatch,tmp_path,valid_until=(utc_now()-timedelta(seconds=1)).isoformat())
    with pytest.raises(HTTPException,match='MUSIC_LICENSE_EXPIRED'):
        select_music('test')


def test_readiness_is_scoped_and_demo_explicit(client,monkeypatch):
    project, headers=setup_project(client,monkeypatch)
    response=client.get(f'/wedding-projects/{project}/photo-readiness',headers=headers)
    assert response.status_code==200
    assert response.json()['demo_bypass'] is True
    assert response.json()['people'][0]['person_key']=='A'
from app.database import get_session_factory
from app.models import WeddingAsset, WeddingParticipant, StoryVersion, ParticipantReview, RenderVersion
from sqlalchemy import select
from tests.test_wedding_creation import prepare_frames, enqueue_frames


def rendered_story(client,monkeypatch):
    project,headers,story,photos,ids=prepare_frames(client,monkeypatch)
    from app.wedding_creation import process_next_creation_job
    assert enqueue_frames(client,project,headers,photos).status_code==202
    assert process_next_creation_job().status=='completed'
    with get_session_factory()() as db:
        p=db.get(__import__('app.models',fromlist=['WeddingProject']).WeddingProject,project)
        p.frame_approved_version=1
        p.status='preview_review'
        story=db.scalar(select(StoryVersion).where(StoryVersion.project_id==project))
        people={p.person_key:p for p in p.participants}
        for s in story.shots:
            s.selected_asset_id=s.frame_asset_id
            s.status='frame_approved'
            for key in s.person_keys:
                db.add(ParticipantReview(project_id=project,participant_id=people[key].id,person_key=key,
                    project_version=1,shot_id=s.id,asset_id=s.frame_asset_id,result='approved',confirmed_at=utc_now()))
        for a in db.scalars(select(WeddingAsset).where(WeddingAsset.id.in_(ids.values()))):
            a.quality=dict(usable=True,warnings=[],demo_bypass=True)
        db.commit()
    return project,headers,photos,ids


def test_edit_restore_immutable_timeline_no_generation(client,monkeypatch):
    project,headers,photos,ids=rendered_story(client,monkeypatch)
    one=client.post(f'/wedding-projects/{project}/render',headers=headers,json={'version':1})
    assert one.status_code==202,one.text
    first=one.json()
    assert len(first['timeline'])==18
    shot=first['timeline'][0]['shot_id']
    two=client.post(f'/wedding-projects/{project}/render',headers=headers,json={'version':1,'subtitles':{shot:'新的字幕'}})
    assert two.status_code==202,two.text
    assert two.json()['timeline'][0]['subtitle']=='新的字幕'
    old=client.get(f"/wedding-projects/{project}/renders/{first['id']}",headers=headers).json()
    assert old['timeline']==first['timeline']
    restore=client.post(f"/wedding-projects/{project}/renders/{first['id']}/restore",headers=headers,json={'version':1})
    assert restore.status_code==202,restore.text
    assert restore.json()['timeline']==first['timeline']
    assert restore.json()['owner_approved_at'] is None
    bad=client.post(f'/wedding-projects/{project}/render',headers=headers,json={'version':1,'subtitles':{shot:'中'*19}})
    assert bad.status_code==422
    with get_session_factory()() as db:
        asset=db.get(WeddingAsset,first['timeline'][0]['asset_id'])
        asset.expires_at=utc_now()-timedelta(seconds=1)
        db.commit()
    assert client.post(f"/wedding-projects/{project}/renders/{first['id']}/restore",headers=headers,json={'version':1}).status_code==409


def test_primary_change_scopes_and_only_rebuilds_dependent_frames(client,monkeypatch):
    project,headers,photos,ids=rendered_story(client,monkeypatch)
    # New explicit A original, owned and classified at upload.
    from PIL import Image
    from io import BytesIO
    image=BytesIO(); Image.new('RGB',(1024,1024),'pink').save(image,format='PNG')
    uploaded=client.post(f'/wedding-projects/{project}/assets',headers=headers,
        data={'person_key':'A'},files={'file':('new.png',image.getvalue(),'image/png')}).json()
    before=client.get(f'/wedding-projects/{project}/storyboard',headers=headers).json()
    assert client.put(f'/wedding-projects/{project}/participants/B/primary-photo',headers=headers,
        json={'version':1,'asset_id':ids['B']}).status_code==403
    changed=client.put(f'/wedding-projects/{project}/participants/A/primary-photo',headers=headers,
        json={'version':1,'asset_id':uploaded['id']})
    assert changed.status_code==200,changed.text
    affected=changed.json()['affected_shot_ids']
    after=client.get(f'/wedding-projects/{project}/storyboard',headers=headers).json()
    for old,new in zip(before['shots'],after['shots']):
        if old['id'] not in affected:
            assert old==new
        else:
            assert new['frame_asset_id'] is None and new['identity_reviews']==[]
    response=enqueue_frames(client,project,headers,photos,key='replacement')
    assert response.status_code==202,response.text
    assert response.json()['total_operations']==len(affected)

def test_live_readiness_requires_three_confirmed_own_photos(client,monkeypatch):
    project,headers,photos,ids=rendered_story(client,monkeypatch)
    monkeypatch.setenv('WEDDING_MEDIA_MODE','live');get_settings.cache_clear()
    from app.wedding_authoring import require_photo_readiness, photo_readiness
    from app.models import WeddingProject
    with get_session_factory()() as db:
        p=db.get(WeddingProject,project)
        with pytest.raises(HTTPException,match='PHOTO_READINESS_REQUIRED'):
            require_photo_readiness(db,p)
        for key in ('A','B'):
            original=db.get(WeddingAsset,ids[key]);original.quality=dict(usable=True,warnings=['blur'])
            original.quality_confirmed_at=utc_now()
            for n in range(2):
                db.add(WeddingAsset(project_id=project,person_key=key,asset_type='photo',object_key=f'{key}-{n}',
                    sha256=key*64,mime='image/png',size_bytes=5,quality=dict(usable=True,warnings=[])))
        db.flush()
        require_photo_readiness(db,p)
        db.get(WeddingAsset,ids['B']).quality_confirmed_at=None
        assert not photo_readiness(db,p)['ready']


def test_export_selects_exact_historical_render(client,monkeypatch):
    project,headers,photos,ids=rendered_story(client,monkeypatch)
    renders=[client.post(f'/wedding-projects/{project}/render',headers=headers,json={'version':1}).json() for _ in range(2)]
    with get_session_factory()() as db:
        for row in renders:
            render=db.get(RenderVersion,row['id']);render.status='approved';render.owner_approved_at=utc_now()
            asset=WeddingAsset(project_id=project,asset_type='final_video',object_key=row['id'],sha256='a'*64,mime='video/mp4',size_bytes=10)
            db.add(asset);db.flush();render.export_asset_id=asset.id
        db.commit()
    response=client.post(f"/wedding-projects/{project}/export?render_version_id={renders[0]['id']}",headers=headers)
    assert response.status_code==200,response.text
    assert response.json()['render_version_id']==renders[0]['id']
    assert client.post(f'/wedding-projects/{project}/export?render_version_id=unknown',headers=headers).status_code==409
    assert client.post(f'/wedding-projects/{project}/render',headers=headers,json={'version':1}).status_code==202
    assert client.post(f'/wedding-projects/{project}/export',headers=headers).status_code==409
    assert client.post(f"/wedding-projects/{project}/export?render_version_id={renders[0]['id']}",headers=headers).status_code==200


def test_worker_uses_immutable_subtitle_after_story_edit(client,monkeypatch):
    project,headers,photos,ids=rendered_story(client,monkeypatch)
    row=client.post(f'/wedding-projects/{project}/render',headers=headers,json={'version':1}).json()
    from app.models import StoryboardShot
    from app.wedding_worker import claim_next_render_version, process_claimed_render_version
    from app.wedding_service import WeddingPrivateStorage
    from app.wedding_media import RenderedVideo
    with get_session_factory()() as db:
        db.get(StoryboardShot,row['timeline'][0]['shot_id']).subtitle='Changed after enqueue'
        db.commit()
    calls=[]
    class Renderer:
        def render(self,inputs,**kwargs):
            calls.append([i.subtitle for i in inputs])
            return RenderedVideo(b'fake',90000,True)
    assert claim_next_render_version()==row['id']
    process_claimed_render_version(get_session_factory(),row['id'],WeddingPrivateStorage(get_settings()),Renderer())
    assert calls[0][0]==row['timeline'][0]['subtitle']

def test_video_batch_reuses_unaffected_candidates(client,monkeypatch):
    project,headers,photos,ids=rendered_story(client,monkeypatch)
    from app.models import GenerationJob,ShotCandidate,WeddingProject,WeddingQuote
    from app.wedding_worker import _job_shots
    with get_session_factory()() as db:
        story=db.scalar(select(StoryVersion).where(StoryVersion.project_id==project))
        ai=[s for s in story.shots if s.shot_type!='photo']
        quote=db.scalar(select(WeddingQuote).where(WeddingQuote.project_id==project))
        old=GenerationJob(quote_id=quote.id,project_id=project,story_version_id=story.id,project_version=1,idempotency_key='old',request_fingerprint='a'*64,status='completed',total_shots=12)
        db.add(old);db.flush()
        for s in ai[1:]:
            candidate=__import__('app.models',fromlist=['ShotCandidate']).ShotCandidate(project_id=project,
                shot_id=s.id,generation_job_id=old.id,asset_id=s.frame_asset_id,
                provider='local_demo',model_version='demo',actual_cost_fen=0)
            db.add(candidate);db.flush();s.selected_candidate_id=candidate.id;s.selected_asset_id=None
        new=GenerationJob(quote_id=quote.id,project_id=project,story_version_id=story.id,project_version=1,idempotency_key='new',request_fingerprint='b'*64,status='queued',total_shots=1)
        db.add(new);db.flush()
        assert [s.id for s in _job_shots(db,new)]==[ai[0].id]


def test_primary_inflight_creation_discards_old_output(client,monkeypatch):
    project,headers,story,photos,ids=prepare_frames(client,monkeypatch)
    from app.wedding_creation import process_next_creation_job
    from app.wedding_media import build_provider_bundle
    from dataclasses import replace
    with get_session_factory()() as db:
        old=db.get(WeddingAsset,ids['A'])
        fresh=WeddingAsset(project_id=project,person_key='A',asset_type='photo',object_key='new-ref',sha256='z'*64,
            mime='image/png',size_bytes=20,quality=dict(usable=True,warnings=[],demo_bypass=True))
        db.add(fresh);db.flush();fresh_id=fresh.id;db.commit()
    assert enqueue_frames(client,project,headers,photos).status_code==202
    bundle=build_provider_bundle()
    class Frames:
        def generate(self,request):
            changed=client.put(f'/wedding-projects/{project}/participants/A/primary-photo',headers=headers,
                json={'version':1,'asset_id':fresh_id})
            assert changed.status_code==200,changed.text
            return bundle.frame.generate(request)
    result=process_next_creation_job(providers=replace(bundle,frame=Frames()))
    assert result.status!='completed'
    current=client.get(f'/wedding-projects/{project}/storyboard',headers=headers).json()
    assert all(s['frame_asset_id'] is None for s in current['shots'] if 'A' in s['person_keys'])

@pytest.mark.parametrize('value',['中'*19,'a'*37,'a\nb\nc','x\r','x\x00'])
def test_subtitle_limits_reject_overlong_and_controls(value):
    from app.wedding_authoring import validate_subtitle
    with pytest.raises(HTTPException,match='SUBTITLE_TOO_LONG'):
        validate_subtitle(value)


def test_licensed_music_only_render_keeps_inputs_and_grant(client,monkeypatch,tmp_path):
    project,headers,photos,ids=rendered_story(client,monkeypatch)
    row=catalog(monkeypatch,tmp_path)
    selected=client.put(f'/wedding-projects/{project}/music',headers=headers,json={'track_id':'test'})
    assert selected.status_code==200 and 'path' not in selected.json()
    first=client.post(f'/wedding-projects/{project}/render',headers=headers,json={'version':1}).json()
    catalog(monkeypatch,tmp_path,valid_until=(utc_now()-timedelta(seconds=1)).isoformat())
    second=client.post(f'/wedding-projects/{project}/render',headers=headers,json={'version':1}).json()
    assert first['timeline']==second['timeline'] and first['music']==second['music']
    assert 'path' not in second['music']
    from app.models import WeddingBillingReceipt,GenerationJob
    with get_session_factory()() as db:
        assert list(db.scalars(select(GenerationJob).where(GenerationJob.project_id==project)))==[]
        assert len(list(db.scalars(select(WeddingBillingReceipt).where(WeddingBillingReceipt.project_id==project))))==14 # prior setup envelope + story + 12 frames
    restored=client.post(f"/wedding-projects/{project}/renders/{first['id']}/restore",headers=headers,json={'version':1})
    assert restored.status_code==202
    from pathlib import Path
    Path(row['path']).write_bytes(b'changed music')
    assert client.post(f"/wedding-projects/{project}/renders/{first['id']}/restore",headers=headers,json={'version':1}).json()['detail']=='MUSIC_FILE_CHANGED'


def test_history_invalidated_after_primary_and_revoked_consent(client,monkeypatch):
    project,headers,photos,ids=rendered_story(client,monkeypatch)
    row=client.post(f'/wedding-projects/{project}/render',headers=headers,json={'version':1}).json()
    with get_session_factory()() as db:
        person=db.scalar(select(WeddingParticipant).where(WeddingParticipant.project_id==project,WeddingParticipant.person_key=='A'))
        person.primary_photo_asset_id=None
        db.commit()
    changed=client.put(f'/wedding-projects/{project}/participants/A/primary-photo',headers=headers,json={'version':1,'asset_id':ids['A']})
    assert changed.status_code==200
    assert client.post(f"/wedding-projects/{project}/renders/{row['id']}/restore",headers=headers,json={'version':1}).json()['detail']=='RENDER_HISTORY_INVALID'
    assert client.post(f'/wedding-projects/{project}/consent/revoke',headers=headers).status_code==200
    assert client.post(f"/wedding-projects/{project}/renders/{row['id']}/restore",headers=headers,json={'version':1}).status_code==409


def test_primary_invalid_quality_and_noop(client,monkeypatch):
    project,headers,photos,ids=rendered_story(client,monkeypatch)
    for body,expected in [({'version':2,'asset_id':ids['A']},'PROJECT_VERSION_STALE'),
                          ({'version':1,'asset_id':ids['B']},'PRIMARY_PHOTO_INVALID')]:
        assert client.put(f'/wedding-projects/{project}/participants/A/primary-photo',headers=headers,json=body).json()['detail']==expected
    response=client.put(f'/wedding-projects/{project}/participants/A/primary-photo',headers=headers,json={'version':1,'asset_id':ids['A']})
    assert response.json()['affected_shot_ids']==[]
    assert client.post(f"/wedding-projects/{project}/photos/{ids['B']}/confirm-quality",headers=headers,json={'confirmed':True}).status_code==404
    assert client.post(f"/wedding-projects/{project}/photos/{ids['A']}/confirm-quality",headers=headers,json={'confirmed':True}).status_code==200
    with get_session_factory()() as db:
        db.get(WeddingAsset,ids['A']).quality={'usable':False,'warnings':['multiple_faces']};db.commit()
    assert client.post(f"/wedding-projects/{project}/photos/{ids['A']}/confirm-quality",headers=headers,json={'confirmed':True}).status_code==409
    assert client.put(f'/wedding-projects/{project}/participants/A/primary-photo',headers=headers,json={'version':1,'asset_id':ids['A']}).status_code==409


def test_empty_and_legacy_music_do_not_invent_rights(monkeypatch,tmp_path):
    from app.wedding_authoring import music_catalog, select_music
    monkeypatch.delenv('WEDDING_MUSIC_CATALOG_FILE',raising=False)
    monkeypatch.setenv('WEDDING_MUSIC_FILE',str(tmp_path/'legacy.wav'))
    monkeypatch.setenv('WEDDING_MUSIC_LICENSE_ID','legacy-id')
    get_settings.cache_clear()
    assert music_catalog()==[]
    with pytest.raises(HTTPException,match='MUSIC_UNAVAILABLE'):
        select_music('legacy')

def test_edit_from_explicit_historical_render_preserves_source(client,monkeypatch):
    project,headers,photos,ids=rendered_story(client,monkeypatch)
    row=client.post(f'/wedding-projects/{project}/render',headers=headers,json={'version':1}).json()
    shot_id=row['timeline'][0]['shot_id']
    result=client.post(f'/wedding-projects/{project}/render',headers=headers,json={'version':1,
        'source_render_id':row['id'],'subtitles':{shot_id:'Historical edit'}})
    assert result.status_code==202,result.text
    assert result.json()['restored_from_id']==row['id']
    assert result.json()['timeline'][0]['asset_id']==row['timeline'][0]['asset_id']
    assert result.json()['timeline'][0]['subtitle']=='Historical edit'
    assert client.get(f"/wedding-projects/{project}/renders/{row['id']}",headers=headers).json()['timeline']==row['timeline']


def test_existing_photo_can_be_assessed_and_confirmed_by_subject(client,monkeypatch):
    project,headers,story,photos,ids=prepare_frames(client,monkeypatch)
    response=client.post(f"/wedding-projects/{project}/photos/{ids['A']}/analyze-quality",headers=headers)
    assert response.status_code==200,response.text
    assert response.json()['quality']['demo_bypass'] is True
    assert client.post(f"/wedding-projects/{project}/photos/{ids['B']}/analyze-quality",headers=headers).status_code==404

def test_targeted_quote_reserves_only_requested_shot(client,monkeypatch):
    project,headers,photos,ids=rendered_story(client,monkeypatch)
    with get_session_factory()() as db:
        story=db.scalar(select(StoryVersion).where(StoryVersion.project_id==project))
        scene=next(s for s in story.shots if s.shot_type=='scene_ai')
        shot_id=scene.id
        for shot in story.shots:
            for key in shot.person_keys:
                existing=db.scalar(select(ParticipantReview.id).where(ParticipantReview.shot_id==shot.id,ParticipantReview.asset_id==shot.frame_asset_id,ParticipantReview.person_key==key))
                assert existing
    response=client.get(f'/wedding-projects/{project}/quote?shot_id={shot_id}',headers=headers)
    assert response.status_code==200,response.text
    assert response.json()['max_budget_fen']==125

def test_participant_can_only_select_and_confirm_own_reference(client,monkeypatch):
    from tests.conftest import login
    from tests.test_wedding import _create_project,_confirm_partner,_image_upload,_headers
    monkeypatch.setenv('WEDDING_MEDIA_MODE','demo');get_settings.cache_clear()
    owner=login(client,'scoped-author@example.com');project=_create_project(client,owner)['id']
    assert client.post(f'/wedding-projects/{project}/self-consent',headers=_headers(owner),json={
        'terms_version':'portrait-v1','scope':['portrait_processing'],'provider_list':['local_demo']}).status_code==200
    partner=_confirm_partner(client,owner,project)
    own=_image_upload(client,owner,project,'A');other=_image_upload(client,partner,project,'B')
    response=client.get(f'/wedding-projects/{project}/photo-readiness',headers=_headers(partner))
    assert [p['person_key'] for p in response.json()['people']]==['B']
    assert client.put(f'/wedding-projects/{project}/participants/A/primary-photo',headers=_headers(partner),json={'version':1,'asset_id':own['id']}).status_code==403
    assert client.put(f'/wedding-projects/{project}/participants/B/primary-photo',headers=_headers(partner),json={'version':1,'asset_id':other['id']}).status_code==200
    assert client.post(f"/wedding-projects/{project}/photos/{other['id']}/confirm-quality",headers=_headers(partner),json={'confirmed':True}).status_code==200
    outsider=login(client,'outsider-author@example.com')
    assert client.get(f'/wedding-projects/{project}/photo-readiness',headers=_headers(outsider)).status_code==404
    foreign=_create_project(client,outsider)['id']
    assert client.get(f'/wedding-projects/{foreign}/photo-readiness',headers=_headers(partner)).status_code==404
    assert client.put(f'/wedding-projects/{project}/music',headers=_headers(partner),json={'track_id':'test'}).status_code==404


@pytest.mark.parametrize('text',['{}','[{}]','[{"track_id":"x"},{"track_id":"x"}]','not json'])
def test_malformed_music_catalog_is_explicit(monkeypatch,tmp_path,text):
    from app.wedding_authoring import music_catalog
    path=tmp_path/'bad.json';path.write_text(text)
    monkeypatch.setenv('WEDDING_MUSIC_CATALOG_FILE',str(path));get_settings.cache_clear()
    with pytest.raises(HTTPException,match='MUSIC_CATALOG_INVALID'):
        music_catalog()


@pytest.mark.parametrize('changes,code',[
    ({'valid_from':'wrong'},'MUSIC_CATALOG_INVALID'),
    ({'path':'/missing/licensed-track.wav'},'MUSIC_FILE_MISSING'),
    ({'allowed_uses':['wedding_playback']},'MUSIC_RIGHTS_INSUFFICIENT'),
])
def test_music_rejects_unusable_grants(monkeypatch,tmp_path,changes,code):
    from app.wedding_authoring import select_music
    catalog(monkeypatch,tmp_path,**changes)
    with pytest.raises(HTTPException,match=code):select_music('test')


def test_history_rejects_changed_reference_consent_and_missing_review(client,monkeypatch):
    project,headers,photos,ids=rendered_story(client,monkeypatch)
    row=client.post(f'/wedding-projects/{project}/render',headers=headers,json={'version':1}).json()
    url=f"/wedding-projects/{project}/renders/{row['id']}/restore"
    assert client.post(url,headers=headers,json={'version':2}).json()['detail']=='PROJECT_VERSION_STALE'
    assert client.post(f'/wedding-projects/{project}/renders/unknown/restore',headers=headers,json={'version':1}).status_code==404
    with get_session_factory()() as db:
        p=db.scalar(select(WeddingParticipant).where(WeddingParticipant.project_id==project,WeddingParticipant.person_key=='A'))
        p.primary_photo_asset_id=None;db.commit()
    assert client.post(url,headers=headers,json={'version':1}).json()['detail']=='PRIMARY_REFERENCE_CHANGED'
    with get_session_factory()() as db:
        p=db.scalar(select(WeddingParticipant).where(WeddingParticipant.project_id==project,WeddingParticipant.person_key=='A'))
        p.primary_photo_asset_id=ids['A'];p.terms_version='renewed';db.commit()
    assert client.post(url,headers=headers,json={'version':1}).json()['detail']=='CONSENT_CHANGED'


def test_renderer_legacy_id_has_no_grant_and_snapshot_is_required(monkeypatch,tmp_path):
    from app.wedding_media import FFmpegRenderer,ProviderUnavailable
    from app.wedding_authoring import select_music
    monkeypatch.setenv('WEDDING_MEDIA_MODE','live');get_settings.cache_clear()
    renderer=FFmpegRenderer()
    with pytest.raises(ProviderUnavailable,match='explicit valid license'):
        renderer.validate_music_license(demo=False)
    row=catalog(monkeypatch,tmp_path)
    assert renderer.validate_music_license(demo=False,music_snapshot=select_music('test')).name=='test.wav'

def test_music_changed_after_validation_is_not_embedded(monkeypatch,tmp_path):
    from app.wedding_authoring import select_music
    from app.wedding_media import FFmpegRenderer,RenderInput,ProviderUnavailable
    from pathlib import Path
    catalog(monkeypatch,tmp_path)
    snapshot=select_music('test')
    renderer=FFmpegRenderer(ffmpeg_path='unused-local-path')
    original=renderer.validate_music_license
    def mutate(**kwargs):
        path=original(**kwargs)
        path.write_bytes(b'different unlicensed content')
        return path
    monkeypatch.setattr(renderer,'validate_music_license',mutate)
    with pytest.raises(ProviderUnavailable) as error:
        renderer.render([RenderInput(_png(),'image/png','',5000)],demo=False,resolution=(1280,720),music_snapshot=snapshot)
    assert error.value.code=='MUSIC_FILE_CHANGED'

def test_live_mode_normalization_cannot_bypass_readiness():
    from app.config import Settings
    assert Settings(_env_file=None,wedding_media_mode=' LIVE ').wedding_media_mode=='live'

def test_primary_rebuild_bills_only_affected_frames_and_keeps_other_person(client,monkeypatch):
    from dataclasses import replace
    from app.models import WeddingProject,WeddingBillingReceipt
    from app.wedding_creation import process_next_creation_job
    from app.wedding_media import build_provider_bundle
    project,headers,photos,ids=rendered_story(client,monkeypatch)
    bundle=build_provider_bundle()
    before=client.get(f'/wedding-projects/{project}/storyboard',headers=headers).json()
    with get_session_factory()() as db:
        for person in db.scalars(select(WeddingParticipant).where(WeddingParticipant.project_id==project)):
            old=db.get(WeddingAsset,ids[person.person_key]);old.quality=dict(usable=True,warnings=[])
            person.provider_list=['seedream']
            for n in range(2):
                fresh=WeddingAsset(project_id=project,person_key=person.person_key,asset_type='photo',
                    object_key=f'controlled-{person.id}-{n}',sha256=str(n)*64,mime='image/png',size_bytes=20,
                    quality=dict(usable=True,warnings=[]))
                db.add(fresh)
            if person.person_key=='A':person.primary_photo_asset_id=None
        db.commit()
    monkeypatch.setenv('WEDDING_MEDIA_MODE','live')
    monkeypatch.setenv('WEDDING_FRAME_PROVIDER','seedream')
    monkeypatch.setenv('WEDDING_FRAME_API_KEY','local-fixture-no-network')
    get_settings.cache_clear()
    changed=client.put(f'/wedding-projects/{project}/participants/A/primary-photo',headers=headers,json={'version':1,'asset_id':ids['A']})
    assert changed.status_code==200,changed.text
    affected=set(changed.json()['affected_shot_ids'])
    response=enqueue_frames(client,project,headers,photos,key='billed-targeted')
    assert response.status_code==202,response.text
    assert response.json()['total_operations']==len(affected)
    calls=[]
    class Frames:
        def generate(self,request):
            calls.append(request.shot_id)
            return replace(bundle.frame.generate(request),provider='seedream',model_version=get_settings().wedding_frame_model,actual_cost_fen=25)
    assert process_next_creation_job(providers=replace(bundle,frame=Frames())).status=='completed'
    assert set(calls)==affected and len(calls)==len(affected)
    after=client.get(f'/wedding-projects/{project}/storyboard',headers=headers).json()
    for old,new in zip(before['shots'],after['shots']):
        if old['id'] not in affected:assert old==new
        else:assert new['identity_reviews']==[]
    with get_session_factory()() as db:
        assert db.get(WeddingProject,project).budget_spent_fen==25*len(affected)
        assert len(list(db.scalars(select(WeddingBillingReceipt).where(WeddingBillingReceipt.job_id==response.json()['id']))))==len(affected)

def test_authoring_migration_upgrades_legacy_columns_once(client,monkeypatch):
    from sqlalchemy import text,inspect
    from app.database import init_db
    from app.models import WeddingProject
    project,headers=setup_project(client,monkeypatch)
    fields={'wedding_projects':['selected_music'],
        'wedding_assets':['quality','quality_confirmed_at','invalidated_at'],
        'wedding_render_versions':['timeline_snapshot','music_snapshot','restored_from_id','invalidated_at']}
    with get_session_factory()() as db:
        engine=db.get_bind()
    with engine.begin() as conn:
        for table,columns in fields.items():
            for column in columns:conn.execute(text(f'ALTER TABLE {table} DROP COLUMN {column}'))
    init_db();init_db()
    with get_session_factory()() as db:
        assert db.get(WeddingProject,project).selected_music=={}
        for table,columns in fields.items():
            assert set(columns)<={c['name'] for c in inspect(db.get_bind()).get_columns(table)}
