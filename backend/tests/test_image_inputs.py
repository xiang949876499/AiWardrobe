import pytest
from io import BytesIO

from PIL import Image


def _encoded_image(image: Image.Image, image_format: str) -> bytes:
    output = BytesIO()
    image.save(output, format=image_format)
    return output.getvalue()


def test_prepares_transparent_png_without_converting_away_alpha() -> None:
    from app.image_inputs import prepare_image

    original = Image.new("RGBA", (3000, 1500), (255, 0, 0, 100))
    prepared = prepare_image(_encoded_image(original, "PNG"), "image/png")
    decoded = Image.open(BytesIO(prepared.data))

    assert prepared.content_type == "image/png"
    assert decoded.mode == "RGBA"
    assert decoded.size == (2048, 1024)
    assert prepared.thumbnail is not None
    assert Image.open(BytesIO(prepared.thumbnail)).mode == "RGBA"


def test_rejects_spoofed_or_unsupported_image_content() -> None:
    from app.image_inputs import ImageInputError, prepare_image

    try:
        prepare_image(b"not an image", "image/jpeg")
    except ImageInputError as exc:
        assert exc.code == "invalid_image"
    else:
        raise AssertionError("Expected invalid image bytes to be rejected")


def test_rejects_images_above_pixel_limit_before_processing() -> None:
    from app.image_inputs import ImageInputError, prepare_image

    oversized = _encoded_image(Image.new("RGB", (5000, 5000), "white"), "JPEG")
    try:
        prepare_image(oversized, "image/jpeg")
    except ImageInputError as exc:
        assert exc.code == "image_pixel_limit_exceeded"
    else:
        raise AssertionError("Expected oversized pixel count to be rejected")


pytestmark = pytest.mark.usefixtures("legacy_worker")
