import base64
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from firefly_projects import ProjectStore  # noqa: E402
from tests import conversation_browser as harness  # noqa: E402

CLIENT_SCRIPT = ROOT / "tests" / "agent_node_settings_browser_client.js"


def tiny_png(color=(42, 123, 231), size=16):
    import zlib

    def chunk(tag, data):
        c = tag + data
        return len(data).to_bytes(4, "big") + c + (zlib.crc32(c) & 0xFFFFFFFF).to_bytes(4, "big")

    r, g, b = color
    raw = b"".join(
        b"\x00" + bytes([r, g, b]) * size for _ in range(size)
    )
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", size.to_bytes(4, "big") + size.to_bytes(4, "big") + b"\x08\x02\x00\x00\x00")
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def seed_store(store: ProjectStore) -> dict:
    project = store.create_project("Agent Node Settings Suite")
    store.apply_mutations(
        project["id"],
        [
            {"op": "upsert_node", "node": {"id": "settings-agent", "type": "smart-agent", "x": 470, "y": 140, "w": 382, "h": 360, "title": "Settings Agent", "agentGoal": ""}},
        ],
        base_revision=0,
    )
    store.flush(project["id"])
    return {
        "project_id": project["id"],
        "canvas_id": project["id"],
        "selected_text": "",
    }


def load_app(root: Path, store: ProjectStore):
    import importlib
    import os

    runtime_root = (root / "runtime").resolve()
    projects_root = (root / "unused-projects").resolve()
    runtime_root.mkdir(parents=True, exist_ok=True)
    projects_root.mkdir(parents=True, exist_ok=True)
    os.environ["FIREFLY_RUNTIME_ROOT"] = str(runtime_root)
    os.environ["FIREFLY_PROJECTS_ROOT"] = str(projects_root)
    module_name = f"_agent_node_settings_main_{__import__('uuid').uuid4().hex}"
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

    import asyncio

    async def fake_agent_image_adapter(request):
        return {"images": [{"type": "b64", "value": base64.b64encode(tiny_png((20, 200, 90))).decode("ascii"), "mime_type": "image/png"}]}

    production_main.agent_image_adapter = fake_agent_image_adapter
    production_main.sync_static_html_versions = lambda: None
    production_main.app.state.issue13_module = production_main
    production_main.app.state.issue13_runtime_root = runtime_root
    production_main.app.state.issue13_projects_root = projects_root
    return production_main.app


def launch(browser: Path, server, viewport: tuple[int, int], fixture: dict):
    return harness.launch(browser, server, viewport, fixture, result_timeout=240)


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    viewports = [(1424, 900)]
    browser = harness.find_browser()
    for index, viewport in enumerate(viewports):
        with tempfile.TemporaryDirectory(prefix="agent-node-settings-project-") as root:
            store = ProjectStore(Path(root) / "projects", snapshot_delay=0)
            fixture = seed_store(store)
            app = load_app(Path(root) / "runtime", store)
            server = harness.ConversationBrowserServer(store, app, client_script=CLIENT_SCRIPT)
            harness.CLIENT_SCRIPT = CLIENT_SCRIPT
            server.start()
            try:
                result = launch(browser, server, viewport, fixture)
                print(f"{tuple(viewport)} PASS: {result}")
            finally:
                server.close()


if __name__ == "__main__":
    main()
