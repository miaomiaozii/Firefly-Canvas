"""Deterministic RGBA compositing for the project local-edit node.

The module deliberately keeps the online adapter boundary small: adapters receive
only the image payload required by the selected mode and never receive a mask.
Selections are persisted in source-image pixel coordinates by the caller.
"""
from __future__ import annotations

import io
import json
from typing import Any, Mapping, Sequence

from PIL import Image, ImageDraw, ImageFilter


def as_rgba(value: Image.Image | bytes | bytearray) -> Image.Image:
    image = value if isinstance(value, Image.Image) else Image.open(io.BytesIO(bytes(value)))
    return image.convert("RGBA")


def encode_png(image: Image.Image) -> bytes:
    output = io.BytesIO()
    as_rgba(image).save(output, format="PNG")
    return output.getvalue()


def resize_rgba(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Resize RGB and alpha independently so translucent RGB is not premultiplied."""
    rgba = as_rgba(image)
    rgb = rgba.convert("RGB").resize(size, Image.Resampling.LANCZOS)
    alpha = rgba.getchannel("A").resize(size, Image.Resampling.LANCZOS)
    return Image.merge("RGBA", (*rgb.split(), alpha))


def map_viewport_rect(rect: Mapping[str, Any], display_size: Sequence[int], image_size: Sequence[int]) -> tuple[int, int, int, int]:
    """Map viewport coordinates once to integer source half-open pixels."""
    dw, dh = (max(1, int(display_size[0])), max(1, int(display_size[1])))
    iw, ih = (max(1, int(image_size[0])), max(1, int(image_size[1])))
    left = round(float(rect.get("left", 0)) * iw / dw)
    top = round(float(rect.get("top", 0)) * ih / dh)
    right = round(float(rect.get("right", dw)) * iw / dw)
    bottom = round(float(rect.get("bottom", dh)) * ih / dh)
    return (max(0, min(iw - 1, left)), max(0, min(ih - 1, top)),
            max(1, min(iw, right)), max(1, min(ih, bottom)))


def validate_rect(rect: Sequence[int], image_size: Sequence[int], aspect_ratio: str | None = None) -> tuple[int, int, int, int]:
    iw, ih = int(image_size[0]), int(image_size[1])
    if len(rect) != 4:
        raise ValueError("selection must be [left, top, right, bottom]")
    left, top, right, bottom = (int(round(float(item))) for item in rect)
    if not (0 <= left < right <= iw and 0 <= top < bottom <= ih):
        raise ValueError("selection is outside the source image")
    if aspect_ratio:
        try:
            aw, ah = (float(item) for item in str(aspect_ratio).split(":", 1))
            if aw <= 0 or ah <= 0 or abs((right - left) / (bottom - top) - aw / ah) > 0.02:
                raise ValueError("selection does not match the fixed aspect ratio")
        except (ValueError, ZeroDivisionError):
            raise ValueError("invalid aspect ratio")
    return left, top, right, bottom


def crop_rect(image: Image.Image | bytes, rect: Sequence[int]) -> Image.Image:
    source = as_rgba(image)
    left, top, right, bottom = validate_rect(rect, source.size)
    return source.crop((left, top, right, bottom))


ROTATE_ANGLES = (90, 180, 270)
FLIP_DIRECTIONS = ("horizontal", "vertical")


def rotate_image(image: Image.Image | bytes, angle: int) -> Image.Image:
    """#266b canvas.edit_rotate: 逆时针旋转 90/180/270（PIL transpose 语义）；其余角度拒绝。"""
    source = as_rgba(image)
    try:
        angle = int(angle)
    except (TypeError, ValueError):
        raise ValueError(f"rotate angle must be one of {ROTATE_ANGLES}, got {angle!r}") from None
    if angle not in ROTATE_ANGLES:
        raise ValueError(f"rotate angle must be one of {ROTATE_ANGLES}, got {angle}")
    constant = {90: Image.ROTATE_90, 180: Image.ROTATE_180, 270: Image.ROTATE_270}[angle]
    return source.transpose(constant)


def flip_image(image: Image.Image | bytes, direction: str) -> Image.Image:
    """#266b canvas.edit_flip: horizontal=左右镜像 / vertical=上下镜像；其余方向拒绝。"""
    source = as_rgba(image)
    direction = str(direction or "").strip().lower()
    if direction not in FLIP_DIRECTIONS:
        raise ValueError(f"flip direction must be one of {FLIP_DIRECTIONS}, got {direction!r}")
    constant = Image.FLIP_LEFT_RIGHT if direction == "horizontal" else Image.FLIP_TOP_BOTTOM
    return source.transpose(constant)


def _blurred_mask(mask: Image.Image, feather_radius: float) -> Image.Image:
    radius = max(0.0, float(feather_radius or 0))
    return mask.filter(ImageFilter.GaussianBlur(radius)) if radius else mask


def rectangular_mask(size: Sequence[int], rect: Sequence[int], feather_radius: float = 0) -> Image.Image:
    width, height = int(size[0]), int(size[1])
    left, top, right, bottom = validate_rect(rect, (width, height))
    hard = Image.new("L", (width, height), 0)
    ImageDraw.Draw(hard).rectangle((left, top, right - 1, bottom - 1), fill=255)
    blurred = _blurred_mask(hard, feather_radius)
    # Feathering is an edge treatment inside the selected rectangle.  Clamp
    # the blurred mask by the hard rectangle so no outside pixel can change.
    return Image.composite(blurred, Image.new("L", (width, height), 0), hard)


def rasterize_preserve_mask(selection: Mapping[str, Any] | Sequence[Any], image_size: Sequence[int], feather_radius: float = 0) -> Image.Image:
    """Rasterize freehand/lasso/curve shapes to 255=restore-original, 0=model."""
    width, height = int(image_size[0]), int(image_size[1])
    mask = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(mask)
    shapes = selection.get("shapes", []) if isinstance(selection, Mapping) else selection
    if isinstance(selection, Mapping) and selection.get("points"):
        shapes = [selection]
    for shape in shapes or []:
        if isinstance(shape, Mapping):
            points = shape.get("points") or shape.get("path") or []
            kind = str(shape.get("type") or "polygon").lower()
            if kind in {"freehand", "curve", "stroke"}:
                width_px = max(1, int(round(float(shape.get("width", 12)))))
                if len(points) > 1:
                    draw.line([(int(round(p[0])), int(round(p[1]))) for p in points], fill=255, width=width_px, joint="curve")
            elif len(points) >= 3:
                draw.polygon([(int(round(p[0])), int(round(p[1]))) for p in points], fill=255)
    return _blurred_mask(mask, feather_radius)


def composite_rect(original: Image.Image | bytes, model_result: Image.Image | bytes, rect: Sequence[int], feather_radius: float = 0) -> Image.Image:
    source = as_rgba(original)
    left, top, right, bottom = validate_rect(rect, source.size)
    # The model result is resized exactly once to the source-pixel rectangle.
    resized = resize_rgba(as_rgba(model_result), (right - left, bottom - top))
    layer = source.copy()
    # Supplying an opaque mask makes this a replacement, preserving all four
    # channels (Pillow otherwise alpha-composites RGBA values on paste).
    layer.paste(resized, (left, top), Image.new("L", resized.size, 255))
    mask = rectangular_mask(source.size, (left, top, right, bottom), feather_radius)
    return Image.composite(layer, source, mask)


def composite_preserve(original: Image.Image | bytes, model_result: Image.Image | bytes, preserve_mask: Image.Image | bytes, feather_radius: float = 0) -> Image.Image:
    source = as_rgba(original)
    result = resize_rgba(as_rgba(model_result), source.size)
    preserve = (preserve_mask if isinstance(preserve_mask, Image.Image) else Image.open(io.BytesIO(bytes(preserve_mask)))).convert("L")
    if preserve.size != source.size:
        preserve = preserve.resize(source.size, Image.Resampling.NEAREST)
    preserve = _blurred_mask(preserve, feather_radius)
    # 255 means original, 0 means model.
    return Image.composite(source, result, preserve)


def selection_json(selection: Any) -> bytes:
    return json.dumps(selection, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
