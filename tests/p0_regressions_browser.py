"""Reproduce the four P0 project-canvas risks in real headless Chrome.

Each scenario uses a fresh ProjectStore and an isolated production ASGI app.
The assertions describe the user-visible contract, so a known regression makes
this runner fail with the exact browser and server state that exposed it.
"""

from __future__ import annotations

import copy
import sys
import tempfile
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from firefly_projects import ProjectStore
from tests import conversation_browser as harness
from tests import generation_browser


CLIENT_SCRIPT = ROOT / "tests" / "p0_regressions_browser_client.js"
VIEWPORTS = [(1408, 710), (500, 749)]
MODES = (
    "restore-preserves-edit",
    "url-save-converges",
    "media-renders-after-save",
    "generation-flush-reference",
)


def apply_canvas(store: ProjectStore, project_id: str, nodes, *, edges=(), logs=None):
    mutations = [{"op": "upsert_node", "node": node} for node in nodes]
    mutations.extend({"op": "upsert_edge", "edge": edge} for edge in edges)
    if logs is not None:
        mutations.append({"op": "set_canvas_meta", "logs": logs})
    store.apply_mutations(project_id, mutations, base_revision=0)
    store.flush(project_id)


def seed_restore_preserves_edit(store: ProjectStore) -> str:
    project = store.create_project("P0 restore preserves edit")
    media = store.import_media(
        project["id"],
        harness.image_bytes("P0 ORIGINAL", (46, 92, 138)),
        "p0-original.png",
        "image/png",
        {"type": "online_generation", "name": "p0-original.png"},
    )
    asset_id = media["asset_id"]
    nodes = [
        {
            "id": "p0-restore-generator",
            "type": "smart-image",
            "x": 80,
            "y": 100,
            "w": 300,
            "h": 240,
            "images": [{"asset_id": asset_id, "kind": "image", "name": "p0-original.png"}],
            "runPrompt": "P0 原图恢复测试",
            "runModelPrompt": "p0 restore test",
            "runInputRefs": [{"asset_id": asset_id, "kind": "image", "name": "p0-original.png"}],
            "runSettings": {"model": "offline-browser-model", "size": "1024x1024"},
            "runFinishedAt": 10,
        },
        {
            "id": "p0-editable-prompt",
            "type": "smart-prompt",
            "x": 500,
            "y": 120,
            "w": 320,
            "h": 220,
            "text": "before restore",
            "title": "待编辑提示词",
            "llmEnabled": True,
            "llmProvider": "browser",
            "llmModel": "offline-model",
        },
    ]
    logs = [
        {
            "id": "p0-restore-history",
            "type": "online-image",
            "status": "succeeded",
            "prompt": "P0 原图恢复测试",
            "params": {"model": "offline-browser-model", "size": "1024x1024"},
            "outputs": [{"asset_id": asset_id, "kind": "image", "name": "p0-original.png"}],
            "result_node_id": "p0-restore-generator",
        }
    ]
    apply_canvas(store, project["id"], nodes, logs=logs)
    return project["id"]


def seed_url_save_converges(store: ProjectStore) -> str:
    project = store.create_project("P0 URL save converges")
    # A URL-only item represents a legacy/imported canvas. New project
    # mutations correctly reject it; the browser save path must still migrate
    # such an already-open canvas instead of leaving it permanently dirty.
    store._canvases[project["id"]]["nodes"] = [
        {
            "id": "p0-url-image",
            "type": "smart-image",
            "x": 180,
            "y": 120,
            "w": 320,
            "h": 240,
            "images": [{"url": "/output/p0-url-only.png", "kind": "image", "name": "p0-url-only.png"}],
        }
    ]
    store.flush(project["id"])
    return project["id"]


def seed_media_renders_after_save(store: ProjectStore) -> str:
    project = store.create_project("P0 media renders after save")
    media = store.import_media(
        project["id"],
        harness.image_bytes("P0 MEDIA", (74, 138, 92)),
        "p0-media.png",
        "image/png",
        {"type": "p0-browser-fixture", "name": "p0-media.png"},
    )
    apply_canvas(
        store,
        project["id"],
        [
            {
                "id": "p0-render-image",
                "type": "smart-image",
                "x": 180,
                "y": 120,
                "w": 320,
                "h": 240,
                "images": [{"asset_id": media["asset_id"], "kind": "image", "name": "p0-media.png"}],
            },
            {
                "id": "p0-render-anchor",
                "type": "smart-prompt",
                "x": 560,
                "y": 120,
                "w": 260,
                "h": 180,
                "text": "render anchor",
            },
        ],
    )
    return project["id"]


