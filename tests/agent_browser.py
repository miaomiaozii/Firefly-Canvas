"""Exercise the shipped smart canvas Agent node in real headless Chrome.

The browser uses a fresh temporary ProjectStore and an offline fake image
adapter.  No project data, credentials, or online model is involved.
"""

from __future__ import annotations

import base64
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


CLIENT_SCRIPT = ROOT / "tests" / "agent_browser_client.js"
VIEWPORTS = [(1424, 805), (390, 844)]


def tiny_png(color=(42, 123, 231)) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (8, 6), color).save(output, format="PNG")
    return output.getvalue()


def seed_store(store: ProjectStore) -> str:
    project = store.create_project("Issue 14 Agent")
    media = store.import_media(project["id"], tiny_png(), "reference.png", "image/png")
    store.apply_mutations(
        project["id"],
        [
            {"op": "upsert_node", "node": {"id": "agent-prompt", "type": "smart-prompt", "x": 20, "y": 120, "w": 316, "h": 240, "text": "一只发光的萤火虫"}},
            {"op": "upsert_node", "node": {"id": "agent-reference", "type": "smart-image", "x": 20, "y": 390, "w": 180, "h": 140, "images": [{"asset_id": media["asset_id"], "kind": "image", "name": "reference.png"}]}},
            {"op": "upsert_node", "node": {"id": "agent-1", "type": "smart-agent", "x": 470, "y": 140, "w": 382, "h": 360, "title": "Agent", "agentGoal": ""}},
            {"op": "upsert_node", "node": {"id": "agent-2", "type": "smart-agent", "x": 900, "y": 140, "w": 382, "h": 360, "title": "Agent prompt", "agentGoal": ""}},
            {"op": "upsert_edge", "edge": {"id": "agent-prompt-edge", "from": "agent-prompt", "to": "agent-1", "kind": "input"}},
            {"op": "upsert_edge", "edge": {"id": "agent-reference-edge", "from": "agent-reference", "to": "agent-1", "kind": "input"}},
            {"op": "upsert_edge", "edge": {"id": "agent-prompt-edge-2", "from": "agent-prompt", "to": "agent-2", "kind": "input"}},
        ],
        base_revision=0,
    )
    store.flush(project["id"])
    return project["id"]


def load_app(root: Path, store: ProjectStore):
    app = harness.load_production_app(root, store)
    module = app.state.issue13_module

    async def fake_agent_image_adapter(request):
        if request.get("kind") != "image":
            raise AssertionError(f"unexpected browser Agent kind: {request}")
        return {"images": [{"type": "b64", "value": base64.b64encode(tiny_png((231, 123, 42))).decode("ascii"), "mime_type": "image/png"}]}

    module.agent_image_adapter = fake_agent_image_adapter
    return app


def launch(browser: Path, server, viewport: tuple[int, int], project_id: str):
    # Reuse the production ASGI bridge, swapping only the injected browser
    # client path.  The bridge still serves the real static canvas and routes.
    width, height = viewport
    import subprocess
    import urllib.parse

    profile = Path(tempfile.mkdtemp(prefix="firefly-issue14-browser-profile-"))
    before = len(server.results)
    query = urllib.parse.urlencode({"project": project_id, "browserTest": "1"})
    url = f"{server.base_url}/static/smart-canvas.html?id={urllib.parse.quote(project_id)}&{query}"
    process = subprocess.Popen([
        str(browser), "--headless=new", "--disable-gpu", "--disable-background-networking",
        "--disable-component-update", "--disable-extensions", "--no-first-run",
        "--no-default-browser-check", "--hide-scrollbars", f"--window-size={width},{height}",
        f"--user-data-dir={profile}", url,
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=ROOT)
    try:
        return server.wait_for_result(before, timeout=60)
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
    # The existing bridge injects the module-level CLIENT_SCRIPT.  Keep this
    # browser contract isolated by swapping it for the Agent client while the
    # server is created; no other browser test runs concurrently in this repo.
    harness.CLIENT_SCRIPT = CLIENT_SCRIPT
    results = []
    with tempfile.TemporaryDirectory(prefix="firefly-issue14-project-library-") as root:
        for index, viewport in enumerate(VIEWPORTS):
            store = ProjectStore(Path(root) / f"projects-{index}", snapshot_delay=0)
            project_id = seed_store(store)
            app = load_app(Path(root) / f"runtime-{index}", store)
            server = harness.ConversationBrowserServer(store, app)
            server.start()
            try:
                result = launch(browser, server, viewport, project_id)
                if not result.get("pass"):
                    raise RuntimeError(json.dumps(result, ensure_ascii=False, indent=2))
                results.append(result)
            finally:
                server.close()
                store.close()
    summary = {"issue": 14, "browser": str(browser), "viewports": VIEWPORTS, "runs": results, "pass": len(results) == len(VIEWPORTS) and all(item.get("pass") for item in results)}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
