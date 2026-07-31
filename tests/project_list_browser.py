"""Exercise project creation-adjacent list, Fly round-trip, and trash UI in Chrome."""

from __future__ import annotations

import io
import json
import sys
import tempfile
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from firefly_projects import ProjectStore
from tests import conversation_browser as harness


CLIENT_SCRIPT = ROOT / "tests" / "project_list_browser_client.js"
VIEWPORTS = [(1408, 710), (500, 749)]


def seed_store(store: ProjectStore) -> str:
    project = store.create_project("Browser lifecycle project")
    store.apply_mutations(
        project["id"],
        [
            {
                "op": "upsert_node",
                "node": {
                    "id": "lifecycle-prompt",
                    "type": "smart-prompt",
                    "x": 80,
                    "y": 100,
                    "w": 316,
                    "h": 240,
                    "text": "Round-trip project content",
                },
            }
        ],
        base_revision=0,
    )
    store.flush(project["id"])
    return project["id"]


def launch(browser: Path, server, viewport: tuple[int, int]):
    import subprocess

    width, height = viewport
    profile = Path(tempfile.mkdtemp(prefix="firefly-project-list-browser-profile-"))
    before = len(server.results)
    url = f"{server.base_url}/static/project-list.html?browserTest=1"
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
        return server.wait_for_result(before, timeout=90)
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
    harness.HTML_PAGE_PATH = "/static/project-list.html"
    harness.CLIENT_SCRIPT_URL = "/tests/project_list_browser_client.js"
    results = []
    with tempfile.TemporaryDirectory(prefix="firefly-project-list-browser-library-") as root_text:
        root = Path(root_text)
        for index, viewport in enumerate(VIEWPORTS):
            store = ProjectStore(root / f"projects-{index}", snapshot_delay=0)
            project_id = seed_store(store)
            app = harness.load_production_app(root / f"runtime-{index}", store)
            module = app.state.issue13_module
            server = harness.ConversationBrowserServer(store, app)
            server.start()
            try:
                result = launch(browser, server, viewport)
                result["pythonChecks"] = {
                    "seedProjectPurgedAfterBrowserFlow": not (root / f"projects-{index}" / project_id).exists(),
                    "trashIsEmptyAfterPurge": not store.list_trashed_projects(),
                }
                result["pass"] = result.get("pass") and all(result["pythonChecks"].values())
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
        "surface": "project-list-lifecycle",
        "browser": str(browser),
        "viewports": VIEWPORTS,
        "runs": results,
        "pass": len(results) == len(VIEWPORTS) and all(item.get("pass") for item in results),
    }
    print(json.dumps(summary, ensure_ascii=True, indent=2))
    return 0 if summary["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
