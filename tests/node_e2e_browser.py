"""逐节点可用性浏览器测试：创建 7 种节点 → 连线 → 传参 → 持久化。"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(r'D:\Firefly-Canvas')
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from firefly_projects import ProjectStore
from tests import conversation_browser as harness

CLIENT_SCRIPT = ROOT / 'tests' / 'node_e2e_browser_client.js'
VIEWPORTS = [(1424, 900)]


def seed_store(store: ProjectStore) -> str:
    project = store.create_project('Node E2E Suite')
    store.apply_mutations(
        project['id'],
        [
            {"op": "upsert_node", "node": {"id": "seed-image", "type": "smart-image", "x": 20, "y": 120, "w": 180, "h": 140, "title": 'Seed', "images": []}},
            {"op": "upsert_node", "node": {"id": "seed-prompt", "type": "smart-prompt", "x": 20, "y": 330, "w": 316, "h": 240, "text": 'seed prompt'}},
        ],
        base_revision=0,
    )
    store.flush(project['id'])
    return project['id']


def load_app(root: Path, store: ProjectStore):
    app = harness.load_production_app(root, store)
    return app


def launch(browser: Path, server, viewport: tuple[int, int], project_id: str):
    width, height = viewport
    import subprocess
    import urllib.parse

    profile = Path(tempfile.mkdtemp(prefix='firefly-node-e2e-profile-'))
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
        return server.wait_for_result(before, timeout=120)
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
    with tempfile.TemporaryDirectory(prefix='firefly-node-e2e-library-') as root:
        for index, viewport in enumerate(VIEWPORTS):
            store = ProjectStore(Path(root) / f'projects-{index}', snapshot_delay=0)
            project_id = seed_store(store)
            app = load_app(Path(root) / f'runtime-{index}', store)
            server = harness.ConversationBrowserServer(store, app)
            server.start()
            try:
                result = launch(browser, server, viewport, project_id)
            finally:
                server.close()
            results.append(result)
            print(json.dumps({
                'viewport': viewport,
                'pass': result.get('pass'),
                'errors': result.get('errors') or [],
                'assertions': len(result.get('assertions') or []),
            }))
    summary = {
        'pass': all(item.get('pass') for item in results),
        'failures': [item for item in results if not item.get('pass')],
    }
    print(json.dumps(summary))
    raise SystemExit(0 if summary['pass'] else 1)


if __name__ == '__main__':
    main()
