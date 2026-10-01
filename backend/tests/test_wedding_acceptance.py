"""Acceptance runner uses mocked application HTTP only; never dispatch providers."""
import copy
import json

import pytest

from scripts import wedding_acceptance as runner


def manifest():
    return {"schema_version": 1, "samples": [
        {"project_id": f"p{i}", "version": 1, "token_env": f"WEDDING_ACTOR_{i}",
         "adult_authorized": True, "provider_scope_checked": True,
         "materials_checked": True, "commercial_music_checked": True,
         "music_track_id": "licensed-track", "categories": {
             "a_closeup": "s1", "both": "s2", "side_turn": "s3", "holding_hands": "s4"}}
        for i in range(10)]}


def snapshot(sample=None):
    sample = sample or manifest()["samples"][0]
    shots, candidates, assets = [], [], []
    for i in range(1, 19):
        kind = "person_ai" if i <= 8 else "scene_ai" if i <= 12 else "photo"
        people = ["A"] if i == 1 else ["A", "B"] if i <= 8 else []
        asset_id = f"asset{i}"
        shots.append(dict(id=f"s{i}", project_version=1, shot_type=kind, duration_ms=5000,
                          person_keys=people, selected_asset_id=asset_id,
                          selected_candidate_id=f"c{i}" if i <= 12 else None,
                          identity_reviews=[dict(person_key=p, asset_id=asset_id, result="approved") for p in people]))
        asset = dict(id=asset_id, mime="video/mp4" if i <= 12 else "image/jpeg", expires_at="2099-01-01T00:00:00Z")
        assets.append(asset)
        if i <= 12:
            candidates.append(dict(id=f"c{i}", shot_id=f"s{i}", generation_job_id="job1", asset_id=asset_id,
                                   asset=asset, provider="wan_r2v" if i <= 8 else "wan_i2v_flash",
                                   model_version=runner.MODELS["character_video" if i <= 8 else "scene_video"], review_result="approved"))
    music = dict(track_id="licensed-track", license_id="license1", license_version="v1",
                 perpetual_embedded_delivery=True, allowed_uses=["customer_embedded_delivery", "wedding_playback"],
                 valid_from="2020-01-01T00:00:00Z", valid_until="2099-01-01T00:00:00Z", sha256="a" * 64)
    return dict(project=dict(id=sample["project_id"], version=1, participants=[dict(person_key=p, consent_status="active") for p in "AB"]),
                capabilities=dict(mode="live", demo_only=False, real_export_available=True,
                                  providers={k: dict(available=True, provider=v) for k, v in runner.PROVIDERS.items()}),
                readiness=dict(ready=True, demo_bypass=False, people=[dict(person_key=p, ready=True, usable_count=3, primary_photo_asset_id=f"primary{p}") for p in "AB"]),
                story=dict(version=1, status="approved", shots=shots), candidates=candidates, assets=assets,
                music=music)


def evidence():
    return dict(project_id="p0", version=1, render_id="render1", rating=4, willing=True,
                role_facts_correct=True, commercial_music_rights=True,
                motion={c: True for c in runner.CATEGORIES},
                bill=dict(authoritative=True, reference_id="bill1", complete=True,
                          model_fen=10000, music_fen=1000, render_fen=1000, storage_fen=1000, other_fen=2000),
                timing=dict(start="2026-10-01T00:00:00Z", end="2026-10-01T00:40:00Z",
                            human_intervals=[dict(start="2026-10-01T00:10:00Z", end="2026-10-01T00:20:00Z")]))


def final_render(snap):
    return dict(id="render1", project_version=1, status="approved", invalidated_at=None,
                output_duration_ms=90000, preview_asset_id="preview", export_asset_id="final", music=snap["music"],
                timeline=[dict(shot_id=s["id"], asset_id=s["selected_asset_id"], shot_type=s["shot_type"], duration_ms=s["duration_ms"]) for s in snap["story"]["shots"]],
                identity_reviews=[dict(person_key=p, asset_id="preview", result="approved") for p in "AB"])


def test_manifest_requires_exact_distinct_ten_and_safe_only_fields():
    runner.validate_manifest(manifest())
    for alter in [lambda m: m["samples"].pop(), lambda m: m["samples"].__setitem__(1, m["samples"][0]),
                  lambda m: m["samples"][0].update(token="secret"),
                  lambda m: m["samples"][0]["categories"].update(both="s1"),
                  lambda m: m["samples"][0].update(adult_authorized=False)]:
        bad = manifest(); alter(bad)
        with pytest.raises(runner.Rejected):
            runner.validate_manifest(bad)


