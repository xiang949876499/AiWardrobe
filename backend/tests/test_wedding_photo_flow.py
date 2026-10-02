"""Public production workflow: photo assignment must retain subject review gates."""
from app.config import get_settings
from app.wedding_creation import process_next_creation_job
from app.wedding_worker import process_next_generation_job
from tests.conftest import invitation_hash, login
from io import BytesIO
from PIL import Image


def _png():
    data = BytesIO()
    Image.new('RGB', (1024, 1024), (180, 80, 120)).save(data, format='PNG')
    return data.getvalue()


def photo_flow(client, monkeypatch):
    monkeypatch.setenv('WEDDING_MEDIA_MODE', 'demo')
    get_settings.cache_clear()
    owner = {'Authorization': 'Bearer ' + login(client, 'photo-flow@example.com')}
    created = client.post('/wedding-projects', headers=owner,
                          json={'title': 'Automatic photo flow', 'owner_name': 'A'})
    assert created.status_code == 201, created.text
    project = created.json()['id']
    base = f'/wedding-projects/{project}'
    assert client.post(base + '/self-consent', headers=owner, json={
        'terms_version': 'photo-v1', 'scope': ['portrait_processing'],
        'provider_list': ['local_demo']}).status_code == 200
    invite = client.post(base + '/consent-invites', headers=owner, json={
        'person_key': 'B', 'display_name': 'B', 'provider_list': ['local_demo'],
        'terms_version': 'photo-v1'})
    partner = client.post(f"/consent-invites/{invite.json()['invite_token']}/confirm",
                          json={**{'accepted': True, 'display_name': 'B', 'terms_version': 'photo-v1'}, "disclosure_hash": invitation_hash(client, invite.json()['invite_token'])})
    assert partner.status_code == 200, partner.text
    actors = {'A': owner, 'B': {'Authorization': 'Bearer ' + partner.json()['access_token']}}
    ids = {}
    for key, headers in actors.items():
        uploaded = client.post(base + '/assets', headers=headers,
            data={'person_key': key, 'asset_type': 'photo'}, files={'file': ('photo.png', _png(), 'image/png')})
        assert uploaded.status_code == 201, uploaded.text
        ids[key] = uploaded.json()['id']
        assert client.put(base + f'/participants/{key}/primary-photo', headers=headers,
                          json={'asset_id': ids[key], 'version': 1}).status_code == 200
    assert client.post(base + '/story-jobs', headers={**owner, 'Idempotency-Key': 'photo-story'},
                       json={'version': 1, 'story_text': 'We met in a library.'}).status_code == 202
    assert process_next_creation_job().status == 'completed'
    approved = client.post(base + '/storyboard/approve', headers=owner, json={'version': 1})
    assert approved.status_code == 200, approved.text
    story = approved.json()
    photos = [s for s in story['shots'] if s['shot_type'] == 'photo']
    assert len(photos) == 6 and all(s['person_keys'] == [] for s in photos)
    assignments = [{'shot_id': s['id'], 'asset_id': ids['A' if i % 2 == 0 else 'B']}
                   for i, s in enumerate(photos)]
    assert client.post(base + '/frame-jobs', headers={**owner, 'Idempotency-Key': 'photo-frames'},
                       json={'version': 1, 'photos': assignments}).status_code == 202
    assert process_next_creation_job().status == 'completed'
    framed = client.get(base + '/storyboard', headers=owner).json()
    frames = [{'shot_id': s['id'], 'asset_id': s['frame_asset_id']} for s in framed['shots']]
    response = client.post(base + '/frames/approve', headers=owner, json={'version': 1, 'frames': frames})
    assert response.status_code == 200, response.text
    current = response.json()
    assert (current['id'], current['version'], current['story_text'], current['facts'], current['owner_approved_at']) == (
        story['id'], story['version'], story['story_text'], story['facts'], story['owner_approved_at'])
    for before, after in zip(story['shots'], current['shots'], strict=True):
        for field in ('id', 'shot_index', 'shot_type', 'duration_ms', 'fact_ids', 'description', 'subtitle', 'creative_scene'):
            assert after[field] == before[field]
        if after['shot_type'] == 'photo':
            subject = next(k for k, asset in ids.items() if asset == after['frame_asset_id'])
            assert after['selected_asset_id'] == ids[subject]
            assert after['selected_candidate_id'] is None
            assert after['person_keys'] == [subject]
        else:
            assert after['person_keys'] == before['person_keys']
    return base, actors, ids, current, frames


def review(client, headers, shot, asset_id, result='approved'):
    return client.post(f"/participant-reviews/{shot['id']}", headers=headers,
                       json={'project_version': 1, 'asset_id': asset_id, 'result': result})


