"""Long browser smoke test for a 500-node canvas backed by real image fixtures."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from firefly_projects import ProjectStore
from tests import conversation_browser as harness


CLIENT_SCRIPT = ROOT / "tests" / "perf500_long_browser_client.js"
FIXTURES = sorted((ROOT / "tests" / "fixtures" / "perf").glob("p-*"))
VIEWPORT = (1408, 805)


def seed_store(store: ProjectStore) -> str:
    project = store.create_project("500 node stress canvas")
    project_id = project["id"]
    assets = []
    for path in FIXTURES:
        mime = "image/jpeg" if path.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
        media = store.import_media(project_id, path.read_bytes(), path.name, mime, {"type": "perf-fixture"})
        assets.append(media["asset_id"])
    if not assets:
        raise RuntimeError("no real image fixtures found")

    nodes = [{
        "id": "perf500-prompt",
        "type": "smart-prompt",
        "x": 60,
        "y": 60,
        "width": 240,
        "height": 80,
        "title": "stress prompt",
        "text": "500 node stress prompt",
    }]
    for index in range(500):
        nodes.append({
            "id": f"perf500-image-{index:03d}",
            "type": "smart-image",
            "x": 40 + (index % 25) * 250,
            "y": 180 + (index // 25) * 190,
            "width": 220,
            "height": 160,
            "title": f"stress image {index}",
            "images": [{
                "asset_id": assets[index % len(assets)],
                "name": FIXTURES[index % len(FIXTURES)].name,
            }],
        })
    store.apply_mutations(project_id, [{"op": "upsert_node", "node": node} for node in nodes], base_revision=0)
    store.flush(project_id)
    return project_id


def launch(browser: Path, server, project_id: str):
    profile = Path(tempfile.mkdtemp(prefix="firefly-perf500-profile-"))
    before = len(server.results)
    query = urllib.parse.urlencode({"project": project_id, "browserTest": "1"})
    url = f"{server.base_url}/static/smart-canvas.html?id={urllib.parse.quote(project_id)}&{query}"
    process = subprocess.Popen(
        [
            str(browser), "--headless=new", "--disable-gpu", "--disable-background-networking",
            "--disable-component-update", "--disable-extensions", "--no-first-run",
            "--no-default-browser-check", "--hide-scrollbars",
            f"--window-size={VIEWPORT[0]},{VIEWPORT[1]}", f"--user-data-dir={profile}", url,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=ROOT,
    )
    try:
        return server.wait_for_result(before, timeout=300)
    finally:
        process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=8)
        harness.remove_profile(profile)


def main() -> int:
    browser = harness.find_browser()
    harness.CLIENT_SCRIPT = CLIENT_SCRIPT
    harness.HTML_PAGE_PATH = "/static/smart-canvas.html"
    harness.CLIENT_SCRIPT_URL = "/tests/perf500_long_browser_client.js"
    with tempfile.TemporaryDirectory(prefix="firefly-perf500-") as root_text:
        root = Path(root_text)
        store = ProjectStore(root / "projects", snapshot_delay=0)
        project_id = seed_store(store)
        app = harness.load_production_app(root / "runtime", store)
        module = app.state.issue13_module
        server = harness.ConversationBrowserServer(store, app)
        server.start()
        try:
            result = launch(browser, server, project_id)
            print(json.dumps({"surface": "perf-500-node", "result": result, "pass": bool(result.get("pass"))}, ensure_ascii=False, indent=2))
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
