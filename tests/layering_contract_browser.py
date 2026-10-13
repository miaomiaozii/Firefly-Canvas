"""Verify the canvas layering contract in real headless Chrome (#163).

Regression: with the history drawer open, the #152 elevation rule
(`body:has(.log-modal.open) .image-node { z-index:87 }`) lifted *all* image
nodes — including the generation group box (.smart-group-node) — above the
composer (z-index:30), so the prompt box inside/next to a group box became
unclickable and untypeable.

Contract asserted (bottom → top, per #163):
连线 < 生成分组框（背板） < 普通节点 < 选中/拖动中的节点 < 提示词框 < 面板类浮层 < 历史抽屉

Scenario:
- a normal node inside a smart group is selected → composer opens
- the group frame is moved to cover the composer's top band (the #163 overlap)
- the history drawer is opened, then collapsed — the user's persistent state
  ("开过一次历史抽屉后长期存在（折叠不解，关闭才解）"): the `.open` class
  (and therefore the #152 node lift) stays active while the panel shrinks to
  a bottom strip that does not cover the composer
- with the drawer open+collapsed, the composer (not the group frame) must be
  the topmost hit target at the overlap, and z-order inside world must stay
  group frame < node < composer
- fully open, the drawer panel remains the topmost surface where it paints
- with the drawer closed, the composer still beats the group frame
"""

from __future__ import annotations

import base64
import io
import json
import subprocess
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from firefly_projects import ProjectStore
from tests import conversation_browser as harness


CLIENT_SCRIPT = ROOT / "tests" / "layering_contract_browser_client.js"
VIEWPORTS = [(1424, 900)]


def tiny_png_base64() -> str:
    output = io.BytesIO()
    Image.new("RGB", (5, 3), (99, 102, 241)).save(output, format="PNG")
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


def run_settings() -> dict:
    return {
        "engine": "api",
        "apiKind": "image",
        "routingMode": "manual",
        "provider_id": "browser-image",
        "model": "browser-image-model",
        "ratio": "square",
        "resolution": "1k",
        "quality": "high",
        "count": 1,
    }


def seed_store(store: ProjectStore) -> str:
    project = store.create_project("Layering contract project")
    media = store.import_media(
        project["id"],
        harness.image_bytes("LAYER MEMBER", (99, 102, 241)),
        "layer-member.png",
        "image/png",
        {"type": "layering-contract-fixture", "name": "layer-member.png"},
    )
    store.apply_mutations(
        project["id"],
        [
            {
                "op": "upsert_node",
                "node": {
                    "id": "layer-node",
                    "type": "smart-image",
                    "x": 320,
                    "y": 240,
                    "w": 260,
                    "h": 220,
                    "images": [],
                    "runSettings": run_settings(),
                },
            },
            {
                "op": "upsert_node",
                "node": {
                    "id": "layer-member",
                    "type": "smart-image",
                    "x": 900,
                    "y": 150,
                    "w": 260,
                    "h": 220,
                    "images": [
                        {
                            "asset_id": media["asset_id"],
                            "kind": "image",
                            "name": "layer-member.png",
                        }
                    ],
                    "runSettings": run_settings(),
                },
            },
            {
                "op": "upsert_node",
                "node": {
                    "id": "layer-group",
                    "type": "smart-group",
                    "x": 900,
                    "y": 150,
                    "w": 300,
                    "h": 240,
                    "title": "智能分组",
                    "items": ["layer-member"],
                    "created_at": int(time.time() * 1000),
                },
            },
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
    module.AI_BASE_URL = "https://browser-layering-contract.invalid"
    image_value = tiny_png_base64()

    async def fake_image(*_args, **_kwargs):
        return {"type": "b64", "value": image_value, "mime_type": "image/png"}, {}

    module.generate_ai_image = fake_image
    return app, module


def launch(browser: Path, server, viewport: tuple[int, int], project_id: str):
    width, height = viewport
    profile = Path(tempfile.mkdtemp(prefix="firefly-layering-contract-browser-profile-"))
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
    with tempfile.TemporaryDirectory(prefix="firefly-layering-contract-browser-library-") as root_text:
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
        "surface": "layering-contract",
        "browser": str(browser),
        "viewports": VIEWPORTS,
        "runs": results,
        "pass": len(results) == len(VIEWPORTS) and all(item.get("pass") for item in results),
    }
    print(json.dumps(summary, ensure_ascii=True, indent=2))
    return 0 if summary["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
