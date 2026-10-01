import base64
import json
from dataclasses import replace
from io import BytesIO

import httpx
import pytest
from PIL import Image

from app.config import Settings
from app.wedding_media import (
    FrameGenerationRequest, ProviderUnavailable, StoryStructureRequest,
    VideoGenerationRequest, build_provider_bundle,
)
from app.wedding_providers import ArkStoryProvider, SeedreamFrameProvider, WanVideoProvider, provider_capabilities


def png():
    out = BytesIO()
    Image.new("RGB", (2, 2)).save(out, format="PNG")
    return out.getvalue()


PNG = png()
MP4 = b'\x00\x00\x00\x18ftypisom\x00\x00\x00\x00isomiso2' + b'\x00\x00\x00\x08mdat'


def settings(**kwargs):
    values = dict(_env_file=None, wedding_media_mode="live", wedding_story_provider="ark",
        wedding_story_api_key="story-secret", wedding_frame_provider="seedream",
        wedding_frame_api_key="frame-secret", wedding_character_video_provider="wan_r2v",
        wedding_character_video_api_key="video-secret", wedding_scene_video_provider="wan_i2v_flash",
        wedding_scene_video_api_key="scene-secret", wedding_story_input_fen_per_million_tokens="100",
        wedding_story_output_fen_per_million_tokens="200", wedding_poll_interval_seconds=0.001)
    values.update(kwargs)
    return Settings(**values)


def video(**kwargs):
    values = dict(project_id="p", job_id="j", shot_id="s", shot_type="person_ai",
        description="A and B walk", subtitle="", reference_image=PNG,
        reference_mime="image/png", idempotency_key="key",
        primary_references=(("B", PNG, "image/png"), ("A", PNG, "image/png")))
    values.update(kwargs)
    return VideoGenerationRequest(**values)


def adapter(handler, kind="wan_r2v", config=None, **kwargs):
    return WanVideoProvider(config or settings(), kind, transport=httpx.MockTransport(handler), **kwargs)


def test_r2v_regional_payload_checkpoint_metadata_and_safe_download():
    calls, checkpoints, heartbeats = [], [], []
    def handler(req):
        calls.append(req)
        if req.method == "POST":
            body = json.loads(req.content)
            assert req.url == "https://dashscope.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis"
            assert body["model"] == "wan2.7-r2v-2026-06-12"
            assert req.headers["X-DashScope-Async"] == "enable"
            assert body["parameters"] == dict(resolution="1080P", duration=5, prompt_extend=False, watermark=True)
            assert [m["type"] for m in body["input"]["media"]] == ["first_frame", "reference_image", "reference_image"]
            assert all(m["url"].startswith("data:image/png;base64,") for m in body["input"]["media"])
            return httpx.Response(200, json={"output": {"task_id": "task-1"}, "request_id": "submit-r"})
        if req.url.host == "dashscope.aliyuncs.com":
            assert checkpoints == ["task-1"]
            return httpx.Response(200, json={"output": {"task_status": "SUCCEEDED", "video_url": "https://video.oss-cn-beijing.aliyuncs.com/result.mp4"}, "usage": {"duration": 5}, "request_id": "poll-r"})
        assert "authorization" not in req.headers
        return httpx.Response(200, headers={"Content-Type": "video/mp4"}, content=MP4)
    out = adapter(handler).generate(video(checkpoint=checkpoints.append, heartbeat=lambda: heartbeats.append(1)))
    assert out.data == MP4 and out.task_id == "task-1" and out.actual_cost_fen == 500
    assert out.request_id == "poll-r" and out.usage == {"duration": 5}
    assert out.price_metadata["cost_source"] == "metered_estimate"
    assert out.price_metadata["authoritative_billing"] is False
    assert heartbeats and len(calls) == 3


def test_flash_audio_disabled_and_no_portraits():
    def handler(req):
        if req.method == "POST":
            body = json.loads(req.content)
            assert body["model"] == "wan2.6-i2v-flash"
            assert body["parameters"]["audio"] is False
            assert set(body["input"]) == {"prompt", "img_url"}
            return httpx.Response(200, json={"output": {"task_id": "t"}})
        return httpx.Response(200, json={"output": {"task_status": "FAILED", "code": "private error"}})
    with pytest.raises(ProviderUnavailable) as err:
        adapter(handler, "wan_i2v_flash").generate(video(shot_type="scene_ai", primary_references=()))
    assert err.value.submission_state == "failed" and err.value.actual_cost_fen == 0