@pytest.mark.parametrize("url", ["http://example.com", "https://user:secret@example.com", "https://example.com/?token=x", "https://example.com/#x", "http://127.0.0.1.evil", "https://example.com/api", "https://example.com\\@evil"])
def test_rejects_unsafe_base_urls(url):
    with pytest.raises(runner.Rejected):
        runner.Transport(url, "TOKEN")


@pytest.mark.parametrize("mutate", [
    lambda s: s["project"]["participants"][0].update(consent_status="revoked"),
    lambda s: s["readiness"]["people"][0].update(usable_count=2),
    lambda s: s["readiness"]["people"][0].update(primary_photo_asset_id=None),
    lambda s: s["capabilities"].update(mode="demo"),
    lambda s: s["capabilities"]["providers"]["character_video"].update(available=False),
    lambda s: s["music"].update(perpetual_embedded_delivery=False),
    lambda s: s["story"]["shots"][0].update(shot_type="scene_ai"),
])
def test_preflight_fails_closed(mutate):
    snap = snapshot(); mutate(snap)
    with pytest.raises(runner.Rejected):
        runner.check_preflight(manifest()["samples"][0], snap)


def test_current_exact_asset_reviews_required():
    snap = snapshot(); shot = snap["story"]["shots"][0]
    assert runner.selected_video(shot, snap["candidates"])
    shot["identity_reviews"][0]["asset_id"] = "old"
    assert not runner.selected_video(shot, snap["candidates"])


class FakeAPI:
    def __init__(self, snap=None, fail=False):
        self.snap = snap or snapshot(); self.calls = []; self.fail = fail

    def request(self, method, path, body=None, key=None):
        self.calls.append((method, path, body, key))
        if method == "POST":
            assert json.loads(self.checkpoint.read_text())["state"] == "submitting"
            if self.fail:
                raise runner.Rejected("TRANSPORT_FAILED")
            return dict(id="job1", project_id="p0", project_version=1, status="queued")
        if "/quote" in path:
            return dict(id="quote1", project_version=1)
        if "generation-jobs/" in path:
            return dict(id="job1", project_id="p0", project_version=1, status="processing")
        if path.endswith("candidates"):
            return self.snap["candidates"]
        raise AssertionError(path)


def test_no_post_without_explicit_flag(tmp_path):
    api = FakeAPI(); api.checkpoint = tmp_path / "cp.json"
    with pytest.raises(runner.Rejected):
        runner.dispatch(api, manifest()["samples"][0], snapshot(), api.checkpoint, "generate", "a_closeup", False)
    assert api.calls == []


def test_lost_response_persists_unknown_and_never_reposts(tmp_path):
    api = FakeAPI(fail=True); api.checkpoint = tmp_path / "cp.json"
    with pytest.raises(runner.Rejected):
        runner.dispatch(api, manifest()["samples"][0], snapshot(), api.checkpoint, "generate", "a_closeup", True)
    assert json.loads(api.checkpoint.read_text())["state"] == "unknown"
    with pytest.raises(runner.Rejected):
        runner.dispatch(api, manifest()["samples"][0], snapshot(), api.checkpoint, "generate", "a_closeup", True)
    assert len([c for c in api.calls if c[0] == "POST"]) == 1
    with pytest.raises(runner.Rejected):
        runner.resume(api, manifest()["samples"][0], api.checkpoint)


def test_resolved_unknown_job_needs_exact_candidate_scope(tmp_path):
    api = FakeAPI(fail=True); api.checkpoint = tmp_path / "cp.json"
    with pytest.raises(runner.Rejected):
        runner.dispatch(api, manifest()["samples"][0], snapshot(), api.checkpoint, "generate", "a_closeup", True)
    with pytest.raises(runner.Rejected):
        runner.resume(api, manifest()["samples"][0], api.checkpoint, "job1")
    api.snap["candidates"] = api.snap["candidates"][:1]
    assert runner.resume(api, manifest()["samples"][0], api.checkpoint, "job1")["status"] == "processing"


def test_fourth_candidate_is_rejected_before_quote(tmp_path):
    snap = snapshot(); snap["candidates"] += [dict(snap["candidates"][0], id=f"extra{i}") for i in range(2)]
    api = FakeAPI(snap); api.checkpoint = tmp_path / "cp.json"
    with pytest.raises(runner.Rejected):
        runner.dispatch(api, manifest()["samples"][0], snap, api.checkpoint, "generate", "a_closeup", True)
    assert api.calls == []


