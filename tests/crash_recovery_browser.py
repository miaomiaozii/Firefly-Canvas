"""Exercise crash-recovery flushing: edits inside the 3s debounce window are
saved when the page hides/unloads (pagehide), so reopening the canvas after a
close/crash restores the unsaved work.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from firefly_projects import ProjectStore

from tests import conversation_browser as harness


CLIENT_SCRIPT = ROOT / "tests" / "crash_recovery_browser_client.js"
VIEWPORTS = [(1424, 805)]


def seed_store(store: ProjectStore) -> dict[str, str]:
    project = store.create_project("Crash Recovery Suite")
    pid = project["id"]
    prompt_node = {
        "id": "seed-prompt",
        "type": "smart-prompt",
        "x": 80,
        "y": 120,
        "w": 316,
        "h": 240,
        "title": "Prompt",
        "text": "seed prompt",
        "llmEnabled": False,
        "llmProvider": "browser",
        "llmModel": "offline-model",
    }
    image_node = {
        "id": "seed-image",
        "type": "smart-image",
        "x": 520,
        "y": 120,
        "w": 316,
        "h": 194,
        "images": [],
    }
    store.apply_mutations(
        pid,
        [
            {"op": "upsert_node", "node": prompt_node},
            {"op": "upsert_node", "node": image_node},
            {"op": "upsert_edge", "edge": {"from": "seed-prompt", "to": "seed-image", "kind": "input"}},
        ],
        base_revision=0,
    )
    store.flush(pid)
    return {"project_id": pid, "canvas_id": pid, "selected_text": ""}


def load_app(root: Path, store: ProjectStore):
    runtime_root = (root / "runtime").resolve()
    projects_root = (root / "unused-projects").resolve()
    runtime_root.mkdir(parents=True, exist_ok=True)
    projects_root.mkdir(parents=True, exist_ok=True)
    os.environ["FIREFLY_RUNTIME_ROOT"] = str(runtime_root)
    os.environ["FIREFLY_PROJECTS_ROOT"] = str(projects_root)
    module_name = f"_firefly_crash_recovery_main_{uuid.uuid4().hex}"
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

    production_main.project_conversation_adapter = None
    production_main.sync_static_html_versions = lambda: None
    production_main.app.state.issue13_module = production_main
    production_main.app.state.issue13_runtime_root = runtime_root
    production_main.app.state.issue13_projects_root = projects_root
    return production_main.app


def main():
    browser = harness.find_browser()
    with tempfile.TemporaryDirectory(prefix="firefly-crash-recovery-") as root:
        results = []
        for index, viewport in enumerate(VIEWPORTS):
            store = ProjectStore(Path(root) / f"projects-{index}", snapshot_delay=0)
            fixture = seed_store(store)
            harness.CLIENT_SCRIPT = CLIENT_SCRIPT
            app = load_app(Path(root) / f"runtime-{index}", store)
            server = harness.ConversationBrowserServer(store, app)
            server.start()
            try:
                results.append(harness.launch(browser, server, viewport, fixture, result_timeout=120))
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
