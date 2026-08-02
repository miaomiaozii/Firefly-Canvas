"""Exercise canvas editing experience details in real headless Chrome.

Covers the off-viewport generation edge-reveal pill:
- in-viewport completion shows nothing
- off-viewport completion shows the edge reveal pill
- pointerdown dismisses it
- clicking it pans the viewport to the completed node
- the pill auto-hides after SMART_EDGE_REVEAL_MS (8s)
"""

from __future__ import annotations

import base64
import io
import json
import subprocess
import sys
import tempfile
import urllib.parse
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from firefly_projects import ProjectStore
from tests import conversation_browser as harness


CLIENT_SCRIPT = ROOT / "tests" / "canvas_experience_browser_client.js"
VIEWPORTS = [(1424, 900)]


def tiny_png_base64() -> str:
    output = io.BytesIO()
    Image.new("RGB", (5, 3), (38, 190, 133)).save(output, format="PNG")
    return base64.b64encode(output.getvalue()).decode("ascii")


IMAGE_PROVIDERS = [
    {
        "id": "browser-image",
        "name": "Browser image adapter",
        "enabled": True,
        "protocol": "openai",
        "image_models": ["browser-image-model"],
        "image_model_capabilities": {
            "browser-image-model": {
                "sizes": ["1024x1024"],
                "aspect_ratios": ["1:1"],
                "resolutions": ["1k"],
                "max_reference_images": 0,
            }
        },
    },
]


def seed_store(store: ProjectStore) -> str:
    project = store.create_project("Canvas experience project")
    store.apply_mutations(
        project["id"],
        [
            {
                "op": "upsert_node",
                "node": {
                    "id": "edge-prompt",
                    "type": "smart-image",
                    "x": 80,
                    "y": 120,
                    "w": 260,
                    "h": 220,
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
            }
        ],
        base_revision=0,
    )
    store.flush(project["id"])
    return project["id"]


def load_app(root: Path, store: ProjectStore):
    app = harness.load_production_app(root, store)
    module = app.state.issue13_module
    module.load_api_providers = lambda: IMAGE_PROVIDERS
    module.provider_env_key_value = lambda *_args, **_kwargs: "browser-test-key"
    module.AI_BASE_URL = "https://browser-canvas-experience.invalid"
    image_value = tiny_png_base64()

    async def fake_image(*_args, **_kwargs):
        return {"type": "b64", "value": image_value, "mime_type": "image/png"}, {}

    module.generate_ai_image = fake_image
    return app, module


def launch(browser: Path, server, viewport: tuple[int, int], project_id: str):
    width, height = viewport
    profile = Path(tempfile.mkdtemp(prefix="firefly-canvas-experience-browser-profile-"))
    before = len(server.results)
    query = urllib.parse.urlencode({"project": project_id, "browserTest": "1"})
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
        return server.wait_for_result(before, timeout=240)
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
    with tempfile.TemporaryDirectory(prefix="firefly-canvas-experience-browser-library-") as root_text:
        root = Path(root_text)
        for index, viewport in enumerate(VIEWPORTS):
            store = ProjectStore(root / f"projects-{index}", snapshot_delay=0)
            project_id = seed_store(store)
            app, module = load_app(root / f"runtime-{index}", store)
            server = harness.ConversationBrowserServer(store, app)
            server.start()
            try:
                result = launch(browser, server, viewport, project_id)
                if not result.get("pass"):
                    raise RuntimeError(json.dumps(result, ensure_ascii=False, indent=2))
                results.append(result)
            finally:
                server.close()
                try:
                    module.FIREFLY_PROJECT_STORE.close()
                except Exception:
                    pass
                if module.FIREFLY_PROJECT_STORE is not store:
                    store.close()
    summary = {
        "surface": "canvas-experience",
        "browser": str(browser),
        "viewports": VIEWPORTS,
        "runs": results,
        "pass": len(results) == len(VIEWPORTS) and all(item.get("pass") for item in results),
    }
    print(json.dumps(summary, ensure_ascii=True, indent=2))
    return 0 if summary["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
