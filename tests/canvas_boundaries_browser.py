"""画布边界浏览器测试：超长文本渲染/编辑、undo 栈深度压力、空文本、超大画布 JSON。

Direct runner — not collected by unittest discover. Run: python tests/canvas_boundaries_browser.py
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import urllib.parse
from pathlib import Path

ROOT = Path(r'D:\Firefly-Canvas')
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from firefly_projects import ProjectStore
from tests import conversation_browser as harness

CLIENT_SCRIPT = ROOT / 'tests' / 'canvas_boundaries_browser_client.js'
VIEWPORTS = [(1424, 900)]


def seed_store(store: ProjectStore) -> str:
    project = store.create_project('Canvas Boundaries')
    big_text = ('这段是超长提示词，用于验证画布在极端文本输入下的渲染与持久化稳定性。' * 60) + ('\n新段落 ' + 'x' * 200) * 5
    store.apply_mutations(
        project['id'],
        [
            {"op": "upsert_node", "node": {"id": "seed-big", "type": "smart-prompt", "x": 100, "y": 100, "w": 316, "h": 200, "text": big_text}},
            {"op": "upsert_node", "node": {"id": "seed-empty", "type": "smart-prompt", "x": 560, "y": 100, "w": 316, "h": 96, "text": ""}},
            {"op": "upsert_node", "node": {"id": "seed-img", "type": "smart-image", "x": 100, "y": 420, "w": 316, "h": 194, "images": []}},
            {"op": "upsert_edge", "edge": {"from": "seed-big", "to": "seed-img", "kind": "input"}},
        ],
        base_revision=0,
    )
    store.flush(project['id'])
    return project['id']


def load_app(root: Path, store: ProjectStore):
    return harness.load_production_app(root, store)


def launch(browser: Path, server, viewport: tuple[int, int], project_id: str):
    width, height = viewport
    profile = Path(tempfile.mkdtemp(prefix='firefly-boundaries-profile-'))
    before = len(server.results)
    query = urllib.parse.urlencode({'project': project_id, 'browserTest': '1'})
    url = f'{server.base_url}/static/smart-canvas.html?id={urllib.parse.quote(project_id)}&{query}'
    process = subprocess.Popen([
        str(browser), '--headless=new', '--disable-gpu', '--disable-background-networking',
        '--disable-component-update', '--disable-extensions', '--no-first-run',
        '--no-default-browser-check', '--hide-scrollbars', f'--window-size={width},{height}',
        f'--user-data-dir={profile}', url,
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=ROOT)
    try:
        return server.wait_for_result(before, timeout=180)
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
        harness.remove_profile(profile)


def main():
    browser = harness.find_browser()
    with tempfile.TemporaryDirectory(prefix='canvas-boundaries-') as root:
        store = ProjectStore(Path(root) / 'projects', snapshot_delay=0)
        project_id = seed_store(store)
        app = load_app(Path(root) / 'runtime', store)
        server = harness.ConversationBrowserServer(store, app)
        harness.CLIENT_SCRIPT = CLIENT_SCRIPT
        server.start()
        try:
            for viewport in VIEWPORTS:
                result = launch(browser, server, viewport, project_id)
                assert result is not None, f'no result for {viewport}'
                print(f'{viewport} PASS: {result.get("assertions")}/{result.get("total")} assertions')
        finally:
            server.close()


if __name__ == '__main__':
    main()
