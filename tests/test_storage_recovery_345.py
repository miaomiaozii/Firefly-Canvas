"""#345 用户裁定：损坏的存储设置只允许进入恢复界面。"""
import json
import os
from pathlib import Path
import subprocess
import sys


PROBE = r'''
from pathlib import Path
from fastapi.testclient import TestClient
from starlette.routing import Match
from starlette.websockets import WebSocketDisconnect
import main
import os

runtime = Path(os.environ['FIREFLY_RUNTIME_ROOT'])
projects = Path(os.environ['FIREFLY_PROJECTS_ROOT'])
base = 'http://127.0.0.1:3198'
with TestClient(main.app, base_url=base, headers={'Origin': base}, raise_server_exceptions=False) as client:
    assert client.get('/api/healthz').status_code == 200
    assert client.get('/static/global-settings.html').status_code == 200
    assert client.get('/api/global-settings/preferences').status_code == 200
    settings = client.get('/api/global-settings')
    assert settings.status_code == 200, settings.text
    assert 'storage_settings.json' in settings.json()['storage_recovery']['message'], settings.text
    diagnostics = client.get('/api/config/data-diagnostics')
    assert diagnostics.status_code == 200 and any('storage_settings.json' in item['file'] for item in diagnostics.json()['items']), diagnostics.text
    for method, path in (
        ('GET', '/api/storage-settings'), ('PATCH', '/api/storage-settings'),
        ('PATCH', '/api/global-settings/preferences'),
        ('POST', '/api/online-image'), ('POST', '/api/canvas-video'),
        ('POST', '/api/project-library/import'), ('POST', '/api/project-library/fake/media/import'),
        ('POST', '/api/shared-folders/import'),
        ('GET', '/api/prompt-libraries'), ('POST', '/api/prompt-libraries'),
        ('GET', '/api/asset-library'),
    ):
        scope = {'type': 'http', 'method': method, 'path': path, 'root_path': ''}
        assert any(route.matches(scope)[0] == Match.FULL for route in main.app.routes), (method, path)
        response = client.request(method, path)
        assert response.status_code == 503 and response.json()['code'] == 'storage_recovery_required', (path, response.status_code, response.text)
    try:
        with client.websocket_connect('/ws/stats'):
            raise AssertionError('恢复状态不允许连接工作 WebSocket')
    except WebSocketDisconnect as exc:
        assert exc.code == 1013, exc

assert not (runtime / 'assets').exists()
assert not (runtime / 'output').exists()
assert list((runtime / 'data').glob('*.json')) == [runtime / 'data/storage_settings.json']
assert not list(projects.iterdir())
'''


def test_corrupt_storage_starts_read_only_and_keeps_original(tmp_path):
    runtime, projects, temp = (tmp_path / name for name in ('runtime', 'projects', 'tmp'))
    for directory in (runtime / 'data', projects, temp):
        directory.mkdir(parents=True)
    settings = runtime / 'data' / 'storage_settings.json'
    damaged = b'{"generated": "broken'
    settings.write_bytes(damaged)
    env = dict(os.environ, FIREFLY_RUNTIME_ROOT=str(runtime), FIREFLY_PROJECTS_ROOT=str(projects),
               TMP=str(temp), TEMP=str(temp), TMPDIR=str(temp), PYTHONUTF8='1')
    result = subprocess.run([sys.executable, '-c', PROBE], cwd=Path(__file__).resolve().parents[1],
                            env=env, capture_output=True, text=True, encoding='utf-8', timeout=40)
    assert result.returncode == 0, result.stdout + result.stderr
    assert settings.read_bytes() == damaged

    # 用户显式恢复有效配置，重启后只使用恢复的路径。
    original = tmp_path / 'original-storage'
    dirs = {key: str(original / key) for key in ('upload', 'generated', 'local')}
    settings.write_text(json.dumps(dirs), encoding='utf-8')
    restored = subprocess.run(
        [sys.executable, '-c',
         "import main; from fastapi.testclient import TestClient; "
         "r = TestClient(main.app, base_url='http://127.0.0.1:3198').get('/api/storage-settings'); "
         "assert r.status_code == 200, r.text; print(r.text)"],
        cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True, text=True,
        encoding='utf-8', timeout=40,
    )
    assert restored.returncode == 0, restored.stdout + restored.stderr
    assert json.loads(restored.stdout.splitlines()[-1])['dirs'] == dirs
    assert all(Path(path).is_dir() for path in dirs.values())
    assert not (runtime / 'assets/output').exists()
