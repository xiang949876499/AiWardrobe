"""Acceptance runner uses mocked application HTTP only; never dispatch providers."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from email.message import Message
from io import BytesIO

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
        shots.append(dict(id=f"s{i}", project_version=1, shot_type=kind, duration_ms=5000, frame_asset_id=f"frame{i}",
                          person_keys=people, selected_asset_id=asset_id,
                          selected_candidate_id=f"c{i}" if i <= 12 else None,
                          identity_reviews=[dict(person_key=p, asset_id=asset_id, result="approved") for p in people]))
        asset = dict(id=asset_id, mime="video/mp4" if i <= 12 else "image/jpeg", expires_at="2099-01-01T00:00:00Z")
        assets.append(asset)
        assets.append(dict(id=f"frame{i}", mime="image/png", person_key=None, generated=True, expires_at="2099-01-01T00:00:00Z"))
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
                motion={c: {"passed": True, "candidate_id": f"c{i}"} for i, c in enumerate(runner.CATEGORIES, 1)},
                bill=dict(authoritative=True, reference_id="bill1", complete=True,
                          model_fen=10000, music_fen=1000, render_fen=1000, storage_fen=1000, other_fen=2000),
                timing=dict(start="2026-10-01T00:00:00Z", end="2026-10-01T00:40:00Z",
                            human_intervals=[dict(start="2026-10-01T00:10:00Z", end="2026-10-01T00:20:00Z")]))


def final_render(snap):
    return dict(id="render1", project_version=1, status="approved", invalidated_at=None,
                output_duration_ms=90000, preview_asset_id="preview", export_asset_id="final", music=snap["music"], owner_approved_at="2026-10-01T01:00:00Z",
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


class Response(BytesIO):
    def __init__(self, body=b'{"safe":true}', url="https://example.com/data", status=200, length=None, mime="application/json"):
        super().__init__(body); self.url = url; self.status = status; self.headers = Message()
        self.headers["Content-Type"] = mime
        if length is not None:
            self.headers["Content-Length"] = str(length)

    def geturl(self):
        return self.url


class Opener:
    def __init__(self, response):
        self.response = response; self.requests = []

    def open(self, request, timeout):
        self.requests.append(request)
        assert timeout == 15
        return self.response


def test_transport_gates_then_uses_only_named_token(monkeypatch):
    api = runner.Transport("https://example.com", "WEDDING_TOKEN")
    with pytest.raises(runner.Rejected, match="EXECUTE_PAID_REQUIRED"):
        api.request("POST", "/data", {})
    with pytest.raises(runner.Rejected, match="TOKEN_ENV_MISSING"):
        api.request("GET", "/data")
    monkeypatch.setenv("WEDDING_TOKEN", "private-secret-value")
    api.opener = Opener(Response())
    assert api._request_local("GET", "/data") == {"safe": True}
    assert api.opener.requests[0].get_header("Authorization") == "Bearer private-secret-value"
    api = runner.Transport("http://127.0.0.1:8031", "WEDDING_TOKEN", True)
    api.opener = Opener(Response(url="http://127.0.0.1:8031/data"))
    api._request_local("POST", "/data", {"version": 1}, "idempotency-key")
    assert api.opener.requests[0].get_header("Idempotency-key") == "idempotency-key"


@pytest.mark.parametrize("response,code", [
    (lambda: Response(url="https://other.example/data"), "ORIGIN_CHANGED"),
    (lambda: Response(status=302), "HTTP_REJECTED"),
    (lambda: Response(length=runner.MAX_BYTES + 1), "RESPONSE_TOO_LARGE"),
    (lambda: Response(body=b"x" * (runner.MAX_BYTES + 1)), "RESPONSE_TOO_LARGE"),
    (lambda: Response(mime="text/html"), "RESPONSE_NOT_JSON"),
    (lambda: Response(body=b"private non-json error"), "TRANSPORT_FAILED"),
])
def test_transport_response_bounds_and_private_error_suppression(monkeypatch, response, code):
    monkeypatch.setenv("TOKEN", "private-secret")
    api = runner.Transport("https://example.com", "TOKEN")
    api.opener = Opener(response())
    with pytest.raises(runner.Rejected, match=code) as error:
        api._request_local("GET", "/data")
    assert "private" not in str(error.value)


def test_redirect_and_cross_origin_path_rejected(monkeypatch):
    with pytest.raises(runner.Rejected, match="REDIRECT_REFUSED"):
        runner.NoRedirect().redirect_request(None, None, 302, None, None, "https://other.example")
    api = runner.Transport("https://example.com", "TOKEN")
    for path in ("//evil.example/data", "https://evil.example", "/data?token=private", "/../data"):
        with pytest.raises(runner.Rejected, match="UNSAFE_REQUEST_PATH"):
            api.request("GET", path)


def test_transport_timeout_and_http_error_are_bounded(monkeypatch):
    monkeypatch.setenv("TOKEN", "private-secret")
    api = runner.Transport("https://example.com", "TOKEN")
    api.opener = Opener(Response())
    ticks = iter([0, 16])
    monkeypatch.setattr(runner.time, "monotonic", lambda: next(ticks))
    with pytest.raises(runner.Rejected, match="RESPONSE_TIMEOUT"):
        api._request_local("GET", "/data")
    monkeypatch.setattr(runner.time, "monotonic", lambda: 0)
    def fail(*args, **kwargs):
        raise runner.HTTPError("https://example.com/private", 403, "private token", {}, None)
    monkeypatch.setattr(api.opener, "open", fail)
    with pytest.raises(runner.Rejected, match="HTTP_REJECTED"):
        api._request_local("GET", "/data")


def test_cli_offline_is_network_free_and_example_cannot_certify(tmp_path, monkeypatch, capsys):
    path = tmp_path / "manifest.json"; path.write_text(json.dumps(manifest()))
    def no_network(*args, **kwargs):
        raise AssertionError("offline opened network")
    monkeypatch.setattr(runner, "Transport", no_network)
    assert runner.main(["--manifest", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "INCOMPLETE"
    assert runner.main(["--manifest", str(path), "--output", str(path)]) == 2
    assert "OUTPUT_OVERWRITES_INPUT" in capsys.readouterr().out
    assert runner.main(["--manifest", str(path) + "missing"]) == 2
    assert "INPUT_UNREADABLE" in capsys.readouterr().out


class SnapshotAPI(FakeAPI):
    def request(self, method, path, body=None, key=None):
        self.calls.append((method, path, body, key))
        assert method == "GET"
        if path == "/wedding-capabilities":
            return self.snap["capabilities"]
        if path == "/wedding-music":
            return {"tracks": [self.snap["music"]]}
        if "/renders/" in path:
            return final_render(self.snap)
        suffix = path.split("/")[-1]
        mapping = {"storyboard": "story", "photo-readiness": "readiness", "candidates": "candidates", "assets": "assets", "generation-jobs": "jobs"}
        if suffix in mapping:
            return self.snap.get(mapping[suffix], [])
        return dict(self.snap["project"], id=suffix)


def test_cli_preflight_and_report_never_mutate_even_with_flag(tmp_path, monkeypatch, capsys):
    path = tmp_path / "manifest.json"; path.write_text(json.dumps(manifest()))
    api = SnapshotAPI()
    monkeypatch.setattr(runner, "Transport", lambda *args: api)
    assert runner.main(["--manifest", str(path), "--stage", "preflight", "--execute-paid"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "PREFLIGHT_OK"
    assert runner.main(["--manifest", str(path), "--stage", "report"]) == 0
    assert json.loads(capsys.readouterr().out)["summary"]["status"] == "INCOMPLETE"
    assert all(c[0] == "GET" for c in api.calls)


def test_render_stage_posts_once_then_readonly_resume(tmp_path):
    sample = manifest()["samples"][0]; snap = snapshot()
    class RenderAPI(FakeAPI):
        def request(self, method, path, body=None, key=None):
            self.calls.append((method, path, body, key))
            if method == "POST":
                assert json.loads(self.checkpoint.read_text())["state"] == "submitting"
                assert body == {"version": 1, "music_track_id": "licensed-track"}
            return final_render(self.snap)
    api = RenderAPI(snap); api.checkpoint = tmp_path / "cp.json"
    assert runner.dispatch(api, sample, snap, api.checkpoint, "render", execute_paid=True)["job_id"] == "render1"
    assert runner.resume(api, sample, api.checkpoint)["status"] == "approved"
    assert [c[0] for c in api.calls] == ["POST", "GET"]


def test_crash_submitting_and_wrong_resolution_cannot_resume(tmp_path):
    sample = manifest()["samples"][0]; api = FakeAPI(fail=True); api.checkpoint = tmp_path / "cp.json"
    with pytest.raises(runner.Rejected):
        runner.dispatch(api, sample, snapshot(), api.checkpoint, "generate", "a_closeup", True)
    cp = json.loads(api.checkpoint.read_text()); cp["state"] = "submitting"
    api.checkpoint.write_text(json.dumps(cp))
    with pytest.raises(runner.Rejected, match="AMBIGUOUS"):
        runner.resume(api, sample, api.checkpoint)
    api.snap["candidates"] = []
    with pytest.raises(runner.Rejected, match="SCOPE_UNPROVEN"):
        runner.resume(api, sample, api.checkpoint, "job1")
    api.base_url = "https://different.example"
    with pytest.raises(runner.Rejected, match="ORIGIN_MISMATCH"):
        runner.resume(api, sample, api.checkpoint, "job1")


def test_stale_lock_and_bad_job_scope_stop(tmp_path):
    cp = tmp_path / "cp.json"
    with runner.checkpoint_lock(cp):
        with pytest.raises(runner.Rejected, match="CHECKPOINT_LOCKED"):
            with runner.checkpoint_lock(cp):
                pass
    with pytest.raises(runner.Rejected, match="JOB_PROJECT_MISMATCH"):
        runner.check_job_scope(dict(project_version=1, project_id="wrong"), dict(version=1, project_id="p0", action="generate"))


@pytest.mark.parametrize("mutate", [
    lambda e: e.update(token="private"), lambda e: e.update(rating=True),
    lambda e: e["bill"].update(other_fen=-1), lambda e: e["bill"].update(reference_id="https://private"),
    lambda e: e["timing"]["human_intervals"].append(e["timing"]["human_intervals"][0]),
    lambda e: e["timing"].update(end="2020-01-01T00:00:00Z"),
    lambda e: e["timing"].update(start="2026-10-01T00:00:00"),
])
def test_bad_evidence_never_becomes_success(mutate):
    e = evidence(); mutate(e)
    with pytest.raises(runner.Rejected):
        runner.validate_evidence([e])


def test_motion_human_record_is_bound_to_current_candidate():
    snap = snapshot(); e = evidence()
    e["motion"]["a_closeup"]["candidate_id"] = "old-candidate"
    row = runner.assess(manifest()["samples"][0], snap, final_render(snap), e)
    assert row["motion_passes"] == 1 and not row["complete"]


def test_missing_actual_owner_approval_cannot_qualify_final():
    snap = snapshot(); snap["assets"].append(dict(id="final", mime="video/mp4", expires_at="2099-01-01T00:00:00Z"))
    render = final_render(snap); render["owner_approved_at"] = None
    assert not runner.assess(manifest()["samples"][0], snap, render, evidence())["qualified"]


def test_scene_frame_must_not_carry_newlywed_portrait_scope():
    snap = snapshot()
    next(a for a in snap["assets"] if a["id"] == "frame9")["person_key"] = "A"
    with pytest.raises(runner.Rejected, match="SCENE_FRAME_PERSON_SCOPE"):
        runner.check_preflight(manifest()["samples"][0], snap)


def test_placeholder_offline_manifest_is_incomplete_without_attesting_consent(tmp_path, capsys):
    data = manifest()
    for sample in data["samples"]:
        sample["adult_authorized"] = False
    path = tmp_path / "manifest.json"; path.write_text(json.dumps(data))
    assert runner.main(["--manifest", str(path)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "INCOMPLETE" and output["prepared"] is False
    assert runner.main(["--manifest", str(path), "--stage", "preflight"]) == 2


def test_failed_unscoped_jobs_cannot_disappear_from_motion_attempts():
    snap = snapshot()
    snap["jobs"] = [dict(id=f"lost{i}", project_version=1, status="failed", total_shots=1) for i in range(3)]
    row = runner.assess(manifest()["samples"][0], snap, final_render(snap), evidence())
    assert row["motion_passes"] == 0
    assert row["selected_evidence"][0]["attempts"] == 4


def test_story_must_start_with_exact_standard_composition():
    snap = snapshot(); snap["story"]["shots"][7].update(shot_type="scene_ai", person_keys=[])
    with pytest.raises(runner.Rejected, match="STANDARD_STORY_COMPOSITION_REQUIRED"):
        runner.check_preflight(manifest()["samples"][0], snap)


def test_failure_evidence_exposes_fixed_codes_never_private_provider_text():
    snap = snapshot()
    snap["jobs"] = [dict(id="failed1", project_version=1, status="failed", total_shots=1,
                         error_code="PROVIDER_OUTCOME_UNKNOWN")]
    row = runner.assess(manifest()["samples"][0], snap, final_render(snap), evidence())
    assert row["failures"] == [{"job_id": "failed1", "reason": "PROVIDER_OUTCOME_UNKNOWN"}]
    snap["jobs"][0]["error_code"] = "private signed URL and token"
    row = runner.assess(manifest()["samples"][0], snap, final_render(snap), evidence())
    assert row["failures"][0]["reason"] == "UNCLASSIFIED_SERVER_FAILURE"
    assert "private signed" not in json.dumps(row)


def deadline_worker(tmp_path, phase):
    """Inject blocking stdlib phases only inside the disposable HTTP process."""
    driver = tmp_path / "worker.py"
    entered, late = tmp_path / "entered", tmp_path / "late"
    source = '''
import http.client, io, json, pathlib, runpy, socket, ssl, sys, time
scope = runpy.run_path(MODULE)
entered, late = pathlib.Path(ENTERED), pathlib.Path(LATE)
def block(*args, **kwargs):
    entered.write_text(PHASE)
    time.sleep(4)
    late.write_text("would continue request")
    raise OSError("synthetic slow phase")
class File:
    def __init__(self): self.lines = 0
    def readline(self, limit=-1):
        self.lines += 1
        if self.lines == 1: return b"HTTP/1.1 200 OK\\r\\n"
        if self.lines == 2 and PHASE == "headers":
            entered.write_text(PHASE)
            for _ in range(40): time.sleep(.1)
            late.write_text("would finish headers")
        return [b"", b"", b"Content-Type: application/json\\r\\n", b"Content-Length: 5\\r\\n", b"\\r\\n"][self.lines]
    def read1(self, size=-1): return block()
    def close(self): pass
class Socket:
    def setsockopt(self, *args): pass
    def sendall(self, data): pass
    def makefile(self, *args): return File()
    def close(self): pass
if PHASE == "dns": socket.getaddrinfo = block
elif PHASE == "connect":
    socket.getaddrinfo = lambda *args: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 9))] * 8
    calls = []
    def connect(*args):
        calls.append(1); entered.write_text(str(len(calls)))
        time.sleep(.25)
        if len(calls) == 8: late.write_text("would finish addresses")
        raise TimeoutError("synthetic connection delay")
    socket.socket.connect = connect
elif PHASE in ("tls", "headers", "body"):
    socket.create_connection = lambda *args, **kwargs: Socket()
    if PHASE == "tls": ssl.SSLContext.wrap_socket = block
elif PHASE == "stdin": block()
scope["http_worker_main"]()
'''
    prefix = "\n".join(f"{key} = {value!r}" for key, value in {
        "MODULE": str(Path(runner.__file__).resolve()), "ENTERED": str(entered),
        "LATE": str(late), "PHASE": phase}.items())
    driver.write_text(prefix + "\n" + source)
    return [sys.executable, "-I", "-B", str(driver)], entered, late


@pytest.mark.parametrize("phase", ["dns", "connect", "tls", "headers", "body", "stdin"])
def test_total_deadline_kills_every_http_phase_and_blocked_ipc(tmp_path, monkeypatch, phase):
    command, entered, late = deadline_worker(tmp_path, phase)
    monkeypatch.setattr(runner, "_http_worker_command", lambda: command, raising=False)
    monkeypatch.setattr(runner, "TIMEOUT", 1.0)
    monkeypatch.setenv("TOKEN", "synthetic-only")
    children = []; original_popen = subprocess.Popen
    def capture(*args, **kwargs):
        if os.name == "nt":
            assert kwargs["creationflags"] & subprocess.CREATE_NO_WINDOW
        assert "synthetic-only" not in repr(args) and kwargs["stderr"] == subprocess.DEVNULL
        child = original_popen(*args, **kwargs); children.append(child); return child
    monkeypatch.setattr(subprocess, "Popen", capture)
    api = runner.Transport("https://127.0.0.1:9" if phase == "tls" else "http://127.0.0.1:9", "TOKEN", True)
    started = time.monotonic()
    with pytest.raises(runner.Rejected, match="RESPONSE_TIMEOUT"):
        api.request("POST", "/data", {"padding": "x" * 131072}, "synthetic-key")
    assert time.monotonic() - started < 2.5
    assert entered.exists(), "regression must reach the selected phase"
    if phase == "connect":
        assert 2 <= int(entered.read_text()) < 8
    assert len(children) == 1 and children[0].poll() is not None
    assert not late.exists(), "killed worker must never resume a later paid submission"


def test_cli_malformed_http_status_never_prints_private_marker_or_traceback(tmp_path):
    path = tmp_path / "manifest.json"; path.write_text(json.dumps(manifest()))
    server = socket.socket(); server.bind(("127.0.0.1", 0)); server.listen(1); server.settimeout(8)
    port = server.getsockname()[1]
    def malformed_status():
        try:
            connection, _ = server.accept()
            with connection:
                connection.recv(8192)
                connection.sendall(b"HTTP/1.1 PRIVATE_MARKER_DO_NOT_LOG\\r\\n".replace(b"\\r\\n", b"\r\n"))
        finally:
            server.close()
    thread = threading.Thread(target=malformed_status, daemon=True); thread.start()
    result = subprocess.run([sys.executable, str(Path(runner.__file__).resolve()), "--manifest", str(path),
                             "--stage", "preflight", "--base-url", f"http://127.0.0.1:{port}"],
                            env={**os.environ, "WEDDING_ACTOR_0": "synthetic-only"},
                            capture_output=True, text=True, timeout=10)
    thread.join(2)
    assert result.returncode == 2
    assert json.loads(result.stdout) == {"status": "STOPPED", "reason": "TRANSPORT_FAILED"}
    assert "PRIVATE_MARKER" not in result.stdout + result.stderr
    assert "Traceback" not in result.stdout + result.stderr and result.stderr == ""


@pytest.mark.parametrize("error", [runner.HTTPException("PRIVATE_PROTOCOL_MARKER"), __import__("http.client").client.BadStatusLine("PRIVATE_PROTOCOL_MARKER")])
def test_all_http_protocol_exceptions_are_sanitized_at_boundary(monkeypatch, error):
    monkeypatch.setenv("TOKEN", "synthetic-only")
    api = runner.Transport("http://127.0.0.1", "TOKEN")
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(api.opener, "open", fail)
    with pytest.raises(runner.Rejected, match="^TRANSPORT_FAILED$") as rejected:
        api._request_local("GET", "/data")
    assert rejected.value.__suppress_context__ is True


def test_timed_out_post_preserves_unknown_before_worker_can_submit_late(tmp_path, monkeypatch):
    command, entered, late = deadline_worker(tmp_path, "dns")
    monkeypatch.setattr(runner, "_http_worker_command", lambda: command)
    monkeypatch.setattr(runner, "TIMEOUT", 1.0)
    monkeypatch.setenv("TOKEN", "synthetic-only")
    sample = manifest()["samples"][0]; checkpoint = tmp_path / "cp.json"
    class API:
        base_url = "http://127.0.0.1:9"
        def __init__(self): self.posts = 0
        def request(self, method, path, body=None, key=None):
            if method == "GET":
                return dict(id="quote1", project_version=1)
            self.posts += 1
            saved = json.loads(checkpoint.read_text())
            assert saved["state"] == "submitting" and saved["key"] == key and saved["quote_id"] == "quote1"
            return runner.Transport(self.base_url, "TOKEN", True).request(method, path, body, key)
    api = API()
    with pytest.raises(runner.Rejected, match="AMBIGUOUS_SUBMISSION_STOP"):
        runner.dispatch(api, sample, snapshot(), checkpoint, "generate", "a_closeup", True)
    assert json.loads(checkpoint.read_text())["state"] == "unknown" and entered.exists() and not late.exists()
    with pytest.raises(runner.Rejected, match="EXISTING_CHECKPOINT_RESUME_ONLY"):
        runner.dispatch(api, sample, snapshot(), checkpoint, "generate", "a_closeup", True)
    with pytest.raises(runner.Rejected, match="AMBIGUOUS_SUBMISSION_STOP"):
        runner.resume(api, sample, checkpoint)
    assert api.posts == 1


def test_control_input_is_bounded_before_worker_start(monkeypatch):
    monkeypatch.setenv("TOKEN", "synthetic-only")
    def forbidden(*args, **kwargs):
        raise AssertionError("oversized control must not start a worker")
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    with pytest.raises(runner.Rejected, match="REQUEST_TOO_LARGE"):
        runner.Transport("http://127.0.0.1", "TOKEN", True).request("POST", "/data", {"padding": "x" * runner.MAX_BYTES})


@pytest.mark.parametrize("source,reason", [
    ("import sys; sys.stdout.buffer.write(b'x' * (5 * 1024 * 1024))", "RESPONSE_TOO_LARGE"),
    ("print('not json PRIVATE_MARKER')", "TRANSPORT_FAILED"),
    ("print('{\"ok\":false,\"reason\":\"PRIVATE_MARKER\"}')", "TRANSPORT_FAILED"),
])
def test_worker_output_is_bounded_and_untrusted_errors_stay_private(tmp_path, monkeypatch, source, reason):
    path = tmp_path / "worker.py"; path.write_text("import sys; sys.stdin.buffer.read()\n" + source)
    monkeypatch.setattr(runner, "_http_worker_command", lambda: [sys.executable, "-I", "-B", str(path)])
    monkeypatch.setenv("TOKEN", "synthetic-only")
    with pytest.raises(runner.Rejected, match=reason):
        runner.Transport("http://127.0.0.1", "TOKEN").request("GET", "/data")


def test_worker_protocol_is_bounded_and_sanitized_in_process(monkeypatch):
    from types import SimpleNamespace
    payload = dict(base_url="http://127.0.0.1", token_env="TOKEN", token="synthetic-only",
                   execute_paid=False, method="GET", path="/data", body=None, key=None)
    output = BytesIO()
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=BytesIO(json.dumps(payload).encode())))
    monkeypatch.setattr(sys, "stdout", SimpleNamespace(buffer=output))
    monkeypatch.setattr(runner.Transport, "_request_local", lambda *args: {"safe": True})
    runner.http_worker_main()
    assert json.loads(output.getvalue()) == {"ok": True, "result": {"safe": True}}
    output.seek(0); output.truncate()
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=BytesIO(b"x" * (runner.MAX_BYTES + 1))))
    runner.http_worker_main()
    assert json.loads(output.getvalue()) == {"ok": False, "reason": "REQUEST_TOO_LARGE"}
    output.seek(0); output.truncate()
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=BytesIO(b"PRIVATE_MARKER")))
    runner.http_worker_main()
    assert json.loads(output.getvalue()) == {"ok": False, "reason": "TRANSPORT_FAILED"}


def test_production_worker_returns_successful_bounded_response(monkeypatch):
    server = socket.socket(); server.bind(("127.0.0.1", 0)); server.listen(1); server.settimeout(8)
    port = server.getsockname()[1]; received = []
    def reply():
        try:
            connection, _ = server.accept()
            with connection:
                received.append(connection.recv(8192))
                connection.sendall(b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 13\r\nConnection: close\r\n\r\n{"safe":true}')
        finally:
            server.close()
    thread = threading.Thread(target=reply, daemon=True); thread.start()
    monkeypatch.setenv("TOKEN", "synthetic-only")
    assert runner.Transport(f"http://127.0.0.1:{port}", "TOKEN").request("GET", "/data") == {"safe": True}
    thread.join(2)
    assert b"Authorization: Bearer synthetic-only" in received[0]


def test_expired_process_start_never_delivers_request_control(tmp_path, monkeypatch):
    marker = tmp_path / "control-delivered"
    driver = tmp_path / "worker.py"
    driver.write_text("import sys, pathlib\npayload = sys.stdin.buffer.read()\nif payload: pathlib.Path(" + repr(str(marker)) + ").write_text('sent')\n")
    monkeypatch.setattr(runner, "_http_worker_command", lambda: [sys.executable, "-I", "-B", str(driver)])
    monkeypatch.setattr(runner, "TIMEOUT", .2)
    monkeypatch.setenv("TOKEN", "synthetic-only")
    original_popen = subprocess.Popen; children = []
    def slow_start(*args, **kwargs):
        child = original_popen(*args, **kwargs); children.append(child)
        time.sleep(.4)
        return child
    monkeypatch.setattr(subprocess, "Popen", slow_start)
    with pytest.raises(runner.Rejected, match="RESPONSE_TIMEOUT"):
        runner.Transport("http://127.0.0.1", "TOKEN", True).request("POST", "/data", {})
    assert children[0].poll() is not None and not marker.exists()
