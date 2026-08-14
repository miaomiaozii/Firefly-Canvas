"""Shared scaffold for the ``tools/*_evidence.py`` evidence validators.

Issue #43: the per-journey validators duplicated near-identical helpers for
resolving manifest-relative evidence files, reading JSON, validating screenshots,
comparing native click coordinates, and checking native click operations.  This
module is the single source of truth; validators import what they need instead
of redefining it.  Signatures and messages are kept compatible with the originals
so the move is behavior-preserving.
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, UnidentifiedImageError

try:
    from tools.native_input_evidence import has_trusted_selector_owner_click
except ModuleNotFoundError:  # Direct `python tools/...py` execution.
    from native_input_evidence import has_trusted_selector_owner_click


def artifact_path(root: Path, relative: object, label: str) -> Path:
    """Resolve a manifest-relative evidence file, refusing escape and absence."""
    resolved_root = root.resolve()
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError(f"{label} must be a non-empty relative path")
    candidate = (resolved_root / relative).resolve()
    try:
        candidate.relative_to(resolved_root)
    except ValueError as error:
        raise ValueError(f"{label} is outside the evidence root: {relative!r}") from error
    if not candidate.is_file():
        raise ValueError(f"{label} is missing: {relative!r}")
    return candidate


def read_json(path: Path, label: str | None = None) -> object:
    """Read a UTF-8 JSON file; wrap decode errors in a labelled ValueError when asked."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        if label is None:
            raise
        raise ValueError(f"{label} is not valid UTF-8 JSON: {error}") from error


def validate_png(path: Path, viewport: tuple[int, int] | None = None) -> dict:
    """Validate a non-blank PNG; optionally enforce an exact pixel size.

    With ``viewport`` the helper keeps the stricter contract used by the
    generation-lifecycle journeys (exact size, 2-colour blank check); without it
    the helper keeps the looser single-viewport contract used by the Edge and
    linked-generation journeys (PNG-only, 256-colour blank check).
    """
    if viewport is not None:
        try:
            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:
                if image.format != "PNG" or image.size != viewport:
                    raise ValueError(
                        f"screenshot {path.name} must be a {viewport[0]}x{viewport[1]} PNG"
                    )
                colors = image.convert("RGB").getcolors(maxcolors=2)
                if colors is not None and len(colors) < 2:
                    raise ValueError(f"screenshot {path.name} is blank")
        except (OSError, UnidentifiedImageError) as error:
            raise ValueError(f"screenshot {path.name} is not a decodable PNG: {error}") from error
        return {"path": str(path), "width": image.width, "height": image.height}
    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            if image.format != "PNG":
                raise ValueError(f"screenshot {path.name} must be a PNG")
            colors = image.convert("RGB").getcolors(maxcolors=256)
    except (OSError, UnidentifiedImageError) as error:
        raise ValueError(f"screenshot {path.name} is not a readable PNG: {error}") from error
    if colors is not None and len(colors) <= 1:
        raise ValueError(f"screenshot {path.name} must not be blank")
    return {"path": str(path), "width": image.width, "height": image.height}


def points_match(first: object, second: object) -> bool:
    """True when two native click points agree within the coordinate tolerance."""
    if not isinstance(first, dict) or not isinstance(second, dict):
        return False
    for axis in ("x", "y"):
        first_value = first.get(axis)
        second_value = second.get(axis)
        if (
            not isinstance(first_value, (int, float))
            or isinstance(first_value, bool)
            or not isinstance(second_value, (int, float))
            or isinstance(second_value, bool)
            or abs(float(first_value) - float(second_value)) > 1.5
        ):
            return False
    return True


def validate_operations(root: Path, relative: object, label: str, click_message: str) -> None:
    """Validate a JSON Lines native-input operations log for trusted clicks."""
    operations_path = artifact_path(root, relative, f"{label} operations")
    try:
        with operations_path.open(encoding="utf-8") as handle:
            lines = [line for line in handle if line.strip()]
            operations = [json.loads(line) for line in lines]
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} operations are invalid: {error}") from error
    if not operations:
        raise ValueError(f"{label} operations must not be empty")
    for operation in operations:
        if not isinstance(operation, dict):
            raise ValueError(f"{label} operations must be objects")
        action = operation.get("action")
        if action in ("reload", "navigate"):
            continue  # page navigation records, not native inputs
        if operation.get("trusted") is not True:
            raise ValueError(f"{label} operations must be trusted native inputs")
        if action == "click" and not has_trusted_selector_owner_click(operation):
            raise ValueError(click_message)