@pytest.mark.parametrize("roles", [("A", "A"), ("C",), ()])
def test_r2v_rejects_missing_duplicate_or_unrelated_primary_roles_without_call(roles):
    with pytest.raises(ProviderUnavailable) as err:
        adapter(lambda _: pytest.fail("unexpected call")).generate(video(primary_references=tuple((r, PNG, "image/png") for r in roles)))
    assert err.value.code == "PROVIDER_INPUT_INVALID"


def test_r2v_sorts_a_b_and_supports_single_person():
    a = BytesIO(); Image.new("RGB", (3, 3), "red").save(a, format="PNG")
    def handler(req):
        if req.method == "POST":
            refs = json.loads(req.content)["input"]["media"][1:]
            assert len(refs) == 2
            assert refs[0]["url"].endswith(base64.b64encode(a.getvalue()).decode())
            return httpx.Response(200, json={"output": {"task_id": "t"}})
        return httpx.Response(200, json={"output": {"task_status": "FAILED"}})
    with pytest.raises(ProviderUnavailable):
        adapter(handler).generate(video(primary_references=(("B", PNG, "image/png"), ("A", a.getvalue(), "image/png"))))


@pytest.mark.parametrize("status,state", [("RUNNING", "running"), ("UNKNOWN", "unknown")])
def test_resume_timeout_retains_known_task_and_cost_without_post(status, state):
    clock = iter([0, 0, 2])
    def handler(req):
        assert req.method == "GET" and req.url.path == "/api/v1/tasks/task-known"
        return httpx.Response(200, json={"output": {"task_status": status}})
    with pytest.raises(ProviderUnavailable) as err:
        adapter(handler, config=settings(wedding_total_timeout_seconds=1), monotonic=lambda: next(clock), sleep=lambda _: None).generate(video(existing_task_id="task-known", primary_references=()))
    assert err.value.task_id == "task-known" and err.value.actual_cost_fen == 500
    assert err.value.submission_state == state


def test_submission_network_failure_unknown_not_retried_and_sanitized():
    calls = []
    def handler(req):
        calls.append(req)
        raise httpx.ReadTimeout("secret private URL", request=req)
    with pytest.raises(ProviderUnavailable) as err:
        adapter(handler).generate(video())
    assert len(calls) == 1 and err.value.submission_state == "unknown"
    assert err.value.actual_cost_fen == 500 and "secret" not in str(err.value)


def test_checkpoint_failure_carries_task_never_reposts():
    calls = []
    def handler(req):
        calls.append(req)
        return httpx.Response(200, json={"output": {"task_id": "saved-or-not"}})
    def checkpoint(_):
        raise RuntimeError("database unavailable")
    with pytest.raises(ProviderUnavailable) as err:
        adapter(handler).generate(video(checkpoint=checkpoint))
    assert len(calls) == 1 and err.value.task_id == "saved-or-not" and err.value.actual_cost_fen == 500


@pytest.mark.parametrize("url", ["http://video.oss-cn-beijing.aliyuncs.com/a", "https://x@video.oss-cn-beijing.aliyuncs.com/a", "https://127.0.0.1/a", "https://192.168.1.2/a", "https://evil.example/a", "https://localhost/a", "https://video.oss-cn-beijing.aliyuncs.com.evil.example/a"])
def test_download_ssrf_denied_after_success_preserves_charge(url):
    def handler(req):
        assert req.url.host == "dashscope.aliyuncs.com"
        return httpx.Response(200, json={"output": {"task_status": "SUCCEEDED", "video_url": url}, "usage": {"duration": 5}})
    with pytest.raises(ProviderUnavailable) as err:
        adapter(handler).generate(video(existing_task_id="t"))
    assert err.value.code == "PROVIDER_DOWNLOAD_BLOCKED" and err.value.actual_cost_fen == 500


@pytest.mark.parametrize("response", [httpx.Response(302, headers={"location": "https://evil.example/a"}), httpx.Response(200, content=b"not mp4", headers={"content-type": "video/mp4"}), httpx.Response(200, content=MP4, headers={"content-type": "text/html"}), httpx.Response(200, content=MP4 * 50, headers={"content-type": "video/mp4"})])
def test_bad_download_envelopes_and_size(response):
    def handler(req):
        if req.url.host == "dashscope.aliyuncs.com":
            return httpx.Response(200, json={"output": {"task_status": "SUCCEEDED", "video_url": "https://video.oss-cn-beijing.aliyuncs.com/a"}, "usage": {"duration": 5}})
        return response
    with pytest.raises(ProviderUnavailable) as err:
        adapter(handler, config=settings(wedding_max_video_bytes=100)).generate(video(existing_task_id="t"))
    assert err.value.actual_cost_fen == 500 and err.value.submission_state == "succeeded"


