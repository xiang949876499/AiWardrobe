from __future__ import annotations

import hashlib

import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol, Sequence

from PIL import Image, ImageDraw, ImageFont, PngImagePlugin

from app.config import Settings, get_settings


class ProviderUnavailable(RuntimeError):
    def __init__(self, code: str, message: str, *, actual_cost_fen: int = 0,
                 task_id: str | None = None, submission_state: str = "not_submitted",
                 request_id: str | None = None, usage: dict | None = None,
                 price_version: str | None = None, price_metadata: dict | None = None) -> None:
        self.code = code
        self.actual_cost_fen = actual_cost_fen
        self.task_id = task_id
        self.submission_state = submission_state
        self.request_id = request_id
        self.usage = usage or {}
        self.price_version = price_version
        self.price_metadata = price_metadata or {}
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class MediaOutput:
    data: bytes
    mime: str
    provider: str
    model_version: str
    actual_cost_fen: int
    request_id: str | None = None
    demo: bool = False
    task_id: str | None = None
    usage: dict = field(default_factory=dict)
    price_version: str | None = None
    price_metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class StructuredOutput:
    data: bytes
    provider: str
    model_version: str
    actual_cost_fen: int
    request_id: str | None = None
    demo: bool = False
    task_id: str | None = None
    usage: dict = field(default_factory=dict)
    price_version: str | None = None
    price_metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class StoryStructureRequest:
    story_text: str
    confirmed_facts: tuple[str, ...]
    idempotency_key: str
    role_names: tuple[tuple[str, str], ...] = ()
    style: str = "cinematic"


@dataclass(frozen=True)
class FrameGenerationRequest:
    shot_id: str
    description: str
    references: tuple[tuple[str, bytes, str], ...]
    idempotency_key: str


@dataclass(frozen=True)
class VideoGenerationRequest:
    project_id: str
    job_id: str
    shot_id: str
    shot_type: str
    description: str
    subtitle: str
    reference_image: bytes
    reference_mime: str
    idempotency_key: str
    primary_references: tuple[tuple[str, bytes, str], ...] = ()
    reconcile_only: bool = False
    existing_task_id: str | None = None
    checkpoint: Callable[[str], None] | None = None
    heartbeat: Callable[[], None] | None = None


class StoryStructureProvider(Protocol):
    def structure(self, request: StoryStructureRequest) -> StructuredOutput: ...


class FrameProvider(Protocol):
    def generate(self, request: FrameGenerationRequest) -> MediaOutput: ...


class CharacterVideoProvider(Protocol):
    def generate(self, request: VideoGenerationRequest) -> MediaOutput: ...


class SceneVideoProvider(Protocol):
    def generate(self, request: VideoGenerationRequest) -> MediaOutput: ...


@dataclass(frozen=True)
class ProviderBundle:
    story: StoryStructureProvider | None = None
    frame: FrameProvider | None = None
    character_video: CharacterVideoProvider | None = None
    scene_video: SceneVideoProvider | None = None


class _UnavailableProvider:
    def __init__(self, provider: str, credential: str | None) -> None:
        self.provider = provider
        self.has_credential = bool(credential and credential.strip())

    def _raise(self) -> None:
        if self.provider == "disabled":
            raise ProviderUnavailable("PROVIDER_UNAVAILABLE", "Wedding media provider is disabled")
        if not self.has_credential:
            raise ProviderUnavailable("PROVIDER_CREDENTIALS_MISSING", "Configured wedding provider has no credentials")
        raise ProviderUnavailable("PROVIDER_UNAVAILABLE", "No live wedding provider adapter is enabled")

    def structure(self, request: StoryStructureRequest) -> StructuredOutput:
        self._raise()

    def generate(self, request: FrameGenerationRequest | VideoGenerationRequest) -> MediaOutput:
        self._raise()


class _DemoProvider:
    """Deterministic cards used only for local plumbing demos; never depicts generated people."""

    provider = "local_demo"
    model_version = "local-demo-v1"

    @staticmethod
    def _card(title: str) -> bytes:
        image = Image.new("RGB", (1280, 720), (35, 24, 42))
        draw = ImageDraw.Draw(image)
        font = ImageFont.load_default()
        lines = (title, "DEMO ONLY", "NO AI MOTION")
        y = 270
        for line in lines:
            box = draw.textbbox((0, 0), line, font=font)
            x = (image.width - (box[2] - box[0])) // 2
            draw.text((x, y), line, fill=(255, 255, 255), font=font)
            y += 36
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text("Description", "DEMO ONLY; NO AI MOTION")
        output = tempfile.SpooledTemporaryFile()
        image.save(output, format="PNG", optimize=False, pnginfo=metadata)
        output.seek(0)
        return output.read()

    def structure(self, request: StoryStructureRequest) -> StructuredOutput:
        from app.wedding_providers import SHOT_TYPES
        facts = [{"text": text, "source_quote": "" if request.confirmed_facts else text,
                  "source": "confirmed_input" if request.confirmed_facts else "story_quote", "confirmed": False}
                 for text in (request.confirmed_facts or (request.story_text[:1000],))]
        shots = [{"shot_index": i, "shot_type": kind, "duration_ms": 5000,
                  "person_keys": (["A"] if i == 5 else ["B"] if i == 6 else ["A", "B"]) if kind == "person_ai" else [],
                  "fact_indexes": [0], "description": "DEMO ONLY: editable visual placeholder",
                  "subtitle": "", "creative_scene": kind == "scene_ai"}
                 for i, kind in enumerate(SHOT_TYPES, 1)]
        data = json.dumps({"story_text": request.story_text, "facts": facts, "shots": shots}).encode("utf-8")
        return StructuredOutput(data, self.provider, self.model_version, 0, demo=True)

    def generate(self, request: FrameGenerationRequest | VideoGenerationRequest) -> MediaOutput:
        return MediaOutput(
            data=self._card("LOCAL WEDDING PIPELINE"),
            mime="image/png",
            provider=self.provider,
            model_version=self.model_version,
            actual_cost_fen=0,
            request_id=None,
            demo=True,
        )


def build_provider_bundle(settings: Settings | None = None) -> ProviderBundle:
    config = settings or get_settings()
    if config.wedding_media_mode.strip().lower() == "demo":
        demo = _DemoProvider()
        return ProviderBundle(demo, demo, demo, demo)
    # Lazy import keeps the established demo contracts independent of HTTP adapters.
    from app.wedding_providers import live_provider_bundle
    return live_provider_bundle(config)


@dataclass(frozen=True)
class RenderInput:
    data: bytes
    mime: str
    subtitle: str
    duration_ms: int


@dataclass(frozen=True)
class RenderedVideo:
    data: bytes
    duration_ms: int
    demo: bool


# Public import remains compatible; renderer is kept separate from paid adapters.
def __getattr__(name):
    if name == 'FFmpegRenderer':
        from app.wedding_render_media import FFmpegRenderer
        return FFmpegRenderer
    raise AttributeError(name)
