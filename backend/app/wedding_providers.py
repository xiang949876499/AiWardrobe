"""Bounded, no-retry wedding provider adapters. Never log private inputs or output URLs."""
from __future__ import annotations

import base64
import binascii
import ipaddress
import json
import re
import socket
import time
from decimal import Decimal, ROUND_CEILING
from io import BytesIO
from typing import Callable, Literal
from urllib.parse import urlsplit

import httpx
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config import Settings
from app.schemas import StoryboardShotInput
from app.wedding_media import (
    FrameGenerationRequest, MediaOutput, ProviderBundle, ProviderUnavailable,
    StoryStructureRequest, StructuredOutput, VideoGenerationRequest, _UnavailableProvider,
)

STORY_MODEL = "doubao-seed-2-0-pro-260215"
FRAME_MODEL = "doubao-seedream-4-5-251128"
R2V_MODEL = "wan2.7-r2v-2026-06-12"
FLASH_MODEL = "wan2.6-i2v-flash"
SHOT_TYPES = ("scene_ai", "photo", "person_ai", "photo", "person_ai", "person_ai", "photo", "scene_ai", "person_ai", "photo", "person_ai", "scene_ai", "photo", "person_ai", "person_ai", "scene_ai", "person_ai", "photo")


class _Fact(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    text: str = Field(min_length=1, max_length=1000)
    source_quote: str = Field(default="", max_length=12000)
    source: Literal["story_quote", "confirmed_input"] = "story_quote"
    confirmed: Literal[False] = False


class _Story(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    story_text: str = Field(min_length=1, max_length=12000)
    facts: list[_Fact] = Field(min_length=1, max_length=40)
    shots: list[StoryboardShotInput] = Field(min_length=18, max_length=18)


def _ceil(value: Decimal) -> int:
    return int(value.to_integral_value(rounding=ROUND_CEILING))


def _number(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise ValueError("Invalid usage")
    result = Decimal(str(value))
    if not result.is_finite() or result < 0:
        raise ValueError("Invalid usage")
    return result


def _resolve(host: str) -> list[str]:
    return list({row[4][0] for row in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)})


def _validate_url(url: str, domains: tuple[str, ...], resolver: Callable[[str], list[str]] | None = None) -> list[str]:
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443) or not host or parsed.fragment:
            raise ValueError()
        if not any(host == domain or host.endswith("." + domain) for domain in domains):
            raise ValueError()
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError()
        addresses = []
        if resolver:
            addresses = resolver(host)
            if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
                raise ValueError()
        return addresses
    except (ValueError, OSError):
        raise ProviderUnavailable("PROVIDER_DOWNLOAD_BLOCKED", "Provider URL is not an allowed public HTTPS endpoint") from None


class _PinnedTransport(httpx.BaseTransport):
    """Use the validated IP for the TCP connection, retaining hostname TLS verification."""
    def __init__(self, host: str, address: str, inner: httpx.BaseTransport | None = None):
        self.host, self.address = host, address
        self.inner = inner or httpx.HTTPTransport(retries=0)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        request.headers["Host"] = self.host
        request.extensions["sni_hostname"] = self.host
        request.url = request.url.copy_with(host=self.address)
        return self.inner.handle_request(request)

    def close(self):
        self.inner.close()


def _image(data: bytes, mime: str | None, limit: int) -> str:
    if not data or len(data) > limit:
        raise ValueError("Invalid image size")
    try:
        with Image.open(BytesIO(data)) as img:
            detected = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}.get(img.format)
            if detected is None or (mime is not None and detected != mime) or img.width * img.height > 40_000_000:
                raise ValueError("Invalid image")
            img.verify()
        return detected
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise ValueError("Invalid image") from None


