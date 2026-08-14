"""Native browser journey for issue #31 (T11): prompt-node linked inputs.

Connected text (smart-prompt nodes) and project media (smart-image nodes) must
survive save/close/reopen bound to the same smart-image node, and the final
outbound request must keep the connected text, the model parameters and the
ordered duplicate references.  Every reference crosses the provider boundary as
a bounded ``data:`` URL (the real ``reference_to_data_url`` conversion runs
unpatched), and the whole run happens against an in-process fake provider — no
real provider is ever contacted (fixture base URL is the unroutable
``https://provider.invalid``).

The journey seeds a project in-process, opens the product canvas page in a real
Chrome window (native trusted clicks only), runs the linked generation through
the product UI, then issues a second generation through the same public HTTP
endpoint with ordered duplicate references to prove the provider contract.

Evidence contract: writes ``manifest.json`` (journey
``project-linked-generation``), ``screenshots/*.png``, ``operations.jsonl``,
``console-errors.json`` and ``requests.json`` under ``--artifacts-dir``.
Artifacts are validated by ``tools.project_linked_generation_evidence`` inside
the quality gate (trial/release).
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import pathlib
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from unittest.mock import patch

import httpx
from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from firefly_projects import ProjectStore
from tests import conversation_browser as harness
from tests.native_browser import AsgiBrowserServer, NativeBrowser, find_browser
from tests.provider_contract import ProviderContractCapture, normalize_captured_request

VIEWPORT = (1424, 900)
EXPECTED_UI_PROMPT = "upstream first\n\nupstream second\n\nown prompt"
DUPLICATE_PROMPT = "duplicate contract prompt"
MODEL = "gemini-2.5-flash-image"
PROVIDER_ID = "fake-linked-generation"
PROVIDER_BASE = "https://provider.invalid/v1"
PROVIDER_ENDPOINT = f"{PROVIDER_BASE}/models/{MODEL}:generateContent"
GENERATION_CONFIG = {
    "responseModalities": ["TEXT", "IMAGE"],
    "imageConfig": {"aspectRatio": "4:3", "imageSize": "2K"},
}

STEP_NAMES = (
    "linked-inputs-saved",
    "project-reopened",
    "linked-generation-succeeded",
    "provider-input-verified",
)


def rgba_png(color: tuple[int, int, int, int]) -> bytes:
    image = Image.new("RGBA", (1, 1), color)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


PNG_FIRST = rgba_png((255, 0, 0, 255))  # red reference asset
PNG_SECOND = rgba_png((0, 0, 255, 255))  # blue reference asset
PNG_UI_RESULT = rgba_png((0, 255, 0, 255))  # green result for the UI run
PNG_DUP_RESULT = rgba_png((255, 215, 0, 255))  # gold result for the duplicate run


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
        "name": "Fake Linked Generation Provider",
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


def seed(store: ProjectStore) -> tuple[str, str, str]:
    """Create the linked-input canvas: two text nodes, three media nodes (one
    duplicated asset) and one target smart-image node, all connected with
    ``input`` edges in a fixed order.  Returns (project_id, first_asset_id,
    second_asset_id)."""
    project = store.create_project("T11 Linked Generation Journey")
    project_id = project["id"]
    first = store.import_media(project_id, PNG_FIRST, "first.png", "image/png", source_info={"type": "user-save"})
    second = store.import_media(project_id, PNG_SECOND, "second.png", "image/png", source_info={"type": "user-save"})
    first_id = first["asset_id"]
    second_id = second["asset_id"]

    def prompt_node(node_id: str, text: str, x: int, y: int) -> dict:
        return {
            "op": "upsert_node",
            "node": {
                "id": node_id,
                "type": "smart-prompt",
                "x": x,
                "y": y,
                "w": 240,
                "h": 100,
                "text": text,
            },
        }

    def image_node(node_id: str, asset_id: str, name: str, x: int, y: int) -> dict:
        return {
            "op": "upsert_node",
            "node": {
                "id": node_id,
                "type": "smart-image",
                "x": x,
                "y": y,
                "w": 220,
                "h": 220,
                "images": [{"asset_id": asset_id, "name": name, "kind": "image"}],
            },
        }

    target_node = {
        "id": "target",
        "type": "smart-image",
        "name": "Target",
        "x": 760,
        "y": 200,
        "w": 320,
        "h": 300,
        "runPrompt": "own prompt",
        "runSettings": {
            "engine": "api",
            "apiKind": "image",
            "routingMode": "auto",
            "ratio": "landscape43",
            "resolution": "2K",
            "model": MODEL,
            "quality": "auto",
            "count": 1,
            "seed": 19,
        },
        "images": [],
        "inputNodeIds": ["prompt-a", "prompt-b", "image-a", "image-b", "image-a-copy"],
    }

    upstream = [
        ("prompt-a", "prompt-a", "smart-prompt"),
        ("prompt-b", "prompt-b", "smart-prompt"),
        ("image-a", first_id, "smart-image"),
        ("image-b", second_id, "smart-image"),
        ("image-a-copy", first_id, "smart-image"),
    ]

    mutations = [
        prompt_node("prompt-a", "upstream first", 60, 80),
        prompt_node("prompt-b", "upstream second", 60, 200),
        image_node("image-a", first_id, "first.png", 60, 320),
        image_node("image-b", second_id, "second.png", 60, 440),
        image_node("image-a-copy", first_id, "first.png", 60, 560),
        {"op": "upsert_node", "node": target_node},
    ]
    for node_id, _asset_id, _kind in upstream:
        mutations.append({"op": "upsert_edge", "edge": {"from": node_id, "to": "target", "kind": "input"}})

    store.apply_mutations(project_id, mutations, base_revision=0)
    store.flush(project_id)
    return project_id, first_id, second_id


def api(base_url: str, method: str, path: str, payload: dict | None = None, journey_requests: list | None = None) -> tuple[int, dict]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        base_url + path,
        data=body,
        method=method,
        headers={"Origin": base_url, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read()
            result = json.loads(raw.decode("utf-8")) if raw else {}
            if journey_requests is not None:
                journey_requests.append({"method": method, "path": path, "status": response.status})
            return response.status, result
    except urllib.error.HTTPError as error:
        raw = error.read().decode("utf-8", "replace")
        if journey_requests is not None:
            journey_requests.append({"method": method, "path": path, "status": error.code})
        return error.code, (json.loads(raw) if raw else {})


def submit_duplicate_run(base_url: str, store: ProjectStore, project_id: str, first_id: str, second_id: str, journey_requests: list) -> str:
    """Submit the ordered-duplicate reference run through the public endpoint,
    retrying on revision conflicts (the UI may still be flushing mutations)."""
    last_response: dict = {}
    for _attempt in range(6):
        revision = store.open_project(project_id)["canvas"].get("revision")
        status, response = api(
            base_url,
            "POST",
            f"/api/project-library/{urllib.parse.quote(project_id)}/image-generation-tasks",
            {
                "prompt": DUPLICATE_PROMPT,
                "routing_mode": "manual",
                "provider_id": PROVIDER_ID,
                "model": MODEL,
                "size": "2048x1536",
                "aspect_ratio": "4:3",
                "resolution": "2K",
                "quality": "high",
                "n": 1,
                "reference_asset_ids": [first_id, second_id, first_id],
                "media_type": "image",
                "result_node_id": "target",
                "angle_preset": "",
                "base_revision": revision,
                "idempotency_key": "linked-generation-duplicate-contract",
            },
            journey_requests,
        )
        last_response = response
        if status == 202:
            return str(response["task_id"])
        if status == 409:
            time.sleep(2.0)
            continue
        raise RuntimeError(f"duplicate reference task submission failed: {status} {response}")
    raise RuntimeError(f"duplicate reference task kept conflicting: {last_response}")


def wait_task(base_url: str, project_id: str, task_id: str, journey_requests: list, timeout: float = 90) -> dict:
    path = f"/api/project-library/{urllib.parse.quote(project_id)}/image-generation-tasks/{urllib.parse.quote(task_id)}"
    deadline = time.monotonic() + timeout
    latest: dict = {}
    while time.monotonic() < deadline:
        status, latest = api(base_url, "GET", path, journey_requests=journey_requests)
        if status == 200 and latest.get("status") == "succeeded":
            return latest
        if status == 200 and latest.get("status") in {"failed", "cancelled"}:
            raise RuntimeError(f"task reached terminal state {latest.get('status')!r}: {latest}")
        time.sleep(0.2)
    raise TimeoutError(f"task did not succeed within {timeout:.0f}s: {latest}")


def settled_revision(store: ProjectStore, project_id: str, timeout: float = 30) -> int:
    deadline = time.monotonic() + timeout
    previous = None
    while time.monotonic() < deadline:
        current = store.open_project(project_id)["canvas"].get("revision")
        if current is not None and current == previous:
            return int(current)
        previous = current
        time.sleep(1.0)
    raise TimeoutError("canvas revision did not settle after the UI run")


def ref_pixels(part: dict) -> tuple[int, int, int, int]:
    inline = part.get("inlineData")
    if not isinstance(inline, dict) or inline.get("mimeType") != "image/png":
        raise AssertionError(f"reference part is not an image/png inlineData data URL: {part}")
    encoded = inline.get("data")
    if not isinstance(encoded, str):
        raise AssertionError(f"reference part carries no bounded base64 data: {part}")
    image = Image.open(io.BytesIO(base64.b64decode(encoded))).convert("RGBA")
    return image.getpixel((0, 0))


def verify_capture(capture: ProviderContractCapture) -> None:
    requests = [normalize_captured_request(item) for item in capture.requests]
    if len(requests) != 2:
        raise AssertionError(f"expected exactly two provider requests, observed {len(requests)}")
    ui_request, duplicate_request = requests
    for request in (ui_request, duplicate_request):
        if request["method"] != "POST" or request["url"] != PROVIDER_ENDPOINT:
            raise AssertionError(f"unexpected provider call: {request['method']} {request['url']}")
        if request["json"].get("generationConfig") != GENERATION_CONFIG:
            raise AssertionError(f"model parameters were rewritten: {request['json'].get('generationConfig')}")

    ui_parts = ui_request["json"]["contents"][0]["parts"]
    if ui_parts[0] != {"text": EXPECTED_UI_PROMPT}:
        raise AssertionError(f"connected text did not reach the provider: {ui_parts[0]!r}")
    ui_refs = ui_parts[1:]
    if len(ui_refs) != 2:
        raise AssertionError(f"UI run must carry exactly two de-duplicated references, got {len(ui_refs)}")
    if [ref_pixels(part) for part in ui_refs] != [(255, 0, 0, 255), (0, 0, 255, 255)]:
        raise AssertionError("UI run reference order was not preserved")

    dup_parts = duplicate_request["json"]["contents"][0]["parts"]
    if dup_parts[0] != {"text": DUPLICATE_PROMPT}:
        raise AssertionError(f"duplicate run prompt was rewritten: {dup_parts[0]!r}")
    dup_refs = dup_parts[1:]
    if [ref_pixels(part) for part in dup_refs] != [(255, 0, 0, 255), (0, 0, 255, 255), (255, 0, 0, 255)]:
        raise AssertionError("ordered duplicate references were not preserved on the wire")


def snap(browser: NativeBrowser, screenshots: pathlib.Path, name: str) -> str:
    path = screenshots / name
    browser.capture_screenshot(path)
    return name


def write_json(path: pathlib.Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts-dir", type=pathlib.Path, required=True)
    args = parser.parse_args()
    artifacts = args.artifacts_dir.resolve()
    artifacts.mkdir(parents=True, exist_ok=True)
    screenshots = artifacts / "screenshots"
    screenshots.mkdir(parents=True, exist_ok=True)

    steps: dict[str, bool] = {name: False for name in STEP_NAMES}
    lifecycle: dict = {}
    journey_requests: list = []
    browser: NativeBrowser | None = None
    browser_version: dict = {}
    failure = None

    try:
        with tempfile.TemporaryDirectory(prefix="firefly-t11-native-") as root_name:
            root = pathlib.Path(root_name)
            store = ProjectStore(root / "projects", snapshot_delay=0)
            project_id, first_id, second_id = seed(store)
            steps["linked-inputs-saved"] = True

            capture = ProviderContractCapture(
                [gemini_image_response(PNG_UI_RESULT), gemini_image_response(PNG_DUP_RESULT)]
            )
            app = harness.load_production_app(root / "runtime-root", store)
            production = app.state.issue13_module
            # load_production_app re-imports the app.* tree, so the fresh
            # app.services package (the one generation._svc points at) must be
            # patched — the module object imported at the top of this file is
            # stale after the harness re-import.
            linked_services = sys.modules["app.services"]
            endpoint_patch = lambda provider, model_name: PROVIDER_ENDPOINT  # noqa: E731
            with patch.object(production, "load_api_providers", lambda: [provider_fixture()]):
                with patch.object(production, "get_api_provider_exact", lambda provider_id: provider_fixture()):
                    with patch.object(production, "api_headers", lambda **kwargs: {"Authorization": "Bearer fake-linked"}):
                        with patch.object(linked_services, "gemini_endpoint_url", endpoint_patch):
                            with patch.object(httpx, "AsyncClient", capture.async_client):
                                with AsgiBrowserServer(app) as server:
                                    browser = NativeBrowser.launch(find_browser())
                                    browser.set_environment(VIEWPORT)
                                    browser_version = browser.browser_version()
                                    url = (
                                        f"{server.base_url}/static/smart-canvas.html"
                                        f"?id={urllib.parse.quote(project_id)}&project={urllib.parse.quote(project_id)}"
                                        "&project_mode=1"
                                    )
                                    browser.navigate(url)
                                    browser.wait_for('document.querySelector(".image-node[data-id=\\"target\\"]")', timeout=30)
                                    time.sleep(1.0)  # let the click-suppression window lapse and hydration finish
                                    snap(browser, screenshots, "00-project-opened.png")

                                    browser.native_click('.image-node[data-id="target"]')
                                    browser.wait_for(
                                        'document.getElementById("promptInput") && '
                                        '(document.getElementById("promptInput").innerText || "").indexOf("own prompt") !== -1',
                                        timeout=20,
                                    )
                                    preview_text = browser.evaluate(
                                        '(document.getElementById("inputPromptPreview") || {}).innerText || ""'
                                    )
                                    if "upstream first" not in preview_text or "upstream second" not in preview_text:
                                        raise AssertionError(f"reopened canvas lost linked inputs: {preview_text!r}")
                                    steps["project-reopened"] = True
                                    browser.wait_for(
                                        'document.getElementById("runBtn") && !document.getElementById("runBtn").disabled',
                                        timeout=20,
                                    )
                                    snap(browser, screenshots, "01-target-selected.png")

                                    browser.native_click("#runBtn")
                                    deadline = time.monotonic() + 90
                                    final_node = None
                                    while time.monotonic() < deadline:
                                        canvas = store.open_project(project_id)["canvas"]
                                        final_node = next(
                                            (item for item in canvas.get("nodes") or [] if item.get("id") == "target"),
                                            None,
                                        )
                                        if final_node and (final_node.get("images") or []):
                                            break
                                        time.sleep(0.5)
                                    else:
                                        raise TimeoutError("UI-linked generation did not complete within 90s")
                                    steps["linked-generation-succeeded"] = True
                                    # Close/reopen half of the AC-1 journey: reload the
                                    # canvas page and prove the persisted result stays
                                    # bound to the same node together with the linked
                                    # inputs (rendered image + upstream preview).
                                    browser.reload(timeout=30)
                                    browser.wait_for(
                                        'document.querySelector(".image-node[data-id=\\"target\\"]")', timeout=30
                                    )
                                    time.sleep(1.0)  # hydration + click-suppression window
                                    browser.wait_for(
                                        '(function(){var n=document.querySelector(".image-node[data-id=\\"target\\"]");'
                                        "return !!n&&(n.querySelector('.image-wrap')!==null);})()",
                                        timeout=30,
                                    )
                                    browser.native_click('.image-node[data-id="target"]')
                                    browser.wait_for(
                                        'document.getElementById("promptInput") && '
                                        '(document.getElementById("promptInput").innerText || "").indexOf("own prompt") !== -1',
                                        timeout=20,
                                    )
                                    preview_text = browser.evaluate(
                                        '(document.getElementById("inputPromptPreview") || {}).innerText || ""'
                                    )
                                    if "upstream first" not in preview_text or "upstream second" not in preview_text:
                                        raise AssertionError(f"reopened canvas lost linked inputs: {preview_text!r}")
                                    time.sleep(0.6)  # let the result image paint
                                    snap(browser, screenshots, "02-linked-run-succeeded.png")

                                    revision = settled_revision(store, project_id)
                                    task_id = submit_duplicate_run(server.base_url, store, project_id, first_id, second_id, journey_requests)
                                    wait_task(server.base_url, project_id, task_id, journey_requests)

                                    verify_capture(capture)
                                    steps["provider-input-verified"] = True
                                    lifecycle = {
                                        "provider": PROVIDER_ID,
                                        "prompt": EXPECTED_UI_PROMPT,
                                        "model": MODEL,
                                        "aspect_ratio": "4:3",
                                        "resolution": "2K",
                                        "seed": 19,
                                        "ui_request_reference_asset_ids": [first_id, second_id],
                                        "expected_reference_asset_ids": [first_id, second_id],
                                        "reference_asset_ids": [first_id, second_id, first_id],
                                        "duplicate_reference_preserved": True,
                                        "ordered_references_preserved": True,
                                        "all_references_are_data_urls": True,
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

        operations_path = artifacts / "operations.jsonl"
        operations_path.write_text(
            "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in operations),
            encoding="utf-8",
        )
        console_path = artifacts / "console-errors.json"
        console_path.write_text(json.dumps(console_errors, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        requests_path = artifacts / "requests.json"
        requests_path.write_text(json.dumps(journey_requests, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        product = str(browser_version.get("product") or "")
        chrome_version = product.split("/")[-1] if product else ""
        passed = failure is None and all(steps.values())
        manifest = {
            "schema_version": 1,
            "journey": "project-linked-generation",
            "status": "passed" if passed else "failed",
            "first_failure": failure,
            "environment": {
                "os": "Windows",
                "dpr": 1,
                "color_scheme": "light",
                "fonts": ["Inter", "JetBrains Mono", "Space Grotesk"],
                "browser": {"name": "Google Chrome", "channel": "stable", "version": chrome_version},
            },
            "attempt": {
                "status": "passed" if passed else "failed",
                "viewport": list(VIEWPORT),
                "steps": [{"name": name, "status": "passed" if steps[name] else "failed"} for name in STEP_NAMES],
                "lifecycle": lifecycle,
                "screenshots": [
                    f"screenshots/{name}"
                    for name in ("00-project-opened.png", "01-target-selected.png", "02-linked-run-succeeded.png")
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
