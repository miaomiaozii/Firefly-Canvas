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
from tools.browser_process import browser_popen
from tools.process_tree import kill_process_tree

import json
import os
import sys
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from firefly_projects import ProjectStore
from tests import conversation_browser as harness

CLIENT_SCRIPT = Path(os.environ.get('FIREFLY_UNDO_CLIENT') or (ROOT / 'tests' / 'undo_redo_browser_client.js'))
AGENT_CLIENT_SCRIPT = ROOT / 'tests' / 'undo_agent_undo_client.js'
VIEWPORTS = [(1424, 900)]
PORT = 3115
RUNTIME_ROOT = Path(os.environ['FIREFLY_RUNTIME_ROOT']).resolve() if os.environ.get('FIREFLY_RUNTIME_ROOT') else None
PROJECTS_ROOT = Path(os.environ['FIREFLY_PROJECTS_ROOT']).resolve() if os.environ.get('FIREFLY_PROJECTS_ROOT') else None
if (RUNTIME_ROOT is None) != (PROJECTS_ROOT is None):
    raise RuntimeError('Both FIREFLY_RUNTIME_ROOT and FIREFLY_PROJECTS_ROOT must be set together')


def seed_store(store: ProjectStore, *, agent: bool = False) -> str:
    # ProjectStore generates UUID-only ids; prefix just this fixture's allocation.
    project_id = f'e2e-tabbit-undo-{uuid.uuid4().hex}'
    with patch('firefly_projects.uuid.uuid4', return_value=SimpleNamespace(hex=project_id)):
        project = store.create_project('e2e-tabbit-Undo Redo Suite')
    store.apply_mutations(
        project['id'],
        [
            {"op": "upsert_node", "node": {"id": "seed-image", "type": "smart-image", "x": 20, "y": 120, "w": 180, "h": 140, "title": 'Seed Image', "images": []}},
            {"op": "upsert_node", "node": {"id": "seed-prompt", "type": "smart-prompt", "x": 20, "y": 330, "w": 316, "h": 240, "text": 'seed prompt'}},
            *([{"op": "upsert_node", "node": {"id": "seed-agent", "type": "smart-agent", "x": 1100, "y": 600, "w": 382, "h": 360, "title": 'Seed Agent', "agentGoal": ''}}] if agent else []),
        ],
        base_revision=0,
    )
    store.flush(project['id'])
    return project['id']


def load_app(root: Path, store: ProjectStore):
    # Rendering the seed Agent queries chat-models; never discover the user's Pi providers.
    os.environ['FIREFLY_PI_AGENT_DIR'] = str((root / 'pi-agent').resolve())
    app = harness.load_production_app(root, store, runtime_root=RUNTIME_ROOT, projects_root=PROJECTS_ROOT)
    production = app.state.issue13_module
    actual_runtime = Path(production.RUNTIME_ROOT).resolve()
    actual_projects = production.FIREFLY_PROJECT_STORE.root.resolve()
    if actual_runtime != (RUNTIME_ROOT or app.state.issue13_runtime_root) or actual_projects != store.root.resolve():
        raise AssertionError(f'Unexpected production roots: {actual_runtime}, {actual_projects}')
    if PROJECTS_ROOT is not None and actual_projects != PROJECTS_ROOT:
        raise AssertionError(f'Unexpected project library: {actual_projects}')
    print(json.dumps({'runtime_root': str(actual_runtime), 'projects_root': str(actual_projects), 'pi_agent_dir': os.environ['FIREFLY_PI_AGENT_DIR']}, ensure_ascii=False), flush=True)
    return app


def launch(browser: Path, server, viewport: tuple[int, int], project_id: str):
    width, height = viewport
    import subprocess
    import urllib.parse

    profile = Path(tempfile.mkdtemp(prefix='e2e-tabbit-undo-profile-', dir=RUNTIME_ROOT.parent if RUNTIME_ROOT else None))
    before = len(server.results)
    query = urllib.parse.urlencode({'project': project_id, 'browserTest': '1'})
    url = f'{server.base_url}/static/smart-canvas.html?id={urllib.parse.quote(project_id)}&{query}'
    process = browser_popen([
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
    print(f'browser={browser}', flush=True)
    with tempfile.TemporaryDirectory(prefix='e2e-tabbit-undo-library-', dir=RUNTIME_ROOT.parent if RUNTIME_ROOT else None) as root:
        for index, viewport in enumerate(VIEWPORTS):
            sessions = [
                ('undo-redo-main', CLIENT_SCRIPT),
                ('undo-redo-agent', AGENT_CLIENT_SCRIPT),
            ]
            for session_index, (session_name, client_script) in enumerate(sessions):
                store = ProjectStore(PROJECTS_ROOT or Path(root) / f'projects-{index}-{session_index}', snapshot_delay=0)
                try:
                    project_id = seed_store(store, agent=session_name == 'undo-redo-agent')
                    app = load_app(Path(root) / f'runtime-{index}-{session_index}', store)
                    server = harness.ConversationBrowserServer(store, app, client_script=client_script, port=PORT)
                    server.start()
                    try:
                        if server.httpd.server_address != ('127.0.0.1', PORT):
                            raise AssertionError(f'Unexpected listener: {server.httpd.server_address}')
                        print(f'{session_name}: {server.base_url} project={project_id} client={client_script}', flush=True)
                        result = launch(browser, server, viewport, project_id)
                    finally:
                        server.close()
                finally:
                    store.close()
                result = {**result, 'session': session_name, 'project_id': project_id, 'client_script': str(client_script)}
                results.append(result)
                print(json.dumps({
                    'session': session_name,
                    'viewport': viewport,
                    'pass': result.get('pass'),
                    'errors': result.get('errors') or [],
                    'assertions': len(result.get('assertions') or []),
                }, ensure_ascii=False))
    summary = {
        'issue': 375,
        'browser': str(browser),
        'base_url': f'http://127.0.0.1:{PORT}',
        'runtime_root': str(RUNTIME_ROOT) if RUNTIME_ROOT else None,
        'projects_root': str(PROJECTS_ROOT) if PROJECTS_ROOT else None,
        'runs': results,
        'pass': len(results) == len(VIEWPORTS) * 2 and all(item.get('pass') and item.get('assertions') for item in results),
        'failures': [item for item in results if not item.get('pass')],
    }
    print(json.dumps(summary))
    raise SystemExit(0 if summary['pass'] else 1)


if __name__ == '__main__':
    main()
