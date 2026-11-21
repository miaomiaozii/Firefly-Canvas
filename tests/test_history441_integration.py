import asyncio
import base64
import io
import json
import subprocess
import threading
import time
from pathlib import Path

import pytest
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

import main
from firefly_projects import ProjectStore


HISTORY441_PROVIDER = {
    'id': 'history441-external', 'name': '零付费测试服务商', 'enabled': True,
    'protocol': 'openai', 'base_url': 'https://provider.invalid/v1',
    'image_models': ['history441-image'],
    'image_model_capabilities': {'history441-image': {
        'sizes': ['1024x1024'], 'aspect_ratios': ['1:1'],
        'resolutions': ['1k'], 'max_reference_images': 0,
    }},
}


def prefix_history441_png():
    buffer = io.BytesIO()
    Image.new('RGB', (8, 6), (232, 163, 58)).save(buffer, format='PNG')
    return base64.b64encode(buffer.getvalue()).decode('ascii')


def prefix_history441_wait(client, url):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        response = client.get(url)
        assert response.status_code == 200, response.text
        task = response.json()
        if task['status'] in {'succeeded', 'failed', 'cancelled'}:
            return task
        time.sleep(0.02)
    raise AssertionError(f'任务未结束: {task}')


def test_history441_actual_api_persists_task_records_and_retry_group(tmp_path):
    root = tmp_path / 'projects'
    store = ProjectStore(root, snapshot_delay=0)
    previous = main.FIREFLY_PROJECT_STORE
    main.FIREFLY_PROJECT_STORE = store
    attempts = 0

    async def prefix_history441_provider(prompt, *_args, **_kwargs):
        nonlocal attempts
        if prompt == '失败后显式重试':
            attempts += 1
            if attempts == 1:
                raise RuntimeError('history441 服务商拒绝')
        png = prefix_history441_png()
        return {'type': 'b64', 'value': png, 'mime_type': 'image/png'}, {'data': [{'b64_json': png}]}

    try:
        with TestClient(main.app, base_url='http://127.0.0.1', headers={'origin': 'http://127.0.0.1'}) as client, \
                patch.object(main, 'load_api_providers', return_value=[HISTORY441_PROVIDER]), \
                patch.object(main, 'generate_ai_image', side_effect=prefix_history441_provider):
            project = client.post('/api/project-library', json={'name': 'history441 混合任务'}).json()['project']['id']
            base = f'/api/project-library/{project}'
            response = client.patch(f'{base}/canvas', json={'base_revision': 0, 'mutations': [
                {'op': 'upsert_node', 'node': {'id': 'result', 'type': 'smart-image', 'x': 0, 'y': 0, 'images': []}},
            ]})
            assert response.status_code == 200, response.text

            def prefix_history441_submit(prompt, key):
                revision = client.get(base).json()['canvas']['revision']
                response = client.post(f'{base}/image-generation-tasks', json={
                    'prompt': prompt, 'provider_id': HISTORY441_PROVIDER['id'], 'model': 'history441-image',
                    'routing_mode': 'manual', 'result_node_id': 'result', 'base_revision': revision,
                    'idempotency_key': key, 'run_id': 'history441-shared-run',
                })
                assert response.status_code == 202, response.text
                return response.json()['task_id']

            failed_id = prefix_history441_submit('失败后显式重试', 'history441-failure')
            failed_url = f'{base}/image-generation-tasks/{failed_id}'
            failed = prefix_history441_wait(client, failed_url)
            assert failed['status'] == 'failed'
            success_id = prefix_history441_submit('成功输出', 'history441-success')
            success = prefix_history441_wait(client, f'{base}/image-generation-tasks/{success_id}')
            assert success['status'] == 'succeeded'
            history = client.get(base).json()['canvas']['history']
            assert len(history) == 2
            records = {record['task_id']: record for record in history}
            assert set(records) == {failed_id, success_id}
            assert {record.get('run_id') for record in history} == {'history441-shared-run'}
            assert records[failed_id]['error'] == 'history441 服务商拒绝'
            assert records[failed_id]['outputs'] == []
            output = records[success_id]['outputs'][0]
            assert output['output_id'] and output['asset_id']

            retry_response = client.post(f'{failed_url}/retry')
            assert retry_response.status_code == 202, retry_response.text
            retry_id = retry_response.json()['task_id']
            assert retry_id not in {failed_id, success_id}
            repeated = client.post(f'{failed_url}/retry')
            assert repeated.json()['task_id'] == retry_id
            retried = prefix_history441_wait(client, f'{base}/image-generation-tasks/{retry_id}')
            assert retried['status'] == 'succeeded'
            assert attempts == 2
            assert client.post(f'{base}/flush').status_code == 200
            store.close()
            store = ProjectStore(root, snapshot_delay=0)
            main.FIREFLY_PROJECT_STORE = store
            reloaded = client.get(base).json()['canvas']
            records = {record['task_id']: record for record in reloaded['history']}
            assert set(records) == {failed_id, success_id, retry_id}
            assert {record.get('run_id') for record in records.values()} == {'history441-shared-run'}
            assert records[failed_id]['status'] == 'failed'
            assert records[success_id]['outputs'][0] == output
            assert records[retry_id]['outputs'][0]['output_id'] != output['output_id']
            assert {item['output_id'] for item in reloaded['nodes'][0]['images']} == {
                output['output_id'], records[retry_id]['outputs'][0]['output_id'],
            }
    finally:
        store.close()
        main.FIREFLY_PROJECT_STORE = previous


