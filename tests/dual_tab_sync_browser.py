"""双标签页实时同步验证：两个独立 Chrome 进程同开同一画布。

与单进程套件不同，本套件用真实 uvicorn 起应用（ConversationBrowserServer 的
HTTP handler 不支持 WebSocket 升级，前端 /ws/stats 广播在那种环境下不可达）。
客户端脚本禁用 meta poll 兜底，因此跨页同步只可能来自 WS 广播。

测试要点：
- 角色 a 先建节点，等待自身落盘（广播在落盘后发出），再等待 b 的节点经 WS 实时到达
- 角色 b 等待 a 的节点到达后创建自己的节点——此时 b 的 base_revision 必然过期，
  必然走过 409 冲突 + rebase 重试路径
- 终态断言：服务端画布含全部节点且无重复 id、双方 DOM 均可见两页节点、无保存错误
"""
from __future__ import annotations
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault('FIREFLY_TEST_MODE', '1')

import uvicorn
from firefly_projects import ProjectStore
from tests import conversation_browser as harness

CLIENT_SCRIPT = ROOT / 'tests' / 'dual_tab_sync_client.js'
CLIENT_SCRIPT_URL = '/tests/dual_tab_sync_client.js'
HTML_PAGE_PATH = '/static/smart-canvas.html'
RESULTS_PATH = '/__conversation_browser_results'
VIEWPORT = (1424, 900)
RESULT_TIMEOUT = 240


def tiny_png(color=(42, 123, 231), size=16):
    def chunk(kind, data):
        checksum = zlib.crc32(kind + data) & 0xFFFFFFFF
        return (
            len(data).to_bytes(4, 'big') + kind + data
            + checksum.to_bytes(4, 'big')
        )
    width = height = size
    raw = b''.join(
        b'\x00' + bytes([color[0], color[1], color[2], 255]) * width
        for _ in range(height)
    )
    payload = (
        b'\x89PNG\r\n\x1a\n'
        + chunk(b'IHDR', width.to_bytes(4, 'big') + height.to_bytes(4, 'big') + b'\x08\x06\x00\x00\x00')
        + chunk(b'IDAT', zlib.compress(raw))
        + chunk(b'IEND', b'')
    )
    return payload


def seed_store(store: ProjectStore) -> dict:
    project = store.create_project('Dual Tab Sync Suite')
    media = store.import_media(project['id'], tiny_png(), 'reference.png', 'image/png')
    store.apply_mutations(
        project['id'],
        [
            {'op': 'upsert_node', 'node': {
                'id': 'seed-prompt', 'type': 'smart-prompt',
                'x': 20, 'y': 120, 'w': 316, 'h': 240, 'text': 'seed prompt',
            }},
            {'op': 'upsert_node', 'node': {
                'id': 'seed-image', 'type': 'smart-image',
                'x': 20, 'y': 390, 'w': 180, 'h': 140,
                'images': [{'asset_id': media['asset_id'], 'kind': 'image', 'name': 'reference.png'}],
            }},
        ],
        base_revision=0,
    )
    store.flush(project['id'])
    return {'project_id': project['id'], 'canvas_id': project['id']}


class ResultCollector:
    def __init__(self):
        self.results = []
        self.condition = threading.Condition()

    def record(self, payload):
        with self.condition:
            self.results.append(payload)
            self.condition.notify_all()

    def wait_for_roles(self, roles, timeout=RESULT_TIMEOUT):
        deadline = time.monotonic() + timeout
        with self.condition:
            while True:
                found = {r.get('role'): r for r in self.results if r.get('role') in roles}
                if all(role in found for role in roles):
                    return [found[role] for role in roles]
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError(
                        'dual-tab sync browser results timed out; '
                        f'partial={json.dumps(self.results, ensure_ascii=False)[:2000]}'
                    )
                self.condition.wait(remaining)


def build_composed_app(app, collector):
    client_js = CLIENT_SCRIPT.read_bytes()

    async def composed(scope, receive, send):
        if scope['type'] != 'http':
            await app(scope, receive, send)
            return
        method = scope.get('method', 'GET')
        path = scope.get('path', '')
        if path == CLIENT_SCRIPT_URL:
            headers = [
                (b'content-type', b'text/javascript; charset=utf-8'),
                (b'content-length', str(len(client_js)).encode()),
                (b'cache-control', b'no-store'),
            ]
            await send({'type': 'http.response.start', 'status': 200, 'headers': headers})
            await send({'type': 'http.response.body', 'body': client_js, 'more_body': False})
            return
        if path == RESULTS_PATH and method == 'POST':
            chunks = []
            while True:
                message = await receive()
                if message.get('type') != 'http.request':
                    break
                chunks.append(message.get('body', b''))
                if not message.get('more_body'):
                    break
            try:
                payload = json.loads(b''.join(chunks).decode('utf-8'))
                collector.record(payload)
                body = json.dumps({'ok': True}, ensure_ascii=False).encode('utf-8')
                status = 200
            except Exception as exc:
                body = json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=False).encode('utf-8')
                status = 400
            headers = [
                (b'content-type', b'application/json'),
                (b'content-length', str(len(body)).encode()),
                (b'cache-control', b'no-store'),
            ]
            await send({'type': 'http.response.start', 'status': status, 'headers': headers})
            await send({'type': 'http.response.body', 'body': body, 'more_body': False})
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


def launch_chrome(browser, base_url, fixture, role):
    profile = Path(tempfile.mkdtemp(prefix='firefly-dual-tab-profile-'))
    query = urllib.parse.urlencode({'project': fixture['project_id'], 'role': role, 'browserTest': '1'})
    url = (
        f"{base_url}/static/smart-canvas.html?id={urllib.parse.quote(fixture['project_id'])}&{query}"
    )
    process = subprocess.Popen(
        [
            str(browser),
            '--headless=new',
            '--disable-gpu',
            '--disable-background-networking',
            '--disable-component-update',
            '--disable-extensions',
            '--no-first-run',
            '--no-default-browser-check',
            '--hide-scrollbars',
            f'--window-size={VIEWPORT[0]},{VIEWPORT[1]}',
            f'--user-data-dir={profile}',
            url,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=ROOT,
    )
    return process, profile


def main():
    browser = harness.find_browser()
    print(f'browser={browser}', flush=True)
    with tempfile.TemporaryDirectory(prefix='firefly-dual-tab-') as root:
        store = ProjectStore(Path(root) / 'projects', snapshot_delay=0)
        fixture = seed_store(store)
        app = harness.load_production_app(Path(root) / 'runtime', store)
        collector = ResultCollector()
        composed = build_composed_app(app, collector)
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
        proc_a, profile_a = launch_chrome(browser, base_url, fixture, 'a')
        proc_b, profile_b = launch_chrome(browser, base_url, fixture, 'b')
        try:
            result_a, result_b = collector.wait_for_roles(['a', 'b'], timeout=RESULT_TIMEOUT)
            passed = bool(result_a.get('pass')) and bool(result_b.get('pass'))
            summary = {
                'suite': 'dual-tab-sync',
                'browser': str(browser),
                'passed': passed,
                'tab_a': result_a,
                'tab_b': result_b,
            }
            print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
            dump_path = Path(os.environ.get('DUAL_TAB_DUMP', ''))
            if dump_path.is_absolute():
                dump_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
            return 0 if passed else 1
        finally:
            for process in (proc_a, proc_b):
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            harness.remove_profile(profile_a)
            harness.remove_profile(profile_b)
            server.should_exit = True
            thread.join(timeout=10)
            store.close()


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print('DUAL_TAB_SYNC_FAIL:', repr(exc))
        raise SystemExit(1)
