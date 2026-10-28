"""Real headless-Chrome journey for the #255 layers panel (issue #269 evidence).

Full chain in one page session: open the image editor -> layers mode ->
create multiple layers (brush-pixel import + blank layer) -> toggle layer
visibility -> drag-reorder layer rows -> export the composited PNG and
assert its pixels follow the current layer order (overlap occlusion
samples: blue-above-red, red-above-blue, all-hidden falls back to base).

The production app is served against a temporary ProjectStore with one
solid-color image; no network providers or real assets are involved.
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from firefly_projects import ProjectStore
from tests import conversation_browser as harness
from tests.canvas_browser_runner_support import finish_run

CLIENT_SCRIPT = ROOT / "tests" / "layers_panel_browser_client.js"
IMAGE_SIZE = (640, 480)
BASE_COLOR = (60, 64, 96)
VIEWPORTS = [(1424, 900)]


def base_png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", IMAGE_SIZE, BASE_COLOR).save(buffer, "PNG")
    return buffer.getvalue()


def seed(store: ProjectStore) -> dict[str, str]:
    project = store.create_project("Issue 269 Layers Panel")
    media = store.import_media(
        project["id"],
        base_png(),
        "layers-base.png",
        "image/png",
        {"type": "user_import", "name": "layers-base.png"},
    )
    store.apply_mutations(
        project["id"],
        [
            {
                "op": "upsert_node",
                "node": {
                    "id": "edit-node",
                    "type": "smart-image",
                    "images": [{"asset_id": media["asset_id"], "kind": "image", "name": "layers-base.png"}],
                },
            }
        ],
        base_revision=0,
    )
    store.flush(project["id"])
    return {"project_id": project["id"], "selected_text": ""}


def main():
    # 固定 UTF-8：中文 Windows 默认 locale（GBK）会编码失败或产出门无法解码的输出。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    browser = harness.find_browser()
    print(f"browser={browser}", flush=True)
    runs: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="firefly-269-layers-") as root:
        store = ProjectStore(Path(root) / "projects", snapshot_delay=0)
        fixture = seed(store)
        print(f"seeded {fixture['project_id']}", flush=True)
        app = harness.load_production_app(Path(root) / "runtime", store)
        server = harness.ConversationBrowserServer(store, app, client_script=CLIENT_SCRIPT)
        server.start()
        print("server started", flush=True)
        try:
            for viewport in VIEWPORTS:
                try:
                    runs.append(harness.launch(browser, server, viewport, fixture, result_timeout=180))
                except Exception as exc:
                    runs.append({"pass": False, "viewport": list(viewport), "reason": str(exc)})
        finally:
            server.close()
            store.close()
    summary = {
        "issue": 269,
        "browser": str(browser),
        "viewports": VIEWPORTS,
        "runs": runs,
        "pass": bool(runs) and all(run.get("pass") for run in runs),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return finish_run("layers-panel", runs)


if __name__ == "__main__":
    raise SystemExit(main())
