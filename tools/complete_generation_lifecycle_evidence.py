"""Validate the complete generation/delete/recovery browser journey."""
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
        validate_png as _validate_screenshot,
    )
except ModuleNotFoundError:  # Direct `python tools/...py` execution.
    from evidence_common import (
        artifact_path as _artifact,
        read_json as _read_json,
        validate_png as _validate_screenshot,
    )

VIEWPORT = (1424, 900)
REQUIRED_STEPS = (
    "overlapping-pending",
    "record-delete-during-pending",
    "original-delete-during-pending",
    "cancel-target-only",
    "failed-retry-new-identity",
    "download-original-bytes",
    "reload-retains-terminal-identities",
    "seven-day-restore",
)
REQUIRED_BOOLEANS = (
    "independent_status_cards",
    "old_output_retained_with_new_task",
    "record_delete_preserved_asset",
    "record_delete_preserved_tasks",
    "original_delete_preserved_task",
    "cancel_preserved_other_tasks",
    "cancel_not_completed",
    "retry_created_new_identity",
    "retry_after_reload",
    "failed_record_retained",
    "download_bytes_preserved",
    "deleted_access_rejected",
    "reload_preserved_terminal_records",
    "restore_bytes_preserved",
    "trash_empty_after_restore",
    "result_nodes_preserved",
)


