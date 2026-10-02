"""Bounded, operator-driven acceptance of ten prepared adult-couple projects.

Default is offline validation. No stage creates consents, approvals, selections,
or exports. A submission checkpoint is write-ahead and never automatically retried.
See docs/examples/wedding-acceptance-README.md for evidence and recovery contracts.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
from http.client import HTTPException
import json
import math
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


CATEGORIES = ("a_closeup", "both", "side_turn", "holding_hands")
MODELS = {"story": "doubao-seed-2-0-pro-260215", "frame": "doubao-seedream-4-5-251128",
          "character_video": "wan2.7-r2v-2026-06-12", "scene_video": "wan2.6-i2v-flash"}
PROVIDERS = dict(story="ark", frame="seedream", character_video="wan_r2v", scene_video="wan_i2v_flash")
MAX_BYTES = 2 * 1024 * 1024
TIMEOUT = 15
CLEANUP_TIMEOUT = 2
MAX_IPC_BYTES = 2 * MAX_BYTES + 1024
ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
ENV = re.compile(r"[A-Z][A-Z0-9_]{0,127}\Z")
ATTESTATIONS = ("adult_authorized", "provider_scope_checked", "materials_checked", "commercial_music_checked")
ERROR_CODES = frozenset(("PROVIDER_OUTCOME_UNKNOWN", "PROVIDER_PENDING", "PROVIDER_UNAVAILABLE",
    "PROVIDER_PREFLIGHT_FAILED", "PROVIDER_RESULT_INVALID", "PROVIDER_RESULT_UNSUPPORTED",
    "PROVIDER_CONFIGURATION_CHANGED", "BUDGET_LIMIT_EXCEEDED", "ACCOUNT_BUDGET_LIMIT_EXCEEDED",
    "ACTUAL_COST_EXCEEDS_RESERVATION", "BUDGET_RESERVATION_MISSING", "BUDGET_RESERVATION_INVALID",
    "PROJECT_VERSION_STALE", "CONSENT_REQUIRED", "STATIC_FRAME_MISSING", "STATIC_FRAME_BYTES_UNAVAILABLE",
    "RENDER_RETRY_EXHAUSTED", "RENDER_LEASE_EXPIRED"))


class Rejected(Exception):
    """Only fixed, nonprivate reason codes may leave the CLI."""


def require(condition, code):
    if not condition:
        raise Rejected(code)


def identifier(value):
    require(isinstance(value, str) and ID.fullmatch(value), "INVALID_ID")
    return value


def failure_code(value):
    return value if value in ERROR_CODES else "UNCLASSIFIED_SERVER_FAILURE" if value else None


def fields(obj, allowed, required=()):
    require(isinstance(obj, dict) and set(obj) <= set(allowed) and set(required) <= set(obj), "INVALID_FIELDS")


def timestamp(value):
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        require(dt.tzinfo is not None, "TIMESTAMP_REQUIRES_ZONE")
        return dt
    except (TypeError, ValueError, AttributeError):
        raise Rejected("INVALID_TIMESTAMP") from None


def live_asset(asset):
    return bool(asset and asset.get("expires_at") and timestamp(asset["expires_at"]) > datetime.now(timezone.utc))


def load_json(path):
    try:
        with Path(path).open("rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
        require(len(raw) <= MAX_BYTES, "INPUT_TOO_LARGE")
        return json.loads(raw)
    except (OSError, ValueError):
        raise Rejected("INPUT_UNREADABLE") from None


def validate_manifest(data, require_prepared=True):
    fields(data, ("schema_version", "samples"), ("schema_version", "samples"))
    require(data["schema_version"] == 1 and len(data["samples"]) == 10, "EXACTLY_TEN_REQUIRED")
    seen = set()
    for sample in data["samples"]:
        keys = ("project_id", "version", "token_env", "adult_authorized", "provider_scope_checked",
                "materials_checked", "commercial_music_checked", "music_track_id", "categories")
        fields(sample, keys, keys)
        project_id = identifier(sample["project_id"])
        require(project_id not in seen, "DUPLICATE_PROJECT")
        seen.add(project_id)
        require(type(sample["version"]) is int and sample["version"] > 0, "INVALID_VERSION")
        require(isinstance(sample["token_env"], str) and ENV.fullmatch(sample["token_env"]), "TOKEN_ENV_NAME_REQUIRED")
        for gate in ATTESTATIONS:
            require(type(sample[gate]) is bool and (not require_prepared or sample[gate] is True), "OPERATOR_ATTESTATION_REQUIRED")
        identifier(sample["music_track_id"])
        fields(sample["categories"], CATEGORIES, CATEGORIES)
        require(len({identifier(v) for v in sample["categories"].values()}) == 4, "DISTINCT_CATEGORY_SHOTS_REQUIRED")
    return data


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise Rejected("REDIRECT_REFUSED")


def _http_worker_command():
    return [sys.executable, "-I", "-B", str(Path(__file__).resolve()), "--_http-worker"]


def run_http_worker(payload):
    """One disposable network process; never leave a live request after timeout.

    Pipe I/O threads only move bounded bytes. They cannot perform HTTP. In particular,
    Windows communicate(input=...) writes synchronously, so it is not used here.
    """
    deadline = time.monotonic() + TIMEOUT
    encoded = json.dumps(payload, allow_nan=False, separators=(",", ":")).encode()
    require(len(encoded) <= MAX_BYTES, "REQUEST_TOO_LARGE")
    output, pipe_errors = [], []
    process = subprocess.Popen(_http_worker_command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, bufsize=0, close_fds=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)

    def write_control():
        try:
            with process.stdin as stream:
                remaining = memoryview(encoded)
                while remaining:
                    count = stream.write(remaining)
                    if not count:
                        raise OSError()
                    remaining = remaining[count:]
        except OSError:
            pipe_errors.append("TRANSPORT_FAILED")

    def read_result():
        try:
            with process.stdout as stream:
                size = 0
                while size <= MAX_IPC_BYTES:
                    chunk = stream.read(min(65536, MAX_IPC_BYTES + 1 - size))
                    if not chunk:
                        break
                    size += len(chunk)
                    output.append(chunk)
                if size > MAX_IPC_BYTES:
                    pipe_errors.append("RESPONSE_TOO_LARGE")
        except OSError:
            pipe_errors.append("TRANSPORT_FAILED")

    threads = [threading.Thread(target=write_control, daemon=True), threading.Thread(target=read_result, daemon=True)]
    try:
        require(time.monotonic() < deadline, "RESPONSE_TIMEOUT")
        for thread in threads:
            thread.start()
        process.wait(timeout=max(0, deadline - time.monotonic()))
        for thread in threads:
            thread.join(max(0, deadline - time.monotonic()))
        require(time.monotonic() <= deadline and not any(t.is_alive() for t in threads), "RESPONSE_TIMEOUT")
        require(not pipe_errors and process.returncode == 0, pipe_errors[0] if pipe_errors else "TRANSPORT_FAILED")
    except subprocess.TimeoutExpired:
        raise Rejected("RESPONSE_TIMEOUT") from None
    finally:
        # A completed timeout is emitted only after OS termination and confirmed exit.
        # This also handles Ctrl-C and unexpected parent exceptions after dispatch.
        cleanup_deadline = time.monotonic() + CLEANUP_TIMEOUT
        try:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=max(0, cleanup_deadline - time.monotonic()))
            for thread in threads:
                if thread.ident is not None:
                    thread.join(max(0, cleanup_deadline - time.monotonic()))
            for thread, stream in zip(threads, (process.stdin, process.stdout)):
                if thread.ident is None:
                    stream.close()
            require(process.poll() is not None and not any(t.is_alive() for t in threads), "TRANSPORT_CLEANUP_UNCONFIRMED")
        except (OSError, subprocess.TimeoutExpired):
            raise Rejected("TRANSPORT_CLEANUP_UNCONFIRMED") from None
    try:
        message = json.loads(b"".join(output))
        if message.get("ok") is not True:
            reasons = {"REDIRECT_REFUSED", "ORIGIN_CHANGED", "HTTP_REJECTED", "RESPONSE_TOO_LARGE",
                       "RESPONSE_NOT_JSON", "RESPONSE_TIMEOUT", "TRANSPORT_FAILED", "REQUEST_TOO_LARGE"}
            raise Rejected(message["reason"] if message.get("reason") in reasons else "TRANSPORT_FAILED")
        return message["result"]
    except (KeyError, TypeError, ValueError, AttributeError):
        raise Rejected("TRANSPORT_FAILED") from None


def http_worker_main():
    """Private pipe protocol; no logging, subprocess descendants, or network retries."""
    try:
        raw = sys.stdin.buffer.read(MAX_BYTES + 1)
        require(len(raw) <= MAX_BYTES, "REQUEST_TOO_LARGE")
        payload = json.loads(raw)
        os.environ[payload["token_env"]] = payload["token"]
        api = Transport(payload["base_url"], payload["token_env"], payload["execute_paid"])
        result = api._request_local(payload["method"], payload["path"], payload["body"], payload["key"])
        output = json.dumps({"ok": True, "result": result}, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
        require(len(output) <= MAX_IPC_BYTES, "RESPONSE_TOO_LARGE")
    except Rejected as exc:
        output = json.dumps({"ok": False, "reason": str(exc)}).encode()
    except Exception:
        output = b'{"ok":false,"reason":"TRANSPORT_FAILED"}'
    sys.stdout.buffer.write(output)
    sys.stdout.buffer.flush()


class Transport:
    """Exact origin, no ambient proxies, no redirects, bounded reads, no retries."""

    def __init__(self, base_url, token_env, execute_paid=False):
        try:
            url = urlsplit(base_url)
            require(not any(c.isspace() for c in base_url) and "\\" not in base_url, "UNSAFE_BASE_URL")
            require(url.scheme in ("https", "http") and url.hostname and url.username is None and url.password is None,
                    "UNSAFE_BASE_URL")
            require("?" not in base_url and "#" not in base_url and url.path in ("", "/"), "UNSAFE_BASE_URL")
            require(url.scheme == "https" or url.hostname in ("127.0.0.1", "localhost", "::1"), "HTTPS_OR_LOOPBACK_REQUIRED")
            require(url.port is None or 1 <= url.port <= 65535, "UNSAFE_BASE_URL")
        except ValueError:
            raise Rejected("UNSAFE_BASE_URL") from None
        require(ENV.fullmatch(token_env), "TOKEN_ENV_NAME_REQUIRED")
        self.base_url = base_url.rstrip("/")
        self.token_env = token_env
        self.execute_paid = execute_paid
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def request(self, method, path, body=None, key=None):
        token = self._validate_request(method, path, key)
        return run_http_worker(dict(base_url=self.base_url, token_env=self.token_env, token=token,
                                    execute_paid=self.execute_paid, method=method, path=path, body=body, key=key))

    def _validate_request(self, method, path, key):
        require(method == "GET" or (method == "POST" and self.execute_paid), "EXECUTE_PAID_REQUIRED")
        require(re.fullmatch(r"/[A-Za-z0-9_/-]+(?:\?shot_id=[A-Za-z0-9_-]+)?", path), "UNSAFE_REQUEST_PATH")
        require(not path.startswith("//") and "/../" not in path, "UNSAFE_REQUEST_PATH")
        token = os.environ.get(self.token_env, "")
        require(token and token.isascii() and not any(c.isspace() for c in token), "TOKEN_ENV_MISSING_OR_INVALID")
        if key:
            identifier(key)
        return token

    def _request_local(self, method, path, body=None, key=None):
        """Called only inside the disposable worker (or narrow unit tests)."""
        token = self._validate_request(method, path, key)
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json", "Accept-Encoding": "identity"}
        if key:
            headers["Idempotency-Key"] = identifier(key)
        data = None if body is None else json.dumps(body).encode()
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = Request(self.base_url + path, data=data, headers=headers, method=method)
        started = time.monotonic()
        try:
            with self.opener.open(req, timeout=TIMEOUT) as response:
                require(response.geturl() == self.base_url + path, "ORIGIN_CHANGED")
                require(200 <= response.status < 300, "HTTP_REJECTED")
                length = response.headers.get("Content-Length")
                require(length is None or 0 <= int(length) <= MAX_BYTES, "RESPONSE_TOO_LARGE")
                require(response.headers.get_content_type() == "application/json", "RESPONSE_NOT_JSON")
                chunks, count = [], 0
                while True:
                    require(time.monotonic() - started <= TIMEOUT, "RESPONSE_TIMEOUT")
                    chunk = response.read1(min(65536, MAX_BYTES + 1 - count))
                    count += len(chunk)
                    require(count <= MAX_BYTES, "RESPONSE_TOO_LARGE")
                    if not chunk:
                        break
                    chunks.append(chunk)
                return json.loads(b"".join(chunks))
        except HTTPError:
            raise Rejected("HTTP_REJECTED") from None
        except (URLError, OSError, ValueError, HTTPException):
            raise Rejected("TRANSPORT_FAILED") from None


def music_valid(music):
    return bool(music.get("license_id") and music.get("license_version") and
                music.get("perpetual_embedded_delivery") is True and
                {"customer_embedded_delivery", "wedding_playback"} <= set(music.get("allowed_uses", [])) and
                timestamp(music.get("valid_from")) <= datetime.now(timezone.utc) < timestamp(music.get("valid_until")))


def fetch_snapshot(api, sample):
    base = "/wedding-projects/" + sample["project_id"]
    result = {"capabilities": api.request("GET", "/wedding-capabilities")}
    for key, suffix in (("project", ""), ("story", "/storyboard"), ("readiness", "/photo-readiness"),
                        ("candidates", "/candidates"), ("assets", "/assets"), ("jobs", "/generation-jobs")):
        result[key] = api.request("GET", base + suffix)
    catalog = api.request("GET", "/wedding-music")
    result["music"] = next((m for m in catalog["tracks"] if m["track_id"] == sample["music_track_id"]), {})
    return result


def check_preflight(sample, snap):
    caps, project, ready, story = (snap[k] for k in ("capabilities", "project", "readiness", "story"))
    require(project["id"] == sample["project_id"] and project["version"] == sample["version"] == story["version"], "STALE_PROJECT")
    require(caps["mode"] == "live" and caps["demo_only"] is False and caps["real_export_available"] is True, "LIVE_BASELINE_REQUIRED")
    require(all(caps["providers"].get(k, {}).get("available") is True and caps["providers"][k]["provider"] == p
                for k, p in PROVIDERS.items()), "BASELINE_UNAVAILABLE")
    require({p["person_key"] for p in project["participants"] if p["consent_status"] == "active"} == {"A", "B"}, "ACTIVE_AB_CONSENT_REQUIRED")
    require(ready["ready"] is True and ready["demo_bypass"] is False and
            {p["person_key"] for p in ready["people"] if p["ready"] is True and p["usable_count"] >= 3 and p["primary_photo_asset_id"]} == {"A", "B"}, "MATERIALS_NOT_READY")
    require(story["status"] == "approved", "HUMAN_STORY_APPROVAL_REQUIRED")
    require(music_valid(snap["music"]), "LICENSED_MUSIC_REQUIRED")
    shots = {s["id"]: s for s in story["shots"]}
    require(len(shots) == 18 and sum(s["duration_ms"] for s in shots.values()) == 90000, "TIMELINE_INVALID")
    require([sum(s["shot_type"] == kind for s in shots.values()) for kind in ("person_ai", "scene_ai", "photo")] == [8, 4, 6], "STANDARD_STORY_COMPOSITION_REQUIRED")
    for shot in shots.values():
        identifier(shot["id"])
    assets = {a["id"]: a for a in snap["assets"]}
    for shot in shots.values():
        if shot["shot_type"] == "scene_ai":
            frame = assets.get(shot.get("frame_asset_id"))
            require(frame is not None and live_asset(frame), "SCENE_FRAME_UNVERIFIED")
            require(not shot["person_keys"] and frame.get("person_key") is None, "SCENE_FRAME_PERSON_SCOPE")
    for category, shot_id in sample["categories"].items():
        shot = shots.get(shot_id, {})
        require(shot.get("project_version") == sample["version"] and shot.get("shot_type") == "person_ai", "CATEGORY_SHOT_INVALID")
        roles = set(shot["person_keys"])
        require(roles == {"A"} if category == "a_closeup" else roles == {"A", "B"} if category in ("both", "holding_hands") else bool(roles), "CATEGORY_ROLE_INVALID")
    return True


def selected_video(shot, candidates):
    candidate = next((c for c in candidates if c["id"] == shot.get("selected_candidate_id") and c["shot_id"] == shot["id"]), None)
    if not candidate:
        return False
    kind = "character_video" if shot["shot_type"] == "person_ai" else "scene_video"
    return bool(candidate["provider"] == PROVIDERS[kind] and candidate["model_version"] == MODELS[kind] and
                candidate["asset"]["mime"].startswith("video/") and live_asset(candidate["asset"]) and
                candidate["asset_id"] == candidate["asset"]["id"] and candidate["review_result"] != "rejected" and
                all(any(r["person_key"] == p and r["asset_id"] == candidate["asset_id"] and r["result"] == "approved"
                        for r in shot["identity_reviews"]) for p in shot["person_keys"]))


def selected_photo(shot, assets):
    asset = assets.get(shot.get("selected_asset_id"))
    return bool((shot["shot_type"] == "photo" or shot.get("status") == "fallback") and
                not shot.get("selected_candidate_id") and live_asset(asset) and asset["mime"].startswith("image/") and
                all(any(r["person_key"] == p and r["asset_id"] == asset["id"] and r["result"] == "approved"
                        for r in shot["identity_reviews"]) for p in shot["person_keys"]))


def selected_asset_id(shot, candidates):
    if shot.get("selected_candidate_id"):
        candidate = next((c for c in candidates if c["id"] == shot["selected_candidate_id"] and c["shot_id"] == shot["id"]), None)
        require(candidate is not None, "SELECTED_CANDIDATE_MISSING")
        return identifier(candidate["asset_id"])
    return identifier(shot.get("selected_asset_id"))


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def checkpoint_lock(path):
    """Crash-left lock requires operator investigation; never timed auto-unlock."""
    lock = Path(str(path) + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise Rejected("CHECKPOINT_LOCKED_OPERATOR_REVIEW_REQUIRED") from None
    os.close(fd)
    try:
        yield
    finally:
        lock.unlink()


def sample_hash(sample):
    return hashlib.sha256(json.dumps(sample, sort_keys=True).encode()).hexdigest()


def attempt_counts(api, sample, snap, targets, receipts, ceiling=3):
    known = {}
    for path in receipts:
        receipt = load_json(path)
        validate_checkpoint(receipt, api, sample)
        require(receipt["state"] == "known", "UNRESOLVED_PRIOR_SUBMISSION")
        if receipt["action"] == "generate":
            known[identifier(receipt["job_id"])] = set(receipt["targets"])
    jobs = [j for j in snap.get("jobs", []) if j["project_version"] == sample["version"]]
    require(not any(j["status"] in ("queued", "processing", "needs_action", "cancel_requested") for j in jobs), "EXISTING_JOB_REQUIRES_OPERATOR_REVIEW")
    counts = {sid: sum(c["shot_id"] == sid for c in snap["candidates"]) for sid in targets}
    unproven = False
    for job in jobs:
        candidate_targets = {c["shot_id"] for c in snap["candidates"] if c["generation_job_id"] == job["id"]}
        scope = known.get(job["id"])
        if scope is None and job["status"] == "completed" and len(candidate_targets) == job["total_shots"]:
            scope = candidate_targets
        for sid in targets:
            # Count attempts that produced no candidate too. Candidate-bearing attempts
            # are already in counts; partial/failed unscoped jobs may include any shot.
            if sid not in candidate_targets and (scope is None or sid in scope):
                counts[sid] += 1
                unproven |= scope is None
    if ceiling is not None:
        require(all(n < ceiling for n in counts.values()), "UNPROVEN_PRIOR_JOB_SCOPE_CEILING" if unproven else "THREE_ATTEMPT_CEILING")
    return counts


def dispatch(api, sample, snap, checkpoint, action, category=None, execute_paid=False, receipts=()):
    require(execute_paid, "EXECUTE_PAID_REQUIRED")
    require(action in ("generate", "render"), "INVALID_ACTION")
    check_preflight(sample, snap)
    with checkpoint_lock(checkpoint):
        require(not Path(checkpoint).exists(), "EXISTING_CHECKPOINT_RESUME_ONLY")
        base = "/wedding-projects/" + sample["project_id"]
        shots = snap["story"]["shots"]
        cp = dict(schema_version=1, sample_hash=sample_hash(sample), project_id=sample["project_id"],
                  version=sample["version"], action=action, state="submitting", key=secrets.token_hex(24),
                  targets=[], assets={}, music_track_id=sample["music_track_id"],
                  origin_hash=hashlib.sha256(getattr(api, "base_url", "mock").encode()).hexdigest())
        body = {"version": sample["version"]}
        if action == "generate":
            require(category is None or category in CATEGORIES, "INVALID_CATEGORY")
            target = sample["categories"][category] if category else None
            cp["targets"] = [target] if target else [s["id"] for s in shots if s["shot_type"] != "photo" and
                not any(c["shot_id"] == s["id"] and c["review_result"] != "rejected" and live_asset(c["asset"]) for c in snap["candidates"])]
            require(cp["targets"], "NO_PENDING_SHOTS")
            cp["prior_attempts"] = attempt_counts(api, sample, snap, cp["targets"], receipts)
            quote = api.request("GET", base + "/quote" + ("?shot_id=" + target if target else ""))
            require(quote["project_version"] == sample["version"], "STALE_QUOTE")
            cp["quote_id"] = identifier(quote["id"])
            body["quote_id"] = cp["quote_id"]
            endpoint = base + (f"/shots/{target}/regenerate" if target else "/generation-jobs")
        else:
            assets = {a["id"]: a for a in snap["assets"]}
            require(all(selected_photo(s, assets) or selected_video(s, snap["candidates"]) for s in shots), "HUMAN_CANDIDATE_APPROVAL_REQUIRED")
            cp["targets"] = [s["id"] for s in shots]
            cp["assets"] = {s["id"]: selected_asset_id(s, snap["candidates"]) for s in shots}
            body["music_track_id"] = sample["music_track_id"]
            endpoint = base + "/render"
        atomic_json(checkpoint, cp)  # Includes exact key, quote, targets before any POST.
        try:
            result = api.request("POST", endpoint, body, cp["key"])
            check_job_scope(result, cp)
            cp.update(state="known", job_id=identifier(result["id"]))
            atomic_json(checkpoint, cp)
        except Exception:
            cp["state"] = "unknown"
            atomic_json(checkpoint, cp)
            raise Rejected("AMBIGUOUS_SUBMISSION_STOP") from None
        return {"status": "SUBMITTED", "job_id": cp["job_id"], "action": action}


def check_job_scope(result, cp):
    require(result["project_version"] == cp["version"], "JOB_VERSION_MISMATCH")
    if cp["action"] == "generate":
        require(result["project_id"] == cp["project_id"], "JOB_PROJECT_MISMATCH")
    else:
        require({s["shot_id"]: s["asset_id"] for s in result["timeline"]} == cp["assets"] and
                result["music"]["track_id"] == cp["music_track_id"], "RENDER_SCOPE_MISMATCH")


def resume(api, sample, checkpoint, resolved_id=None):
    with checkpoint_lock(checkpoint):
        cp = load_json(checkpoint)
        validate_checkpoint(cp, api, sample)
        ambiguous = cp["state"] in ("submitting", "unknown")
        require(not ambiguous or resolved_id, "AMBIGUOUS_SUBMISSION_STOP")
        job_id = identifier(resolved_id if ambiguous else cp["job_id"])
        base = "/wedding-projects/" + sample["project_id"]
        endpoint = "/wedding-generation-jobs/" + job_id if cp["action"] == "generate" else base + "/renders/" + job_id
        result = api.request("GET", endpoint)
        require(result["id"] == job_id, "JOB_ID_MISMATCH")
        check_job_scope(result, cp)
        if ambiguous and cp["action"] == "generate":
            candidates = api.request("GET", base + "/candidates")
            actual = {c["shot_id"] for c in candidates if c["generation_job_id"] == job_id}
            require(actual == set(cp["targets"]), "RESOLUTION_TARGET_SCOPE_UNPROVEN")
        cp.update(state="known", job_id=job_id)
        atomic_json(checkpoint, cp)
        allowed = {"queued", "processing", "needs_action", "completed", "failed", "cancel_requested", "cancelled", "ready", "approved"}
        require(result["status"] in allowed, "UNKNOWN_SERVER_STATUS")
        return {"job_id": job_id, "status": result["status"], "action": cp["action"], "error_code": failure_code(result.get("error_code"))}


def validate_checkpoint(cp, api, sample):
    fields(cp, ("schema_version", "sample_hash", "project_id", "version", "action", "state", "key", "targets", "assets", "music_track_id", "origin_hash", "prior_attempts", "quote_id", "job_id"))
    require(cp["schema_version"] == 1 and cp["action"] in ("generate", "render") and cp["state"] in ("submitting", "unknown", "known"), "INVALID_CHECKPOINT")
    require(cp["sample_hash"] == sample_hash(sample) and cp["project_id"] == sample["project_id"] and cp["version"] == sample["version"], "CHECKPOINT_SCOPE_MISMATCH")
    require(cp["origin_hash"] == hashlib.sha256(getattr(api, "base_url", "mock").encode()).hexdigest(), "CHECKPOINT_ORIGIN_MISMATCH")
    require(cp["targets"] and len(cp["targets"]) == len({identifier(s) for s in cp["targets"]}), "INVALID_CHECKPOINT_TARGETS")


def measured_minutes(timing):
    fields(timing, ("start", "end", "human_intervals"), ("start", "end", "human_intervals"))
    start, end = timestamp(timing["start"]), timestamp(timing["end"])
    require(end >= start, "TIMING_INVALID")
    intervals = []
    for interval in timing["human_intervals"]:
        fields(interval, ("start", "end"), ("start", "end"))
        a, b = timestamp(interval["start"]), timestamp(interval["end"])
        require(start <= a <= b <= end, "TIMING_INVALID")
        intervals.append((a, b))
    intervals.sort()
    require(all(a[1] <= b[0] for a, b in zip(intervals, intervals[1:])), "TIMING_OVERLAP")
    return ((end - start).total_seconds() - sum((b - a).total_seconds() for a, b in intervals)) / 60


def validate_evidence(rows):
    require(isinstance(rows, list) and len(rows) <= 10, "INVALID_EVIDENCE")
    seen = set()
    for row in rows:
        fields(row, ("project_id", "version", "render_id", "rating", "willing", "role_facts_correct", "commercial_music_rights", "motion", "bill", "timing"), ("project_id", "version"))
        pid = identifier(row["project_id"])
        require(pid not in seen, "DUPLICATE_EVIDENCE")
        seen.add(pid)
        require(type(row["version"]) is int and row["version"] > 0, "INVALID_VERSION")
        if "render_id" in row:
            identifier(row["render_id"])
        if "rating" in row:
            require(type(row["rating"]) is int and 1 <= row["rating"] <= 5, "INVALID_RATING")
        for key in ("willing", "role_facts_correct", "commercial_music_rights"):
            if key in row:
                require(type(row[key]) is bool, "INVALID_ATTESTATION")
        if "motion" in row:
            fields(row["motion"], CATEGORIES)
            for result in row["motion"].values():
                fields(result, ("passed", "candidate_id"), ("passed",))
                require(type(result["passed"]) is bool, "INVALID_MOTION_ATTESTATION")
                if result["passed"] or "candidate_id" in result:
                    identifier(result.get("candidate_id"))
        if "bill" in row:
            bill = row["bill"]
            fields(bill, ("authoritative", "complete", "reference_id", "model_fen", "music_fen", "render_fen", "storage_fen", "other_fen"))
            if "reference_id" in bill:
                identifier(bill["reference_id"])
            require(all(type(v) is bool if k in ("complete", "authoritative") else type(v) is int and v >= 0
                        for k, v in bill.items() if k != "reference_id"), "INVALID_BILL")
        if "timing" in row:
            measured_minutes(row["timing"])
    return rows


def assess(sample, snap, render, human):
    """No raw API objects or private free text are returned or persisted."""
    validate_evidence([human])
    require(human["project_id"] == sample["project_id"] and human["version"] == sample["version"], "EVIDENCE_SCOPE_MISMATCH")
    check_preflight(sample, snap)
    shots, candidates = snap["story"]["shots"], snap["candidates"]
    assets = {a["id"]: a for a in snap["assets"]}
    video = {s["id"]: selected_video(s, candidates) for s in shots if s["shot_type"] != "photo"}
    person = sum(s["shot_type"] == "person_ai" and video.get(s["id"], False) for s in shots)
    scene = sum(s["shot_type"] == "scene_ai" and video.get(s["id"], False) for s in shots)
    photo = sum(selected_photo(s, assets) for s in shots)
    standard = (person, scene, photo) == (8, 4, 6)
    composition = person >= 6 and person + scene >= 10 and person + scene + photo == 18 and 6 <= photo <= 8
    render_ok = bool(render and render.get("id") == human.get("render_id") and render["project_version"] == sample["version"] and
                     render["status"] == "approved" and render.get("owner_approved_at") and not render.get("invalidated_at") and render["output_duration_ms"] == 90000 and
                     live_asset(assets.get(render.get("export_asset_id"))) and assets[render["export_asset_id"]]["mime"].startswith("video/") and
                     {t["shot_id"]: (t["asset_id"], t["shot_type"], t["duration_ms"]) for t in render["timeline"]} ==
                     {s["id"]: (selected_asset_id(s, candidates), s["shot_type"], s["duration_ms"]) for s in shots} and
                     music_valid(render["music"]) and render["music"]["track_id"] == sample["music_track_id"] and
                     bool(render["music"].get("sha256")) and
                     all(any(r["person_key"] == p and r["asset_id"] == render["preview_asset_id"] and r["result"] == "approved"
                             for r in render["identity_reviews"]) for p in "AB"))
    bill = human.get("bill", {})
    costs = ("model_fen", "music_fen", "render_fen", "storage_fen", "other_fen")
    official = bill.get("authoritative") is True and bill.get("complete") is True and bool(bill.get("reference_id")) and all(k in bill for k in costs)
    total = sum(bill[k] for k in costs) if official else None
    minutes = measured_minutes(human["timing"]) if "timing" in human else None
    complete = bool(render and human.get("render_id") and all(k in human for k in ("rating", "willing", "role_facts_correct", "commercial_music_rights")) and
                    set(human.get("motion", {})) == set(CATEGORIES) and official and minutes is not None)
    category_passes, stress_passes = 0, 0
    selected_evidence = []
    attempt_bounds = attempt_counts(None, sample, snap, sample["categories"].values(), (), ceiling=None)
    for category, sid in sample["categories"].items():
        attempts = attempt_bounds[sid]
        shot = next(s for s in shots if s["id"] == sid)
        review = human.get("motion", {}).get(category, {})
        candidate = next((c for c in candidates if c["id"] == shot.get("selected_candidate_id") and c["shot_id"] == sid), None)
        bound = review.get("candidate_id") == shot.get("selected_candidate_id")
        if review.get("passed") is True and not bound:
            complete = False
        passed = bool(review.get("passed") is True and bound and video.get(sid) and 1 <= attempts <= 3)
        selected_evidence.append(dict(category=category, shot_id=identifier(sid), attempts=attempts,
            candidate_id=identifier(candidate["id"]) if candidate else None,
            asset_id=identifier(candidate["asset_id"]) if candidate else None,
            provider=candidate["provider"] if candidate and candidate["provider"] in PROVIDERS.values() else "unverified",
            model=candidate["model_version"] if candidate and candidate["model_version"] in MODELS.values() else "unverified",
            video_mime=candidate["asset"]["mime"] if candidate and candidate["asset"]["mime"] in ("video/mp4", "video/webm", "video/quicktime") else "unverified",
            identity_approved=bool(video.get(sid)), passed=passed))
        if category in ("a_closeup", "both"):
            category_passes += passed
        else:
            stress_passes += passed
    qualified = composition and render_ok
    return dict(project_id=sample["project_id"], complete=complete, qualified=qualified, standard=standard and qualified,
                cost_pass=bool(qualified and official and total <= 15000), official_total_fen=total,
                provider_metered_estimate_fen=snap["project"].get("budget_spent_fen"),
                happy=human.get("rating", 0) >= 4 and human.get("willing") is True,
                facts=human.get("role_facts_correct") is True, rights=human.get("commercial_music_rights") is True and music_valid(snap["music"]),
                motion_passes=category_passes, stress_passes=stress_passes, selected_evidence=selected_evidence,
                minutes=minutes, person_ai=person, scene_ai=scene, photo=photo,
                failures=[dict(job_id=identifier(j["id"]), reason=failure_code(j.get("error_code")))
                          for j in snap.get("jobs", []) if j.get("error_code") and j["project_version"] == sample["version"]])


def summarize(rows):
    require(len(rows) == 10, "EXACTLY_TEN_REQUIRED")
    counts = {key: sum(bool(r.get(key)) for r in rows) for key in ("complete", "qualified", "standard", "cost_pass", "happy", "facts", "rights")}
    motion = sum(r.get("motion_passes", 0) for r in rows)
    times = sorted(r["minutes"] for r in rows if r.get("minutes") is not None)
    p50 = (times[4] + times[5]) / 2 if len(times) == 10 else None
    p95 = times[math.ceil(.95 * len(times)) - 1] if len(times) == 10 else None
    passed = motion >= 18 and counts["qualified"] >= 8 and counts["cost_pass"] >= 8 and counts["standard"] >= 6 and counts["happy"] >= 8 and counts["facts"] == counts["rights"] == 10
    return dict(status="INCOMPLETE" if counts["complete"] < 10 else "PASS" if passed else "FAIL", denominator=10,
                motion_denominator=20, motion_passes=motion, stress_denominator=20, stress_passes=sum(r.get("stress_passes", 0) for r in rows),
                **counts, p50_minutes=p50, p95_minutes=p95,
                latency_goal_met=None if p50 is None else p50 <= 30 and p95 <= 90,
                cost_basis="authoritative_complete_variable_costs_only", metered_costs="estimates_not_invoices",
                comparison="external_manual_evaluation_required")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--stage", choices=("offline", "preflight", "generate", "render", "resume", "report"), default="offline")
    parser.add_argument("--base-url", default="http://127.0.0.1:8031")
    parser.add_argument("--execute-paid", action="store_true")
    parser.add_argument("--project-id")
    parser.add_argument("--category", choices=CATEGORIES)
    parser.add_argument("--checkpoint")
    parser.add_argument("--resolved-id")
    parser.add_argument("--evidence")
    parser.add_argument("--receipt", action="append", default=[], help="Prior known checkpoint; repeat for proven failed-job target scopes")
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    try:
        manifest = validate_manifest(load_json(args.manifest), require_prepared=args.stage != "offline")
        rows = validate_evidence(load_json(args.evidence)) if args.evidence else []
        samples = manifest["samples"]
        require({r["project_id"] for r in rows} <= {s["project_id"] for s in samples}, "UNKNOWN_EVIDENCE_PROJECT")
        if args.stage == "offline":
            result = dict(status="INCOMPLETE", manifest_valid=True, samples=10,
                          prepared=all(s[k] is True for s in samples for k in ATTESTATIONS), external_acceptance="NOT_EXECUTED")
        elif args.stage in ("generate", "render", "resume"):
            require(args.checkpoint and args.project_id, "PROJECT_AND_CHECKPOINT_REQUIRED")
            sample = next((s for s in samples if s["project_id"] == args.project_id), None)
            require(sample is not None, "UNKNOWN_PROJECT")
            api = Transport(args.base_url, sample["token_env"], args.execute_paid)
            if args.stage == "resume":
                current = api.request("GET", "/wedding-projects/" + sample["project_id"])
                require(current["version"] == sample["version"], "STALE_PROJECT")
                result = resume(api, sample, args.checkpoint, args.resolved_id)
            else:
                require(args.execute_paid, "EXECUTE_PAID_REQUIRED")
                result = dispatch(api, sample, fetch_snapshot(api, sample), args.checkpoint, args.stage, args.category, True, args.receipt)
        else:
            results = []
            for sample in samples:
                api = Transport(args.base_url, sample["token_env"])
                snap = fetch_snapshot(api, sample)
                check_preflight(sample, snap)
                if args.stage == "report":
                    human = next((r for r in rows if r["project_id"] == sample["project_id"]), dict(project_id=sample["project_id"], version=sample["version"]))
                    render = api.request("GET", "/wedding-projects/" + sample["project_id"] + "/renders/" + human["render_id"]) if human.get("render_id") else None
                    results.append(assess(sample, snap, render, human))
            result = {"summary": summarize(results), "samples": results} if args.stage == "report" else dict(status="PREFLIGHT_OK", samples=10, external_acceptance="INCOMPLETE")
        if args.output:
            require(not args.checkpoint or Path(args.output).resolve() != Path(args.checkpoint).resolve(), "OUTPUT_OVERWRITES_CHECKPOINT")
            require(Path(args.output).resolve() not in {Path(p).resolve() for p in (args.manifest, args.evidence) if p}, "OUTPUT_OVERWRITES_INPUT")
            atomic_json(args.output, result)
        print(json.dumps(result, allow_nan=False))
        return 0
    except Rejected as exc:
        print(json.dumps({"status": "STOPPED", "reason": str(exc)}))
        return 2
    except (KeyError, TypeError, ValueError, OSError, HTTPException):
        print(json.dumps({"status": "STOPPED", "reason": "INVALID_INPUT_OR_API_SHAPE"}))
        return 2


if __name__ == "__main__":
    if sys.argv[1:] == ["--_http-worker"]:
        http_worker_main()
    else:
        raise SystemExit(main())
