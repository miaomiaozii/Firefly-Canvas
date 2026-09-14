"""Exercise "run replaces the current node's image" in real headless Chrome.

Regression loop for: clicking Run on a node that already has an image must
replace that node's image in place -- NOT create a new branch output node.

Seed: one smart-image node that already owns one media asset. The browser
clicks it, enters a prompt, clicks Run, waits for completion, then asserts:
  1. canvas node count stays 1 (no new node was created)
     (the #85 candidate pool / history-group node that archives the replaced
     image is excluded from the count -- it is part of in-place replace, not
     a new branch output node)
  2. the current node's images[0] asset_id differs from the seed asset
     (the image was replaced, not kept untouched beside a new node)
"""

from __future__ import annotations

import base64
import io
import json
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


CLIENT_SCRIPT = ROOT / "tests" / "node_replace_browser_client.js"
VIEWPORT = (1408, 710)


def image_bytes(text: str, color: tuple[int, int, int]) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (16, 12), color).save(output, format="PNG")
    return output.getvalue()


def tiny_png_base64() -> str:
    return base64.b64encode(image_bytes("REPLACED", (190, 133, 38))).decode("ascii")


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
                "max_reference_images": 1,
            }
        },
    },
]


def seed_store(store: ProjectStore) -> tuple[str, str]:
    project = store.create_project("Node replace browser project")
    first_media = store.import_media(
        project["id"],
        image_bytes("FIRST", (44, 82, 130)),
        "first.png",
        "image/png",
        {"type": "online_generation", "name": "first.png"},
    )
    store.apply_mutations(
        project["id"],
        [
            {
                "op": "upsert_node",
                "node": {
                    "id": "replace-node",
                    "type": "smart-image",
                    "x": 80,
                    "y": 120,
                    "w": 260,
                    "h": 220,
                    "images": [
                        {"asset_id": first_media["asset_id"], "kind": "image", "name": "first.png"}
                    ],
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
    return project["id"], first_media["asset_id"]


def load_app(root: Path, store: ProjectStore):
    app = harness.load_production_app(root, store)
    module = app.state.issue13_module
    module.load_api_providers = lambda: IMAGE_PROVIDERS
    module.provider_env_key_value = lambda *_args, **_kwargs: "browser-test-key"
    module.AI_BASE_URL = "https://browser-replace.invalid"
    image_value = tiny_png_base64()

    async def fake_image(*_args, **_kwargs):
        return {"type": "b64", "value": image_value, "mime_type": "image/png"}, {}

    module.generate_ai_image = fake_image
    return app, module


def launch(browser: Path, server, viewport: tuple[int, int], project_id: str, seed_asset: str):
    import subprocess

    width, height = viewport
    profile = Path(tempfile.mkdtemp(prefix="firefly-node-replace-browser-profile-"))
    before = len(server.results)
    query = urllib.parse.urlencode(
        {"project": project_id, "browserTest": "1", "seedAsset": seed_asset}
    )
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
        return server.wait_for_result(before, timeout=120)
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
    with tempfile.TemporaryDirectory(prefix="firefly-node-replace-browser-library-") as root_text:
        root = Path(root_text)
        store = ProjectStore(root / "projects-0", snapshot_delay=0)
        project_id, seed_asset = seed_store(store)
        app, module = load_app(root / "runtime-0", store)
        server = harness.ConversationBrowserServer(store, app)
        server.start()
        try:
            result = launch(browser, server, VIEWPORT, project_id, seed_asset)
            if not result.get("pass"):
                raise RuntimeError(json.dumps(result, ensure_ascii=False, indent=2))
            print(json.dumps(result, ensure_ascii=True, indent=2))
            return 0 if result.get("pass") else 1
        finally:
            server.close()
            try:
                module.FIREFLY_PROJECT_STORE.close()
            except Exception:
                pass
            if module.FIREFLY_PROJECT_STORE is not store:
                store.close()


if __name__ == "__main__":
    raise SystemExit(main())
