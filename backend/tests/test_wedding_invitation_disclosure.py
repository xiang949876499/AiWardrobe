from sqlalchemy import select
from app.database import get_session_factory
from app.models import WeddingConsent, WeddingParticipant
from tests.conftest import login


def test_immutable_disclosure_reentry_preserves_consent_and_rotates_session(client):
    headers = {'Authorization':'Bearer '+login(client)}
    project = client.post('/wedding-projects',headers=headers,json={'title':'Private story title','owner_name':'A'}).json()['id']
    invite = client.post(f'/wedding-projects/{project}/consent-invites',headers=headers,
        json={'display_name':'B','provider_list':['wan_r2v','seedream'],'terms_version':'actual-v3'}).json()['invite_token']
    preview = client.post('/consent-invites/preview',json={'token':invite})
    assert preview.status_code == 200, preview.text
    disclosure = preview.json()
    assert disclosure['provider_list'] == ['seedream','wan_r2v']
    assert disclosure['terms_version'] == 'actual-v3' and disclosure['deletion']
    assert 'Private story title' not in preview.text
    body = {'token':invite,'accepted':True,'display_name':'B','terms_version':'actual-v3','disclosure_hash':disclosure['disclosure_hash']}
    missing = client.post(f'/consent-invites/{invite}/confirm',json={k:v for k,v in body.items() if k not in ('token','disclosure_hash')})
    assert missing.status_code == 422
    old = client.post('/consent-invites/confirm',json=body)
    assert old.status_code == 200, old.text
    with get_session_factory()() as db:
        before = db.scalar(select(WeddingConsent).where(WeddingConsent.project_id==project)).id
    # Expired/lost browser session is recovered by a new one-use invitation, not new consent.
    from datetime import timedelta
    from app.models import utc_now
    with get_session_factory()() as db:
        person=db.scalar(select(WeddingParticipant).where(WeddingParticipant.project_id==project,WeddingParticipant.person_key=='B'))
        person.session_expires_at=utc_now()-timedelta(seconds=1)
        db.commit()
    assert client.get(f'/wedding-projects/{project}',headers={'Authorization':'Bearer '+old.json()['access_token']}).status_code==401
    access = client.post(f'/wedding-projects/{project}/access-invites',headers=headers)
    assert access.status_code == 201, access.text
    token = access.json()['invite_token']
    resumed = client.post('/consent-invites/preview',json={'token':token}).json()
    assert resumed['kind'] == 'access'
    mismatch=client.post('/consent-invites/confirm',json={**body,'token':token,'disclosure_hash':'0'*64})
    assert mismatch.status_code==409
    renewed = client.post('/consent-invites/confirm',json={**body,'token':token,'disclosure_hash':resumed['disclosure_hash']})
    assert renewed.status_code == 200, renewed.text
    assert renewed.json()['access_token'] != old.json()['access_token']
    assert client.get(f'/wedding-projects/{project}',headers={'Authorization':'Bearer '+old.json()['access_token']}).status_code == 401
    assert client.get(f'/wedding-projects/{project}',headers={'Authorization':'Bearer '+renewed.json()['access_token']}).status_code == 200
    assert client.post('/consent-invites/confirm',json={**body,'token':token,'disclosure_hash':resumed['disclosure_hash']}).status_code == 409
    with get_session_factory()() as db:
        assert list(db.scalars(select(WeddingConsent.id).where(WeddingConsent.project_id==project))) == [before]

    from app.models import ConsentInvite
    from app.security import hash_code
    expired=client.post(f'/wedding-projects/{project}/access-invites',headers=headers).json()['invite_token']
    with get_session_factory()() as db:
        invite=db.scalar(select(ConsentInvite).where(ConsentInvite.token_hash==hash_code(expired)))
        invite.expires_at=utc_now()-timedelta(seconds=1)
        db.commit()
    assert client.post('/consent-invites/preview',json={'token':expired}).status_code==410