def _mp4(data: bytes) -> None:
    # Structural envelope check, not an assurance that a codec is decodable.
    offset, boxes = 0, []
    while offset < len(data):
        if offset + 8 > len(data):
            raise ValueError("Invalid MP4")
        size = int.from_bytes(data[offset:offset + 4], "big")
        box = data[offset + 4:offset + 8]
        header = 8
        if size == 1:
            if offset + 16 > len(data):
                raise ValueError("Invalid MP4")
            size = int.from_bytes(data[offset + 8:offset + 16], "big")
            header = 16
        if size == 0:
            size = len(data) - offset
        if size < header or offset + size > len(data):
            raise ValueError("Invalid MP4")
        if not boxes and (box != b"ftyp" or size < 16):
            raise ValueError("Invalid MP4")
        boxes.append(box)
        offset += size
    if b"ftyp" not in boxes or not any(box in boxes for box in (b"mdat", b"moov")):
        raise ValueError("Invalid MP4")


class _HTTP:
    def __init__(self, settings: Settings, *, transport: httpx.BaseTransport | None = None,
                 monotonic: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep,
                 resolver: Callable[[str], list[str]] = _resolve) -> None:
        self.settings, self.transport = settings, transport
        self.monotonic, self.sleep, self.resolver = monotonic, sleep, resolver

    def _deadline(self) -> float:
        return self.monotonic() + self.settings.wedding_total_timeout_seconds

    def _remaining(self, deadline: float) -> float:
        remaining = deadline - self.monotonic()
        if remaining <= 0:
            raise ProviderUnavailable("PROVIDER_TIMEOUT", "Provider operation exceeded its time budget")
        return remaining

    def _enabled(self, name: str) -> None:
        row = provider_capabilities(self.settings)[name]
        if not row["available"]:
            _UnavailableLive(row)._raise()

    @staticmethod
    def _heartbeat(callback: Callable[[], None] | None) -> None:
        if callback:
            try:
                callback()
            except Exception:
                raise ProviderUnavailable("PROVIDER_HEARTBEAT_FAILED", "Provider operation lease could not be refreshed") from None

    def _read(self, method: str, url: str, *, deadline: float, limit: int, headers: dict | None = None,
              payload: dict | None = None, json_response: bool = True, heartbeat: Callable[[], None] | None = None,
              pinned_address: str | None = None) -> tuple[bytes, str, dict]:
        remaining = self._remaining(deadline)
        timeout = httpx.Timeout(min(self.settings.wedding_read_timeout_seconds, remaining),
            connect=min(self.settings.wedding_connect_timeout_seconds, remaining))
        self._heartbeat(heartbeat)
        transport = self.transport
        if pinned_address and transport is None:
            transport = _PinnedTransport(urlsplit(url).hostname, pinned_address)
        with httpx.Client(transport=transport, timeout=timeout, follow_redirects=False, trust_env=False) as client:
            with client.stream(method, url, headers={"Accept-Encoding": "identity", **(headers or {})}, json=payload) as response:
                if response.status_code < 200 or response.status_code >= 300:
                    state = "failed" if 400 <= response.status_code < 500 and response.status_code != 408 else "unknown"
                    raise ProviderUnavailable("PROVIDER_HTTP_ERROR", "Provider returned a non-success response", submission_state=state)
                mime = response.headers.get("content-type", "").split(";")[0].lower()
                if response.headers.get("content-encoding", "identity").lower() != "identity":
                    raise ProviderUnavailable("PROVIDER_OUTPUT_INVALID", "Compressed provider responses are unsupported")
                if json_response and mime != "application/json":
                    raise ProviderUnavailable("PROVIDER_OUTPUT_INVALID", "Provider response has an invalid content type")
                length = response.headers.get("content-length")
                if length and (not length.isdigit() or int(length) > limit):
                    raise ProviderUnavailable("PROVIDER_OUTPUT_TOO_LARGE", "Provider response exceeds the byte limit")
                data = bytearray()
                for chunk in response.iter_bytes(chunk_size=65536):
                    self._remaining(deadline)
                    if len(data) + len(chunk) > limit:
                        raise ProviderUnavailable("PROVIDER_OUTPUT_TOO_LARGE", "Provider response exceeds the byte limit")
                    data.extend(chunk)
                    self._heartbeat(heartbeat)
                return bytes(data), mime, dict(response.headers)

    def _json(self, method: str, url: str, **kwargs) -> dict:
        data, _, headers = self._read(method, url, limit=self.settings.wedding_max_json_bytes, **kwargs)
        try:
            result = json.loads(data)
            if not isinstance(result, dict):
                raise ValueError()
            result["_transport_request_id"] = headers.get("x-request-id") or headers.get("x-tt-logid")
            return result
        except (ValueError, UnicodeError, RecursionError):
            raise ProviderUnavailable("PROVIDER_OUTPUT_INVALID", "Provider response is not a valid JSON object") from None

    def _data_uri(self, data: bytes, mime: str) -> str:
        try:
            _image(data, mime, self.settings.wedding_max_image_bytes)
        except ValueError:
            raise ProviderUnavailable("PROVIDER_INPUT_INVALID", "Reference must be a bounded supported image") from None
        return "data:" + mime + ";base64," + base64.b64encode(data).decode("ascii")

    def _request_bound(self, payload: dict) -> None:
        # Check before dispatch so an oversized payload is a zero-cost input failure.
        if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > self.settings.wedding_max_request_bytes:
            raise ProviderUnavailable("PROVIDER_INPUT_INVALID", "Provider request exceeds its byte limit")

    def _download(self, url: str, *, deadline: float, heartbeat=None) -> tuple[bytes, str]:
        domains = tuple(d.strip().lower() for d in self.settings.wedding_download_allowed_domains.split(",") if d.strip())
        addresses = _validate_url(url, domains, self.resolver)
        data, mime, _ = self._read("GET", url, deadline=deadline, limit=self.settings.wedding_max_video_bytes,
                                  json_response=False, heartbeat=heartbeat, pinned_address=addresses[0])
        return data, mime

    def _price(self, unit: str, rate: Decimal | None, model: str, **extra) -> dict:
        return dict(cost_source="metered_estimate", authoritative_billing=False, currency="CNY",
                    unit=unit, rate_fen=str(rate), model=model, price_version=self.settings.wedding_price_version, **extra)

    def _error(self, exc: Exception, *, cost: int, state: str, task_id=None, request_id=None, usage=None, price=None):
        code = exc.code if isinstance(exc, ProviderUnavailable) else "PROVIDER_TRANSPORT_ERROR" if isinstance(exc, httpx.HTTPError) else "PROVIDER_OUTPUT_INVALID"
        return ProviderUnavailable(code, "Wedding provider operation could not complete safely", actual_cost_fen=cost,
            submission_state=state, task_id=task_id, request_id=request_id, usage=usage,
            price_version=self.settings.wedding_price_version, price_metadata=price)


