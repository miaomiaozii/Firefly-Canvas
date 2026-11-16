"""撤销/重做顶栏按钮浏览器测试：按钮状态同步、撤销/重做/删除往返、redo 栈清理、持久化。

#375 拆成两个独立浏览器会话（各自 store/服务/种子项目）：
  1) undo_redo_browser_client.js —— 既有主套件，不掺 agent 阶段；
  2) undo_agent_undo_client.js —— agent 服务端写入的撤回链（合并落盘/撤回落盘/终局一致）。
合并到单页实测（2026-11-16）会让两阶段的保存动力学互相干扰，末条既有断言拖动。"""
from __future__ import annotations
import sys as _bootstrap_sys
from pathlib import Path as _bootstrap_Path
_REPO_ROOT = str(_bootstrap_Path(__file__).resolve().parents[1])
if _REPO_ROOT not in _bootstrap_sys.path:
    _bootstrap_sys.path.insert(0, _REPO_ROOT)
from tools.process_tree import kill_process_tree

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from firefly_projects import ProjectStore
from tests import conversation_browser as harness

import os as _os
CLIENT_SCRIPT = Path(_os.environ.get('FIREFLY_UNDO_CLIENT') or (ROOT / 'tests' / 'undo_redo_browser_client.js'))
AGENT_CLIENT_SCRIPT = ROOT / 'tests' / 'undo_agent_undo_client.js'
VIEWPORTS = [(1424, 900)]


def seed_store(store: ProjectStore) -> str:
    project = store.create_project('Undo Redo Suite')
    store.apply_mutations(
        project['id'],
        [
            {"op": "upsert_node", "node": {"id": "seed-image", "type": "smart-image", "x": 20, "y": 120, "w": 180, "h": 140, "title": 'Seed Image', "images": []}},
            {"op": "upsert_node", "node": {"id": "seed-prompt", "type": "smart-prompt", "x": 20, "y": 330, "w": 316, "h": 240, "text": 'seed prompt'}},
        ],
        base_revision=0,
    )
    store.flush(project['id'])
    return project['id']


def load_app(root: Path, store: ProjectStore):
    return harness.load_production_app(root, store)


def launch(browser: Path, server, viewport: tuple[int, int], project_id: str):
    width, height = viewport
    import subprocess
    import urllib.parse

    profile = Path(tempfile.mkdtemp(prefix='firefly-undo-redo-profile-'))
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
        kill_process_tree(process)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            kill_process_tree(process)
            process.wait(timeout=5)
        harness.remove_profile(profile)


def main():
    browser = harness.find_browser()
    results = []
    with tempfile.TemporaryDirectory(prefix='firefly-undo-redo-library-') as root:
        for index, viewport in enumerate(VIEWPORTS):
            sessions = [
                ('undo-redo-main', CLIENT_SCRIPT),
                ('undo-redo-agent', AGENT_CLIENT_SCRIPT),
            ]
            for session_index, (session_name, client_script) in enumerate(sessions):
                store = ProjectStore(Path(root) / f'projects-{index}-{session_index}', snapshot_delay=0)
                project_id = seed_store(store)
                app = load_app(Path(root) / f'runtime-{index}-{session_index}', store)
                server = harness.ConversationBrowserServer(store, app, client_script=client_script)
                server.start()
                try:
                    result = launch(browser, server, viewport, project_id)
                finally:
                    server.close()
                result = {**result, 'session': session_name}
                results.append(result)
                print(json.dumps({
                    'session': session_name,
                    'viewport': viewport,
                    'pass': result.get('pass'),
                    'errors': result.get('errors') or [],
                    'assertions': len(result.get('assertions') or []),
                }, ensure_ascii=False))
    summary = {
        'pass': all(item.get('pass') for item in results),
        'failures': [item for item in results if not item.get('pass')],
    }
    print(json.dumps(summary))
    raise SystemExit(0 if summary['pass'] else 1)


if __name__ == '__main__':
    main()