def test_metrics_require_real_media_humans_and_official_total_bill():
    snap = snapshot(); snap["assets"].append(dict(id="final", mime="video/mp4", expires_at="2099-01-01T00:00:00Z"))
    good = runner.assess(manifest()["samples"][0], snap, final_render(snap), evidence())
    assert good["qualified"] and good["standard"] and good["cost_pass"] and good["minutes"] == 30
    for field in ["rating", "willing", "role_facts_correct", "bill", "timing"]:
        bad = evidence(); bad.pop(field)
        assert not runner.assess(manifest()["samples"][0], snap, final_render(snap), bad)["complete"]
    bad = evidence(); bad["bill"]["authoritative"] = False
    assert not runner.assess(manifest()["samples"][0], snap, final_render(snap), bad)["cost_pass"]
    bad = evidence(); bad["bill"]["other_fen"] += 1
    assert not runner.assess(manifest()["samples"][0], snap, final_render(snap), bad)["cost_pass"]


def test_summary_fixed_denominators_thresholds_and_goals():
    row = dict(complete=True, qualified=True, standard=True, cost_pass=True, happy=True,
               facts=True, rights=True, motion_passes=2, stress_passes=2, minutes=30)
    rows = [dict(row) for _ in range(10)]
    rows[0].update(qualified=False, standard=False, cost_pass=False, happy=False, motion_passes=0)
    rows[1].update(qualified=False, standard=False, cost_pass=False, happy=False)
    assert runner.summarize(rows)["status"] == "PASS"
    rows[2]["cost_pass"] = False
    assert runner.summarize(rows)["status"] == "FAIL"
    rows[2]["complete"] = False
    assert runner.summarize(rows)["status"] == "INCOMPLETE"
    assert runner.summarize(rows)["motion_denominator"] == 20
    assert runner.summarize(rows)["stress_denominator"] == 20
    with pytest.raises(runner.Rejected):
        runner.summarize(rows[:9])


def test_public_fallback_keeps_ai_type_and_requires_exact_photo_approval():
    snap = snapshot()
    for shot in snap["story"]["shots"][6:8]:
        shot.update(status="fallback", selected_candidate_id=None)
        next(a for a in snap["assets"] if a["id"] == shot["selected_asset_id"])["mime"] = "image/jpeg"
    snap["assets"].append(dict(id="final", mime="video/mp4", expires_at="2099-01-01T00:00:00Z"))
    row = runner.assess(manifest()["samples"][0], snap, final_render(snap), evidence())
    assert row["qualified"] and not row["standard"] and row["photo"] == 8 and row["person_ai"] == 6
    snap["story"]["shots"][7]["identity_reviews"] = []
    assert not runner.assess(manifest()["samples"][0], snap, final_render(snap), evidence())["qualified"]


def test_batch_targets_exclude_pending_unselected_valid_candidates(tmp_path):
    snap = snapshot()
    for shot in snap["story"]["shots"][:12]:
        shot["selected_candidate_id"] = None
    api = FakeAPI(snap); api.checkpoint = tmp_path / "cp.json"
    with pytest.raises(runner.Rejected, match="NO_PENDING_SHOTS"):
        runner.dispatch(api, manifest()["samples"][0], snap, api.checkpoint, "generate", None, True)


def test_stress_failures_do_not_change_default_motion_denominator():
    rows = [dict(complete=True, qualified=True, standard=True, cost_pass=True, happy=True,
                 facts=True, rights=True, motion_passes=2, stress_passes=0, minutes=90) for _ in range(10)]
    summary = runner.summarize(rows)
    assert summary["status"] == "PASS" and summary["stress_passes"] == 0
    assert summary["latency_goal_met"] is False


def test_selected_candidate_api_clears_direct_asset_id():
    snap = snapshot(); shot = snap["story"]["shots"][0]
    shot["selected_asset_id"] = None
    assert runner.selected_video(shot, snap["candidates"])


def test_proven_other_job_targets_do_not_spend_this_category_attempt(tmp_path):
    snap = snapshot()
    snap["jobs"] = [dict(id=f"other{i}", project_version=1, status="completed", total_shots=1) for i in range(3)]
    for i in range(3):
        snap["candidates"].append(dict(snap["candidates"][1], id=f"otherc{i}", generation_job_id=f"other{i}"))
    api = FakeAPI(snap); api.checkpoint = tmp_path / "cp.json"
    assert runner.dispatch(api, manifest()["samples"][0], snap, api.checkpoint, "generate", "a_closeup", True)["status"] == "SUBMITTED"


def test_known_scope_receipts_exclude_failed_other_jobs(tmp_path):
    snap = snapshot(); sample = manifest()["samples"][0]
    api = FakeAPI(snap); api.checkpoint = tmp_path / "old.json"
    runner.dispatch(api, sample, snap, api.checkpoint, "generate", "both", True)
    receipt = api.checkpoint
    snap["jobs"] = [dict(id="job1", project_version=1, status="failed", total_shots=1)]
    api.checkpoint = tmp_path / "new.json"
    assert runner.dispatch(api, sample, snap, api.checkpoint, "generate", "a_closeup", True, receipts=[receipt])["status"] == "SUBMITTED"
