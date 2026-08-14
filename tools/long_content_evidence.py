"""Validate Issue #33 prompt-node and read-only long-content browser evidence."""
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
    "prompt_same_dom",
    "prompt_selection_preserved",
    "prompt_ime_preserved",
    "prompt_undo_redo_preserved",
    "prompt_autosaved_and_reopened",
    "source_relationships_preserved",
    "connection_reader_complete",
    "history_prompt_reader_complete",
    "history_focus_restored_after_virtualization",
    "error_reader_complete",
    "readonly_views_immutable",
)
GENERATION_MARKERS = ("/image-generation-tasks", "/api/generate", "/api/canvas-llm")


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def validate(root: Path) -> dict:
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError("missing manifest.json")
    manifest = load_json(manifest_path)
    if manifest.get("journey") != "long-content-reading" or manifest.get("status") != "passed":
        raise ValueError("long-content manifest is not passed")
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

    operation_count = 0
    screenshot_count = 0
    for attempt in attempts:
        if attempt.get("status") != "passed":
            raise ValueError("every viewport attempt must pass")
        lifecycle = attempt.get("lifecycle") or {}
        if not all(lifecycle.get(key) is True for key in REQUIRED_FLAGS):
            raise ValueError("lifecycle is missing a required long-content preservation assertion")
        if lifecycle.get("provider_calls") != 0:
            raise ValueError("long-content journey recorded a provider call")
        for key in (
            "prompt_scroll_top",
            "connection_reader_scroll_top",
            "history_prompt_scroll_top",
            "error_reader_scroll_top",
        ):
            if float(lifecycle.get(key) or 0) <= 0:
                raise ValueError(f"long-content evidence lacks real scrolling: {key}")

        console_path = root / str(attempt.get("console_errors") or "")
        operations_path = root / str(attempt.get("operations") or "")
        requests_path = root / str(attempt.get("requests") or "")
        if not console_path.is_file() or load_json(console_path):
            raise ValueError("console evidence is missing or non-empty")
        if not operations_path.is_file() or not requests_path.is_file():
            raise ValueError("native operations or request evidence is missing")
        requests = load_json(requests_path)
        if not isinstance(requests, list) or any(
            any(marker in str(item.get("url") or "") for marker in GENERATION_MARKERS)
            for item in requests
            if isinstance(item, dict)
        ):
            raise ValueError("long-content journey contains an unexpected generation request")

        operations = [
            json.loads(line)
            for line in operations_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        operation_count += len(operations)
        trusted_clicks = {
            str(item.get("selector") or "")
            for item in operations
            if has_trusted_selector_owner_click(item, button="left")
        }
        required_clicks = {
            '[data-id="reader-prompt"] .prompt-node-flow-toggle',
            '[data-long-content-open="input-prompt"]',
            '[data-canvas-log-id="reader-history"] [data-long-content-open="history-prompt"]',
            "#longContentReaderClose",
        }
        if not required_clicks.issubset(trusted_clicks):
            raise ValueError("missing trusted native long-content controls")
        if not any(item.get("action") == "reload" for item in operations):
            raise ValueError("missing browser reload evidence")
        if not any(
            item.get("action") == "keyboard"
            and item.get("key") == "Enter"
            and item.get("trusted") is True
            and item.get("observed_type") == "keyup"
            for item in operations
        ):
            raise ValueError("missing trusted native keyboard activation")
        if not any(
            item.get("action") == "keyboard"
            and item.get("key") == "Escape"
            and item.get("trusted") is True
            and item.get("observed_type") == "keyup"
            for item in operations
        ):
            raise ValueError("missing trusted native Escape evidence")
        if not any(
            item.get("action") == "keyboard"
            and item.get("key") == "z"
            and item.get("trusted") is True
            and item.get("observed_type") == "keyup"
            and "Control" in (item.get("modifiers") or [])
            for item in operations
        ):
            raise ValueError("missing trusted native textarea undo evidence")
        wheel_selectors = {
            str(item.get("selector") or "")
            for item in operations
            if item.get("action") == "wheel"
            and item.get("trusted") is True
            and item.get("observed_type") == "wheel"
            and abs(float(item.get("observed_delta_y") or 0)) > 0
        }
        if '[data-id="reader-prompt"] .prompt-node-text' not in wheel_selectors or "#longContentReaderBody" not in wheel_selectors:
            raise ValueError("missing trusted native scrolling for editable and read-only content")

        screenshots = attempt.get("screenshots") or []
        if len(screenshots) < 5 or any(not (root / str(path)).is_file() for path in screenshots):
            raise ValueError("each viewport needs at least five long-content screenshots")
        screenshot_count += len(screenshots)
    return {
        "status": "passed",
        "viewports": len(attempts),
        "operations": operation_count,
        "screenshots": screenshot_count,
    }


def validate_long_content_artifacts(root: Path) -> dict:
    return validate(root)


def failed_long_content_evidence(root: Path) -> dict:
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