def test_seedream_multireference_base64_and_round_up_metering():
    def handler(req):
        body = json.loads(req.content)
        assert req.url == "https://ark.cn-beijing.volces.com/api/v3/images/generations"
        assert body["model"] == "doubao-seedream-4-5-251128"
        assert body["response_format"] == "b64_json" and body["watermark"] is True
        assert len(body["image"]) == 2 and body["sequential_image_generation"] == "disabled"
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(PNG).decode()}], "usage": {"generated_images": 1}, "id": "r"})
    out = SeedreamFrameProvider(settings(wedding_frame_fen_per_image="25.01"), transport=httpx.MockTransport(handler)).generate(FrameGenerationRequest("s", "frame", (("A", PNG, "image/png"), ("B", PNG, "image/png")), "key"))
    assert out.actual_cost_fen == 26 and out.mime == "image/png" and out.request_id == "r"


@pytest.mark.parametrize("body", [{"data": [{"b64_json": "broken"}]}, {"data": [{"b64_json": base64.b64encode(b"not image").decode()}]}, {"data": []}])
def test_seedream_rejects_malformed_output_but_retains_estimated_cost(body):
    with pytest.raises(ProviderUnavailable) as err:
        SeedreamFrameProvider(settings(), transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))).generate(FrameGenerationRequest("s", "frame", (), "key"))
    assert err.value.actual_cost_fen == 25


def story_output():
    return {"story_text": "confirmed", "shots": [dict(shot_index=i, shot_type="person_ai" if i <= 8 else "scene_ai" if i <= 12 else "photo", duration_ms=5000, person_keys=["A"] if i <= 8 else [], fact_indexes=[0], description="confirmed", subtitle="", creative_scene=False) for i in range(1, 19)]}


def test_ark_structured_story_metering_and_prompt():
    def handler(req):
        body = json.loads(req.content)
        assert body["model"] == "doubao-seed-2-0-pro-260215"
        assert body["response_format"] == {"type": "json_object"}
        assert body["stream"] is False
        assert "18" in body["messages"][0]["content"]
        assert "role_names" in body["messages"][1]["content"]
        return httpx.Response(200, json={"id": "chat-id", "choices": [{"message": {"content": json.dumps(story_output())}}], "usage": {"prompt_tokens": 1000, "completion_tokens": 1000}})
    out = ArkStoryProvider(settings(), transport=httpx.MockTransport(handler)).structure(StoryStructureRequest("confirmed", ("fact",), "k", role_names=(("A", "Alice"),), style="documentary"))
    assert out.actual_cost_fen == 1 and json.loads(out.data)["shots"][0]["fact_indexes"] == [0]


@pytest.mark.parametrize("content", ["no json", "{}", '{"story_text":"x","shots":[]}', json.dumps({**story_output(), "shots": [dict(s, fact_indexes=[5]) for s in story_output()["shots"]]})])
def test_ark_malformed_or_unfounded_story_is_not_success(content):
    with pytest.raises(ProviderUnavailable):
        ArkStoryProvider(settings(), transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"choices": [{"message": {"content": content}}], "usage": {"prompt_tokens": 10, "completion_tokens": 10}}))).structure(StoryStructureRequest("s", ("f",), "k"))


def test_bounded_json_and_malformed_submit_are_unknown():
    for response in [httpx.Response(200, content=b"{}" * 100, headers={"content-type": "application/json"}), httpx.Response(200, json={"output": {}}), httpx.Response(200, content=b"broken", headers={"content-type": "application/json"})]:
        with pytest.raises(ProviderUnavailable) as err:
            adapter(lambda _: response, config=settings(wedding_max_json_bytes=100)).generate(video())
        assert err.value.submission_state == "unknown" and err.value.actual_cost_fen == 500


def test_disabled_bundle_and_capability_distinguish_unsupported_missing_configured():
    cfg = settings(wedding_media_mode="disabled")
    with pytest.raises(ProviderUnavailable):
        build_provider_bundle(cfg).character_video.generate(video())
    assert all(not row["available"] for row in provider_capabilities(cfg).values())
    live = settings(wedding_scene_video_provider="other", wedding_frame_api_key=None)
    caps = provider_capabilities(live)
    assert caps["story"]["available"]
    assert caps["frame"]["reason"] == "credentials_missing"
    assert caps["scene_video"]["reason"] == "unsupported_provider"
    assert isinstance(build_provider_bundle(live).character_video, WanVideoProvider)