def test_history441_legacy_unscoped_history_replay_keeps_idempotency(tmp_path):
    store = ProjectStore(tmp_path / 'projects', snapshot_delay=0)
    previous = main.FIREFLY_PROJECT_STORE
    main.FIREFLY_PROJECT_STORE = store
    calls = []

    async def prefix_history441_unexpected_provider(*args, **_kwargs):
        calls.append(args)
        raise AssertionError('历史重放不能调用服务商')

    try:
        with TestClient(main.app, base_url='http://127.0.0.1', headers={'origin': 'http://127.0.0.1'}) as client, \
                patch.object(main, 'load_api_providers', return_value=[HISTORY441_PROVIDER]), \
                patch.object(main, 'generate_ai_image', side_effect=prefix_history441_unexpected_provider):
            project = client.post('/api/project-library', json={'name': 'history441 旧无分组请求'}).json()['project']['id']
            base = f'/api/project-library/{project}'
            request = {'prompt': '历史请求', 'provider_id': HISTORY441_PROVIDER['id'],
                       'model': 'history441-image', 'routing_mode': 'manual', 'result_node_id': 'result',
                       'base_revision': 0, 'idempotency_key': 'history441-old-key'}
            record = {'id': 'history441-old-record', 'task_id': 'project_img_history441_legacy',
                      'idempotency_key': request['idempotency_key'], 'status': 'failed', 'outputs': [],
                      'result_node_id': 'result', 'error': '历史失败',
                      'request_payload': main.ProjectImageGenerationRequest(**request).model_dump(exclude={'run_id'})}
            response = client.patch(base + '/canvas', json={'base_revision': 0, 'mutations': [
                {'op': 'upsert_node', 'node': {'id': 'result', 'type': 'smart-image', 'x': 0, 'y': 0, 'images': []}},
                {'op': 'set_canvas_meta', 'history': [record]},
            ]})
            assert response.status_code == 200, response.text
            assert client.post(base + '/flush').status_code == 200
            store.close()
            store = ProjectStore(tmp_path / 'projects', snapshot_delay=0)
            main.FIREFLY_PROJECT_STORE = store
            response = client.post(base + '/image-generation-tasks', json=request)
            assert response.status_code == 202, response.text
            assert response.json()['task_id'] == 'project_img_history441_legacy'
            conflict = client.post(base + '/image-generation-tasks', json={**request, 'run_id': 'different-run'})
            assert conflict.status_code == 409
            assert conflict.json()['detail']['code'] == 'image_generation_idempotency_conflict'
            assert calls == []
            history = client.get(base).json()['canvas']['history']
            assert len(history) == 1
            assert 'run_id' not in history[0]
    finally:
        store.close()
        main.FIREFLY_PROJECT_STORE = previous


