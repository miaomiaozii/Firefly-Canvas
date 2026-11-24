"""Export offline, restarted ProjectStore/ResultDownloads DTOs and exercise the real history bindings."""
import asyncio
import json
import os
import subprocess
import uuid
from pathlib import Path
from types import SimpleNamespace

from firefly_projects import ProjectStore


def test_history_cells_with_restarted_canonical_dto(tmp_path, monkeypatch):
    native_appdata = {key: os.environ.get(key) for key in ('APPDATA', 'LOCALAPPDATA')}
    required = 'HOME USERPROFILE APPDATA LOCALAPPDATA TMP TEMP TMPDIR PI_CODING_AGENT_DIR PI_CODING_AGENT_SESSION_DIR CODEX_HOME XDG_CONFIG_HOME XDG_CACHE_HOME XDG_DATA_HOME XDG_STATE_HOME FIREFLY_RUNTIME_ROOT FIREFLY_PROJECTS_ROOT'.split()
    # 隐私隔离属于测试本身，不依赖启动器。
    for key in required:
        private_root = tmp_path / key.lower()
        private_root.mkdir()
        monkeypatch.setenv(key, str(private_root))
    assert all(os.environ.get(key) for key in required)
    from app.services.result_downloads import ResultDownloads
    store = ProjectStore(tmp_path / 'projects', snapshot_delay=0)
    host = SimpleNamespace(FIREFLY_PROJECT_STORE=store, api_headers=lambda **kw: {},
                           project_store_call=lambda method, *a, **kw: getattr(store, method)(*a, **kw))
    queue = ResultDownloads(store.root, host)
    cases = []
    try:
        for saved in (0, 2, 3):
            project = store.create_project('Offline history cells')['id']
            store.apply_mutations(project, [{'op': 'upsert_node', 'node': {'id': 'result', 'type': 'smart-image', 'images': []}}], base_revision=0)
            store.begin_image_generation(project, 'result', {'taskId': 'offline-task', 'count': 4}, base_revision=1)
            history = {'id': 'generation-' + uuid.uuid4().hex, 'task_id': 'offline-task', 'result_node_id': 'result',
                       'actual_model': 'offline-model', 'prompt': '<user prompt> & title', 'outputs': []}
            asyncio.run(queue.checkpoint(project, history,
                [{'type': 'url', 'value': f'https://example.invalid/{i}.png'} for i in range(4)], {},
                {'id': 'offline-provider', 'protocol': 'openai', 'base_url': 'https://example.invalid/v1'}))
            q = queue.load(history['id'])
            for index, item in enumerate(q['items']):
                item['status'] = 'unavailable' if saved == 3 else 'failed'
                if index < saved:
                    media = store.import_media(project, b'offline fixture ' + bytes([index]), f'saved-{index}.png', 'image/png')
                    item['output'] = {'asset_id': media['asset_id'], 'kind': 'image', 'output_id': item['output_id'], 'output_order': index}
                    item['status'] = 'succeeded'
            q['status'] = 'download_failed'
            queue.save(q)
            dto = queue.public(q)
            store.publish_image_result_download(project, 'result', 'offline-task', history['id'], q['generation'],
                [item['output'] for item in q['items'] if item.get('output')], dto)
            store.flush(project)
            restarted = ProjectStore(store.root, snapshot_delay=0)
            try:
                canonical = restarted.open_project(project)['canvas']
                assert len(canonical['history'][0]['outputs']) == saved
                cases.append({'canvas': canonical, 'listing': {'groups': [dto]}, 'saved': saved})
            finally:
                restarted.close()
    finally:
        store.close()
    target = Path(os.environ.get('FIREFLY_HISTORY_EVIDENCE', str(tmp_path))) / 'history-canonical.json'
    target.write_text(json.dumps(cases), encoding='utf-8')
    env = {**os.environ, 'FIREFLY_HISTORY_CANONICAL': str(target)}
    # Node 在 Windows 上依赖原生 AppData 初始化；仅子进程保留原值，Python 继续私有隔离。
    for key, original in native_appdata.items():
        if original is None:
            env.pop(key, None)
        else:
            env[key] = original
    result = subprocess.run(['node', '--test', 'tests/canvas_module_units/test-history-cells-final-493.mjs'],
                            cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True, text=True, encoding='utf-8')
    target.with_suffix('.node.txt').write_text(result.stdout + result.stderr, encoding='utf-8')
    assert result.returncode == 0, result.stdout + result.stderr
