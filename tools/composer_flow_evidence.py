"""Validate Issue #32 Composer expand/focus-mode browser evidence."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from tools.native_input_evidence import has_trusted_selector_owner_click
except ModuleNotFoundError:  # Direct `python tools/...py` execution.
    from native_input_evidence import has_trusted_selector_owner_click

EXPECTED_VIEWPORTS = {(1920, 1080), (1440, 900), (1280, 720), (1024, 768), (800, 600)}
REQUIRED_FLAGS = (
    "selection_preserved",
    "ime_composition_preserved",
    "undo_redo_preserved",
    "scroll_intent_preserved",
    "draft_autosaved_and_reopened",
    "source_relationship_preserved",
)


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def validate(root: Path) -> dict:
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError("missing manifest.json")
    manifest = load_json(manifest_path)
    if manifest.get("status") != "passed":
        raise ValueError("manifest is not passed")
    environment = manifest.get("environment") or {}
    browser = environment.get("browser") or {}
    if browser.get("name") != "Google Chrome" or browser.get("channel") != "stable" or not browser.get("version"):
        raise ValueError("evidence must identify stable Google Chrome")
    if environment.get("provider") != "offline-none":
        raise ValueError("evidence must remain offline with no provider calls")
    attempts = manifest.get("attempts") or []
    actual = {tuple(item.get("viewport") or ()) for item in attempts}
    if actual != EXPECTED_VIEWPORTS or len(attempts) != len(EXPECTED_VIEWPORTS):
        raise ValueError("evidence must cover all five required desktop viewports exactly once")
    screenshot_count = 0
    operation_count = 0
    for attempt in attempts:
        if attempt.get("status") != "passed":
            raise ValueError("every viewport attempt must pass")
        lifecycle = attempt.get("lifecycle") or {}
        if not all(lifecycle.get(key) is True for key in REQUIRED_FLAGS):
            raise ValueError("lifecycle is missing a required Composer preservation assertion")
        if lifecycle.get("escape_generated") is not False or lifecycle.get("provider_calls") != 0:
            raise ValueError("Escape/provider side-effect assertion failed")
        if lifecycle.get("expanded_height", 0) <= lifecycle.get("initial_height", 0):
            raise ValueError("full expansion did not increase editor height")
        if lifecycle.get("manually_extended_height", 0) <= lifecycle.get("expanded_height", 0):
            raise ValueError("expanded editor was not manually extended")
        if abs(lifecycle.get("restored_height", 0) - lifecycle.get("initial_height", 0)) > 2:
            raise ValueError("collapse did not restore the prior manual height")
        if lifecycle.get("flow_scroll_top", 0) <= 0:
            raise ValueError("focus mode lacks real scroll evidence")
        console_path = root / str(attempt.get("console_errors") or "")
        operations_path = root / str(attempt.get("operations") or "")
        requests_path = root / str(attempt.get("requests") or "")
        if not console_path.is_file() or load_json(console_path):
            raise ValueError("console evidence is missing or non-empty")
        if not operations_path.is_file() or not requests_path.is_file():
            raise ValueError("native operations or request evidence is missing")
        requests = load_json(requests_path)
        if not isinstance(requests, list) or any(
            "/api/project-image-generation" in str(item.get("url") or "")
            for item in requests
            if isinstance(item, dict)
        ):
            raise ValueError("Composer journey contains an unexpected generation request")
        operations = [json.loads(line) for line in operations_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        operation_count += len(operations)
        trusted_clicks = {
            str(item.get("selector") or "")
            for item in operations
            if has_trusted_selector_owner_click(item, button="left")
        }
        if not any(
            item.get("action") == "drag"
            and item.get("selector") == "#promptResize"
            and item.get("trusted") is True
            and item.get("observed_type") == "mouseup"
            and item.get("observed_within_selector") is True
            for item in operations
        ):
            raise ValueError("missing trusted native expanded-height drag")
        if not {"#composerExpandBtn", "#composerFlowBtn"}.issubset(trusted_clicks):
            raise ValueError("missing trusted native expand/focus controls")
        if not any(item.get("action") == "reload" for item in operations):
            raise ValueError("missing browser reload evidence")
        if not any(
            item.get("action") == "keyboard"
            and item.get("key") == "Escape"
            and item.get("trusted") is True
            and item.get("observed_type") == "keyup"
            for item in operations
        ):
            raise ValueError("missing trusted native Escape evidence")
        if not any(
            item.get("action") == "wheel"
            and item.get("selector") == "#promptInput"
            and item.get("trusted") is True
            and item.get("observed_type") == "wheel"
            and abs(float(item.get("observed_delta_y") or 0)) > 0
            for item in operations
        ):
            raise ValueError("missing trusted native focus-mode scrolling")
        screenshots = attempt.get("screenshots") or []
        if len(screenshots) < 3 or any(not (root / str(path)).is_file() for path in screenshots):
            raise ValueError("each viewport needs at least three screenshots")
        screenshot_count += len(screenshots)
    return {"status": "passed", "viewports": len(attempts), "operations": operation_count, "screenshots": screenshot_count}


def validate_composer_flow_artifacts(root: Path) -> dict:
    return validate(root)


def failed_composer_flow_evidence(root: Path) -> dict:
    manifest = root / "manifest.json"
    summary = {"status": "unvalidated", "root": str(root.resolve()), "manifest": str(manifest.resolve())}
    if manifest.is_file():
        try:
            payload = load_json(manifest)
            summary["first_failure"] = payload.get("first_failure") if isinstance(payload, dict) else None
            summary["status"] = "captured" if isinstance(summary["first_failure"], dict) else "invalid"
        except (OSError, json.JSONDecodeError):
            summary["status"] = "invalid"
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        summary = validate(args.artifacts_dir)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "failed", "error": str(error)}, ensure_ascii=False))
        return 1
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
