"""Native-browser journey for issue #188: deleting an image node must not delete its candidate pool.

Repro path (per the reported issue, both delete entries run through the same
journey):
  1. Seed one smart-image node with a prompt and API run settings.
  2. Run a generation through the product UI (in-process fake provider,
     unroutable fixture base URL) -> the node gets result image A.
  3. Open the image editor via the node toolbar (native click), switch to
     outpaint mode, drag the outpaint frame outwards (native drag) and apply
     (native click) -> the node image becomes the red-backed composite
     (the upload and project-media import run against the in-process server).
  4. Run the generation again -> the old output (red composite) is archived
     into the node's candidate pool (history group), the node gets result B.
  5. Delete the node through one of the two entries:
       'button': the node .node-delete (x) button, native click
       'key'   : select the node, press Delete, native keyboard
  6. Assert: the node is gone BUT the candidate pool survives with its
     archived image (store state + DOM presence + image count).
     This is the assertion that is RED before the fix and GREEN after.
  7. Undo (native click #smartUndoButton): the node is restored with its
     image AND the pool is back as the linked candidate pool
     (isHistoryGroup + historyFor) with its image still present.

Evidence contract: writes ``manifest.json``, ``screenshots/*.png``,
``operations.jsonl``, ``console-errors.json`` and ``requests.json`` under
``--artifacts-dir`` (per-entry subdirectories), screenshots under
``--shots-dir`` (per-entry subdirectories).  No real provider is ever
contacted (fixture base URL is the unroutable ``https://provider.invalid``).
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
from tests.provider_contract import ProviderContractCapture

VIEWPORT = (1424, 900)
MODEL = "gemini-2.5-flash-image"
PROVIDER_ID = "fake-pool-delete"
PROVIDER_BASE = "https://provider.invalid/v1"
PROVIDER_ENDPOINT = f"{PROVIDER_BASE}/models/{MODEL}:generateContent"
NODE_ID = "pool-target"
RED = (255, 0, 68, 255)      # #ff0044 outpaint backdrop
GREEN = (0, 255, 0, 255)     # run-1 result
BLUE = (0, 0, 255, 255)      # run-2 result
IMAGE_SIZE = (256, 192)

STEP_NAMES = (
    "generated",
    "outpaint-applied",
    "pool-archived",
    "node-deleted",
    "pool-survives",
    "undo-restores",
)


def solid_png(color: tuple[int, int, int, int], size: tuple[int, int]) -> bytes:
    image = Image.new("RGBA", size, color)
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
        "name": "Fake Pool Delete Provider",
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


def seed(store: ProjectStore) -> str:
    project = store.create_project("Issue 188 Candidate Pool Delete Journey")
    project_id = project["id"]
    store.apply_mutations(
        project_id,
        [
            {
                "op": "upsert_node",
                "node": {
                    "id": NODE_ID,
                    "type": "smart-image",
                    "name": "Pool Target",
                    "x": 300,
                    "y": 160,
                    "w": 320,
                    "h": 300,
                    "runPrompt": "repro-188 base prompt",
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
                },
            }
        ],
        base_revision=0,
    )
    store.flush(project_id)
    return project_id


def canvas_nodes(store: ProjectStore, project_id: str) -> list:
    return store.open_project(project_id)["canvas"].get("nodes") or []


def wait_node_images(store: ProjectStore, project_id: str, timeout: float, *, expect_name_part: str | None = None, forbid_name_part: str | None = None) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for node in canvas_nodes(store, project_id):
            if node.get("id") != NODE_ID:
                continue
            images = node.get("images") or []
            if not images or images[0] is None:
                continue  # pending placeholder while a generation is in flight
            name = str(images[0].get("name") or "")
            if expect_name_part and expect_name_part not in name:
                continue
            if forbid_name_part and forbid_name_part in name:
                continue
            return node
        time.sleep(0.5)
    raise TimeoutError(f"node {NODE_ID} did not reach the expected image state within {timeout:.0f}s")


def wait_pool(store: ProjectStore, project_id: str, timeout: float, *, expect: bool) -> dict | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pool = next((n for n in canvas_nodes(store, project_id) if n.get("historyFor") == NODE_ID), None)
        if (pool is not None) == expect:
            return pool
        time.sleep(0.5)
    raise TimeoutError(f"candidate pool presence {expect} not reached within {timeout:.0f}s")


def live_image_url(browser: NativeBrowser, node_id: str, name_part: str) -> str:
    """URL of the node's current image from the LIVE canvas state.

    The project store strips image URLs on save (mediaItemForStorage keeps
    only asset_id), so the live canvasState is the only place the download
    URL exists."""
    expression = (
        "(function(){var n=canvasState.nodes.find(function(x){return x.id===" + json.dumps(node_id) + ";});"
        "if(!n)return '';"
        "var img=(n.images||[]).filter(function(i){return (i.name||'').indexOf(" + json.dumps(name_part) + ")>=0;})[0]"
        "||(n.images||[])[0];"
        "return img?img.url:'';})()"
    )
    return str(browser.evaluate(expression) or "")


def is_reddish(pixel: tuple) -> bool:
    r, g, b = pixel[0], pixel[1], pixel[2]
    return r > 180 and g < 110 and b < 130 and r > g * 2 and r > b * 1.5


def is_greenish(pixel: tuple) -> bool:
    r, g, b = pixel[0], pixel[1], pixel[2]
    return g > 180 and r < 110 and b < 130


def verify_outpaint_composite(base_url: str, url: str) -> dict:
    """Download the node's current image and prove it is a red-backed
    composite larger than the source (red corner + intact green centre)."""
    absolute = url if url.startswith("http") else base_url + (url if url.startswith("/") else "/" + url)
    raw = urllib.request.urlopen(absolute, timeout=20).read()
    image = Image.open(io.BytesIO(raw)).convert("RGB")
    width, height = image.size
    corner = image.getpixel((0, 0))
    centre = image.getpixel((width // 2, height // 2))
    if not is_reddish(corner):
        raise AssertionError(f"composite corner pixel is not red-backed: {corner}")
    if not is_greenish(centre):
        raise AssertionError(f"composite centre pixel lost the source image: {centre}")
    if width <= IMAGE_SIZE[0] or height <= IMAGE_SIZE[1]:
        raise AssertionError(f"composite size {width}x{height} did not grow beyond source {IMAGE_SIZE[0]}x{IMAGE_SIZE[1]}")
    return {"width": width, "height": height, "corner": list(corner), "centre": list(centre)}


def write_json(path: pathlib.Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def snap(browser: NativeBrowser, shots_dir: pathlib.Path, name: str) -> None:
    browser.capture_screenshot(shots_dir / name)


def pool_dom_check(browser: NativeBrowser, pool_id: str) -> dict:
    expression = (
        "(function(){var n=document.querySelector('.image-node[data-id=\"" + pool_id + "\"]');"
        "if(!n)return{present:false};"
        "var imgs=n.querySelectorAll('img');var src=0;"
        "for(var i=0;i<imgs.length;i++){if(imgs[i].getAttribute('src'))src++;}"
        "var r=n.getBoundingClientRect();"
        "return{present:true,historyGroup:n.classList.contains('history-group-node'),imgsWithSrc:src,visible:r.width>0&&r.height>0,title:(n.querySelector('.node-title')||{}).textContent||''};"
        "})()"
    )
    return browser.evaluate(expression) or {"present": False}


def wait_node_clickable(browser: NativeBrowser, selector: str, timeout: float = 20) -> dict:
    """Poll until a native click at the element centre would settle: the
    element exists, is visible and elementFromPoint lands on it or a child
    (exactly the condition native_click enforces)."""
    expression = (
        "(function(){var el=document.querySelector(" + json.dumps(selector) + ");"
        "if(!el)return{ok:false,reason:'missing'};"
        "var r=el.getBoundingClientRect();"
        "if(r.width<=0||r.height<=0)return{ok:false,reason:'invisible',rect:{w:r.width,h:r.height}};"
        "var hit=document.elementFromPoint(r.left+r.width/2,r.top+r.height/2);"
        "if(!hit||!(hit===el||el.contains(hit)))return{ok:false,reason:'obscured',hit:hit?hit.tagName+'.'+(hit.className||''):''};"
        "return{ok:true};})()"
    )
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = browser.evaluate(expression)
        if last and last.get("ok"):
            return last
        time.sleep(0.4)
    raise TimeoutError(f"click target never settled for {selector}: {last}")


def wait_image_painted(browser: NativeBrowser, node_id: str, timeout: float = 30) -> None:
    expression = (
        "(function(){var n=document.querySelector('.image-node[data-id=\"" + node_id + "\"]');"
        "if(!n)return false;var img=n.querySelector('img');"
        "return !!img&&img.naturalWidth>0&&img.naturalHeight>0;})()"
    )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if browser.evaluate(expression):
            return
        time.sleep(0.4)
    raise TimeoutError(f"result image not painted for node {node_id} within {timeout:.0f}s")


def run_entry(entry: str, artifacts: pathlib.Path, shots_dir: pathlib.Path) -> dict:
    entry_artifacts = artifacts / entry
    entry_shots = shots_dir / entry
    entry_artifacts.mkdir(parents=True, exist_ok=True)
    entry_shots.mkdir(parents=True, exist_ok=True)
    steps = {name: False for name in STEP_NAMES}
    assertions: list[dict] = []
    browser: NativeBrowser | None = None
    failure = None
    try:
        with tempfile.TemporaryDirectory(prefix=f"firefly-188-{entry}-") as root_name:
            root = pathlib.Path(root_name)
            store = ProjectStore(root / "projects", snapshot_delay=0)
            project_id = seed(store)

            capture = ProviderContractCapture(
                [gemini_image_response(solid_png(GREEN, IMAGE_SIZE)), gemini_image_response(solid_png(BLUE, IMAGE_SIZE))]
            )
            app = harness.load_production_app(root / "runtime-root", store)
            production = app.state.issue13_module
            # load_production_app re-imports the app.* tree, so patch the fresh
            # app.services module (the one the task worker resolves at call time).
            linked_services = sys.modules["app.services"]
            endpoint_patch = lambda provider, model_name: PROVIDER_ENDPOINT  # noqa: E731
            with patch.object(production, "load_api_providers", lambda: [provider_fixture()]):
                with patch.object(production, "get_api_provider_exact", lambda provider_id: provider_fixture()):
                    with patch.object(production, "api_headers", lambda **kwargs: {"Authorization": "Bearer fake-pool"}):
                        with patch.object(linked_services, "gemini_endpoint_url", endpoint_patch):
                            with patch.object(httpx, "AsyncClient", capture.async_client):
                                with AsgiBrowserServer(app) as server:
                                    browser = NativeBrowser.launch(find_browser())
                                    browser.set_environment(VIEWPORT)
                                    url = (
                                        f"{server.base_url}/static/smart-canvas.html"
                                        f"?id={urllib.parse.quote(project_id)}&project={urllib.parse.quote(project_id)}"
                                        "&project_mode=1"
                                    )
                                    browser.navigate(url)
                                    browser.wait_for(f'document.querySelector(".image-node[data-id=\\"{NODE_ID}\\"]")', timeout=30)
                                    time.sleep(1.0)  # let the click-suppression window lapse and hydration finish
                                    snap(browser, entry_shots, "00-opened.png")

                                    browser.native_click(f'.image-node[data-id="{NODE_ID}"]')
                                    browser.wait_for(
                                        'document.getElementById("promptInput") && '
                                        '(document.getElementById("promptInput").innerText || "").indexOf("repro-188 base prompt") !== -1',
                                        timeout=20,
                                    )
                                    browser.wait_for('document.getElementById("runBtn") && !document.getElementById("runBtn").disabled', timeout=20)

                                    # --- step 1: first generation (result A, green)
                                    wait_node_clickable(browser, "#runBtn")
                                    browser.native_click("#runBtn")
                                    wait_node_images(store, project_id, 90)
                                    steps["generated"] = True
                                    wait_image_painted(browser, NODE_ID)
                                    time.sleep(0.8)  # let run-timer/chip overlays settle
                                    snap(browser, entry_shots, "01-run1-done.png")

                                    # --- step 2: outpaint apply (red-backed composite)
                                    wait_node_clickable(browser, f'.image-node[data-id="{NODE_ID}"]')
                                    browser.native_click(f'.image-node[data-id="{NODE_ID}"]')
                                    browser.wait_for(
                                        f'document.querySelector(\'[data-smart-node-action="outpaint"]\') && '
                                        f'document.querySelector(\'[data-smart-node-action="outpaint"]\').getBoundingClientRect().width > 0',
                                        timeout=20,
                                    )
                                    wait_node_clickable(browser, f'.image-node[data-id="{NODE_ID}"] [data-smart-node-action="outpaint"]')
                                    browser.native_click(f'.image-node[data-id="{NODE_ID}"] [data-smart-node-action="outpaint"]')
                                    browser.wait_for('document.getElementById("imageEditModal").classList.contains("open")', timeout=20)
                                    browser.wait_for(
                                        "(function(){var i=document.getElementById('cropImage');"
                                        "return !!i&&i.naturalWidth>0&&i.naturalHeight>0;})()",
                                        timeout=30,
                                    )
                                    time.sleep(0.5)
                                    # grow the outpaint frame so the composite gains red margins
                                    grown = False
                                    for attempt in range(3):
                                        drag_dx = 90 + 50 * attempt
                                        browser.native_drag('.outpaint-handle[data-outpaint-handle="corner"]', dx=drag_dx, dy=drag_dx)
                                        state = browser.evaluate(
                                            "(function(){if(!cropState)return null;var b=cropBounds();"
                                            "return{w:cropState.w,h:cropState.h,bw:b.w,bh:b.h};;})()"
                                        )
                                        if state and state.get("w", 0) > state.get("bw", 0) + 1 and state.get("h", 0) > state.get("bh", 0) + 1:
                                            grown = True
                                            break
                                    if not grown:
                                        raise AssertionError(f"outpaint frame did not grow beyond the source image: {state}")
                                    snap(browser, entry_shots, "02-outpaint-frame-grown.png")
                                    wait_node_clickable(browser, "#imageEditApplyBtn")
                                    browser.native_click("#imageEditApplyBtn")
                                    browser.wait_for('!document.getElementById("imageEditModal").classList.contains("open")', timeout=30)
                                    outpaint_node = wait_node_images(store, project_id, 30, expect_name_part="_outpaint_")
                                    wait_image_painted(browser, NODE_ID)
                                    time.sleep(0.8)
                                    image_url = live_image_url(browser, NODE_ID, "_outpaint_")
                                    if not image_url:
                                        raise AssertionError("live canvas state has no outpaint composite image URL")
                                    composite = verify_outpaint_composite(server.base_url, image_url)
                                    steps["outpaint-applied"] = True
                                    assertions.append({
                                        "name": "outpaint composite is red-backed and larger than the source",
                                        "expected": f"corner reddish + centre greenish + size > {IMAGE_SIZE[0]}x{IMAGE_SIZE[1]}",
                                        "actual": composite,
                                        "passed": True,
                                    })
                                    snap(browser, entry_shots, "03-outpaint-applied.png")

                                    # --- step 3: second generation archives the composite into the pool
                                    wait_node_clickable(browser, "#runBtn")
                                    browser.native_click("#runBtn")
                                    wait_node_images(store, project_id, 90, forbid_name_part="_outpaint_")
                                    wait_image_painted(browser, NODE_ID)
                                    time.sleep(0.8)
                                    pool = wait_pool(store, project_id, 30, expect=True)
                                    if pool is None:
                                        raise AssertionError("candidate pool was not created after the second generation")
                                    pool_images = pool.get("images") or []
                                    steps["pool-archived"] = True
                                    assertions.append({
                                        "name": "pool holds the archived red composite after re-generation",
                                        "expected": "pool present with exactly 1 image (the composite)",
                                        "actual": {"pool_id": pool.get("id"), "image_count": len(pool_images), "historyFor": pool.get("historyFor")},
                                        "passed": len(pool_images) == 1,
                                    })
                                    snap(browser, entry_shots, "04-before-delete.png")

                                    # --- step 4: delete the node through the entry under test
                                    pool_id = str(pool.get("id"))
                                    pool_before = pool_dom_check(browser, pool_id)
                                    if entry == "button":
                                        # A non-empty image node hides its .node-head (display:none), so the
                                        # visible × is the floating action button; it is pointer-events:auto
                                        # only while the node is hovered. Click the node first (selects + hovers it).
                                        wait_node_clickable(browser, f'.image-node[data-id="{NODE_ID}"]')
                                        browser.native_click(f'.image-node[data-id="{NODE_ID}"]')
                                        browser.wait_for(
                                            f'(() => {{ const b = document.querySelector(\'.image-node[data-id="{NODE_ID}"] .floating-node-actions .node-delete\'); '
                                            f'return Boolean(b && getComputedStyle(b).pointerEvents === "auto"); }})()',
                                            timeout=20,
                                        )
                                        browser.native_click(f'.image-node[data-id="{NODE_ID}"] .floating-node-actions .node-delete')
                                    else:
                                        wait_node_clickable(browser, f'.image-node[data-id="{NODE_ID}"]')
                                        browser.native_click(f'.image-node[data-id="{NODE_ID}"]')
                                        time.sleep(0.4)
                                        browser.native_press("Delete")
                                    deadline = time.monotonic() + 30
                                    node_gone = False
                                    while time.monotonic() < deadline:
                                        if not any(n.get("id") == NODE_ID for n in canvas_nodes(store, project_id)):
                                            node_gone = True
                                            break
                                        time.sleep(0.5)
                                    steps["node-deleted"] = node_gone
                                    if not node_gone:
                                        raise AssertionError("image node was not removed after the delete entry")
                                    snap(browser, entry_shots, "05-after-delete.png")

                                    # --- THE assertion: the pool must survive with its image
                                    pool_after_store = next((n for n in canvas_nodes(store, project_id) if n.get("id") == pool_id), None)
                                    pool_after_dom = pool_dom_check(browser, pool_id)
                                    pool_alive = (
                                        pool_after_store is not None
                                        and len(pool_after_store.get("images") or []) == 1
                                        and pool_after_dom.get("present")
                                        and pool_after_dom.get("imgsWithSrc", 0) >= 1
                                    )
                                    steps["pool-survives"] = pool_alive
                                    assertions.append({
                                        "name": f"candidate pool survives the {entry} delete with its archived image",
                                        "expected": f"pool {pool_id} still on canvas + DOM with 1 image",
                                        "actual": {
                                            "store": None if pool_after_store is None else {"image_count": len(pool_after_store.get("images") or [])},
                                            "dom": pool_after_dom,
                                            "dom_before": pool_before,
                                        },
                                        "passed": pool_alive,
                                    })
                                    if not pool_alive:
                                        raise AssertionError(
                                            "candidate pool vanished together with the deleted image node "
                                            f"(entry={entry}): store={json.dumps(assertions[-1]['actual']['store'], ensure_ascii=False)} "
                                            f"dom={json.dumps(pool_after_dom, ensure_ascii=False)}"
                                        )

                                    # --- step 5: undo restores the node AND keeps the pool intact
                                    browser.wait_for('document.getElementById("smartUndoButton") && !document.getElementById("smartUndoButton").disabled', timeout=15)
                                    wait_node_clickable(browser, "#smartUndoButton")
                                    browser.native_click("#smartUndoButton")
                                    deadline = time.monotonic() + 30
                                    restored = None
                                    while time.monotonic() < deadline:
                                        nodes = canvas_nodes(store, project_id)
                                        restored = next((n for n in nodes if n.get("id") == NODE_ID), None)
                                        if restored is not None:
                                            break
                                        time.sleep(0.5)
                                    if restored is None:
                                        raise AssertionError("undo did not restore the deleted image node")
                                    pool_restored = next((n for n in canvas_nodes(store, project_id) if n.get("id") == pool_id), None)
                                    pool_ok = (
                                        pool_restored is not None
                                        and pool_restored.get("isHistoryGroup") is True
                                        and pool_restored.get("historyFor") == NODE_ID
                                        and len(pool_restored.get("images") or []) == 1
                                        and len(restored.get("images") or []) == 1
                                    )
                                    steps["undo-restores"] = pool_ok
                                    assertions.append({
                                        "name": "undo restores the node and the pool stays linked with its image",
                                        "expected": "node back with 1 image; pool isHistoryGroup + historyFor + 1 image",
                                        "actual": {
                                            "node_images": len(restored.get("images") or []),
                                            "pool": None if pool_restored is None else {
                                                "isHistoryGroup": pool_restored.get("isHistoryGroup"),
                                                "historyFor": pool_restored.get("historyFor"),
                                                "image_count": len(pool_restored.get("images") or []),
                                            },
                                        },
                                        "passed": pool_ok,
                                    })
                                    if not pool_ok:
                                        raise AssertionError(f"undo broke the pool/node state: {assertions[-1]['actual']}")
                                    snap(browser, entry_shots, "06-after-undo.png")
    except Exception as error:  # noqa: BLE001 — journey evidence must capture any failure
        failure = {"message": str(error), "traceback": traceback.format_exc()}
        print(f"FAILED [{entry}]: {error}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
    finally:
        operations = []
        console_errors = []
        if browser is not None:
            try:
                operations = list(browser.operations)
                console_errors = list(browser.console_errors)
            finally:
                browser.close()

        (entry_artifacts / "operations.jsonl").write_text(
            "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in operations),
            encoding="utf-8",
        )
        write_json(entry_artifacts / "console-errors.json", console_errors)
        write_json(entry_artifacts / "requests.json", [
            {k: v for k, v in item.items() if k != "files"} for item in capture.requests
        ])
        passed = failure is None and all(steps.values())
        write_json(entry_artifacts / "manifest.json", {
            "schema_version": 1,
            "journey": "candidate-pool-delete",
            "entry": entry,
            "status": "passed" if passed else "failed",
            "first_failure": failure,
            "viewport": list(VIEWPORT),
            "steps": [{"name": name, "status": "passed" if steps[name] else "failed"} for name in STEP_NAMES],
            "assertions": assertions,
            "screenshots": [str(p.relative_to(shots_dir)) for p in sorted(entry_shots.glob("*.png"))],
        })
        print(json.dumps({"entry": entry, "status": "passed" if passed else "failed", "steps": steps, "failure": (failure or {}).get("message")}, ensure_ascii=False))
    return {"entry": entry, "passed": passed, "steps": steps, "failure": failure}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts-dir", type=pathlib.Path, required=True)
    parser.add_argument("--shots-dir", type=pathlib.Path, required=True)
    parser.add_argument("--entry", choices=("button", "key", "both"), default="both")
    args = parser.parse_args()
    artifacts = args.artifacts_dir.resolve()
    shots_dir = args.shots_dir.resolve()
    artifacts.mkdir(parents=True, exist_ok=True)
    shots_dir.mkdir(parents=True, exist_ok=True)

    entries = ("button", "key") if args.entry == "both" else (args.entry,)
    results = [run_entry(entry, artifacts, shots_dir) for entry in entries]
    all_passed = all(r["passed"] for r in results)
    print(json.dumps({"status": "passed" if all_passed else "failed", "results": [{k: r[k] for k in ("entry", "passed")} for r in results]}, ensure_ascii=False))
    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
