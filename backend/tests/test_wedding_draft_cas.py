from tests.conftest import login
from tests.test_wedding import _create_project, _headers


def draft(text):
    return dict(version=1, story_text=text, facts=[dict(source_field='source', text=text)],
                shots=[dict(shot_index=1, shot_type='scene_ai', duration_ms=5000,
                            person_keys=[], fact_indexes=[0], description='scene', subtitle='', creative_scene=True)])


def test_draft_fingerprint_fences_two_tabs_at_same_content_version(client):
    token = login(client, 'draft-cas@example.com')
    project = _create_project(client, token)['id']
    path = f'/wedding-projects/{project}/storyboard'
    first = client.post(path, headers=_headers(token), json=draft('first'))
    assert first.status_code == 201, first.text
    original = first.json()['draft_fingerprint']
    assert client.get(path, headers=_headers(token)).json()['draft_fingerprint'] == original
    headers = {**_headers(token), 'If-Match': original}
    second = client.put(path, headers=headers, json=draft('second'))
    assert second.status_code == 200, second.text
    assert second.json()['draft_fingerprint'] != original
    stale = client.put(path, headers=headers, json=draft('stale tab'))
    assert stale.status_code == 409
    assert stale.json()['detail'] == 'DRAFT_CONFLICT'
    assert client.get(path, headers=_headers(token)).json()['story_text'] == 'second'


def test_draft_precondition_is_checked_for_initial_creation(client):
    token = login(client, 'draft-initial@example.com')
    project = _create_project(client, token)['id']
    result = client.post(f'/wedding-projects/{project}/storyboard',
        headers={**_headers(token), 'If-Match': 'stale-fingerprint'}, json=draft('initial'))
    assert result.status_code == 409
    assert result.json()['detail'] == 'DRAFT_CONFLICT'


def test_approval_does_not_confirm_a_concurrently_changed_draft(client):
    token=login(client,'draft-approval-cas@example.com')
    project=_create_project(client,token)['id']
    path=f'/wedding-projects/{project}/storyboard'
    original=client.post(path,headers=_headers(token),json=draft('first')).json()['draft_fingerprint']
    assert client.put(path,headers={**_headers(token),'If-Match':original},json=draft('other tab')).status_code==200
    response=client.post(path+'/approve',headers={**_headers(token),'If-Match':original},json={'version':1})
    assert response.status_code==409 and response.json()['detail']=='DRAFT_CONFLICT'
    state=client.get(path,headers=_headers(token)).json()
    assert state['owner_approved_at'] is None and state['facts'][0]['confirmed_at'] is None


def test_saved_draft_fences_a_pending_story_result(client,monkeypatch):
    from dataclasses import replace
    from tests.test_wedding_creation import setup_project,submit
    from app.wedding_creation import process_next_creation_job
    from app.wedding_media import build_provider_bundle
    project,headers=setup_project(client,monkeypatch)
    path=f'/wedding-projects/{project}/storyboard'
    original=client.post(path,headers=headers,json=draft('first')).json()['draft_fingerprint']
    assert submit(client,project,headers).status_code==202
    bundle=build_provider_bundle()
    class EditingStory:
        def structure(self,request):
            response=client.put(path,headers={**headers,'If-Match':original},json=draft('new local edit'))
            assert response.status_code==200,response.text
            return bundle.story.structure(request)
    result=process_next_creation_job(providers=replace(bundle,story=EditingStory()))
    assert result.error_code=='DRAFT_CHANGED'
    assert client.get(path,headers=headers).json()['story_text']=='new local edit'
