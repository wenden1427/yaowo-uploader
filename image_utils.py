import io

from PIL import Image, ImageOps


MARKETPLACE_MIN_SIDE = 600
MARKETPLACE_MAX_SIDE = 1200
MARKETPLACE_MAX_BYTES = 300_000


def ensure_marketplace_image_spec(image_bytes, max_bytes=MARKETPLACE_MAX_BYTES):
    """Return JPEG bytes sized for marketplace upload.

    Guarantees:
    - JPEG/RGB output
    - width and height are at least 600px
    - longest side is capped at 1200px when possible
    - final bytes are <= max_bytes

    Very thin images are padded on a white canvas instead of being enlarged by
    their short edge, which avoids turning small banners into huge files.
    """
    img = _load_rgb(image_bytes)
    img = _fit_dimensions(img)
    data = _encode_jpeg(img, max_bytes)
    if len(data) <= max_bytes:
        return data

    # Last-resort fallback: 600x600 still satisfies marketplace dimensions and
    # is small enough even for noisy images at low quality.
    img = img.resize(
        (MARKETPLACE_MIN_SIDE, MARKETPLACE_MIN_SIDE),
        Image.Resampling.LANCZOS,
    )
    data = _encode_jpeg(img, max_bytes, qualities=(55, 45, 38, 32, 28, 24, 20))
    if len(data) <= max_bytes:
        return data
    raise ValueError(f"image cannot be compressed under {max_bytes} bytes, final size={len(data)}")


def _load_rgb(image_bytes):
    img = Image.open(io.BytesIO(image_bytes))
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        bg = Image.new("RGB", rgba.size, (255, 255, 255))
        bg.paste(rgba, mask=rgba.split()[-1])
        return bg
    return img.convert("RGB")


def _fit_dimensions(img):
    width, height = img.size
    aspect = max(width, height) / max(1, min(width, height))

    if max(width, height) > MARKETPLACE_MAX_SIDE:
        scale = MARKETPLACE_MAX_SIDE / max(width, height)
        img = _resize(img, width * scale, height * scale)
        width, height = img.size

    if aspect <= 3 and min(width, height) < MARKETPLACE_MIN_SIDE:
        scale = MARKETPLACE_MIN_SIDE / min(width, height)
        img = _resize(img, width * scale, height * scale)
        width, height = img.size
        if max(width, height) > MARKETPLACE_MAX_SIDE:
            scale = MARKETPLACE_MAX_SIDE / max(width, height)
            img = _resize(img, width * scale, height * scale)
            width, height = img.size

    if width < MARKETPLACE_MIN_SIDE or height < MARKETPLACE_MIN_SIDE:
        canvas = Image.new(
            "RGB",
            (max(MARKETPLACE_MIN_SIDE, width), max(MARKETPLACE_MIN_SIDE, height)),
            (255, 255, 255),
        )
        canvas.paste(img, ((canvas.size[0] - width) // 2, (canvas.size[1] - height) // 2))
        img = canvas

    return img


def _resize(img, width, height):
    return img.resize(
        (max(1, int(width)), max(1, int(height))),
        Image.Resampling.LANCZOS,
    )


def _encode_jpeg(img, max_bytes, qualities=(88, 82, 76, 70, 64, 58, 52, 46, 40, 35, 30)):
    last = b""
    work = img
    for _ in range(10):
        for quality in qualities:
            buf = io.BytesIO()
            work.save(buf, format="JPEG", quality=quality, optimize=True)
            data = buf.getvalue()
            last = data
            if len(data) <= max_bytes:
                return data

        width, height = work.size
        if min(width, height) <= MARKETPLACE_MIN_SIDE and max(width, height) <= MARKETPLACE_MIN_SIDE:
            break

        scale = 0.9
        new_width = max(MARKETPLACE_MIN_SIDE, int(width * scale))
        new_height = max(MARKETPLACE_MIN_SIDE, int(height * scale))
        if (new_width, new_height) == (width, height):
            break
        work = work.resize((new_width, new_height), Image.Resampling.LANCZOS)

    return last
