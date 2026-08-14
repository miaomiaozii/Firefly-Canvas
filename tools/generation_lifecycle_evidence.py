"""Validate the browser evidence for history and generated-original isolation."""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlsplit

try:
    from tools.native_input_evidence import has_trusted_selector_owner_click
except ModuleNotFoundError:  # Direct `python tools/...py` execution.
    from native_input_evidence import has_trusted_selector_owner_click

try:
    from tools.evidence_common import (
        artifact_path as _artifact,
        read_json as _read_json,
        points_match as _points_match,
        validate_png as _validate_screenshot,
    )
except ModuleNotFoundError:  # Direct `python tools/...py` execution.
    from evidence_common import (
        artifact_path as _artifact,
        read_json as _read_json,
        points_match as _points_match,
        validate_png as _validate_screenshot,
    )


VIEWPORTS = ((1920, 1080), (1440, 900), (1280, 720), (1024, 768), (800, 600))
REQUIRED_BOOLEANS = (
    "record_removed_only",
    "original_retained_after_record_delete",
    "task_retained_after_original_delete",
    "downstream_retained_after_original_delete",
    "media_retained_after_occurrence_delete",
    "output_identity_restored",
    "restart_exposed_restore",
    "trash_empty_after_restore",
    "task_retained_after_restore",
    "downstream_retained_after_restore",
    "unrelated_retained_after_restore",
)


def _validate_lifecycle(lifecycle: object, viewport: tuple[int, int]) -> dict:
    if not isinstance(lifecycle, dict):
        raise ValueError(f"viewport {viewport[0]}x{viewport[1]} lifecycle evidence is missing")
    identity_keys = (
        "project_id",
        "task_id",
        "pending_log_id",
        "result_node_id",
        "record_log_id",
        "original_log_id",
        "original_asset_id",
        "original_output_id",
        "deletion_id",
        "downstream_asset_id",
        "original_sha256",
        "retained_media_sha256",
        "restored_sha256",
    )
    if not all(isinstance(lifecycle.get(key), str) and lifecycle[key] for key in identity_keys):
        raise ValueError(f"viewport {viewport[0]}x{viewport[1]} lifecycle identities are invalid")
    asset_id = lifecycle["original_asset_id"]
    original_bytes = lifecycle.get("original_bytes")
    if (
        not re.fullmatch(r"[0-9a-f]{64}", asset_id)
        or lifecycle.get("original_sha256") != asset_id
        or lifecycle.get("retained_media_sha256") != asset_id
        or lifecycle.get("restored_sha256") != asset_id
        or not re.fullmatch(r"output-[0-9a-f]{32}", lifecycle.get("original_output_id") or "")
        or not re.fullmatch(r"deletion-[0-9a-f]{32}", lifecycle.get("deletion_id") or "")
        or not isinstance(original_bytes, int)
        or isinstance(original_bytes, bool)
        or original_bytes <= 0
        or lifecycle.get("restored_bytes") != original_bytes
        or lifecycle.get("retention_ms") != 604_800_000
        or lifecycle.get("phase_sequence")
        != ["queued", "provider_generating", "receiving", "persisting"]
        or not all(lifecycle.get(key) is True for key in REQUIRED_BOOLEANS)
    ):
        raise ValueError(f"viewport {viewport[0]}x{viewport[1]} lifecycle retention or identity is invalid")
    return lifecycle


def _validate_operations(root: Path, attempt: dict, lifecycle: dict, viewport: tuple[int, int]) -> None:
    path = _artifact(root, attempt.get("operations"), "lifecycle operations")
    try:
        operations = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"lifecycle operations are not valid JSON Lines: {error}") from error
    asset_id = lifecycle["original_asset_id"]
    expected = (
        ("before-restart", 1, "#smartLogToggle", False),
        (
            "before-restart",
            2,
            f'[data-canvas-log-id="{lifecycle["record_log_id"]}"] [data-log-delete="record"]',
            True,
        ),
        (
            "before-restart",
            3,
            f'[data-canvas-log-id="{lifecycle["original_log_id"]}"] [data-log-delete-original="0"]',
            True,
        ),
        ("after-restart", 1, "#smartLogToggle", False),
        (
            "after-restart",
            2,
            f'[data-canvas-log-id="{lifecycle["original_log_id"]}"] '
            f'[data-log-restore-original="{asset_id}"]',
            True,
        ),
    )
    if len(operations) != len(expected):
        raise ValueError(f"viewport {viewport[0]}x{viewport[1]} native operation count is invalid")
    for operation, values in zip(operations, expected, strict=True):
        session, sequence, selector, dialog = values
        if not isinstance(operation, dict) or (
            operation.get("session") != session
            or operation.get("sequence") != sequence
            or operation.get("selector") != selector
            or operation.get("observed_button") != 0
            or not has_trusted_selector_owner_click(operation, button="left")
            or operation.get("unobscured") is not True
            or operation.get("dialog_accepted") is not dialog
            or not _points_match(operation.get("point"), operation.get("observed_point"))
            or not all(
                isinstance(operation.get(key), str) and operation[key]
                for key in ("hit", "observed_target", "focus_after")
            )
        ):
            raise ValueError(f"viewport {viewport[0]}x{viewport[1]} native lifecycle operation is invalid")


