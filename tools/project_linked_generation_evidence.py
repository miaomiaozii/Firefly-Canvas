"""Validate the native browser evidence for the prompt-node linked input journey.

Contract: tests/project_linked_generation_native_browser.py produces a manifest
with journey ``project-linked-generation`` at a single canonical viewport
(1424x900).  The validator enforces the issue #31 acceptance evidence: connected
text and project media survive save/reopen bound to the same prompt node, the
final fake-provider call keeps connected text, model parameters and ordered
duplicate references, and every reference crosses the provider boundary as a
bounded ``data:`` URL.
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, UnidentifiedImageError

try:
    from tools.native_input_evidence import has_trusted_selector_owner_click
except ModuleNotFoundError:  # Direct `python tools/...py` execution.
    from native_input_evidence import has_trusted_selector_owner_click


VIEWPORT = (1424, 900)
REQUIRED_STEPS = (
    "linked-inputs-saved",
    "project-reopened",
    "linked-generation-succeeded",
    "provider-input-verified",
)
EXPECTED_PROMPT = "upstream first\n\nupstream second\n\nown prompt"


def _artifact(root: Path, relative: object, label: str) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError(f"{label} must be a non-empty relative path")
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise ValueError(f"{label} is outside the evidence root: {relative!r}") from error
    if not candidate.is_file():
        raise ValueError(f"{label} is missing: {relative!r}")
    return candidate


def _read_json(path: Path, label: str) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is not valid UTF-8 JSON: {error}") from error


def _validate_screenshot(path: Path) -> dict:
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
    return {"path": str(path), "width": image.size[0], "height": image.size[1]}


def _validate_operations(root: Path, relative: object) -> None:
    operations_path = _artifact(root, relative, "linked generation operations")
    try:
        with operations_path.open(encoding="utf-8") as handle:
            lines = [line for line in handle if line.strip()]
            operations = [json.loads(line) for line in lines]
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"linked generation operations are invalid: {error}") from error
    if not operations:
        raise ValueError("linked generation operations must not be empty")
    for operation in operations:
        if not isinstance(operation, dict):
            raise ValueError("linked generation operations must be objects")
        action = operation.get("action")
        if action in ("reload", "navigate"):
            continue  # page navigation records, not native inputs
        if operation.get("trusted") is not True:
            raise ValueError("linked generation operations must be trusted native inputs")
        if action == "click" and not has_trusted_selector_owner_click(operation):
            raise ValueError("linked generation click lacks selector owner evidence")


def _validate_console(root: Path, relative: object) -> None:
    console = _read_json(_artifact(root, relative, "linked generation console errors"), "linked generation console errors")
    if console != []:
        raise ValueError("linked generation journey recorded console errors")


def validate_project_linked_generation_artifacts(artifact_root: Path) -> dict:
    root = artifact_root.resolve()
    manifest_path = root / "manifest.json"
    manifest = _read_json(manifest_path, "manifest.json") if manifest_path.is_file() else None
    if not isinstance(manifest, dict) or (
        manifest.get("schema_version") != 1
        or manifest.get("journey") != "project-linked-generation"
        or manifest.get("status") != "passed"
        or manifest.get("first_failure") is not None
    ):
        raise ValueError("manifest identity must be a passed project-linked-generation schema 1 journey")
    environment = manifest.get("environment")
    browser = environment.get("browser") if isinstance(environment, dict) else None
    if not isinstance(browser, dict) or (
        environment.get("os") != "Windows"
        or environment.get("dpr") != 1
        or environment.get("color_scheme") != "light"
        or browser.get("name") != "Google Chrome"
        or browser.get("channel") != "stable"
        or not isinstance(browser.get("version"), str)
        or not browser["version"].strip()
    ):
        raise ValueError("manifest environment is not fixed Windows Chrome stable with DPR 1 and light mode")
    attempt = manifest.get("attempt")
    if not isinstance(attempt, dict) or attempt.get("status") != "passed":
        raise ValueError("attempt must be present and passed")
    if attempt.get("viewport") != list(VIEWPORT):
        raise ValueError(f"attempt must use the canonical viewport {VIEWPORT[0]}x{VIEWPORT[1]}")
    steps = attempt.get("steps")
    if not isinstance(steps, list) or {item.get("name") for item in steps if isinstance(item, dict)} & set(REQUIRED_STEPS) != set(REQUIRED_STEPS):
        raise ValueError("attempt steps are missing linked-input journey checkpoints")
    for item in steps:
        if isinstance(item, dict) and item.get("name") in REQUIRED_STEPS and item.get("status") != "passed":
            raise ValueError(f"checkpoint {item.get('name')} did not pass")
    lifecycle = attempt.get("lifecycle")
    if not isinstance(lifecycle, dict) or lifecycle.get("provider") != "fake-linked-generation":
        raise ValueError("linked generation evidence must come from the fake provider")
    reference_ids = lifecycle.get("reference_asset_ids")
    expected_ids = lifecycle.get("expected_reference_asset_ids")
    ui_request_ids = lifecycle.get("ui_request_reference_asset_ids")
    if (
        lifecycle.get("prompt") != EXPECTED_PROMPT
        or lifecycle.get("model") != "gemini-2.5-flash-image"
        or lifecycle.get("aspect_ratio") != "4:3"
        or lifecycle.get("resolution") != "2K"
        or lifecycle.get("seed") != 19
        or not isinstance(expected_ids, list)
        or len(expected_ids) != 2
        or expected_ids[0] == expected_ids[1]
        or not isinstance(reference_ids, list)
        or len(reference_ids) != 3
        or reference_ids[:2] != expected_ids
        or reference_ids[0] != reference_ids[2]
        or not isinstance(ui_request_ids, list)
        or ui_request_ids != expected_ids
        or lifecycle.get("duplicate_reference_preserved") is not True
        or lifecycle.get("ordered_references_preserved") is not True
        or lifecycle.get("all_references_are_data_urls") is not True
    ):
        raise ValueError("lifecycle does not prove connected text, model parameters and ordered duplicate references")
    screenshots = attempt.get("screenshots")
    if not isinstance(screenshots, list) or not screenshots:
        raise ValueError("attempt must contain at least one screenshot")
    _validate_operations(root, attempt.get("operations"))
    _validate_console(root, attempt.get("console_errors"))
    requests = _read_json(_artifact(root, attempt.get("requests"), "linked generation requests"), "linked generation requests")
    if not isinstance(requests, list):
        raise ValueError("linked generation requests must be a list")
    screenshot_paths = []
    screenshot_evidence = []
    for relative in screenshots:
        screenshot = _artifact(root, relative, "linked generation screenshot")
        screenshot_paths.append(str(screenshot))
        screenshot_evidence.append(_validate_screenshot(screenshot))
    return {
        "status": "validated",
        "root": str(root),
        "manifest": str(manifest_path.resolve()),
        "viewport": list(VIEWPORT),
        "screenshots": screenshot_paths,
        "screenshot_evidence": screenshot_evidence,
        "provider": lifecycle.get("provider"),
        "reference_asset_ids": list(reference_ids),
        "first_failure": None,
    }


def failed_project_linked_generation_evidence(artifact_root: Path) -> dict:
    root = artifact_root.resolve()
    manifest_path = root / "manifest.json"
    result = {"status": "unvalidated", "root": str(root), "manifest": str(manifest_path)}
    if not manifest_path.is_file():
        result["first_failure"] = None
        return result
    try:
        manifest = _read_json(manifest_path, "manifest.json")
    except ValueError as error:
        result.update({"manifest_error": str(error), "first_failure": None})
        return result
    result["first_failure"] = manifest.get("first_failure") if isinstance(manifest, dict) else None
    if isinstance(manifest, dict) and manifest.get("journey") == "project-linked-generation":
        result["status"] = "captured"
    return result