def test_automatic_story_photos_are_selected_scoped_reviewed_and_renderable(client, monkeypatch):
    base, actors, ids, story, frames = photo_flow(client, monkeypatch)
    b_photos = [s for s in story['shots'] if s['shot_type'] == 'photo' and s['person_keys'] == ['B']]
    visible = client.get(base + '/storyboard', headers=actors['B']).json()['shots']
    assert {s['id'] for s in b_photos} <= {s['id'] for s in visible}
    assert all('B' in s['person_keys'] for s in visible)
    assert review(client, actors['A'], b_photos[0], ids['B']).status_code == 403
    for shot in story['shots']:
        for key in shot['person_keys']:
            assert review(client, actors[key], shot, shot['frame_asset_id']).status_code == 200
    quote = client.get(base + '/quote', headers=actors['A'])
    assert quote.status_code == 200, quote.text
    generated = client.post(base + '/generation-jobs', headers={**actors['A'], 'Idempotency-Key': 'photo-videos'},
                            json={'version': 1, 'quote_id': quote.json()['id']})
    assert generated.status_code == 202, generated.text
    result = process_next_generation_job()
    assert result.status == 'completed', result
    by_id = {s['id']: s for s in story['shots']}
    for candidate in client.get(base + '/candidates', headers=actors['A']).json():
        shot = by_id[candidate['shot_id']]
        assert client.post(base + f"/shots/{shot['id']}/select-candidate", headers=actors['A'],
            json={'project_version': 1, 'candidate_id': candidate['id']}).status_code == 200
        for key in shot['person_keys']:
            assert review(client, actors[key], shot, candidate['asset_id']).status_code == 200
    assert review(client, actors['B'], b_photos[0], ids['B'], 'rejected').status_code == 200
    blocked = client.post(base + '/render', headers=actors['A'], json={'version': 1})
    assert blocked.status_code == 409 and blocked.json()['detail'] == 'IDENTITY_REVIEW_REQUIRED'
    assert review(client, actors['B'], b_photos[0], ids['B']).status_code == 200
    queued = client.post(base + '/render', headers=actors['A'], json={'version': 1})
    assert queued.status_code == 202, queued.text
    assert len(queued.json()['timeline']) == 18
    # Re-approval is idempotent: other selections and all existing reviews survive.
    before = client.get(base + '/storyboard', headers=actors['A']).json()
    repeated = client.post(base + '/frames/approve', headers=actors['A'], json={'version': 1, 'frames': frames})
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()['shots'] == before['shots']
    # Select a different original owned by the same subject through upload.
    uploaded = client.post(base + '/assets', headers=actors['B'], data={'person_key': 'B', 'asset_type': 'photo'},
                           files={'file': ('new.png', _png(), 'image/png')})
    assert uploaded.status_code == 201, uploaded.text
    changed = [{**f, 'asset_id': uploaded.json()['id']} if f['shot_id'] == b_photos[0]['id'] else f for f in frames]
    assert client.post(base + '/frames/approve', headers=actors['A'], json={'version': 1, 'frames': changed}).status_code == 200
    invalidated = client.get(base + f"/renders/{queued.json()['id']}", headers=actors['A']).json()
    assert invalidated['status'] == 'cancelled' and invalidated['invalidated_at'] is not None


def test_photo_assignment_rejects_generated_frame_and_only_invalidates_changed_photo(client, monkeypatch):
    base, actors, ids, story, frames = photo_flow(client, monkeypatch)
    photo = next(s for s in story['shots'] if s['shot_type'] == 'photo' and s['person_keys'] == ['A'])
    other = next(s for s in story['shots'] if s['shot_type'] == 'person_ai' and 'A' in s['person_keys'])
    assert review(client, actors['A'], photo, ids['A']).status_code == 200
    assert review(client, actors['A'], other, other['frame_asset_id']).status_code == 200
    invalid = [{**f, 'asset_id': other['frame_asset_id']} if f['shot_id'] == photo['id'] else f for f in frames]
    rejected = client.post(base + '/frames/approve', headers=actors['A'], json={'version': 1, 'frames': invalid})
    assert rejected.status_code == 422 and rejected.json()['detail'] == 'PHOTO_FRAME_ASSET_INVALID'
    uploaded = client.post(base + '/assets', headers=actors['A'], data={'person_key': 'A', 'asset_type': 'photo'},
                           files={'file': ('new.png', _png(), 'image/png')})
    assert uploaded.status_code == 201, uploaded.text
    changed = [{**f, 'asset_id': uploaded.json()['id']} if f['shot_id'] == photo['id'] else f for f in frames]
    response = client.post(base + '/frames/approve', headers=actors['A'], json={'version': 1, 'frames': changed})
    assert response.status_code == 200, response.text
    by_id = {s['id']: s for s in response.json()['shots']}
    assert by_id[photo['id']]['identity_reviews'] == []
    assert by_id[photo['id']]['selected_asset_id'] == uploaded.json()['id']
    assert len(by_id[other['id']]['identity_reviews']) == 1
    reassigned = [{**f, 'asset_id': ids['B']} if f['shot_id'] == photo['id'] else f for f in changed]
    response = client.post(base + '/frames/approve', headers=actors['A'], json={'version': 1, 'frames': reassigned})
    assert response.status_code == 200, response.text
    moved = next(s for s in response.json()['shots'] if s['id'] == photo['id'])
    assert moved['person_keys'] == ['B'] and moved['selected_asset_id'] == ids['B']
    assert review(client, actors['A'], moved, ids['B']).status_code == 403
    assert review(client, actors['B'], moved, ids['B']).status_code == 200
