"""Explicit immutable content revisions with exact, recorded input reuse."""
from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from app.database import get_db
from app.models import StoryVersion, StoryFact, StoryboardShot, ParticipantReview, RenderVersion, GenerationJob, utc_now
from app.schemas import StoryboardSubmitRequest, StoryVersionResponse
from app.wedding_service import get_wedding_actor

router = APIRouter(tags=['wedding revisions'])
FIELDS = ('shot_index','shot_type','duration_ms','person_keys','description','subtitle','creative_scene')


def apply_draft(db, project, story, body, *, source=None):
    previous = source or story
    old_facts = list(previous.facts)
    old_shots = {s.shot_index:s for s in previous.shots}
    if any(i < 0 or i >= len(body.facts) for s in body.shots for i in s.fact_indexes):
        raise HTTPException(422,'FACT_REFERENCE_INVALID')
    if len({s.shot_index for s in body.shots}) != len(body.shots):
        raise HTTPException(422,'SHOT_INDEX_DUPLICATE')
    changed_facts = {f.id for i,f in enumerate(old_facts) if i >= len(body.facts)
        or (f.source_field,f.text) != (body.facts[i].source_field,body.facts[i].text)}
    unchanged = {}
    for incoming in body.shots:
        old = old_shots.get(incoming.shot_index)
        refs = [i for i,f in enumerate(old_facts) if old and f.id in old.fact_ids]
        unchanged[incoming.shot_index] = bool(old and not set(old.fact_ids)&changed_facts
            and incoming.fact_indexes == refs and all(getattr(old,k)==getattr(incoming,k) for k in FIELDS if k != 'subtitle'))
        if incoming.shot_type == 'scene_ai' and incoming.person_keys or incoming.shot_type == 'person_ai' and not incoming.person_keys:
            raise HTTPException(422,'STORYBOARD_SCOPE_INVALID')
    if not source:
        story.facts.clear()
    story.story_text = body.story_text
    for fact in body.facts:
        story.facts.append(StoryFact(source_field=fact.source_field,text=fact.text))
    db.flush()
    facts = list(story.facts)
    for incoming in body.shots:
        old = old_shots.get(incoming.shot_index)
        same = unchanged[incoming.shot_index]
        if source or old is None:
            shot = StoryboardShot(project_id=project.id,story_version_id=story.id,project_version=project.current_version,
                **{k:getattr(incoming,k) for k in FIELDS},fact_ids=[facts[i].id for i in incoming.fact_indexes],status='draft')
            story.shots.append(shot)
            db.flush()
        else:
            shot = old
            for k in FIELDS:
                setattr(shot,k,getattr(incoming,k))
            shot.fact_ids = [facts[i].id for i in incoming.fact_indexes]
        if same and source:
            shot.source_shot_id = old.id
            shot.frame_asset_id = old.frame_asset_id
            shot.selected_asset_id = old.selected_asset_id
            shot.selected_candidate_id = old.selected_candidate_id
            shot.generation_attempts = old.generation_attempts
            shot.status = old.status
            for review in old.reviews:
                if review.render_version_id:
                    continue
                db.add(ParticipantReview(project_id=project.id,participant_id=review.participant_id,person_key=review.person_key,
                    project_version=project.current_version,shot_id=shot.id,asset_id=review.asset_id,result=review.result,
                    confirmed_at=review.confirmed_at,carried_from_review_id=review.id))
        elif not same:
            if not source:
                from app.wedding_guards import invalidate_shots
                invalidate_shots(db,project,[shot])
            shot.source_shot_id = None
            shot.frame_asset_id = shot.selected_asset_id = shot.selected_candidate_id = None
            shot.generation_attempts = 0
            shot.status = 'draft'
    if not source:
        wanted = {s.shot_index for s in body.shots}
        for shot in list(story.shots):
            if shot.shot_index not in wanted:
                story.shots.remove(shot)
    db.flush()


@router.post('/wedding-projects/{project_id}/storyboard/revisions',response_model=StoryVersionResponse,status_code=201)
def revise(project_id:str,body:StoryboardSubmitRequest,if_match:str|None=Header(default=None),
           actor=Depends(get_wedding_actor),db=Depends(get_db)):
    from app.routers.wedding import _locked_draft_project, _current_story, _check_version, _story_response
    from app.wedding_creation import draft_fingerprint, cancel_project_creation
    project = _locked_draft_project(db,actor,project_id,if_match)
    _check_version(project,body.version)
    source = _current_story(db,project)
    if not source.owner_approved_at:
        raise HTTPException(409,'APPROVED_STORY_REQUIRED')
    # Quarantine unresolved paid input independently of revision/idempotency keys.
    from app.models import WeddingBillingReceipt
    if db.scalar(select(WeddingBillingReceipt.id).where(WeddingBillingReceipt.project_id==project.id,
            WeddingBillingReceipt.settled.is_(False),WeddingBillingReceipt.state.in_(['unknown','dispatching','running']))):
        raise HTTPException(409,'PROVIDER_RECONCILIATION_REQUIRED')
    cancel_project_creation(db,project,'PROJECT_VERSION_STALE')
    from app.wedding_service import release_generation_budget
    for job in db.scalars(select(GenerationJob).where(GenerationJob.project_id==project.id,
            GenerationJob.status.in_(['queued','processing','cancel_requested']))):
        if job.status=='queued' and not job.started_at:
            release_generation_budget(db,job);job.status='cancelled'
        else:
            job.status='cancel_requested';job.cancel_requested_at=utc_now()
    project.current_version += 1
    project.frame_approved_version = None
    project.status = 'story_review'
    story = StoryVersion(project_id=project.id,version=project.current_version,story_text=body.story_text,style=project.style,status='draft')
    db.add(story); db.flush()
    apply_draft(db,project,story,body,source=source)
    db.commit()
    return _story_response(story,draft_fingerprint(db,project))
