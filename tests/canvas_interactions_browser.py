"""画布交互浏览器测试：框选/多选/复制粘贴/删除/undo/缩放/平移/分组。

Direct runner — not collected by unittest discover. Run: python tests/canvas_interactions_browser.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(r'D:\Firefly-Canvas')
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from firefly_projects import ProjectStore
from tests import conversation_browser as harness
from tests import canvas_browser_runner_support as runner_support

CLIENT_SCRIPT = ROOT / 'tests' / 'canvas_interactions_browser_client.js'
VIEWPORTS = [(1424, 900)]


def seed_store(store: ProjectStore) -> str:
    project = store.create_project('Canvas Interactions')
    landscape = store.import_media(
        project['id'],
        harness.image_bytes('LANDSCAPE', (46, 92, 138)),
        'landscape.png',
        'image/png',
        {'type': 'canvas-interactions-browser-fixture', 'name': 'landscape.png'},
    )
    image = {'asset_id': landscape['asset_id'], 'kind': 'image', 'name': 'landscape.png'}
    store.apply_mutations(
        project['id'],
        [
            {"op": "upsert_node", "node": {"id": "seed-prompt", "type": "smart-prompt", "x": 120, "y": 120, "w": 316, "h": 96, "text": 'interaction seed prompt'}},
            {"op": "upsert_node", "node": {"id": "seed-img-a", "type": "smart-image", "x": 560, "y": 120, "w": 316, "h": 194, "images": [dict(image)]}},
            {"op": "upsert_node", "node": {"id": "seed-img-b", "type": "smart-image", "x": 120, "y": 420, "w": 316, "h": 194, "images": [dict(image)]}},
            {"op": "upsert_edge", "edge": {"from": "seed-prompt", "to": "seed-img-a", "kind": "input"}},
        ],
        base_revision=0,
    )
    store.flush(project['id'])
    return project['id']


def load_app(root: Path, store: ProjectStore):
    return harness.load_production_app(root, store)


def launch(browser: Path, server, viewport: tuple[int, int], project_id: str):
    width, height = viewport
    import urllib.parse

    profile = Path(tempfile.mkdtemp(prefix='firefly-ci-profile-'))
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
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
        try:
            import shutil
            shutil.rmtree(profile, ignore_errors=True)
        except Exception:
            pass


def main():
    browser = harness.find_browser()
    harness.CLIENT_SCRIPT = CLIENT_SCRIPT
    results = []
    for viewport in VIEWPORTS:
        root = Path(tempfile.mkdtemp(prefix='firefly-ci-run-'))
        store = ProjectStore(root / 'projects', snapshot_delay=0)
        try:
            project_id = seed_store(store)
            app = load_app(root / 'runtime', store)
            server = harness.ConversationBrowserServer(store, app)
            server.start()
            try:
                result = launch(browser, server, viewport, project_id)
            finally:
                server.close()
        finally:
            try:
                store.flush(project_id)
            except Exception:
                pass
        print(json.dumps(result, ensure_ascii=False))
        results.append(result)
    return runner_support.finish_run('canvas interactions', results)


if __name__ == '__main__':
    sys.exit(main())