def seed_generation_flush_reference(store: ProjectStore) -> str:
    project = store.create_project("P0 generation reference flush")
    media = store.import_media(
        project["id"],
        harness.image_bytes("P0 REFERENCE", (138, 74, 46)),
        "p0-reference.png",
        "image/png",
        {"type": "p0-browser-fixture", "name": "p0-reference.png"},
    )
    apply_canvas(
        store,
        project["id"],
        [
            {
                "id": "p0-reference-source",
                "type": "smart-image",
                "x": 80,
                "y": 120,
                "w": 260,
                "h": 220,
                "images": [{"asset_id": media["asset_id"], "kind": "image", "name": "p0-reference.png"}],
            },
            {
                "id": "p0-reference-generator",
                "type": "smart-image",
                "x": 480,
                "y": 120,
                "w": 300,
                "h": 240,
                "imageInput": True,
                "images": [],
                "runSettings": {
                    "engine": "api",
                    "apiKind": "image",
                    "routingMode": "manual",
                    "provider_id": "browser-image",
                    "model": "browser-image-model",
                    "ratio": "square",
                    "resolution": "1k",
                    "quality": "high",
                    "count": 1,
                },
            },
        ],
        edges=(
            {
                "id": "p0-reference-to-generator",
                "from": "p0-reference-source",
                "to": "p0-reference-generator",
                "kind": "input",
                "role": "references",
            },
        ),
    )
    return project["id"]


def seed_store(store: ProjectStore, mode: str) -> str:
    return {
        "restore-preserves-edit": seed_restore_preserves_edit,
        "url-save-converges": seed_url_save_converges,
        "media-renders-after-save": seed_media_renders_after_save,
        "generation-flush-reference": seed_generation_flush_reference,
    }[mode](store)


def load_app(root: Path, store: ProjectStore, mode: str):
    app = harness.load_production_app(root, store)
    module = app.state.issue13_module
    if mode == "generation-flush-reference":
        providers = copy.deepcopy(generation_browser.IMAGE_PROVIDERS)
        providers[0]["image_model_capabilities"]["browser-image-model"]["max_reference_images"] = 1
        module.load_api_providers = lambda: providers
        module.provider_env_key_value = lambda *_args, **_kwargs: "browser-test-key"
        module.AI_BASE_URL = "https://browser-generation.invalid"
        image_value = generation_browser.tiny_png_base64()

        async def fake_image(*_args, **_kwargs):
            return {"type": "b64", "value": image_value, "mime_type": "image/png"}, {}

        module.generate_ai_image = fake_image
    return app, module


def launch(browser: Path, server, viewport: tuple[int, int], project_id: str, mode: str):
    import subprocess

    width, height = viewport
    profile = Path(tempfile.mkdtemp(prefix="firefly-p0-browser-profile-"))
    before = len(server.results)
    query = urllib.parse.urlencode({"project": project_id, "p0Mode": mode, "browserTest": "1"})
    url = f"{server.base_url}/static/smart-canvas.html?id={urllib.parse.quote(project_id)}&{query}"
    process = subprocess.Popen(
        [
            str(browser),
            "--headless=new",
            "--disable-gpu",
            "--disable-background-networking",
            "--disable-component-update",
            "--disable-extensions",
            "--no-first-run",
            "--no-default-browser-check",
            "--hide-scrollbars",
            f"--window-size={width},{height}",
            f"--user-data-dir={profile}",
            url,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=ROOT,
    )
    try:
        return server.wait_for_result(before, timeout=150)
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        harness.remove_profile(profile)


def main():
    browser = harness.find_browser()
    harness.CLIENT_SCRIPT = CLIENT_SCRIPT
    results = []
    failures = []
    with tempfile.TemporaryDirectory(prefix="firefly-p0-browser-library-") as root_text:
        root = Path(root_text)
        for mode in MODES:
            for index, viewport in enumerate(VIEWPORTS):
                store = ProjectStore(root / f"{mode}-projects-{index}", snapshot_delay=0)
                project_id = seed_store(store, mode)
                app, module = load_app(root / f"{mode}-runtime-{index}", store, mode)
                if mode == "url-save-converges":
                    output_dir = Path(module.OUTPUT_OUTPUT_DIR)
                    output_dir.mkdir(parents=True, exist_ok=True)
                    (output_dir / "p0-url-only.png").write_bytes(harness.image_bytes("P0 URL", (38, 190, 133)))
                server = harness.ConversationBrowserServer(store, app)
                server.start()
                try:
                    result = launch(browser, server, viewport, project_id, mode)
                    result["mode"] = mode
                    result["pythonChecks"] = {
                        "projectStillReadable": bool(store.canvas(project_id).get("project_id") == project_id),
                    }
                    result["pass"] = bool(result.get("pass")) and all(result["pythonChecks"].values())
                    results.append(result)
                    if not result["pass"]:
                        failures.append({"mode": mode, "viewport": viewport, "result": result})
                finally:
                    server.close()
                    try:
                        module.FIREFLY_PROJECT_STORE.close()
                    except Exception:
                        pass
                    if module.FIREFLY_PROJECT_STORE is not store:
                        store.close()
    summary = {
        "surface": "p0-regressions",
        "browser": str(browser),
        "modes": MODES,
        "viewports": VIEWPORTS,
        "runs": results,
        "failures": failures,
        "pass": len(results) == len(MODES) * len(VIEWPORTS) and not failures,
    }
    import json

    print(json.dumps(summary, ensure_ascii=True, indent=2))
    return 0 if summary["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
