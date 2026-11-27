"""Shared image fixtures for provider fakes and canvas tests."""

import io
import time

from PIL import Image


def fake_image_payload(png: str):
    return (
        {"type": "b64", "value": png, "mime_type": "image/png"},
        {"data": [{"b64_json": png}], "id": f"upstream-{time.monotonic_ns()}"},
    )


def open_canvas_asset(store, project_id, asset_id):
    record = store.media_record(project_id, asset_id)
    with open(record["path"], "rb") as f:
        # Pillow can decode lazily after the file handle has closed.
        return Image.open(io.BytesIO(f.read()))


def rgba_png(color: tuple[int, int, int, int]) -> bytes:
    image = Image.new("RGBA", (1, 1), color)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()
