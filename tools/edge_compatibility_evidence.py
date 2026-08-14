"""Validate the Edge compatibility browser evidence (issue #39 / T19 release gate).

Contract: tests/edge_compatibility_browser.py produces a manifest with journey
``edge-compatibility`` at the canonical 1440x900 viewport, recorded by a real
Microsoft Edge (stable) process driven through the shared CDP native-input
harness.  The validator enforces: Windows stable Edge ran the product canvas,
the journey interacted through native trusted inputs, a generation completed
against the in-process fake provider, the console stayed clean, and every
screenshot is a real non-blank PNG.
"""

from __future__ import annotations

from pathlib import Path

try:
    from tools.native_input_evidence import has_trusted_selector_owner_click
except ModuleNotFoundError:  # Direct `python tools/...py` execution.
    from native_input_evidence import has_trusted_selector_owner_click

try:
    from tools.evidence_common import (
        artifact_path as _artifact,
        read_json as _read_json,
        validate_png as _validate_screenshot,
        validate_operations as _validate_operations,
    )
except ModuleNotFoundError:  # Direct `python tools/...py` execution.
    from evidence_common import (
        artifact_path as _artifact,
        read_json as _read_json,
        validate_png as _validate_screenshot,
        validate_operations as _validate_operations,
    )


VIEWPORT = (1440, 900)
REQUIRED_STEPS = (
    "edge-launched",
    "edge-canvas-interacted",
    "edge-generation-succeeded",
)
EXPECTED_PROMPT = "edge compatibility generation"


def validate_edge_compatibility_artifacts(artifact_root: Path) -> dict:
    root = artifact_root.resolve()
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError("edge compatibility manifest.json is missing")
    manifest = _read_json(manifest_path, "manifest.json")
    if not isinstance(manifest, dict):
        raise ValueError("edge compatibility manifest must be an object")
    if manifest.get("schema_version") != 1:
        raise ValueError("edge compatibility manifest schema_version must be 1")
    if manifest.get("journey") != "edge-compatibility":
        raise ValueError("manifest journey must be 'edge-compatibility'")
    if manifest.get("status") != "passed":
        raise ValueError(
            f"edge compatibility journey did not pass: {manifest.get('first_failure')!r}"
        )
    if manifest.get("first_failure") is not None:
        raise ValueError("passed manifest must not carry a first_failure")
    environment = manifest.get("environment")
    if not isinstance(environment, dict):
        raise ValueError("manifest environment must be an object")
    if environment.get("os") != "Windows":
        raise ValueError("edge compatibility must run on Windows")
    if environment.get("dpr") != 1 or environment.get("color_scheme") != "light":
        raise ValueError("edge compatibility must run at DPR 1 in light color scheme")
    browser = environment.get("browser")
    if not isinstance(browser, dict):
        raise ValueError("manifest environment.browser must be an object")
    if browser.get("name") != "Microsoft Edge" or browser.get("channel") != "stable":
        raise ValueError("edge compatibility requires Windows stable Microsoft Edge")
    if not isinstance(browser.get("version"), str) or not browser.get("version"):
        raise ValueError("edge compatibility must record the Edge version")
    attempt = manifest.get("attempt")
    if not isinstance(attempt, dict):
        raise ValueError("manifest attempt must be an object")
    if attempt.get("status") != "passed":
        raise ValueError("edge compatibility attempt did not pass")
    if list(attempt.get("viewport") or []) != list(VIEWPORT):
        raise ValueError(f"edge compatibility viewport must be {list(VIEWPORT)}")
    steps = attempt.get("steps")
    if not isinstance(steps, list):
        raise ValueError("edge compatibility steps must be a list")
    step_status = {step.get("name"): step.get("status") for step in steps if isinstance(step, dict)}
    for required in REQUIRED_STEPS:
        if step_status.get(required) != "passed":
            raise ValueError(f"edge compatibility step {required!r} did not pass")
    lifecycle = attempt.get("lifecycle")
    if not isinstance(lifecycle, dict):
        raise ValueError("edge compatibility lifecycle must be an object")
    if lifecycle.get("provider") != "fake-edge-generation":
        raise ValueError("edge compatibility must run against the fake-edge-generation provider")
    if lifecycle.get("generation_succeeded") is not True:
        raise ValueError("edge compatibility generation did not succeed")
    if lifecycle.get("prompt") != EXPECTED_PROMPT:
        raise ValueError("edge compatibility prompt was rewritten")
    screenshots = attempt.get("screenshots")
    if not isinstance(screenshots, list) or not screenshots:
        raise ValueError("edge compatibility screenshots must be a non-empty list")
    screenshot_evidence = [
        _validate_screenshot(_artifact(root, relative, "edge compatibility screenshot"))
        for relative in screenshots
    ]
    _validate_operations(
        root,
        attempt.get("operations"),
        "edge compatibility",
        "edge compatibility click operations must carry selector owner evidence",
    )
    console_errors = _read_json(
        _artifact(root, attempt.get("console_errors"), "edge compatibility console errors"),
        "console errors",
    )
    if console_errors != []:
        raise ValueError("edge compatibility console must stay clean")
    requests = _read_json(
        _artifact(root, attempt.get("requests"), "edge compatibility requests"),
        "requests",
    )
    if not isinstance(requests, list):
        raise ValueError("edge compatibility requests must be a list")
    return {
        "status": "validated",
        "root": str(root),
        "manifest": str(manifest_path),
        "viewport": list(VIEWPORT),
        "screenshots": screenshot_evidence,
        "screenshot_evidence": screenshot_evidence,
        "provider": lifecycle.get("provider"),
        "first_failure": None,
    }


def failed_edge_compatibility_evidence(artifact_root: Path) -> dict:
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
    if isinstance(manifest, dict) and manifest.get("journey") == "edge-compatibility":
        result["status"] = "captured"
    return result
