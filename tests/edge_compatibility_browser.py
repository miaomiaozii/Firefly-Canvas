"""Edge compatibility browser journey for issue #39 (T19): release gate check.

The release gate must prove the product runs on Windows stable Edge in addition
to Windows stable Chrome (Edge is release-only in the support matrix; Chrome
covers every mode).  This journey launches the real installed ``msedge.exe``
through the same CDP harness that drives Chrome, opens the product canvas,
interacts with it using native trusted clicks only, and runs one image
generation through the product UI against an in-process fake provider.

Evidence contract: writes ``manifest.json`` (journey ``edge-compatibility``),
``screenshots/*.png``, ``operations.jsonl``, ``console-errors.json`` and
``requests.json`` under ``--artifacts-dir``.  Artifacts are validated by
``tools.edge_compatibility_evidence`` inside the quality gate (release mode).

If no Edge executable can be located the journey still writes a failed
manifest (step ``edge-launched``) and exits 1 — an honest evidence record
instead of a silent skip.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import pathlib
import sys
import tempfile
import time
import traceback
from unittest.mock import patch

import httpx
from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from firefly_projects import ProjectStore
from tests import conversation_browser as harness
from tests.native_browser import AsgiBrowserServer, NativeBrowser
from tests.provider_contract import ProviderContractCapture

VIEWPORT = (1440, 900)
MODEL = "gemini-2.5-flash-image"
PROVIDER_ID = "fake-edge-generation"
PROVIDER_BASE = "https://provider.invalid/v1"
PROVIDER_ENDPOINT = f"{PROVIDER_BASE}/models/{MODEL}:generateContent"
GENERATION_CONFIG = {
    "responseModalities": ["TEXT", "IMAGE"],
    "imageConfig": {"aspectRatio": "1:1", "imageSize": "2K"},
}
PROMPT = "edge compatibility generation"
RESULT_PNG = None  # built lazily from rgba_png

STEP_NAMES = ("edge-launched", "edge-canvas-interacted", "edge-generation-succeeded")


def rgba_png(color: tuple[int, int, int, int]) -> bytes:
    image = Image.new("RGBA", (1, 1), color)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def gemini_image_response(png: bytes) -> dict:
    return {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "inlineData": {
                                "mimeType": "image/png",
                                "data": base64.b64encode(png).decode("ascii"),
                            }
                        }
                    ]
                }
            }
        ]
    }


def provider_fixture() -> dict:
    return {
        "id": PROVIDER_ID,
        "name": "Fake Edge Generation Provider",
        "enabled": True,
        "protocol": "gemini",
        "base_url": PROVIDER_BASE,
        "image_models": [MODEL],
        "available_models": {"image_models": [MODEL]},
        "image_model_capabilities": {
            MODEL: {
                "arbitrary_size": True,
                "arbitrary_aspect_ratio": True,
                "arbitrary_resolution": True,
                "max_reference_images": 9,
                "media_types": ["image"],
            }
        },
    }


def find_edge_executable() -> str | None:
    override = os.environ.get("EDGE_EXECUTABLE", "").strip()
    if override:
        candidate = pathlib.Path(override)
        if candidate.is_file():
            return str(candidate)
    for candidate in (
        pathlib.Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        pathlib.Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
    ):
        if candidate.is_file():
            return str(candidate)
    return None


def seed(store: ProjectStore) -> str:
    project = store.create_project("EDGE COMPATIBILITY JOURNEY")
    project_id = project["id"]
    store.apply_mutations(
        project_id,
        [
            {
                "op": "upsert_node",
                "node": {
                    "id": "edge-target",
                    "type": "smart-image",
                    "x": 640,
                    "y": 180,
                    "width": 360,
                    "height": 360,
                    "runPrompt": PROMPT,
                    "runSettings": {
                        "engine": "api",
                        "apiKind": "image",
                        "routingMode": "auto",
                        "ratio": "square",
                        "resolution": "2K",
                        "model": MODEL,
                        "quality": "auto",
                        "count": 1,
                    },
                    "images": [],
                },
            }
        ],
        base_revision=0,
    )
    return project_id


def write_json(path: pathlib.Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def snapshot(browser: NativeBrowser, folder: pathlib.Path, name: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    browser.capture_screenshot(folder / name)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts-dir", type=pathlib.Path, required=True)
    args = parser.parse_args(argv)
    artifacts = args.artifacts_dir
    artifacts.mkdir(parents=True, exist_ok=True)
    screenshots = artifacts / "screenshots"
    screenshots.mkdir(parents=True, exist_ok=True)

    steps = {name: False for name in STEP_NAMES}
    lifecycle: dict = {}
    failure = None
    browser = None
    browser_version: dict = {}
    journey_requests: list = []
    runtime = pathlib.Path(tempfile.mkdtemp(prefix="firefly-edge-runtime-"))
    store = ProjectStore(runtime / "projects", snapshot_delay=0)
    capture = ProviderContractCapture([gemini_image_response(rgba_png((0, 220, 120, 255)))])

    try:
        edge_executable = find_edge_executable()
        if edge_executable is None:
            raise RuntimeError(
                "msedge executable not found; Windows stable Edge is required for the release gate"
            )
        steps["edge-launched"] = True

        project_id = seed(store)
        app = harness.load_production_app(runtime / "runtime-root", store)
        production = app.state.issue13_module
        linked_services = sys.modules["app.services"]
        with (
            patch.object(production, "load_api_providers", lambda: [provider_fixture()]),
            patch.object(production, "get_api_provider_exact", lambda provider_id: provider_fixture()),
            patch.object(production, "api_headers", lambda **kwargs: {"Authorization": "Bearer fake-edge"}),
            patch.object(
                linked_services,
                "gemini_endpoint_url",
                lambda provider, model_name: PROVIDER_ENDPOINT,
            ),
            patch.object(httpx, "AsyncClient", capture.async_client),
        ):
            with AsgiBrowserServer(app) as server:
                browser = NativeBrowser.launch(edge_executable)
                browser.set_environment(VIEWPORT)
                browser_version = browser.browser_version()

                canvas_url = (
                    f"{server.base_url}/static/smart-canvas.html"
                    f"?id={project_id}&project={project_id}&project_mode=1"
                )
                browser.navigate(canvas_url)
                browser.wait_for(
                    'document.querySelector(".image-node[data-id=\\"edge-target\\"]")',
                    timeout=30,
                )
                time.sleep(1.0)  # hydration + click-suppression window
                snapshot(browser, screenshots, "00-edge-canvas-loaded.png")

                browser.native_click('.image-node[data-id="edge-target"]')
                browser.wait_for(
                    'document.getElementById("promptInput") && '
                    f'(document.getElementById("promptInput").innerText || "").indexOf({json.dumps(PROMPT)}) !== -1',
                    timeout=20,
                )
                browser.wait_for(
                    'document.getElementById("runBtn") && !document.getElementById("runBtn").disabled',
                    timeout=20,
                )
                steps["edge-canvas-interacted"] = True
                snapshot(browser, screenshots, "01-edge-target-selected.png")

                browser.native_click("#runBtn")
                deadline = time.monotonic() + 90
                while time.monotonic() < deadline:
                    canvas = store.open_project(project_id).get("canvas", {})
                    target = next(
                        (node for node in canvas.get("nodes", []) if node.get("id") == "edge-target"),
                        None,
                    )
                    if target and (target.get("images") or []):
                        break
                    time.sleep(0.5)
                else:
                    raise TimeoutError("Edge generation did not complete within 90s")
                browser.wait_for(
                    '(function(){var n=document.querySelector(".image-node[data-id=\\"edge-target\\"]");'
                    "return !!n&&(n.querySelector('.image-wrap')!==null);})()",
                    timeout=30,
                )
                time.sleep(0.6)  # let the result image paint
                steps["edge-generation-succeeded"] = True
                snapshot(browser, screenshots, "02-edge-generation-succeeded.png")

                captured = capture.requests
                if len(captured) != 1:
                    raise AssertionError(f"expected exactly one provider call, got {len(captured)}")
                request = captured[0]
                if request["method"] != "POST" or request["url"] != PROVIDER_ENDPOINT:
                    raise AssertionError(f"unexpected provider request: {request['url']}")
                body = request["json"]
                parts = body["contents"][0]["parts"]
                if parts[0] != {"text": PROMPT}:
                    raise AssertionError(f"prompt was rewritten at the provider boundary: {parts[0]!r}")
                if body.get("generationConfig") != GENERATION_CONFIG:
                    raise AssertionError(
                        f"generation config was rewritten: {body.get('generationConfig')!r}"
                    )
                lifecycle = {
                    "provider": PROVIDER_ID,
                    "model": MODEL,
                    "prompt": PROMPT,
                    "aspect_ratio": "1:1",
                    "resolution": "2K",
                    "generation_succeeded": True,
                    "provider_calls": len(captured),
                }
    except Exception as error:  # noqa: BLE001 — journey evidence must capture any failure
        failure = {"message": str(error), "traceback": traceback.format_exc()}
        print(f"FAILED: {error}", file=sys.stderr)
    finally:
        operations = []
        console_errors = []
        if browser is not None:
            try:
                operations = list(browser.operations)
                console_errors = list(browser.console_errors)
            finally:
                browser.close()
        (artifacts / "operations.jsonl").write_text(
            "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in operations),
            encoding="utf-8",
        )
        write_json(artifacts / "console-errors.json", console_errors)
        write_json(artifacts / "requests.json", journey_requests)

        product = str(browser_version.get("product") or "")
        edge_version = product.split("/")[-1] if product else ""
        passed = failure is None and all(steps.values())
        manifest = {
            "schema_version": 1,
            "journey": "edge-compatibility",
            "status": "passed" if passed else "failed",
            "first_failure": failure,
            "environment": {
                "os": "Windows",
                "dpr": 1,
                "color_scheme": "light",
                "fonts": ["Inter", "JetBrains Mono", "Space Grotesk"],
                "browser": {"name": "Microsoft Edge", "channel": "stable", "version": edge_version},
            },
            "attempt": {
                "status": "passed" if passed else "failed",
                "viewport": list(VIEWPORT),
                "steps": [{"name": name, "status": "passed" if steps[name] else "failed"} for name in STEP_NAMES],
                "lifecycle": lifecycle,
                "screenshots": [
                    f"screenshots/{name}"
                    for name in (
                        "00-edge-canvas-loaded.png",
                        "01-edge-target-selected.png",
                        "02-edge-generation-succeeded.png",
                    )
                    if (screenshots / name).is_file()
                ],
                "operations": "operations.jsonl",
                "console_errors": "console-errors.json",
                "requests": "requests.json",
            },
        }
        write_json(artifacts / "manifest.json", manifest)
        print(json.dumps({"status": manifest["status"], "steps": steps, "first_failure": failure}, ensure_ascii=False))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
