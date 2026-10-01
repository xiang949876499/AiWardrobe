from __future__ import annotations

import hashlib
import warnings
from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_INPUT_BYTES = 10 * 1024 * 1024
MAX_OUTPUT_BYTES = 10 * 1024 * 1024
MAX_PIXELS = 24_000_000
MAX_EDGE = 2048
MIN_EDGE = 1024
TARGET_COMPRESSED_BYTES = 2 * 1024 * 1024
ALLOWED_FORMATS = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}


class ImageInputError(Exception):
    def __init__(self, code: str, status_code: int = 422) -> None:
        self.code = code
        self.status_code = status_code
        super().__init__(code)


@dataclass(frozen=True)
class PreparedImage:
    data: bytes
    content_type: str
    width: int
    height: int
    sha256: str
    thumbnail: bytes


def prepare_image(data: bytes, claimed_content_type: str | None = None) -> PreparedImage:
    if not data:
        raise ImageInputError("empty_image")
    if len(data) > MAX_INPUT_BYTES:
        raise ImageInputError("image_size_limit_exceeded", 413)

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as source:
                source_format = (source.format or "").upper()
                if source_format not in ALLOWED_FORMATS:
                    raise ImageInputError("unsupported_image_format", 415)
                width, height = source.size
                if width <= 0 or height <= 0 or width * height > MAX_PIXELS:
                    raise ImageInputError("image_pixel_limit_exceeded", 413)
                source.verify()
            with Image.open(BytesIO(data)) as source:
                image = ImageOps.exif_transpose(source)
                if getattr(image, "is_animated", False):
                    raise ImageInputError("animated_image_not_supported", 415)
                image.load()
    except ImageInputError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ImageInputError("image_pixel_limit_exceeded", 413) from exc
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ImageInputError("invalid_image") from exc

    output_format = source_format
    has_alpha = "A" in image.getbands() or "transparency" in image.info
    if source_format == "JPEG" and image.mode not in {"RGB", "L"}:
        image = image.convert("RGB")
    elif source_format in {"PNG", "WEBP"} and has_alpha and image.mode not in {"RGBA", "LA"}:
        image = image.convert("RGBA")
    elif source_format in {"PNG", "WEBP"} and not has_alpha and image.mode not in {"RGB", "L"}:
        image = image.convert("RGB")

    image.thumbnail((MAX_EDGE, MAX_EDGE), Image.Resampling.LANCZOS)
    encoded = _encode_with_limits(image, output_format, has_alpha)
    if len(encoded) > MAX_OUTPUT_BYTES:
        raise ImageInputError("image_processing_limit_exceeded", 413)

    with Image.open(BytesIO(encoded)) as normalized:
        normalized.load()
        thumb = normalized.copy()
        thumb.thumbnail((480, 480), Image.Resampling.LANCZOS)
        thumbnail = _encode_thumbnail(thumb, output_format, has_alpha)
        width, height = normalized.size
    return PreparedImage(
        data=encoded,
        content_type=ALLOWED_FORMATS[output_format],
        width=width,
        height=height,
        sha256=hashlib.sha256(encoded).hexdigest(),
        thumbnail=thumbnail,
    )


def _encode_with_limits(image: Image.Image, image_format: str, has_alpha: bool) -> bytes:
    if image_format == "PNG":
        buffer = BytesIO()
        image.save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()

    longest_edge = max(image.size)
    while True:
        if longest_edge != max(image.size):
            scale = longest_edge / max(image.size)
            size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
            candidate = image.resize(size, Image.Resampling.LANCZOS)
        else:
            candidate = image
        for quality in (88, 80, 72, 64):
            buffer = BytesIO()
            if image_format == "JPEG":
                candidate.save(buffer, format="JPEG", quality=quality, optimize=True, progressive=True)
            else:
                candidate.save(buffer, format="WEBP", quality=quality, method=6, lossless=False)
            encoded = buffer.getvalue()
            if len(encoded) <= TARGET_COMPRESSED_BYTES:
                return encoded
        if longest_edge <= MIN_EDGE:
            break
        longest_edge = int(longest_edge * 0.9)
    raise ImageInputError("image_compression_limit_exceeded", 413)


def _encode_thumbnail(image: Image.Image, image_format: str, has_alpha: bool) -> bytes:
    buffer = BytesIO()
    if image_format == "JPEG":
        if image.mode not in {"RGB", "L"}:
            image = image.convert("RGB")
        image.save(buffer, format="JPEG", quality=82, optimize=True)
    elif image_format == "WEBP":
        image.save(buffer, format="WEBP", quality=82, method=6, lossless=False)
    else:
        if has_alpha and image.mode not in {"RGBA", "LA"}:
            image = image.convert("RGBA")
        image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()