class ArkStoryProvider(_HTTP):
    def structure(self, request: StoryStructureRequest) -> StructuredOutput:
        self._enabled("story")
        cfg = self.settings
        if not cfg.wedding_story_input_fen_per_million_tokens or not cfg.wedding_story_output_fen_per_million_tokens:
            raise ProviderUnavailable("PROVIDER_PRICING_MISSING", "Explicit story token prices are required")
        if (not request.story_text.strip() or len(request.story_text) > 12000 or len(request.confirmed_facts) > 40
            or any(not f.strip() or len(f) > 1000 for f in request.confirmed_facts)
            or len(request.style) > 80 or len(request.role_names) > 2
            or any(role not in ("A", "B") or len(name) > 120 for role, name in request.role_names)):
            raise ProviderUnavailable("PROVIDER_INPUT_INVALID", "Story input exceeds supported bounds")
        url = cfg.wedding_ark_base_url.rstrip("/") + "/chat/completions"
        _validate_url(url, ("ark.cn-beijing.volces.com", "ark.ap-southeast-1.volces.com"))
        price = self._price("million_tokens", cfg.wedding_story_input_fen_per_million_tokens, cfg.wedding_story_model,
                            output_rate_fen=str(cfg.wedding_story_output_fen_per_million_tokens), region_endpoint=cfg.wedding_ark_base_url)
        system = (
            "Return only a JSON object with story_text, facts, shots. Facts: [{text, source_quote, source, confirmed:false}]. "
            "If confirmed_facts is nonempty preserve their exact text/order only, source=confirmed_input, source_quote=''. "
            "Otherwise extract facts verbatim: text and source_quote must be equal literal nonempty substrings of the original story, "
            "source=story_quote. Never confirm facts. Do not invent dates, locations, names, events or emotions. "
            "Use role_names and style only as presentation constraints. Produce exactly 18 shots, each 5000 ms, ordered 1..18. "
            "Each shot has shot_index, shot_type, duration_ms, person_keys (A/B only), fact_indexes (zero-based facts), "
            "description, subtitle (<=80 chars), creative_scene. Creative scene is visual atmosphere, never factual evidence. "
            "Scenes have no people; person shots require A/B keys. Types in order: " + json.dumps(SHOT_TYPES) + ". "
            "Shot 5 depicts A only, shot 6 B only; other person shots depict A and B. Use only supported facts in copy. "
            "Leave date/location subtitles empty if unsupported. All output remains a human-review draft."
        )
        payload = dict(model=cfg.wedding_story_model, stream=False, thinking={"type": "disabled"}, max_tokens=cfg.wedding_story_max_tokens,
                       response_format={"type": "json_object"}, messages=[{"role": "system", "content": system},
                       {"role": "user", "content": json.dumps(dict(story_text=request.story_text, confirmed_facts=request.confirmed_facts,
                        role_names=request.role_names, style=request.style), ensure_ascii=False)}])
        self._request_bound(payload)
        # UTF-8 bytes plus protocol overhead bound input token count conservatively.
        bounded_input_tokens = len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) + 4096
        maximum = _ceil((bounded_input_tokens * cfg.wedding_story_input_fen_per_million_tokens +
                        cfg.wedding_story_max_tokens * cfg.wedding_story_output_fen_per_million_tokens) / Decimal(1_000_000))
        if maximum > cfg.wedding_story_max_cost_fen:
            raise ProviderUnavailable("PROVIDER_PRICE_LIMIT", "Story price bounds exceed its configured reservation")
        price["reservation_fen"] = cfg.wedding_story_max_cost_fen
        cost, usage, request_id, state = cfg.wedding_story_max_cost_fen, {}, None, "unknown"
        try:
            result = self._json("POST", url, deadline=self._deadline(), headers={"Authorization": "Bearer " + cfg.wedding_story_api_key}, payload=payload)
            state = "succeeded"
            request_id = result.get("id") or result.get("request_id") or result.get("_transport_request_id")
            usage = result.get("usage", {})
            if not isinstance(usage, dict):
                raise ValueError()
            cost = _ceil((_number(usage["prompt_tokens"]) * cfg.wedding_story_input_fen_per_million_tokens +
                          _number(usage["completion_tokens"]) * cfg.wedding_story_output_fen_per_million_tokens) / Decimal(1_000_000))
            content = result["choices"][0]["message"]["content"]
            story = _Story.model_validate_json(content, strict=True)
            if request.confirmed_facts:
                if tuple(f.text for f in story.facts) != request.confirmed_facts or any(f.source != "confirmed_input" or f.source_quote for f in story.facts):
                    raise ValueError()
            elif any(f.source != "story_quote" or not f.source_quote or f.text != f.source_quote or f.source_quote not in request.story_text for f in story.facts):
                raise ValueError()
            if tuple(s.shot_type for s in story.shots) != SHOT_TYPES or [s.shot_index for s in story.shots] != list(range(1, 19)):
                raise ValueError()
            for shot in story.shots:
                if shot.duration_ms != 5000 or any(i < 0 or i >= len(story.facts) for i in shot.fact_indexes):
                    raise ValueError()
                expected = ["A"] if shot.shot_index == 5 else ["B"] if shot.shot_index == 6 else ["A", "B"]
                if shot.shot_type == "person_ai" and shot.person_keys != expected:
                    raise ValueError()
                if shot.shot_type == "scene_ai" and shot.person_keys:
                    raise ValueError()
            return StructuredOutput(story.model_dump_json().encode(), "ark", cfg.wedding_story_model, cost,
                request_id=request_id, usage=usage, price_version=cfg.wedding_price_version, price_metadata=price)
        except (ProviderUnavailable, httpx.HTTPError, KeyError, IndexError, TypeError, ValueError, ValidationError) as exc:
            if isinstance(exc, ProviderUnavailable) and exc.submission_state == "failed":
                cost, state = 0, "failed"
            raise self._error(exc, cost=cost, state=state, request_id=request_id, usage=usage, price=price) from None


