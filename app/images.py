"""Headshot processing: validate, crop, resize, strip metadata."""
import io
import uuid

from PIL import Image, ImageOps

MAX_UPLOAD_BYTES = 8 * 1024 * 1024
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}
OUTPUT_SIZE = 800  # square headshot edge, px


class ImageRejected(ValueError):
    pass


def process_headshot(data: bytes, crop: tuple[int, int, int, int] | None = None) -> tuple[str, bytes]:
    """Returns (storage_key, jpeg_bytes). Metadata is stripped by re-encoding."""
    if len(data) > MAX_UPLOAD_BYTES:
        raise ImageRejected("Image must be under 8 MB.")
    try:
        img = Image.open(io.BytesIO(data))
        img.verify()
        img = Image.open(io.BytesIO(data))
    except Exception as e:
        raise ImageRejected("That file doesn't look like an image.") from e
    if img.format not in ALLOWED_FORMATS:
        raise ImageRejected("Please upload a JPEG, PNG, or WebP image.")

    img = ImageOps.exif_transpose(img).convert("RGB")

    if crop:
        x, y, w, h = crop
        x, y = max(0, x), max(0, y)
        img = img.crop((x, y, min(x + w, img.width), min(y + h, img.height)))

    # center-crop to square if no crop supplied, then resize
    if not crop:
        side = min(img.width, img.height)
        left = (img.width - side) // 2
        top = (img.height - side) // 2
        img = img.crop((left, top, left + side, top + side))
    img = img.resize((OUTPUT_SIZE, OUTPUT_SIZE), Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88)  # re-encode strips EXIF/metadata
    return f"headshots/{uuid.uuid4().hex}.jpg", buf.getvalue()


def process_event_image(data: bytes) -> tuple[str, bytes]:
    if len(data) > MAX_UPLOAD_BYTES:
        raise ImageRejected("Image must be under 8 MB.")
    try:
        img = Image.open(io.BytesIO(data))
        img.verify()
        img = Image.open(io.BytesIO(data))
    except Exception as e:
        raise ImageRejected("That file doesn't look like an image.") from e
    if img.format not in ALLOWED_FORMATS:
        raise ImageRejected("Please upload a JPEG, PNG, or WebP image.")
    img = ImageOps.exif_transpose(img).convert("RGB")
    img.thumbnail((1600, 1200), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return f"events/{uuid.uuid4().hex}.jpg", buf.getvalue()
