"""Browser test: video generation failure path in the smart canvas.
Seeds a prompt + video node chain, runs the cascade with a fake video adapter
that fails on the first call, then succeeds on retry.
"""
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

from tests import conversation_browser as harness
from tests import canvas_browser_runner_support as runner_support
from firefly_projects import ProjectStore

CLIENT_SCRIPT = ROOT / "tests" / "video_failure_browser_client.js"
def make_fake_mp4() -> str:
    ftyp = b'\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2'
    moov = b'\x00\x00\x00\x08moov' + b'\x00\x00\x00\x08trak' + b'\x00\x00\x00\x08mdia' + b'\x00\x00\x00\x08hdlr' + (b'vide' * 16)
    return base64.b64encode(ftyp + moov).decode('ascii')


VIDEO_B64 = make_fake_mp4()


def tiny_png_base64():
    from io import BytesIO
    from PIL import Image
    buf = BytesIO()
    Image.new('RGB', (64, 64), (42, 123, 231)).save(buf, format='PNG')
    return base64.b64encode(buf.getvalue()).decode('ascii')


def seed_store(store: ProjectStore) -> str:
    project = store.create_project('Video Failure Suite')
    png = tiny_png_base64()
    record = store.import_media(project['id'], base64.b64decode(png), 'seed.png', 'image/png')
    asset_id = record['asset_id']
    store.apply_mutations(
        project['id'],
        [
            {"op": "upsert_node", "node": {"id": "vf-prompt", "type": "smart-prompt", "x": 20, "y": 120, "w": 316, "h": 240, "text": '一只发光的萤火虫在夜空中飞舞'}},
            {"op": "upsert_node", "node": {"id": "vf-source", "type": "smart-image", "x": 380, "y": 120, "w": 180, "h": 140, "title": 'Seed Image', "images": [{"asset_id": asset_id, "name": "seed.png", "mime_type": "image/png"}]}},
            {"op": "upsert_node", "node": {"id": "vf-video", "type": "smart-image", "media_type": "video", "x": 380, "y": 420, "w": 320, "h": 240, "title": 'Output', "images": []}},
            {"op": "upsert_edge", "edge": {"from": "vf-source", "to": "vf-video"}},
            {"op": "upsert_edge", "edge": {"from": "vf-prompt", "to": "vf-video"}},
        ],
        base_revision=0,
    )
    store.flush(project['id'])
    return project['id']


def load_app(root: Path, store: ProjectStore):
    app = harness.load_production_app(root, store)
    module = app.state.issue13_module
    import sys
    router_mod = sys.modules.get('app.routers.api_project_library')
    calls = []
    fail = {"count": 0}

    providers = [
        {
            "id": "comfly",
            "name": "Comfly",
            "protocol": "openai",
            "image_request_mode": "openai",
            "enabled": True,
            "base_url": "https://browser-flow.invalid",
            "models": ["kling-video-v3"],
            "image_models": [],
            "video_models": ["kling-video-v3"],
            "video_model_capabilities": {
                "kling-video-v3": {
                    "media_types": ["video"],
                    "sizes": ["1024x1024", "1280x720", "720x720", "1280x544", "544x1280", "960x720", "720x960"],
                    "aspect_ratios": ["16:9", "9:16", "1:1", "4:3", "3:4", "21:9", "9:21"],
                    "resolutions": ["1k", "2k", "4k"],
                    "max_reference_images": 4,
                    "min_duration": 1,
                    "max_duration": 600,
                }
            },
            "chat_models": [],
            "api_key": "browser-key",
        }
    ]
    module.load_api_providers = lambda: providers
    router_mod.load_api_providers = lambda: providers
    module.provider_env_key_value = lambda *_args, **_kwargs: "browser-key"
    router_mod.provider_env_key_value = lambda *_args, **_kwargs: "browser-key"
    module.AI_BASE_URL = "https://browser-flow.invalid"

    async def fake_video(*_args, **_kwargs):
        calls.append(dict(_kwargs))
        fail["count"] += 1
        if fail["count"] == 1:
            raise RuntimeError("simulated video service failure")
        return [{"type": "b64", "value": VIDEO_B64, "asset_id": "video-asset-ok", "name": "vf-out.mp4", "mime_type": "video/mp4"}], {}

    module.generate_project_video = fake_video
    router_mod.generate_project_video = fake_video
    return app, calls


def launch(browser: Path, server, viewport: tuple[int, int], project_id: str):
    width, height = viewport
    profile = Path(tempfile.mkdtemp(prefix='firefly-videofail-profile-'))
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
    with tempfile.TemporaryDirectory(prefix='firefly-videofail-library-') as root:
        for index, viewport in enumerate([(1424, 900)]):
            store = ProjectStore(Path(root) / f'projects-{index}', snapshot_delay=0)
            project_id = seed_store(store)
            app, calls = load_app(Path(root) / f'runtime-{index}', store)
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
                'fake_calls': len(calls),
            }))
    return runner_support.finish_run('video failure', results)


if __name__ == '__main__':
    raise SystemExit(main())
