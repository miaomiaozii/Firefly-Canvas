"""#170 运行时回归守卫：Agent 节点的真实路由必须可达。

与 tests/agent_fork_browser.py 等旧套件的区别：旧套件跑在
`tests/conversation_browser.py` 的裸 `http.server` 桥接上（不挂载 FastAPI、不支持
WebSocket），所以任何 `/api/projects/<pid>/agent-nodes/**` 请求在那里都是 404，
路径写错也照样全绿。本 runner 用真实 uvicorn 挂载生产 app（connectivity_browser 同款），
因此：

1. 浏览器里 Agent 节点的 WebSocket 必须真的握手成功并进入 OPEN（服务端 journal 记录
   websocket.accept），并且收到服务端 accept 后必发的 `session` 事件；
2. 应用自己字面量拼出的 HTTP 端点（/model、/autonomy、/fork-messages）必须返回非 404；
3. journal 里不得出现任何含 `canvasState` 的请求路径（#170 的污染签名）。

pi 子进程用假进程替代（`_FakeProcess` + 打桩 spawn_session/get_fork_messages），
所以本测试不需要真实 pi。

用法：
    python tests/agent_node_routes_browser.py [--artifacts-dir tmp/repro170]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault('FIREFLY_TEST_MODE', '1')

import uvicorn  # noqa: E402
from firefly_projects import ProjectStore  # noqa: E402
from tests import conversation_browser as harness  # noqa: E402
from tests.fake_agent_host import STUB_PI_CLI  # noqa: E402

CLIENT_SCRIPT = ROOT / 'tests' / 'agent_node_routes_browser_client.js'
CLIENT_SCRIPT_URL = '/tests/agent_node_routes_browser_client.js'
HTML_PAGE_PATH = '/static/smart-canvas.html'
RESULTS_PATH = '/__conversation_browser_results'
NODE_ID = 'route-agent'
VIEWPORT = (1424, 900)
RESULT_TIMEOUT = 240


class FakeStdin:
    def __init__(self):
        self.lines: list[str] = []

    def write(self, data):
        self.lines.append(str(data))
        return len(data)

    def flush(self):
        return None

    def close(self):
        return None


class FakeProcess:
    """Stand-in for the `pi --mode rpc` subprocess: keeps AgentSession.is_running true."""

    def __init__(self):
        self.stdin = FakeStdin()
        self.returncode = None

    def poll(self):
        return None

    def terminate(self):
        return None

    def kill(self):
        return None

    def wait(self, timeout=None):
        return None


class Journal:
    def __init__(self):
        self.entries: list[dict] = []
        self.lock = threading.Lock()

    def add(self, entry: dict):
        with self.lock:
            self.entries.append(entry)

    def snapshot(self) -> list[dict]:
        with self.lock:
            return list(self.entries)


class ResultCollector:
    def __init__(self):
        self.results: list[dict] = []
        self.condition = threading.Condition()

    def record(self, payload):
        with self.condition:
            self.results.append(payload)
            self.condition.notify_all()

    def wait_for_result(self, timeout=RESULT_TIMEOUT):
        deadline = time.monotonic() + timeout
        with self.condition:
            while True:
                if self.results:
                    return self.results[-1]
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError(
                        'agent route browser result timed out; '
                        f'partial={json.dumps(self.results, ensure_ascii=False)[:2000]}'
                    )
                self.condition.wait(remaining)


def seed_store(store: ProjectStore) -> dict:
    project = store.create_project('Agent Route Guard')
    store.apply_mutations(
        project['id'],
        [{'op': 'upsert_node', 'node': {
            'id': NODE_ID,
            'type': 'smart-agent',
            'x': 470,
            'y': 140,
            'w': 382,
            'h': 360,
            'title': 'Route Guard Agent',
            'agentGoal': '',
        }}],
        base_revision=0,
    )
    store.flush(project['id'])
    return {'project_id': project['id'], 'canvas_id': project['id'], 'selected_text': ''}


def load_app(root: Path, store: ProjectStore):
    app = harness.load_production_app(root, store)
    production_main = app.state.issue13_module
    production_main.sync_static_html_versions = lambda: None

    agent_host_module = sys.modules['app.services.agent_host']
    # #179: belt-and-braces so a stray spawn can never reach the real CLI
    # ("pi" resolves through PATH to pi.cmd on Windows).
    agent_host_module.PI_CLI = str(STUB_PI_CLI)
    manager = agent_host_module.agent_host_manager

    async def fake_spawn(session, model='', autonomy='', continue_session=False):
        session.process = FakeProcess()
        session.status = 'running'
        session.session_id = session.session_id or 'fake-session-route-guard'
        session.session_file = session.session_file or 'fake-session.jsonl'
        return True

    async def fake_fork_messages(session, timeout: float = 10.0):
        return []

    manager.spawn_session = fake_spawn
    manager.get_fork_messages = fake_fork_messages
    return app


def build_composed_app(app, collector: ResultCollector, journal: Journal):
    client_js = CLIENT_SCRIPT.read_bytes()

    async def serve_client_js(scope, send):
        headers = [
            (b'content-type', b'text/javascript; charset=utf-8'),
            (b'content-length', str(len(client_js)).encode()),
            (b'cache-control', b'no-store'),
        ]
        await send({'type': 'http.response.start', 'status': 200, 'headers': headers})
        await send({'type': 'http.response.body', 'body': client_js, 'more_body': False})

    async def read_body(receive):
        chunks = []
        while True:
            message = await receive()
            if message.get('type') != 'http.request':
                break
            chunks.append(message.get('body', b''))
            if not message.get('more_body'):
                break
        return b''.join(chunks)

    async def serve_json(send, payload: dict, status: int = 200):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        headers = [
            (b'content-type', b'application/json'),
            (b'content-length', str(len(body)).encode()),
            (b'cache-control', b'no-store'),
        ]
        await send({'type': 'http.response.start', 'status': status, 'headers': headers})
        await send({'type': 'http.response.body', 'body': body, 'more_body': False})

    async def composed(scope, receive, send):
        path = scope.get('path', '')

        if scope['type'] == 'websocket':
            entry = {'kind': 'websocket', 'path': path, 'accepted': False, 'closed': False}
            journal.add(entry)

            async def ws_send(message):
                if message.get('type') == 'websocket.accept':
                    entry['accepted'] = True
                elif message.get('type') == 'websocket.close':
                    entry['closed'] = True
                await send(message)

            await app(scope, receive, ws_send)
            return

        if scope['type'] != 'http':
            await app(scope, receive, send)
            return

        method = scope.get('method', 'GET')
        if path == CLIENT_SCRIPT_URL and method == 'GET':
            await serve_client_js(scope, send)
            return
        if path == RESULTS_PATH and method == 'POST':
            raw = await read_body(receive)
            try:
                collector.record(json.loads(raw.decode('utf-8')))
                await serve_json(send, {'ok': True})
            except Exception as exc:  # noqa: BLE001
                await serve_json(send, {'ok': False, 'error': str(exc)}, status=400)
            return

        if path.startswith('/api/'):
            entry = {'kind': 'http', 'method': method, 'path': path, 'status': None}
            journal.add(entry)

            async def http_send(message):
                if message.get('type') == 'http.response.start':
                    entry['status'] = message.get('status')
                await send(message)

            await app(scope, receive, http_send)
            return

        if path == HTML_PAGE_PATH:
            body_parts = []
            start = {}

            async def capture_send(message):
                if message.get('type') == 'http.response.start':
                    start['message'] = message
                elif message.get('type') == 'http.response.body':
                    body_parts.append(message.get('body', b''))

            await app(scope, receive, capture_send)
            html = b''.join(body_parts).decode('utf-8', 'replace')
            injection = f'<script src="{CLIENT_SCRIPT_URL}"></script>'
            if start.get('message') and int(start['message'].get('status') or 500) == 200 and '</body>' in html:
                html = html.replace('</body>', f'{injection}</body>', 1)
                body = html.encode('utf-8')
                headers = [
                    (key, value) for key, value in start['message'].get('headers') or []
                    if key.lower() != b'content-length'
                ]
                headers.append((b'content-length', str(len(body)).encode()))
                await send({'type': 'http.response.start', 'status': 200, 'headers': headers})
                await send({'type': 'http.response.body', 'body': body, 'more_body': False})
                return
            await send(start['message'])
            for part in body_parts:
                await send({'type': 'http.response.body', 'body': part, 'more_body': False})
            return

        await app(scope, receive, send)

    return composed


def launch_chrome(browser: Path, base_url: str, fixture: dict):
    profile = Path(tempfile.mkdtemp(prefix='firefly-agent-routes-profile-'))
    query = urllib.parse.urlencode({
        'project': fixture['project_id'],
        'conversationProject': fixture['project_id'],
        'selectedText': '',
        'browserTest': '1',
    })
    url = f"{base_url}/static/smart-canvas.html?id={urllib.parse.quote(fixture['project_id'])}&{query}"
    process = subprocess.Popen(
        [
            str(browser), '--headless=new', '--disable-gpu', '--disable-background-networking',
            '--disable-component-update', '--disable-extensions', '--no-first-run',
            '--no-default-browser-check', '--hide-scrollbars',
            f'--window-size={VIEWPORT[0]},{VIEWPORT[1]}', f'--user-data-dir={profile}', url,
        ],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=ROOT,
    )
    return process, profile


def evaluate(project_id: str, client_result: dict, journal: Journal) -> tuple[bool, list[str]]:
    failures: list[str] = []
    entries = journal.snapshot()

    expected_ws = f'/api/projects/{project_id}/agent-nodes/{NODE_ID}/stream'
    ws_entries = [entry for entry in entries if entry['kind'] == 'websocket']
    if not ws_entries:
        failures.append('浏览器从未发起任何 WebSocket 连接（Agent 节点没有连 /stream？）')
    elif not any(entry['path'] == expected_ws and entry['accepted'] for entry in ws_entries):
        failures.append(
            f'WebSocket 未能在 {expected_ws} 完成握手；实际观测：'
            + json.dumps([{'path': e['path'], 'accepted': e['accepted']} for e in ws_entries], ensure_ascii=False)
        )

    polluted = [entry for entry in entries if 'canvasState' in entry['path']]
    if polluted:
        failures.append(
            '请求路径被状态容器改名污染（#170 签名）：'
            + json.dumps([e['path'] for e in polluted], ensure_ascii=False)
        )

    agent_http = [
        entry for entry in entries
        if entry['kind'] == 'http' and entry['path'].startswith(f'/api/projects/{project_id}/agent-')
    ]
    not_found = [entry for entry in agent_http if entry.get('status') == 404]
    if not_found:
        failures.append(
            'Agent 端点返回 404：'
            + json.dumps([{'method': e['method'], 'path': e['path']} for e in not_found], ensure_ascii=False)
        )

    for action in ('model', 'autonomy', 'fork-messages'):
        expected_path = f'/api/projects/{project_id}/agent-nodes/{NODE_ID}/{action}'
        matched = [entry for entry in agent_http if entry['path'] == expected_path]
        if not matched:
            failures.append(f'应用没有请求 {expected_path}（前端字面量拼错了路径？）')
        elif any(entry.get('status') == 404 for entry in matched):
            failures.append(f'{expected_path} 返回 404')

    if not client_result.get('pass'):
        failed_assertions = [
            item for item in client_result.get('assertions') or [] if not item.get('ok')
        ]
        failures.append(
            '浏览器侧断言失败：'
            + json.dumps({'assertions': failed_assertions, 'errors': client_result.get('errors')}, ensure_ascii=False)
        )

    return (not failures, failures)


def main() -> int:
    parser = argparse.ArgumentParser(description='#170 agent node route runtime regression guard')
    parser.add_argument('--artifacts-dir', default=None, help='把 journal / 浏览器断言 / 汇总写到该目录')
    args = parser.parse_args()

    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:  # noqa: BLE001
        pass

    browser = harness.find_browser()
    print(f'browser={browser}', flush=True)

    with tempfile.TemporaryDirectory(prefix='firefly-agent-routes-') as root:
        store = ProjectStore(Path(root) / 'projects', snapshot_delay=0)
        fixture = seed_store(store)
        app = load_app(Path(root) / 'runtime', store)
        collector = ResultCollector()
        journal = Journal()
        composed = build_composed_app(app, collector, journal)
        config = uvicorn.Config(composed, host='127.0.0.1', port=0, log_level='error')
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        for _ in range(200):
            if server.started and server.servers:
                break
            time.sleep(0.05)
        if not server.started:
            raise RuntimeError('uvicorn failed to start')
        port = server.servers[0].sockets[0].getsockname()[1]
        base_url = f'http://127.0.0.1:{port}'
        print(f'seeded {fixture["project_id"]} on {base_url}', flush=True)

        process, profile = launch_chrome(browser, base_url, fixture)
        try:
            client_result = collector.wait_for_result(timeout=RESULT_TIMEOUT)
            time.sleep(1.0)
            ok, failures = evaluate(fixture['project_id'], client_result, journal)
            summary = {
                'suite': 'agent_node_routes',
                'issue': 170,
                'browser': str(browser),
                'passed': ok,
                'failures': failures,
                'assertions': client_result.get('assertions'),
                'websocket_requests': [e for e in journal.snapshot() if e['kind'] == 'websocket'],
                'agent_http_requests': [
                    e for e in journal.snapshot()
                    if e['kind'] == 'http' and e['path'].startswith(f'/api/projects/{fixture["project_id"]}/agent-')
                ],
            }
            print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
            if args.artifacts_dir:
                artifacts = Path(args.artifacts_dir)
                artifacts.mkdir(parents=True, exist_ok=True)
                (artifacts / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
                (artifacts / 'journal.json').write_text(json.dumps(journal.snapshot(), ensure_ascii=False, indent=2), encoding='utf-8')
                (artifacts / 'client-result.json').write_text(json.dumps(client_result, ensure_ascii=False, indent=2), encoding='utf-8')
                print(f'artifacts={artifacts.resolve()}', flush=True)
            if not ok:
                print('AGENT_ROUTE_GUARD_FAIL:', ' | '.join(failures), flush=True)
            return 0 if ok else 1
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            harness.remove_profile(profile)
            server.should_exit = True
            thread.join(timeout=10)
            store.close()


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print('AGENT_ROUTE_GUARD_FAIL:', repr(exc), flush=True)
        raise SystemExit(1)
