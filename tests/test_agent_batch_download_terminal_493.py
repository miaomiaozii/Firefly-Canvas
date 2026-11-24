"""Real approved batch: a paid URL failure is retrieval, never another generation."""
import asyncio
import copy
import socket

import httpx
import pytest

import main
from app import core
from app.routers import api_agent_tools as agent
from app.services import agent_batch, result_downloads as rd
from tests.test_agent_batch_400 import fx, rest, confirm
from tests.test_result_downloads_493 import URL, _REAL_CLIENT


@pytest.mark.parametrize('max_retries', [0, 2])
def test_approved_batch_settles_download_failure_and_runs_next(fx, monkeypatch, max_retries):
    generated, requests = [], []
    original_provider = main.generate_ai_image
    async def provider(*args, **kwargs):
        generated.append(args[0] if args else kwargs['prompt'])
        if len(generated) == 1:
            return {'type': 'url', 'value': URL}, {'task_id': 'original-paid-task'}
        return await original_provider(*args, **kwargs)
    async def dns(hostname, port, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', port))]
    def get(request):
        requests.append(request)
        assert request.method == 'GET'
        return httpx.Response(503)
    monkeypatch.setattr(main, 'generate_ai_image', provider)
    monkeypatch.setattr(main, 'api_headers', lambda **kw: {'Authorization': 'Bearer offline-lifecycle-fixture'})
    monkeypatch.setattr(core, '_resolve_generation_host', dns)
    monkeypatch.setattr(rd, 'BACKOFF', (0, 0, 0))
    monkeypatch.setattr(rd.httpx, 'AsyncClient', lambda **kw: _REAL_CLIENT(transport=httpx.MockTransport(get), **kw))
    preview = rest(fx, 'preflight', {'node_ids': ['a', 'b']})[1]
    receipt = rest(fx, 'request_batch', {'preflight_id': preview['preflight_id'], 'max_retries': max_retries})[1]
    batch = agent_batch._BATCHES[receipt['batch_id']]
    confirm(fx, fx.events.get(timeout=2))
    async def settled():
        async def poll():
            while batch.status not in agent_batch.TERMINAL:
                await asyncio.sleep(.01)
            return batch.status_dict()
        return await asyncio.wait_for(poll(), 4)
    final = asyncio.run_coroutine_threadsafe(settled(), batch.loop).result(timeout=6)
    assert final['status'] == 'done'
    first, second = final['items']
    assert first['status'] == 'download_failed' and second['status'] == 'succeeded', final
    assert first['attempts'] == second['attempts'] == 1
    assert len(generated) == 2 and len(requests) == 4
    assert final['spent'] == final['approved_count'] == 2
    status = asyncio.run_coroutine_threadsafe(agent.get_task_status(fx.pid, first['task_id']), batch.loop).result(timeout=2)
    assert first['failure'] == status['failure'] == {'code': 'RESULT_DOWNLOAD_FAILED', 'message': '图片已生成，待取回（不会重新生成、不扣费）'}
    actual, expected = copy.deepcopy(first['retrieval']), copy.deepcopy(status['retrieval'])
    # The public countdown advances while the next approved item runs; identity must not.
    for retrieval in (actual, expected):
        retrieval.pop('remaining_seconds')
        for item in retrieval['items']:
            item.pop('remaining_seconds')
    assert actual == expected
    assert first['result'] == status['result']
    queue = rd.downloads(main).find_task(fx.pid, first['task_id'])
    history = next(h for h in fx.store.canvas(fx.pid)['history'] if h['id'] == queue['history_id'])
    assert history['task_id'] == first['task_id'] and history['result_node_id'] == first['result_node_id']
    assert history['id'] == first['retrieval']['history_id']
    assert queue['upstream_task_id'] == 'original-paid-task'
    assert first['retrieval']['generates'] is False and first['retrieval']['additional_cost'] is False
    assert history['op_id'] == first['retrieval']['op_id']
    assert 'PRIVATE-URL' not in str(first)