@pytest.mark.parametrize('success_first', [False, True])
def test_history441_frontend_real_poll_ledger_retry_and_api_reload(tmp_path, success_first):
    store = ProjectStore(tmp_path / 'projects', snapshot_delay=0)
    previous = main.FIREFLY_PROJECT_STORE
    main.FIREFLY_PROJECT_STORE = store
    gates = {name: threading.Event() for name in ('failure', 'success', 'pending')}
    attempts = 0
    process = None
    stderr = (tmp_path / 'frontend.stderr.log').open('w+', encoding='utf-8')

    async def prefix_history441_provider(prompt, *_args, **_kwargs):
        nonlocal attempts
        if prompt == 'failure':
            attempts += 1
            if attempts == 1:
                while not gates['failure'].is_set():
                    await asyncio.sleep(0.01)
                raise RuntimeError('history441 服务商拒绝')
        else:
            while not gates[prompt].is_set():
                await asyncio.sleep(0.01)
        png = prefix_history441_png()
        return {'type': 'b64', 'value': png, 'mime_type': 'image/png'}, {'data': [{'b64_json': png}]}

    try:
        with TestClient(main.app, base_url='http://127.0.0.1', headers={'origin': 'http://127.0.0.1'}) as client, \
                patch.object(main, 'load_api_providers', return_value=[HISTORY441_PROVIDER]), \
                patch.object(main, 'generate_ai_image', side_effect=prefix_history441_provider):
            project = client.post('/api/project-library', json={'name': 'history441 前后端真实历史'}).json()['project']['id']
            base = f'/api/project-library/{project}'
            response = client.patch(f'{base}/canvas', json={'base_revision': 0, 'mutations': [
                {'op': 'upsert_node', 'node': {'id': 'result', 'type': 'smart-image', 'x': 80, 'y': 80, 'images': []}},
            ]})
            assert response.status_code == 200, response.text
            ids = {}
            for name in ('failure', 'success', 'pending'):
                revision = client.get(base).json()['canvas']['revision']
                response = client.post(f'{base}/image-generation-tasks', json={
                    'prompt': name, 'provider_id': HISTORY441_PROVIDER['id'], 'model': 'history441-image',
                    'routing_mode': 'manual', 'result_node_id': 'result', 'base_revision': revision,
                    'idempotency_key': f'history441-{name}',
                    'run_id': 'history441-other-run' if name == 'pending' else 'history441-shared-run',
                })
                assert response.status_code == 202, response.text
                ids[name] = response.json()['task_id']
            initial = client.get(base).json()['canvas']
            for name in (('success', 'failure') if success_first else ('failure', 'success')):
                gates[name].set()
                prefix_history441_wait(client, f'{base}/image-generation-tasks/{ids[name]}')
            process = subprocess.Popen(
                ['node', 'tests/history441_frontend_bridge.mjs'], cwd=Path(__file__).resolve().parents[1],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr,
                text=True, encoding='utf-8',
            )
            process.stdin.write(json.dumps({'initial': {
                'project': project, 'canvas': initial, 'failedId': ids['failure'],
                'successId': ids['success'], 'pendingId': ids['pending'],
            }}) + '\n')
            process.stdin.flush()
            receipt = None
            for line in process.stdout:
                message = json.loads(line)
                if message.get('done'):
                    receipt = message
                    break
                options = message.get('options') or {}
                response = client.request(options.get('method', 'GET'), message['url'],
                                          content=options.get('body'), headers=options.get('headers'))
                try:
                    process.stdin.write(json.dumps({'id': message['id'], 'status': response.status_code,
                                                   'body': response.text}) + '\n')
                    process.stdin.flush()
                except BrokenPipeError:
                    stderr.seek(0)
                    raise AssertionError(stderr.read()) from None
            process.communicate(timeout=10)
            stderr.seek(0)
            assert process.returncode == 0, stderr.read()
            assert receipt is not None
            assert attempts == 2
            assert client.post(f'{base}/flush').status_code == 200
            store.close()
            store = ProjectStore(tmp_path / 'projects', snapshot_delay=0)
            main.FIREFLY_PROJECT_STORE = store
            reloaded = client.get(base).json()['canvas']
            records = {record['task_id']: record for record in reloaded['history']}
            assert set(records) == {ids['failure'], ids['success'], receipt['retryId']}
            assert {record['run_id'] for record in records.values()} == {'history441-shared-run'}
            assert records[ids['failure']]['status'] == 'failed'
            node = reloaded['nodes'][0]
            assert [task['taskId'] for task in node['pendingTasks']] == [ids['pending']]
            assert len(node['images']) == 2
            assert {item['output_id'] for item in node['images']} == {
                item['output_id'] for record in records.values() for item in record['outputs']
            }
            assert client.post(f"{base}/image-generation-tasks/{ids['pending']}/cancel").status_code == 200
    finally:
        for gate in gates.values():
            gate.set()
        if process and process.poll() is None:
            process.kill()
            process.communicate()
        stderr.close()
        store.close()
        main.FIREFLY_PROJECT_STORE = previous
