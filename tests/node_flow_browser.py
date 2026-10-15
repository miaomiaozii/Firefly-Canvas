"""逐节点传参闭环浏览器测试：prompt 文本传递、节点级设置覆盖、上游输出作为下游 refs、adapter 参数捕获。"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import urllib.parse
import base64
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from firefly_projects import ProjectStore
from tests import conversation_browser as harness

CLIENT_SCRIPT = ROOT / 'tests' / 'node_flow_browser_client.js'
VIEWPORTS = [(1424, 900)]

CAPTURED = []


def tiny_png_base64():
    import base64
    from io import BytesIO
    from PIL import Image
    buf = BytesIO()
    Image.new('RGB', (64, 64), (42, 123, 231)).save(buf, format='PNG')
    return base64.b64encode(buf.getvalue()).decode('ascii')


def seed_store(store: ProjectStore) -> str:
    project = store.create_project('Node Flow Suite')
    png = tiny_png_base64()
    record = store.import_media(project['id'], base64.b64decode(png), 'seed.png', 'image/png')
    asset_id = record['asset_id']
    store.apply_mutations(
        project['id'],
        [
            {"op": "upsert_node", "node": {"id": "seed-prompt", "type": "smart-prompt", "x": 20, "y": 120, "w": 316, "h": 240, "text": 'seed prompt'}},
            {"op": "upsert_node", "node": {"id": "seed-image", "type": "smart-image", "x": 380, "y": 120, "w": 180, "h": 140, "title": 'Seed Image', "images": [{"asset_id": asset_id, "name": "seed.png", "mime_type": "image/png"}]}},
            {"op": "upsert_node", "node": {"id": "smart-prompt", "type": "smart-prompt", "x": 20, "y": 420, "w": 316, "h": 240, "text": ''}},
            {"op": "upsert_node", "node": {"id": "smart-image", "type": "smart-image", "x": 380, "y": 420, "w": 316, "h": 316, "title": 'Output', "images": []}},
        ],
        base_revision=0,
    )
    store.flush(project['id'])
    return project['id']


def load_app(root: Path, store: ProjectStore):
    app = harness.load_production_app(root, store)
    module = app.state.issue13_module
    image_value = tiny_png_base64()

    async def fake_image(*_args, **_kwargs):
        CAPTURED.append(dict(_kwargs))
        return {"type": "b64", "value": image_value, "mime_type": "image/png"}, {}

    module.generate_ai_image = fake_image
    module.load_api_providers = lambda: [
        {
            "id": "comfly",
            "name": "Comfy",
            "protocol": "openai",
            "image_request_mode": "openai",
            "enabled": True,
            "base_url": "https://browser-flow.invalid",
            "models": ["nano-banana-pro"],
            "image_models": ["nano-banana-pro"],
            "api_key": "browser-key",
        }
    ]
    module.provider_env_key_value = lambda *_args, **_kwargs: "browser-key"
    module.AI_BASE_URL = "https://browser-flow.invalid"
    return app


def launch(browser: Path, server, viewport: tuple[int, int], project_id: str):
    width, height = viewport
    profile = Path(tempfile.mkdtemp(prefix='firefly-node-flow-profile-'))
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
    with tempfile.TemporaryDirectory(prefix='firefly-node-flow-library-') as root:
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
                'captured_calls': len(CAPTURED),
            }))
            for call in CAPTURED:
                summary = {k: (v[:60] + '...' if isinstance(v, str) and len(v) > 60 else v) for k, v in call.items()}
                print('CAPTURED:', json.dumps(summary, ensure_ascii=False))
    summary = {
        'pass': all(item.get('pass') for item in results),
        'failures': [item for item in results if not item.get('pass')],
    }
    print(json.dumps(summary))
    raise SystemExit(0 if summary['pass'] else 1)


if __name__ == '__main__':
    main()
