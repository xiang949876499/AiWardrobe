from __future__ import annotations

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


class FFmpegRenderer:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        ffmpeg_path: str | None = None,
        timeout_seconds: int = 180,
    ) -> None:
        self.settings = settings or get_settings()
        self.ffmpeg_path = ffmpeg_path or self.settings.wedding_ffmpeg_path or shutil.which("ffmpeg")
        self.timeout_seconds = timeout_seconds

    def validate_music_license(self, *, demo: bool) -> Path | None:
        if demo:
            return None
        music_file = self.settings.wedding_music_file
        license_id = self.settings.wedding_music_license_id
        if not music_file or not license_id:
            raise ProviderUnavailable("MUSIC_LICENSE_REQUIRED", "A licensed music file and license ID are required")
        path = Path(music_file)
        if not path.is_file():
            raise ProviderUnavailable("MUSIC_FILE_MISSING", "The configured licensed music file is unavailable")
        return path

    def render(
        self,
        inputs: Sequence[RenderInput],
        *,
        demo: bool,
        resolution: tuple[int, int],
    ) -> RenderedVideo:
        if not self.ffmpeg_path:
            raise ProviderUnavailable("FFMPEG_UNAVAILABLE", "FFmpeg executable is unavailable")
        if not inputs or any(item.duration_ms <= 0 for item in inputs):
            raise ProviderUnavailable("TIMELINE_INVALID", "Render timeline has no valid clips")
        music_path = self.validate_music_license(demo=demo)
        duration_ms = sum(item.duration_ms for item in inputs)

        try:
            with tempfile.TemporaryDirectory(prefix="wedding-render-") as temp_name:
                workdir = Path(temp_name)
                args = [self.ffmpeg_path, "-hide_banner", "-loglevel", "error", "-y"]
                subtitle_rows: list[str] = []
                cursor_ms = 0
                for index, item in enumerate(inputs):
                    suffix = {
                        "image/jpeg": ".jpg",
                        "image/png": ".png",
                        "image/webp": ".webp",
                        "video/mp4": ".mp4",
                        "video/webm": ".webm",
                    }.get(item.mime)
                    if suffix is None:
                        raise ProviderUnavailable("RENDER_INPUT_UNSUPPORTED", "Selected render asset type is unsupported")
                    source_path = workdir / f"input-{index}{suffix}"
                    source_path.write_bytes(item.data)
                    seconds = f"{item.duration_ms / 1000:.3f}"
                    if item.mime.startswith("video/"):
                        args.extend(["-stream_loop", "-1", "-t", seconds, "-i", source_path.name])
                    else:
                        args.extend(["-loop", "1", "-framerate", "30", "-t", seconds, "-i", source_path.name])
                    subtitle = (item.subtitle or "").replace("\r", " ").strip()
                    if subtitle:
                        end = cursor_ms + item.duration_ms
                        subtitle_rows.extend(
                            [str(index + 1), self._srt_timestamp(cursor_ms) + " --> " + self._srt_timestamp(end), subtitle, ""]
                        )
                    cursor_ms += item.duration_ms
                (workdir / "subtitles.srt").write_text("\n".join(subtitle_rows), encoding="utf-8")

                width, height = resolution
                filter_parts = []
                for index in range(len(inputs)):
                    filter_parts.append(
                        f"[{index}:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
                        f"crop={width}:{height},fps=30,setsar=1,format=yuv420p[v{index}]"
                    )
                concatenated = "".join(f"[v{index}]" for index in range(len(inputs)))
                filter_parts.append(
                    f"{concatenated}concat=n={len(inputs)}:v=1:a=0,subtitles=subtitles.srt[base]"
                )
                if demo:
                    filter_parts.append(
                        "[base]drawtext=text='DEMO ONLY - NO AI MOTION':fontcolor=white:fontsize=36:"
                        "box=1:boxcolor=black@0.75:boxborderw=14:x=24:y=24[video]"
                    )
                else:
                    filter_parts.append("[base]null[video]")
                args.extend(["-filter_complex", ";".join(filter_parts)])

                if music_path is not None:
                    music_name = "licensed-music" + music_path.suffix
                    (workdir / music_name).write_bytes(music_path.read_bytes())
                    args.extend(["-stream_loop", "-1", "-i", music_name])
                    audio_index = len(inputs)
                    filter_parts.append(
                        f"[{audio_index}:a]atrim=duration={duration_ms / 1000:.3f},"
                        "asetpts=PTS-STARTPTS,volume=0.15[audio]"
                    )
                    # Audio is added as a second filter graph so the video graph remains stable.
                    args[args.index("-filter_complex") + 1] = ";".join(filter_parts)
                    args.extend(["-map", "[video]", "-map", "[audio]"])
                else:
                    args.extend(["-map", "[video]", "-an"])

                args.extend(
                    [
                        "-t",
                        f"{duration_ms / 1000:.3f}",
                        "-c:v",
                        "libx264",
                        "-preset",
                        "veryfast",
                        "-pix_fmt",
                        "yuv420p",
                        "-r",
                        "30",
                    ]
                )
                if music_path is not None:
                    args.extend(["-c:a", "aac", "-b:a", "192k", "-ar", "48000"])
                output_name = "render.mp4"
                args.extend(["-movflags", "+faststart", output_name])
                completed = subprocess.run(
                    args,
                    cwd=workdir,
                    capture_output=True,
                    timeout=self.timeout_seconds,
                    check=False,
                )
                output_path = workdir / output_name
                if completed.returncode != 0 or not output_path.is_file() or output_path.stat().st_size == 0:
                    raise ProviderUnavailable("FFMPEG_FAILED", "FFmpeg could not produce a valid MP4")
                return RenderedVideo(output_path.read_bytes(), duration_ms, demo)
        except ProviderUnavailable:
            raise
        except (OSError, subprocess.SubprocessError) as exc:
            raise ProviderUnavailable("FFMPEG_FAILED", "FFmpeg could not produce a valid MP4") from exc

    @staticmethod
    def _srt_timestamp(value_ms: int) -> str:
        hours, remainder = divmod(value_ms, 3_600_000)
        minutes, remainder = divmod(remainder, 60_000)
        seconds, milliseconds = divmod(remainder, 1_000)
        return f"{hours:02}:{minutes:02}:{seconds:02},{milliseconds:03}"
