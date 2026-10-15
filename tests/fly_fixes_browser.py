"""浏览器 e2e 修复验证：T1 扩图 / T4 历史删除 / T5 失败取回。"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import urllib.parse
import base64
from pathlib import Path

# Windows 控制台默认 GBK，断言文本可能含 emoji/中文，统一 UTF-8 输出
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from firefly_projects import ProjectStore
from tests import conversation_browser as harness

CLIENT_SCRIPT = ROOT / 'tests' / 'fly_fixes_browser_client.js'
VIEWPORTS = [(1424, 900)]

CALL_LOG = []


def tiny_png_base64(color=(42, 123, 231)):
    from io import BytesIO
    from PIL import Image
    buf = BytesIO()
    Image.new('RGB', (64, 64), color).save(buf, format='PNG')
    return base64.b64encode(buf.getvalue()).decode('ascii')


def seed_store(store: ProjectStore) -> str:
    project = store.create_project('Fly Fixes E2E')
    png = tiny_png_base64()
    record = store.import_media(project['id'], base64.b64decode(png), 'edit-seed.png', 'image/png')
    asset_id = record['asset_id']
    store.apply_mutations(
        project['id'],
        [
            # T1 扩图编辑节点
            {"op": "upsert_node", "node": {"id": "edit-node", "type": "smart-image", "x": 20, "y": 20, "w": 220, "h": 220, "title": 'Edit Node', "images": [{"asset_id": asset_id, "name": "edit-seed.png", "mime_type": "image/png"}]}},
            # T4 生成节点（成功）
            {"op": "upsert_node", "node": {"id": "gen-prompt1", "type": "smart-prompt", "x": 20, "y": 320, "w": 316, "h": 240, "text": 't4gen prompt one'}},
            {"op": "upsert_node", "node": {"id": "gen-node", "type": "smart-image", "x": 380, "y": 320, "w": 316, "h": 316, "title": 'Gen Node', "images": [], "prompt_node_id": "gen-prompt1", "promptDraftText": "t4gen prompt one", "runSettings": {"engine": "api", "apiKind": "image", "routingMode": "manual", "provider_id": "comfly", "model": "nano-banana-pro", "ratio": "square", "resolution": "1k", "quality": "high", "count": 1}}},
            # T5 生成节点（首次失败）
            {"op": "upsert_node", "node": {"id": "gen-prompt2", "type": "smart-prompt", "x": 20, "y": 700, "w": 316, "h": 240, "text": 't5gen prompt two'}},
            {"op": "upsert_node", "node": {"id": "gen-node2", "type": "smart-image", "x": 380, "y": 700, "w": 316, "h": 316, "title": 'Gen Node 2', "images": [], "prompt_node_id": "gen-prompt2", "promptDraftText": "t5gen prompt two", "runSettings": {"engine": "api", "apiKind": "image", "routingMode": "manual", "provider_id": "comfly", "model": "nano-banana-pro", "ratio": "square", "resolution": "1k", "quality": "high", "count": 1}}},
            # T3 局部编辑节点
            {"op": "upsert_node", "node": {"id": "local-edit-node", "type": "local-edit", "x": 750, "y": 20, "w": 360, "h": 330, "title": '局部编辑', "mode": "modify", "original_asset_id": asset_id, "reference_asset_ids": [], "prompt": "t3 local edit prompt", "localEditInputRoles": {"originalNode": "edit-node", "referenceNodes": [], "promptNode": "", "promptNodes": []}, "localEditActiveRole": "original", "aspect_ratio": "1:1", "generation_size": "1024x1024", "generation_resolution": "1k", "selectionType": "polygon", "selection": [8, 8, 56, 56], "feather_radius": 4, "images": [], "created_at": int(time.time() * 1000)}},
            {"op": "upsert_edge", "edge": {"from": "edit-node", "to": "local-edit-node", "kind": "input", "role": "original"}},
            {"op": "upsert_edge", "edge": {"from": "gen-prompt1", "to": "gen-node", "kind": "flow"}},
            {"op": "upsert_edge", "edge": {"from": "gen-prompt2", "to": "gen-node2", "kind": "flow"}},
        ],
        base_revision=0,
    )
    # T9 独立资产 + 引用节点（与 T1/T3 不共用，避免互扰）
    png_t9 = tiny_png_base64((40, 200, 90))
    record_t9 = store.import_media(project['id'], base64.b64decode(png_t9), 't9-seed.png', 'image/png')
    t9_asset_id = record_t9['asset_id']
    store.apply_mutations(
        project['id'],
        [
            {"op": "upsert_node", "node": {"id": "t9-ref-node", "type": "smart-image", "x": 1200, "y": 20, "w": 220, "h": 220, "title": 'T9 Ref Node', "images": [{"asset_id": t9_asset_id, "name": "t9-seed.png", "mime_type": "image/png"}]}},
        ],
        base_revision=1,
    )
    store.flush(project['id'])
    return project['id']


def load_app(root: Path, store: ProjectStore):
    app = harness.load_production_app(root, store)
    module = app.state.issue13_module
    import sys as _sys
    router_mod = _sys.modules.get('app.routers.api_project_library')

    image_value = tiny_png_base64((200, 80, 60))

    def _prompt_of(args, kwargs):
        return str(kwargs.get('prompt') or (args[0] if args else '') or '')

    async def fake_image(*args, **kwargs):
        prompt = _prompt_of(args, kwargs)
        CALL_LOG.append({'prompt': prompt})
        if 't5gen' in prompt:
            # 首次失败，重试成功
            t5_attempts = [c for c in CALL_LOG if 't5gen' in str(c.get('prompt') or '')]
            if len(t5_attempts) == 1:
                raise RuntimeError('模拟服务端生成失败 T5')
        return {"type": "b64", "value": image_value, "mime_type": "image/png"}, {}

    module.generate_ai_image = fake_image
    router_mod.generate_ai_image = fake_image

    # T3 局部编辑：fake 适配器（不依赖真实生图）
    def fake_local_edit(request):
        from PIL import Image
        from io import BytesIO
        image_data = request.get('image') or b''
        try:
            img = Image.open(BytesIO(image_data)).convert('RGB')
        except Exception:
            img = Image.new('RGB', (128, 128), (240, 160, 40))
        out = BytesIO()
        img.save(out, format='PNG')
        return out.getvalue()

    module.local_edit_adapter = fake_local_edit
    providers = [
        {
            "id": "comfly",
            "name": "Comfy",
            "protocol": "openai",
            "image_request_mode": "openai",
            "enabled": True,
            "base_url": "https://browser-flow.invalid",
            "models": ["nano-banana-pro"],
            "image_models": ["nano-banana-pro"],
            "image_model_capabilities": {
                "nano-banana-pro": {
                    "sizes": ["1024x1024"],
                    "aspect_ratios": ["1:1", "4:3", "3:4", "16:9", "9:16"],
                    "resolutions": ["1k", "2k", "4k"],
                    "max_reference_images": 4,
                }
            },
            "api_key": "browser-key",
        }
    ]
    module.load_api_providers = lambda: providers
    router_mod.load_api_providers = lambda: providers
    module.provider_env_key_value = lambda *_args, **_kwargs: "browser-key"
    router_mod.provider_env_key_value = lambda *_args, **_kwargs: "browser-key"
    module.AI_BASE_URL = "https://browser-flow.invalid"
    return app


def launch(browser: Path, server, viewport: tuple[int, int], project_id: str):
    width, height = viewport
    profile = Path(tempfile.mkdtemp(prefix='firefly-fixes-profile-'))
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
        return server.wait_for_result(before, timeout=240)
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
    with tempfile.TemporaryDirectory(prefix='firefly-fixes-library-') as root:
        for index, viewport in enumerate(VIEWPORTS):
            store = ProjectStore(Path(root) / f'projects-{index}', snapshot_delay=0)
            project_id = seed_store(store)
            app = load_app(Path(root) / f'runtime-{index}', store)
            server = harness.ConversationBrowserServer(store, app, client_script=CLIENT_SCRIPT)
            server.start()
            try:
                result = launch(browser, server, viewport, project_id)
            finally:
                server.close()
            results.append(result)
    summary = {
        'pass': all(item.get('pass') for item in results),
        'failures': [item for item in results if not item.get('pass')],
    }
    for item in results:
        print('PASS:', item.get('pass'))
        for a in (item.get('assertions') or []):
            print('  -', 'OK' if a.get('pass') else 'XX', a.get('name'), (a.get('actual') or '')[:90])
        for e in (item.get('errors') or []):
            print('  !', str(e)[:300])
    print('CALL_LOG prompts:', [str(c.get('prompt'))[:60] for c in CALL_LOG])
    print(json.dumps(summary))
    raise SystemExit(0 if summary['pass'] else 1)


if __name__ == '__main__':
    main()
