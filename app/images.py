"""Headshot processing: validate, crop, resize, strip metadata."""
import hashlib
import io
import uuid

from PIL import Image, ImageDraw, ImageFont, ImageOps

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


MONOGRAM_PALETTE = ["#33493a", "#7a4b33", "#4b5e7a", "#6b5b8c", "#8c6a3a", "#3a6b64"]


def monogram_avatar(name: str) -> tuple[str, bytes]:
    """Fallback headshot: initials on a warm solid, chosen deterministically
    from the name so it stays stable across edits."""
    img = Image.new("RGB", (800, 800), MONOGRAM_PALETTE[
        int(hashlib.sha256(name.encode()).hexdigest(), 16) % len(MONOGRAM_PALETTE)])
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf", 340)
    except OSError:
        font = ImageFont.load_default(size=340)
    initials = "".join(w[0] for w in name.split()[:2]) or "?"
    try:
        d.text((400, 390), initials.upper(), font=font, fill="#f6f1e8", anchor="mm")
    except TypeError:
        # bitmap font fallback without anchor support
        box = d.textbbox((0, 0), initials.upper(), font=font)
        d.text((400 - box[2] / 2, 390 - box[3] / 2), initials.upper(),
               font=font, fill="#f6f1e8")
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88)
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