class SeedreamFrameProvider(_HTTP):
    def generate(self, request: FrameGenerationRequest) -> MediaOutput:
        self._enabled("frame")
        cfg = self.settings
        url = cfg.wedding_ark_base_url.rstrip("/") + "/images/generations"
        _validate_url(url, ("ark.cn-beijing.volces.com", "ark.ap-southeast-1.volces.com"))
        if len(request.references) > 14 or not request.description.strip() or len(request.description) > 1000:
            raise ProviderUnavailable("PROVIDER_INPUT_INVALID", "Frame input exceeds supported bounds")
        payload = dict(model=cfg.wedding_frame_model, prompt=request.description, size="2K", response_format="b64_json",
                       watermark=True, sequential_image_generation="disabled", stream=False)
        if request.references:
            payload["image"] = [self._data_uri(data, mime) for _, data, mime in request.references]
        self._request_bound(payload)
        price = self._price("image", cfg.wedding_frame_fen_per_image, cfg.wedding_frame_model, region_endpoint=cfg.wedding_ark_base_url)
        price["reservation_fen"] = _ceil(cfg.wedding_frame_fen_per_image)
        cost, usage, request_id, state = _ceil(cfg.wedding_frame_fen_per_image), {}, None, "unknown"
        try:
            result = self._json("POST", url, deadline=self._deadline(), headers={"Authorization": "Bearer " + cfg.wedding_frame_api_key}, payload=payload)
            state = "succeeded"
            request_id = result.get("id") or result.get("request_id") or result.get("_transport_request_id")
            usage = result.get("usage", {})
            if not isinstance(usage, dict):
                raise ValueError()
            count = _number(usage.get("generated_images", 1))
            if count < 1:
                raise ValueError()
            cost = _ceil(count * cfg.wedding_frame_fen_per_image)
            if len(result["data"]) != 1:
                raise ValueError()
            encoded = result["data"][0]["b64_json"]
            if not isinstance(encoded, str) or len(encoded) > ((cfg.wedding_max_image_bytes + 2) // 3) * 4:
                raise ValueError()
            data = base64.b64decode(encoded, validate=True)
            mime = _image(data, None, cfg.wedding_max_image_bytes)
            return MediaOutput(data, mime, "seedream", cfg.wedding_frame_model, cost, request_id=request_id,
                               usage=usage, price_version=cfg.wedding_price_version, price_metadata=price)
        except (ProviderUnavailable, httpx.HTTPError, KeyError, IndexError, TypeError, ValueError, binascii.Error) as exc:
            if isinstance(exc, ProviderUnavailable) and exc.submission_state == "failed":
                cost, state = 0, "failed"
            raise self._error(exc, cost=cost, state=state, request_id=request_id, usage=usage, price=price) from None


class WanVideoProvider(_HTTP):
    def __init__(self, settings: Settings, kind: str, **kwargs):
        super().__init__(settings, **kwargs)
        if kind not in ("wan_r2v", "wan_i2v_flash"):
            raise ValueError("Unsupported video adapter")
        self.kind = kind

    def generate(self, request: VideoGenerationRequest) -> MediaOutput:
        cfg = self.settings
        r2v = self.kind == "wan_r2v"
        self._enabled("character_video" if r2v else "scene_video")
        model = cfg.wedding_character_video_model if r2v else cfg.wedding_scene_video_model
        key = cfg.wedding_character_video_api_key if r2v else cfg.wedding_scene_video_api_key
        rate = cfg.wedding_r2v_fen_per_second if r2v else cfg.wedding_i2v_flash_fen_per_second
        base = cfg.wedding_dashscope_base_url.rstrip("/")
        _validate_url(base, ("dashscope.aliyuncs.com", "dashscope-intl.aliyuncs.com",
                             "cn-beijing.maas.aliyuncs.com", "ap-southeast-1.maas.aliyuncs.com"))
        price = self._price("second", rate, model, region_endpoint=base, audio=False if not r2v else "discarded_in_render")
        price["reservation_fen"] = _ceil(rate * 5)
        cost, usage, request_id, state = _ceil(rate * 5), {}, None, "unknown"
        task_id = request.existing_task_id
        if task_id and not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", task_id):
            raise ProviderUnavailable("PROVIDER_INPUT_INVALID", "Provider task identity is invalid")
        headers = {"Authorization": "Bearer " + (key or "")}
        payload = None
        if not task_id:
            if not request.description.strip() or len(request.description) > 1000:
                raise ProviderUnavailable("PROVIDER_INPUT_INVALID", "Video prompt exceeds supported bounds")
            first_frame = self._data_uri(request.reference_image, request.reference_mime)
            params = dict(resolution="1080P", duration=5, prompt_extend=False, watermark=True)
            if r2v:
                roles = [role for role, _, _ in request.primary_references]
                if not roles or len(roles) > 2 or len(set(roles)) != len(roles) or any(role not in ("A", "B") for role in roles):
                    raise ProviderUnavailable("PROVIDER_INPUT_INVALID", "R2V requires distinct A/B primary portraits")
                media = [{"type": "first_frame", "url": first_frame}]
                ordered = sorted(request.primary_references, key=lambda item: item[0])
                media.extend({"type": "reference_image", "url": self._data_uri(data, mime)} for _, data, mime in ordered)
                mapping = " ".join(f"The person in Image {i} is {role}." for i, (role, _, _) in enumerate(ordered, 1))
                inputs = dict(prompt=mapping + " " + request.description, media=media)
            else:
                if request.primary_references:
                    raise ProviderUnavailable("PROVIDER_INPUT_INVALID", "Scene I2V accepts no primary portraits")
                inputs = dict(prompt=request.description, img_url=first_frame)
                params["audio"] = False
            payload = dict(model=model, input=inputs, parameters=params)
            self._request_bound(payload)
        deadline = self._deadline()
        try:
            if not task_id:
                result = self._json("POST", base + "/api/v1/services/aigc/video-generation/video-synthesis", deadline=deadline,
                    headers={**headers, "X-DashScope-Async": "enable"}, payload=payload, heartbeat=request.heartbeat)
                request_id = result.get("request_id") or result.get("_transport_request_id")
                task_id = result["output"]["task_id"]
                if not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", task_id):
                    task_id = None
                    raise ValueError()
                state = "running"
                if request.checkpoint:
                    try:
                        request.checkpoint(task_id)
                    except Exception:
                        raise ProviderUnavailable("PROVIDER_CHECKPOINT_FAILED", "Provider task checkpoint could not be saved") from None
            state = "running"
            while True:
                self._remaining(deadline)
                result = self._json("GET", base + "/api/v1/tasks/" + task_id, deadline=deadline,
                                    headers=headers, heartbeat=request.heartbeat)
                request_id = result.get("request_id") or result.get("_transport_request_id") or request_id
                output = result["output"]
                status = output["task_status"]
                usage = result.get("usage", {})
                if not isinstance(usage, dict):
                    raise ValueError()
                if status in ("FAILED", "CANCELED"):
                    state = "failed"
                    cost = _ceil(rate * _number(usage.get("duration", 0)))
                    raise ProviderUnavailable("PROVIDER_TASK_FAILED", "Provider task definitively failed")
                if status == "SUCCEEDED":
                    state = "succeeded"
                    cost = _ceil(rate * _number(usage.get("duration", 5)))
                    data, mime = self._download(output["video_url"], deadline=deadline, heartbeat=request.heartbeat)
                    if mime != "video/mp4":
                        raise ValueError()
                    _mp4(data)
                    return MediaOutput(data, mime, self.kind, model, cost, request_id=request_id,
                        task_id=task_id, usage=usage, price_version=cfg.wedding_price_version, price_metadata=price)
                state = "running" if status in ("PENDING", "RUNNING") else "unknown"
                # Pulse leases during waiting, even with a large configured poll interval.
                pause = min(cfg.wedding_poll_interval_seconds, self._remaining(deadline))
                while pause > 0:
                    self._heartbeat(request.heartbeat)
                    step = min(pause, 1.0)
                    self.sleep(step)
                    pause -= step
        except (ProviderUnavailable, httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            # A rejected submit is definite; GET failure never changes the already accepted task state.
            if not task_id and isinstance(exc, ProviderUnavailable) and exc.submission_state == "failed":
                cost, state = 0, "failed"
            raise self._error(exc, cost=cost, state=state, task_id=task_id, request_id=request_id, usage=usage, price=price) from None


def provider_capabilities(settings: Settings) -> dict[str, dict]:
    config = settings
    rows = {
        "story": (config.wedding_story_provider, "ark", config.wedding_story_api_key, config.wedding_story_model, STORY_MODEL),
        "frame": (config.wedding_frame_provider, "seedream", config.wedding_frame_api_key, config.wedding_frame_model, FRAME_MODEL),
        "character_video": (config.wedding_character_video_provider, "wan_r2v", config.wedding_character_video_api_key, config.wedding_character_video_model, R2V_MODEL),
        "scene_video": (config.wedding_scene_video_provider, "wan_i2v_flash", config.wedding_scene_video_api_key, config.wedding_scene_video_model, FLASH_MODEL),
    }
    result = {}
    for name, (provider, supported, credential, model, pinned) in rows.items():
        reason = None
        if config.wedding_media_mode.strip().lower() != "live" or provider == "disabled":
            reason = "disabled"
        elif provider != supported:
            reason = "unsupported_provider"
        elif not credential or not credential.strip():
            reason = "credentials_missing"
        elif model != pinned:
            reason = "unsupported_model"
        elif name == "story" and (not config.wedding_story_input_fen_per_million_tokens or not config.wedding_story_output_fen_per_million_tokens):
            reason = "pricing_missing"
        result[name] = dict(available=reason is None, reason=reason, provider=provider, model=model)
    return result


class _UnavailableLive(_UnavailableProvider):
    def __init__(self, row):
        self.row = row

    def _raise(self):
        codes = dict(credentials_missing="PROVIDER_CREDENTIALS_MISSING", pricing_missing="PROVIDER_PRICING_MISSING")
        raise ProviderUnavailable(codes.get(self.row["reason"], "PROVIDER_UNAVAILABLE"), "Wedding adapter is not configured for this capability")


def live_provider_bundle(settings: Settings) -> ProviderBundle:
    caps = provider_capabilities(settings)
    factories = dict(story=lambda: ArkStoryProvider(settings), frame=lambda: SeedreamFrameProvider(settings),
        character_video=lambda: WanVideoProvider(settings, "wan_r2v"), scene_video=lambda: WanVideoProvider(settings, "wan_i2v_flash"))
    credentials = dict(story=settings.wedding_story_api_key, frame=settings.wedding_frame_api_key,
        character_video=settings.wedding_character_video_api_key, scene_video=settings.wedding_scene_video_api_key)
    providers = {}
    for name, row in caps.items():
        if row["available"]:
            providers[name] = factories[name]()
        elif row["provider"] != "disabled" and not (credentials[name] or "").strip():
            # Preserve legacy callers' explicit missing-credential error, even while
            # the global mode blocks dispatch. Capability still reports disabled.
            providers[name] = _UnavailableProvider(row["provider"], credentials[name])
        else:
            providers[name] = _UnavailableLive(row)
    return ProviderBundle(**providers)
