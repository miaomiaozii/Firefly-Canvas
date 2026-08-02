"""Exercise the conversation smart-node UI in the shipped canvas through real headless Chrome.

Covers: message sending, pending/status states, assistant reply rendering, and
connected reference chips (image message display surface).
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from firefly_projects import ProjectStore

from tests import conversation_browser as harness


CLIENT_SCRIPT = ROOT / "tests" / "conversation_ui_browser_client.js"
VIEWPORTS = [(1424, 805)]


def image_bytes(label: str, color: tuple[int, int, int]) -> bytes:
    image = Image.new("RGB", (480, 320), color)
    draw = ImageDraw.Draw(image)
    draw.rectangle((12, 12, 468, 308), outline=(255, 255, 255), width=5)
    draw.text((28, 28), label, fill=(255, 255, 255))
    output = __import__("io").BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def seed_store(store: ProjectStore) -> dict[str, str]:
    project = store.create_project("Conversation UI Suite")
    pid = project["id"]
    asset = store.import_media(pid, image_bytes("REF-IMG", (120, 180, 200)), "reference.png", "image/png")
    conversation_node = {
        "id": "conv-main",
        "type": "smart-conversation",
        "x": 240,
        "y": 160,
        "w": 380,
        "h": 300,
        "conversation_adapter": {"provider": "browser", "model": "offline-model"},
        "messages": [],
    }
    image_node = {
        "id": "img-ref",
        "type": "smart-image",
        "x": 720,
        "y": 160,
        "w": 316,
        "h": 194,
        "images": [
            {
                "asset_id": asset["asset_id"],
                "name": asset["name"],
                "mime_type": asset["mime_type"],
            }
        ],
    }
    store.apply_mutations(
        pid,
        [
            {"op": "upsert_node", "node": conversation_node},
            {"op": "upsert_node", "node": image_node},
            {"op": "upsert_edge", "edge": {"from": "img-ref", "to": "conv-main", "kind": "input"}},
        ],
        base_revision=0,
    )
    store.flush(pid)
    return {
        "project_id": pid,
        "canvas_id": pid,
        "asset_id": asset["asset_id"],
        "selected_text": "",
    }


def load_app(root: Path, store: ProjectStore):
    runtime_root = (root / "runtime").resolve()
    projects_root = (root / "unused-projects").resolve()
    runtime_root.mkdir(parents=True, exist_ok=True)
    projects_root.mkdir(parents=True, exist_ok=True)
    os.environ["FIREFLY_RUNTIME_ROOT"] = str(runtime_root)
    os.environ["FIREFLY_PROJECTS_ROOT"] = str(projects_root)
    module_name = f"_firefly_ui_main_{__import__('uuid').uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, ROOT / "main.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot create an isolated production app module")
    previous_main_module = os.environ.get("FIREFLY_MAIN_MODULE")
    os.environ["FIREFLY_MAIN_MODULE"] = module_name
    for _name in list(sys.modules):
        if _name.startswith("app.") or _name in ("app", "main"):
            sys.modules.pop(_name, None)
    production_main = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = production_main
    try:
        spec.loader.exec_module(production_main)
    finally:
        sys.modules.pop(module_name, None)
        if previous_main_module is None:
            os.environ.pop("FIREFLY_MAIN_MODULE", None)
        else:
            os.environ["FIREFLY_MAIN_MODULE"] = previous_main_module
    previous_store = production_main.FIREFLY_PROJECT_STORE
    production_main.FIREFLY_PROJECT_STORE = store
    if previous_store is not None and previous_store is not store:
        previous_store.close()

    async def fake_conversation_adapter(request):
        import sys as _sys

        _sys.stderr.write(f"[UI-FAKE-ADAPTER] called request={json.dumps({k: request.get(k) for k in ('message', 'request_id')}, ensure_ascii=False)}\n")
        _sys.stderr.flush()
        prompt = request.get("message") or request.get("text") or ""
        if "参考" in str(prompt):
            return {"text": "已看到你直连的参考图片。", "model": "offline-browser-model"}
        return {"text": f"这是对「{str(prompt)[:40]}」的离线回复。", "model": "offline-browser-model"}

    production_main.project_conversation_adapter = fake_conversation_adapter
    production_main.sync_static_html_versions = lambda: None
    production_main.app.state.issue13_module = production_main
    production_main.app.state.issue13_runtime_root = runtime_root
    production_main.app.state.issue13_projects_root = projects_root
    return production_main.app


def _launch(browser, server, viewport, fixture):
    original_wait = server.wait_for_result

    def wait_longer(start_count, timeout=120):
        return original_wait(start_count, timeout)

    server.wait_for_result = wait_longer
    try:
        return harness.launch(browser, server, viewport, fixture)
    finally:
        server.wait_for_result = original_wait


def main():
    browser = harness.find_browser()
    with tempfile.TemporaryDirectory(prefix="firefly-ui-") as root:
        results = []
        for index, viewport in enumerate(VIEWPORTS):
            store = ProjectStore(Path(root) / f"projects-{index}", snapshot_delay=0)
            fixture = seed_store(store)
            harness.CLIENT_SCRIPT = CLIENT_SCRIPT
            app = load_app(Path(root) / f"runtime-{index}", store)
            server = harness.ConversationBrowserServer(store, app)
            server.start()
            try:
                results.append(_launch(browser, server, viewport, fixture))
            finally:
                server.close()
                store.close()
    final = results[-1] if results else {"timeout": True}
    if final.get("pass") is True:
        print(f"PASS: {json.dumps(final, ensure_ascii=False)}", flush=True)
        return 0
    print(f"FAIL: {json.dumps(final, ensure_ascii=False)}", flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