def _validate_requests(root: Path, attempt: dict, lifecycle: dict, viewport: tuple[int, int]) -> None:
    requests = _read_json(_artifact(root, attempt.get("requests"), "lifecycle requests"), "requests")
    if not isinstance(requests, list) or not all(isinstance(item, dict) for item in requests):
        raise ValueError(f"viewport {viewport[0]}x{viewport[1]} request evidence is invalid")
    prefix = f"/api/project-library/{lifecycle['project_id']}"
    required = {
        ("before-restart", f"{prefix}/history/delete"),
        ("before-restart", f"{prefix}/original/delete"),
        ("after-restart", f"{prefix}/original/restore"),
    }
    observed = {
        (item.get("session"), urlsplit(str(item.get("url") or "")).path)
        for item in requests
        if item.get("status") == 200
    }
    if not required.issubset(observed) or any(
        "/history/delete-with-media" in urlsplit(str(item.get("url") or "")).path
        for item in requests
    ):
        raise ValueError(f"viewport {viewport[0]}x{viewport[1]} lifecycle API request evidence is invalid")


def validate_generation_lifecycle_artifacts(artifact_root: Path) -> dict:
    root = artifact_root.resolve()
    manifest_path = root / "manifest.json"
    manifest = _read_json(manifest_path, "manifest.json") if manifest_path.is_file() else None
    if not isinstance(manifest, dict) or (
        manifest.get("schema_version") != 1
        or manifest.get("journey") != "generation-lifecycle"
        or manifest.get("status") != "passed"
        or manifest.get("first_failure") is not None
        or manifest.get("retries") not in (None, [], 0)
    ):
        raise ValueError("manifest identity must be a passed generation-lifecycle schema 1 journey")
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
    attempts = manifest.get("attempts")
    if not isinstance(attempts, list) or [
        item.get("viewport") for item in attempts if isinstance(item, dict)
    ] != [list(viewport) for viewport in VIEWPORTS]:
        raise ValueError("attempts must cover the five canonical viewports exactly once and in order")

    screenshots = []
    screenshot_evidence = []
    for attempt, viewport in zip(attempts, VIEWPORTS, strict=True):
        if not isinstance(attempt, dict) or attempt.get("status") != "passed":
            raise ValueError(f"viewport {viewport[0]}x{viewport[1]} did not pass")
        if attempt.get("retry") not in (None, False, 0) or attempt.get("retries") not in (None, [], 0):
            raise ValueError(f"viewport {viewport[0]}x{viewport[1]} contains retry evidence")
        lifecycle = _validate_lifecycle(attempt.get("lifecycle"), viewport)
        _validate_operations(root, attempt, lifecycle, viewport)
        _validate_requests(root, attempt, lifecycle, viewport)
        console_errors = _read_json(
            _artifact(root, attempt.get("console_errors"), "lifecycle console errors"),
            "lifecycle console errors",
        )
        if console_errors != []:
            raise ValueError(f"viewport {viewport[0]}x{viewport[1]} has console errors")
        refs = attempt.get("screenshots")
        if not isinstance(refs, list) or len(refs) != 5 or len(set(refs)) != 5:
            raise ValueError(f"viewport {viewport[0]}x{viewport[1]} must contain five unique screenshots")
        for relative in refs:
            screenshot = _artifact(root, relative, "lifecycle screenshot")
            screenshots.append(str(screenshot))
            screenshot_evidence.append(_validate_screenshot(screenshot, viewport))
    return {
        "status": "validated",
        "root": str(root),
        "manifest": str(manifest_path.resolve()),
        "viewport_count": len(attempts),
        "screenshots": screenshots,
        "screenshot_evidence": screenshot_evidence,
        "first_failure": None,
    }


def failed_generation_lifecycle_evidence(artifact_root: Path) -> dict:
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
    if isinstance(manifest, dict) and manifest.get("journey") == "generation-lifecycle":
        result["status"] = "captured"
    return result
