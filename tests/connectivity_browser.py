"""节点连通性实测：生图节点模型切换、参数传递（比例/分辨率/质量/参考图/提示词）、生成闭环、其他节点冒烟。

用真实 uvicorn + 自定义中间件：注入 client 脚本、暴露 /__captured_requests 让浏览器
直接读取服务端 fake 适配器捕获到的真实生成请求 payload，逐字段断言传参正确性。
"""
from __future__ import annotations
import base64
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

CLIENT_SCRIPT = ROOT / 'tests' / 'connectivity_browser_client.js'
CLIENT_SCRIPT_URL = '/tests/connectivity_browser_client.js'
HTML_PAGE_PATH = '/static/smart-canvas.html'
RESULTS_PATH = '/__conversation_browser_results'
CAPTURED_PATH = '/__captured_requests'
VIEWPORT = (1424, 900)
RESULT_TIMEOUT = 240

IMAGE_PROVIDERS = [
    {
        'id': 'browser-image',
        'name': 'Browser Image',
        'enabled': True,
        'image_models': ['browser-image-model', 'browser-image-model-2'],
        'chat_models': [],
        'video_models': [],
        'image_model_capabilities': {
            'browser-image-model': {
                'arbitrary_size': True,
                'arbitrary_aspect_ratio': True,
                'arbitrary_resolution': True,
                'max_reference_images': 3,
            },
            'browser-image-model-2': {
                'arbitrary_size': True,
                'arbitrary_aspect_ratio': True,
                'arbitrary_resolution': True,
                'max_reference_images': 3,
            },
        },
    },
]

CAPTURED = []
CAPTURED_LOCK = threading.Lock()


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


async def fake_generate_ai_image(prompt, size=None, quality=None, model=None,
                                 references=None, provider_id=None, *args, **kwargs):
    entry = {
        'prompt': prompt,
        'size': size,
        'quality': quality,
        'model': model,
        'references': [r.get('url') if isinstance(r, dict) else r for r in (references or [])],
        'provider_id': provider_id,
        'extra': {'args_len': len(args), 'kwargs': {k: v for k, v in kwargs.items() if k in ('count', 'n', 'ratio')}},
    }
    with CAPTURED_LOCK:
        CAPTURED.append(entry)
    data = {
        'type': 'b64',
        'value': base64.b64encode(tiny_png(color=(38, 190, 133), size=5)).decode('ascii'),
        'mime_type': 'image/png',
    }
    return data, {'usage': {'total_tokens': 1}}


def seed_store(store: ProjectStore) -> dict:
    project = store.create_project('Connectivity Suite')
    media = store.import_media(project['id'], tiny_png(), 'reference.png', 'image/png')
    store.apply_mutations(
        project['id'],
        [
            {'op': 'upsert_node', 'node': {
                'id': 'img-ref', 'type': 'smart-image',
                'x': 20, 'y': 120, 'w': 180, 'h': 140,
                'images': [{'asset_id': media['asset_id'], 'kind': 'image', 'name': 'reference.png'}],
            }},
            {'op': 'upsert_node', 'node': {
                'id': 'prompt-conn', 'type': 'smart-prompt',
                'x': 20, 'y': 340, 'w': 316, 'h': 240, 'text': 'seed prompt',
            }},
            {'op': 'upsert_node', 'node': {
                'id': 'gen-conn', 'type': 'smart-image',
                'x': 420, 'y': 120, 'w': 260, 'h': 220,
                'images': [],
                'runSettings': {
                    'engine': 'api', 'apiKind': 'image', 'routingMode': 'manual',
                    'provider_id': 'browser-image', 'model': 'browser-image-model',
                    'ratio': 'square', 'resolution': '1k', 'quality': 'auto', 'count': 1,
                },
            }},
            {'op': 'upsert_edge', 'edge': {'from': 'prompt-conn', 'to': 'gen-conn', 'kind': 'smart'}},
            {'op': 'upsert_edge', 'edge': {'from': 'img-ref', 'to': 'gen-conn', 'kind': 'input'}},
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

    def wait_for_role(self, role, timeout=RESULT_TIMEOUT):
        deadline = time.monotonic() + timeout
        with self.condition:
            while True:
                for item in reversed(self.results):
                    if item.get('role') == role:
                        return item
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError(
                        'connectivity browser result timed out; '
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
        if path == CAPTURED_PATH and method == 'GET':
            with CAPTURED_LOCK:
                body = json.dumps(list(CAPTURED), ensure_ascii=False).encode('utf-8')
            headers = [
                (b'content-type', b'application/json'),
                (b'content-length', str(len(body)).encode()),
                (b'cache-control', b'no-store'),
            ]
            await send({'type': 'http.response.start', 'status': 200, 'headers': headers})
            await send({'type': 'http.response.body', 'body': body, 'more_body': False})
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


def load_app(root: Path, store: ProjectStore):
    app = harness.load_production_app(root, store)
    production_main = app.state.issue13_module
    production_main.load_api_providers = lambda: IMAGE_PROVIDERS
    production_main.provider_env_key_value = lambda *args, **kwargs: 'browser-test-key'
    production_main.AI_BASE_URL = 'https://browser.invalid'
    production_main.generate_ai_image = fake_generate_ai_image
    return app


def launch_chrome(browser, base_url, fixture):
    profile = Path(tempfile.mkdtemp(prefix='firefly-connectivity-profile-'))
    query = urllib.parse.urlencode({'project': fixture['project_id'], 'role': 'probe', 'browserTest': '1'})
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


def main():
    browser = harness.find_browser()
    print(f'browser={browser}', flush=True)
    with tempfile.TemporaryDirectory(prefix='firefly-connectivity-') as root:
        store = ProjectStore(Path(root) / 'projects', snapshot_delay=0)
        fixture = seed_store(store)
        app = load_app(Path(root) / 'runtime', store)
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
        proc, profile = launch_chrome(browser, base_url, fixture)
        try:
            result = collector.wait_for_role('probe', timeout=RESULT_TIMEOUT)
            passed = bool(result.get('pass'))
            summary = {
                'suite': 'connectivity',
                'browser': str(browser),
                'passed': passed,
                'result': result,
                'captured_count': len(CAPTURED),
                'captured': list(CAPTURED),
            }
            print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
            return 0 if passed else 1
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
            harness.remove_profile(profile)
            server.should_exit = True
            thread.join(timeout=10)
            store.close()


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print('CONNECTIVITY_FAIL:', repr(exc))
        raise SystemExit(1)
