"""#491: real image-worker publications honor committed output occurrences, offline."""
import asyncio
import base64
import copy
import io
import socket
import subprocess
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
from PIL import Image
import pytest

import main
from app.routers import api_project_library as library
from firefly_projects import ProjectStore


@pytest.fixture
def image_occurrence_worker(tmp_path, monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError('Network/native CLI forbidden in #491 regression')

    # Windows asyncio needs its self-pipe before socket connections are forbidden.
    self_pipe = socket.socketpair()
    monkeypatch.setattr(socket, 'socketpair', lambda *args, **kwargs: self_pipe)
    monkeypatch.setattr(socket, 'create_connection', denied)
    monkeypatch.setattr(socket.socket, 'connect', denied)
    monkeypatch.setattr(subprocess, 'Popen', denied)
    monkeypatch.setattr(httpx.AsyncClient, 'send', denied)
    monkeypatch.setattr(httpx.Client, 'send', denied)
    monkeypatch.setattr(main, 'load_api_providers', lambda **kwargs: [])
    monkeypatch.setattr(library.manager, 'broadcast_canvas_updated', AsyncMock())
    store = ProjectStore(tmp_path / 'projects', snapshot_delay=0)
    monkeypatch.setattr(main, 'FIREFLY_PROJECT_STORE', store)
    pid = store.create_project('e2e-tabbit-491-output-worker')['id']
    store.apply_mutations(pid, [{'op': 'upsert_node', 'node': {
        'id': 'result', 'type': 'smart-image', 'images': [],
    }}], base_revision=0)
    route = SimpleNamespace(provider_id='offline-491', provider_name='Offline', model='fixture',
                            reason='fixture', to_dict=lambda: {'provider_id': 'offline-491', 'model': 'fixture'})
    task_id = f'project_img_491_{pid}'
    state = SimpleNamespace(store=store, pid=pid, task_id=task_id, calls=0, partial_inputs=[],
                            final_inputs=[], deleted=None)
    real_complete = store.complete_image_generation

    async def run(same_bytes):
        payload = main.ProjectImageGenerationRequest(
            prompt='offline fixture', provider_id=route.provider_id, model=route.model,
            n=2, result_node_id='result', base_revision=1,
        )
        request = library.project_image_request(payload)
        registered = store.begin_image_generation(pid, 'result', {
            'taskId': task_id, 'kind': 'image', 'count': 2,
            'requestPayload': payload.model_dump(), 'route': route.to_dict(),
        }, base_revision=1)
        with main.CANVAS_TASK_LOCK:
            main.CANVAS_TASKS[task_id] = {'id': task_id, 'project_id': pid, 'status': 'queued'}
        first_deleted = threading.Event()

        async def provider(*args, **kwargs):
            index = state.calls
            state.calls += 1
            if index:
                assert await asyncio.to_thread(first_deleted.wait, 5), 'first partial was not deleted'
            png = io.BytesIO()
            Image.new('RGB', (3, 2), (40 if same_bytes else 40 + index, 100, 180)).save(png, format='PNG')
            return {'type': 'b64', 'value': base64.b64encode(png.getvalue()).decode('ascii')}, {}

        def complete(*args, **kwargs):
            target = state.partial_inputs if kwargs.get('partial') else state.final_inputs
            target.append(copy.deepcopy({'media': args[2], 'record': args[3]}))
            # A mutation adapter may own its argument copies; publication cannot rely
            # on the Store mutating the worker's local history_record as a side effect.
            canvas = real_complete(*copy.deepcopy(args), **kwargs)
            if kwargs.get('partial') and len(state.partial_inputs) == 1:
                record = canvas['history'][0]
                output = record['outputs'][0]
                state.deleted = store.delete_generated_original(
                    pid, output['asset_id'], base_revision=canvas['revision'],
                    history_id=record['id'], output_id=output['output_id'], output_index=0,
                )['deleted']
                first_deleted.set()
            return canvas

        monkeypatch.setattr(library, 'run_routed_image_candidate', provider)
        monkeypatch.setattr(store, 'complete_image_generation', complete)
        await library.run_project_image_task(task_id, pid, payload, request, route, registered['revision'])
        state.task = main.CANVAS_TASKS[task_id]
        state.canvas = store.open_project(pid)['canvas']
        state.history = state.canvas['history'][0]
        state.node = next(node for node in state.canvas['nodes'] if node['id'] == 'result')
        return state

    state.run = run
    try:
        yield state
    finally:
        with main.CANVAS_TASK_LOCK:
            main.CANVAS_TASKS.pop(task_id, None)
            main.PROJECT_IMAGE_GENERATION_ORPHANS.pop(pid, None)
        store.close()
        for sock in self_pipe:
            sock.close()


@pytest.mark.parametrize('same_bytes', [False, True], ids=['different-original', 'same-original'])
def test_worker_success_publishes_only_canonical_retained_occurrences(image_occurrence_worker, same_bytes):
    state = asyncio.run(image_occurrence_worker.run(same_bytes))
    assert state.task['status'] == state.history['status'] == 'succeeded'
    assert state.calls == 2
    result = state.task['result']
    assert len(state.history['outputs']) == len(state.node['images']) == 1
    assert len(result['image_items']) == len(result['images']) == 1
    assert result['history_record'] == state.history
    assert result['history_id'] == state.history['id']
    assert result['final_node'] == state.node
    assert result['project_revision'] == state.canvas['revision']
    assert result['executed_prompt'] == 'offline fixture'
    assert result['provider_id'] == 'offline-491' and result['model'] == 'fixture'
    assert result['registered_revision'] < result['project_revision']
    first, fresh = [entry['media'][0] for entry in state.partial_inputs]
    assert first['output_id'] != fresh['output_id']
    assert state.final_inputs[0]['media'] == [first, fresh]
    assert [item['output_id'] for item in state.final_inputs[0]['record']['outputs']] == [first['output_id'], fresh['output_id']]
    assert same_bytes == (first['asset_id'] == fresh['asset_id'])
    retained = result['image_items'][0]
    assert retained['output_id'] == fresh['output_id'] == state.history['outputs'][0]['output_id']
    assert retained['output_id'] != state.deleted['output_id']
    assert retained['output_order'] == state.history['outputs'][0]['output_order']
    assert (retained['natural_w'], retained['natural_h']) == (3, 2)
    assert result['images'] == [retained['url']]
    assert not state.node.get('pendingTasks') and state.node['pending'] == 0
    state.store.flush(state.pid)
    state.store.close()
    state.store = ProjectStore(state.store.root, snapshot_delay=0)
    try:
        reopened = state.store.open_project(state.pid)['canvas']
        assert reopened['history'][0]['outputs'] == state.history['outputs']
    finally:
        state.store.close()