def validate_complete_generation_lifecycle_artifacts(artifact_root: Path) -> dict:
    root = artifact_root.resolve()
    manifest_path = root / "manifest.json"
    manifest = _read_json(manifest_path, "manifest.json") if manifest_path.is_file() else None
    if not isinstance(manifest, dict) or (
        manifest.get("schema_version") != 1
        or manifest.get("journey") != "complete-generation-lifecycle"
        or manifest.get("status") != "passed"
        or manifest.get("first_failure") is not None
    ):
        raise ValueError("manifest must be a passed complete-generation-lifecycle schema 1 journey")
    environment = manifest.get("environment")
    browser = environment.get("browser") if isinstance(environment, dict) else None
    if not isinstance(browser, dict) or (
        environment.get("os") != "Windows"
        or environment.get("dpr") != 1
        or environment.get("color_scheme") != "light"
        or browser.get("name") != "Google Chrome"
        or browser.get("channel") != "stable"
        or not str(browser.get("version") or "").strip()
    ):
        raise ValueError("environment must be fixed Windows Chrome stable")
    attempt = manifest.get("attempt")
    if not isinstance(attempt, dict) or attempt.get("status") != "passed" or attempt.get("viewport") != list(VIEWPORT):
        raise ValueError("the 1424x900 journey attempt did not pass")
    steps = attempt.get("steps")
    if not isinstance(steps, list) or [item.get("name") for item in steps if isinstance(item, dict)] != list(REQUIRED_STEPS) or not all(item.get("status") == "passed" for item in steps):
        raise ValueError("journey steps are incomplete or out of order")
    lifecycle = attempt.get("lifecycle")
    if not isinstance(lifecycle, dict) or not all(lifecycle.get(key) is True for key in REQUIRED_BOOLEANS):
        raise ValueError("lifecycle isolation assertions are incomplete")
    identity_keys = (
        "project_id", "failed_task_id", "retry_task_id", "cancel_task_id", "download_task_id",
        "record_history_id", "original_history_id", "record_asset_id", "original_asset_id",
        "original_output_id",
    )
    if not all(isinstance(lifecycle.get(key), str) and lifecycle[key] for key in identity_keys):
        raise ValueError("lifecycle identities are missing")
    task_ids = [lifecycle[key] for key in ("failed_task_id", "retry_task_id", "cancel_task_id", "download_task_id")]
    if len(set(task_ids)) != len(task_ids) or lifecycle["failed_task_id"] == lifecycle["retry_task_id"]:
        raise ValueError("task identities are not independent")
    status_card_ids = lifecycle.get("status_card_task_ids")
    if not isinstance(status_card_ids, list) or set(status_card_ids) != {
        lifecycle["failed_task_id"], lifecycle["download_task_id"], lifecycle["cancel_task_id"]
    }:
        raise ValueError("overlapping tasks do not have independent status-card evidence")
    terminal_counts = lifecycle.get("terminal_record_counts")
    if not isinstance(terminal_counts, dict) or any(
        int(terminal_counts.get(task_id) or 0) != 1 for task_id in task_ids
    ):
        raise ValueError("terminal task-to-record identity evidence is not one-to-one")
    if not lifecycle.get("download_record_history_id") or not lifecycle.get("download_asset_id"):
        raise ValueError("download is not bound to record and asset identities")
    if lifecycle.get("retention_ms") != 604_800_000:
        raise ValueError("deleted original does not have exact seven-day retention")
    for key in ("original_sha256", "restored_sha256", "download_sha256"):
        if not re.fullmatch(r"[0-9a-f]{64}", str(lifecycle.get(key) or "")):
            raise ValueError(f"{key} is invalid")
    if lifecycle["original_sha256"] != lifecycle["restored_sha256"] or lifecycle.get("original_bytes") != lifecycle.get("restored_bytes"):
        raise ValueError("restored original bytes changed")
    if lifecycle.get("generated_sha256") != lifecycle.get("download_sha256") or lifecycle.get("generated_bytes") != lifecycle.get("download_bytes"):
        raise ValueError("downloaded generated original bytes changed")
    attempts = lifecycle.get("adapter_attempts")
    if not isinstance(attempts, list) or (
        attempts.count("NATIVE_FAILURE") != 2
        or attempts.count("NATIVE_CANCEL") != 1
        or attempts.count("NATIVE_DOWNLOAD") != 1
    ):
        raise ValueError("fake adapter attempts do not prove cancellation and safe retry")

    operations_path = _artifact(root, attempt.get("operations"), "operations")
    operations = [json.loads(line) for line in operations_path.read_text(encoding="utf-8").splitlines() if line]
    if len(operations) < 9 or not all(
        has_trusted_selector_owner_click(item, button="left") for item in operations
    ):
        raise ValueError("native UI operations are incomplete, untrusted, or lack browser semantic clicks")
    ordered_selectors = [str(item.get("selector") or "") for item in operations]
    selectors = set(ordered_selectors)
    required_selectors = {
        "#smartHistoryToggle",
        f'[data-history-cancel-task="{lifecycle["cancel_task_id"]}"]',
        f'[data-history-retry-task="{lifecycle["failed_task_id"]}"]',
        f'[data-canvas-history-id="{lifecycle["record_history_id"]}"] [data-history-delete="record"]',
        f'[data-canvas-history-id="{lifecycle["original_history_id"]}"] [data-history-delete-original="{lifecycle["original_output_id"]}"]',
        f'[data-history-restore-original="{lifecycle["original_asset_id"]}"]',
        f'[data-canvas-history-id="{lifecycle["download_record_history_id"]}"] [data-history-download-original]',
    }
    if not required_selectors.issubset(selectors):
        raise ValueError("native UI operations do not cover every destructive product entry")
    cancel_index = ordered_selectors.index(f'[data-history-cancel-task="{lifecycle["cancel_task_id"]}"]')
    retry_index = ordered_selectors.index(f'[data-history-retry-task="{lifecycle["failed_task_id"]}"]')
    if cancel_index >= retry_index or ordered_selectors[:retry_index].count("#smartHistoryToggle") < 2:
        raise ValueError("safe retry was not exercised after reopening the reloaded history UI")

    requests = _read_json(_artifact(root, attempt.get("requests"), "requests"), "requests")
    if not isinstance(requests, list):
        raise ValueError("request evidence is invalid")
    paths = {urlsplit(str(item.get("url") or "")).path for item in requests if int(item.get("status") or 0) in {200, 202, 404, 410}}
    required_requests = {
        "record-delete": lambda path: path.endswith("/history/delete"),
        "original-delete": lambda path: path.endswith("/original/delete"),
        "original-restore": lambda path: path.endswith("/original/restore"),
        "task-cancel": lambda path: "/image-generation-tasks/" in path and path.endswith("/cancel"),
        "task-retry": lambda path: "/image-generation-tasks/" in path and path.endswith("/retry"),
        "project-media": lambda path: "/api/project-library/" in path and "/media/" in path,
    }
    missing_requests = [
        name
        for name, matches in required_requests.items()
        if not any(matches(path) for path in paths)
    ]
    if missing_requests:
        raise ValueError(f"request evidence does not cover the complete lifecycle: {missing_requests}")
    if lifecycle.get("download_request_path") not in paths:
        raise ValueError("request evidence does not bind download to the generated asset identity")
    console = _read_json(_artifact(root, attempt.get("console_errors"), "console errors"), "console errors")
    if console != []:
        raise ValueError("browser console contains errors")
    screenshots = attempt.get("screenshots")
    if not isinstance(screenshots, list) or len(screenshots) < 6 or len(set(screenshots)) != len(screenshots):
        raise ValueError("at least six unique lifecycle screenshots are required")
    evidence = [_validate_screenshot(_artifact(root, item, "screenshot"), VIEWPORT) for item in screenshots]
    return {
        "status": "validated",
        "root": str(root),
        "manifest": str(manifest_path.resolve()),
        "step_count": len(steps),
        "screenshots": [item["path"] for item in evidence],
        "first_failure": None,
    }


def failed_complete_generation_lifecycle_evidence(artifact_root: Path) -> dict:
    root = artifact_root.resolve()
    manifest_path = root / "manifest.json"
    result = {"status": "unvalidated", "root": str(root), "manifest": str(manifest_path), "first_failure": None}
    if not manifest_path.is_file():
        return result
    try:
        manifest = _read_json(manifest_path, "manifest.json")
    except ValueError as error:
        result["manifest_error"] = str(error)
        return result
    if isinstance(manifest, dict) and manifest.get("journey") == "complete-generation-lifecycle":
        result["status"] = "captured"
        result["first_failure"] = manifest.get("first_failure")
    return result
