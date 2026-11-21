from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
import threading
import time
from queue import Queue

import httpx
import pytest

import main
from app.routers import api_agent_tools as agent
from tests.test_agent_batch_400 import confirm, fx, mcp, patch_node, rest, status_until  # noqa: F401

from app.services.generation_failures import classify_generation_failure, retry_after_seconds


@pytest.mark.parametrize('text,status,code', [
    ('无可用渠道', 503, 'MODEL_UNAVAILABLE'), ('Model not exist', 404, 'MODEL_UNAVAILABLE'),
    ('gateway failed', 502, 'TRANSIENT'), ('Connection error', None, 'TRANSIENT'),
    ('request timed out', None, 'TRANSIENT'), ('busy', 429, 'RATE_LIMITED'),
    ('saldo insuficiente', 403, 'QUOTA_EXHAUSTED'), ('余额不足', 403, 'QUOTA_EXHAUSTED'),
    ('Forbidden', 403, 'UNKNOWN'), ('content_policy_violation', 400, 'CONTENT_REJECTED'),
    ('unsupported aspect ratio', 400, 'INVALID_PARAMS'), ('参考图 2 上传失败', 502, 'REF_UPLOAD_FAILED'),
    ('not found', 404, 'UNKNOWN'), ('unexpected', None, 'UNKNOWN'),
])
def test_failure_policy(text, status, code):
    assert classify_generation_failure(text, status, 'fixture').code == code


def test_retry_after_delta_http_date_and_default():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()
    assert retry_after_seconds('12', now=now) == 12
    assert retry_after_seconds('Thu, 01 Jan 2026 00:00:25 GMT', now=now) == 25
    assert retry_after_seconds('inf', now=now) == 30
    assert retry_after_seconds(None, now=now) == 30


def upstream(status, message, **headers):
    request = httpx.Request('POST', 'https://example.invalid/v1/images/generations')
    response = httpx.Response(status, json={'error': message}, headers=headers, request=request)
    return httpx.HTTPStatusError('offline stub', request=request, response=response)


def start_batch(fx, ids=('b',), **opts):
    pre = rest(fx, 'preflight', {'node_ids': list(ids)})[1]
    assert pre['blockers'] == []
    status, receipt = rest(fx, 'request_batch', {'preflight_id': pre['preflight_id'], **opts})
    assert status == 200
    confirm(fx, fx.events.get(timeout=2))
    assert fx.events.get(timeout=2)['type'] == 'confirm_resolved'
    return receipt['batch_id']


def test_l1_first_503_only_uses_exact_approved_fallback_and_cleans_failed_node(fx, monkeypatch):
    config = fx.providers[0]
    config['image_models'] += ['fallback', 'not-approved']
    config['image_model_capabilities']['fallback'] = deepcopy(config['image_model_capabilities']['fixture'])
    offline = main.generate_ai_image
    calls = []

    async def provider(*args, **kw):
        calls.append((args[0], args[1], args[3], [r['asset_id'] for r in args[4]]))
        if args[3] == 'fixture':
            raise upstream(503, {'message': '无可用渠道; token=do-not-leak; stub'})
        return await offline(*args, **kw)

    monkeypatch.setattr(main, 'generate_ai_image', provider)
    batch = start_batch(fx, ('a',), fallback_models=['fixture', 'missing', 'fallback', 'fallback'])
    final = status_until(fx, batch, lambda d: d['status'] == 'done')
    row, = final['items']
    assert row['status'] == 'succeeded' and row['attempts'] == 2 and row['model'] == 'fallback'
    assert row['last_failure']['code'] == 'MODEL_UNAVAILABLE'
    assert [c[2] for c in calls] == ['fixture', 'fallback']
    assert calls[0][:2] == calls[1][:2] and calls[0][3] == calls[1][3] == fx.ids
    assert final['approved_count'] == final['spent'] == agent._GENERATION_COUNTS[fx.pid, 'actor'] == 1
    canvas = fx.store.canvas(fx.pid)
    failed, succeeded = canvas['history']
    assert failed['status'] == 'failed' and failed['outputs'] == [] and failed['actual_model'] == 'fixture'
    assert len(failed['details']['raw']) <= 500 and not {'stub', 'do-not-leak'}.intersection(failed['details']['raw'].split())
    assert 'do-not-leak' not in failed['details']['raw'] and 'stub' not in failed['details']['raw']
    status = fx.client.get(f'/api/projects/{fx.pid}/generate/tasks/{failed["task_id"]}').json()
    assert status['failure']['code'] == 'MODEL_UNAVAILABLE' and 'details' not in status['result']['history_record']
    assert failed['result_node_id'] not in {node['id'] for node in canvas['nodes']}
    assert succeeded['retry_parent_id'] == failed['task_id'] and succeeded['retry_index'] == 1
    assert fx.store.find_canvas_node(fx.pid, 'a')['agentStatus'] == 'succeeded'
    assert 'lastFailure' not in fx.store.find_canvas_node(fx.pid, 'a')


@pytest.mark.parametrize('mode,calls,code', [
    ('rate', 3, 'RATE_LIMITED'), ('quota', 1, 'QUOTA_EXHAUSTED'), ('content', 2, 'CONTENT_REJECTED'),
    ('uncertain', 2, 'TRANSIENT'), ('unknown', 2, 'UNKNOWN'), ('ref', 4, 'REF_UPLOAD_FAILED'),
])
def test_batch_policy_and_limits(fx, monkeypatch, mode, calls, code):
    observed = []

    async def provider(*args, **kw):
        observed.append((args[0], time.monotonic()))
        if mode == 'rate':
            raise upstream(429, 'busy', **{'Retry-After': '0.25'})
        if mode == 'quota':
            raise upstream(403, 'saldo insuficiente')
        if mode == 'content':
            raise upstream(400, {'code': 'content_policy_violation'})
        if mode == 'uncertain':
            raise httpx.ReadTimeout('remote call may have started')
        if mode == 'ref':
            raise upstream(400, {'code': 'reference_upload_failed'})
        raise RuntimeError('Connection error: not a typed transport error')

    monkeypatch.setattr(main, 'generate_ai_image', provider)
    batch = start_batch(fx, ('a',) if mode == 'rate' else ('a', 'b'), max_retries=17)
    final = status_until(fx, batch, lambda d: d['status'] in ('done', 'paused'))
    assert len(observed) == calls and final['items'][0]['failure']['code'] == code
    if mode == 'quota':
        assert final['status'] == 'paused' and final['reason'] == code
        assert final['items'][1]['status'] == 'queued' and final['spent'] == 1
    if mode == 'rate':
        assert all(b[1] - a[1] >= .24 for a, b in zip(observed, observed[1:]))
        assert final['spent'] == 1 and final['items'][0]['attempts'] == 3
    assert fx.store.find_canvas_node(fx.pid, 'a')['lastFailure']['code'] == code
    assert fx.store.find_canvas_node(fx.pid, 'a')['prompt'] == 'A sculpture; overlay text “Firefly”'


def test_known_aspect_change_preserves_other_snapshot_parameters(fx, monkeypatch):
    fx.providers[0]['image_model_capabilities']['fixture']['aspect_ratios'] += ['4:3', '9:16']
    patch_node(fx, 'b', runSettings={'model': 'fixture', 'provider_id': 'offline', 'routingMode': 'manual',
                                    'ratio': 'wide', 'resolution': '1k', 'quality': 'high', 'count': 1})
    offline = main.generate_ai_image
    seen = []

    async def provider(*args, **kw):
        seen.append((args[0], args[2], kw['image_parameters']['aspect_ratio']))
        if len(seen) == 1:
            raise upstream(400, 'unsupported aspect ratio')
        return await offline(*args, **kw)

    monkeypatch.setattr(main, 'generate_ai_image', provider)
    batch = start_batch(fx)
    row, = status_until(fx, batch, lambda d: d['status'] == 'done')['items']
    assert row['status'] == 'succeeded'
    assert seen == [('Minimalist art', 'high', '16:9'), ('Minimalist art', 'high', '4:3')]
    assert fx.store.find_canvas_node(fx.pid, 'b')['runSettings']['ratio'] == 'wide'


def test_stop_during_transient_backoff_never_starts_next_attempt(fx, monkeypatch):
    calls = []

    async def provider(*args, **kw):
        calls.append(args[3])
        raise httpx.ConnectTimeout('offline timeout')

    monkeypatch.setattr(main, 'generate_ai_image', provider)
    batch = start_batch(fx, ('a', 'b'))
    status_until(fx, batch, lambda d: bool(d['items'][0].get('last_failure')))
    assert rest(fx, 'stop_batch', {'batch_id': batch})[1]['status'] == 'stopped'
    final = status_until(fx, batch, lambda d: d['items'][0]['status'] == 'stopped')
    assert final['items'][0]['attempts'] == final['spent'] == len(calls) == 1
    assert final['items'][1]['status'] == 'stopped'


def wait_task(fx, task):
    return fx.client.get(f'/api/projects/{fx.pid}/generate/tasks/{task}/wait?timeout=5').json()


@pytest.fixture
def operation_results(monkeypatch):
    from app.services.agent_confirmation import Origin
    results = Queue()

    async def finished(origin, result):
        results.put(result)

    monkeypatch.setattr(Origin, 'continue_with', finished)
    return results


def test_explicit_retry_requires_fresh_confirmation_scope_tip_and_revalidates(fx, monkeypatch, operation_results):
    calls = []

    async def provider(*args, **kw):
        calls.append(args[0])
        raise upstream(503, {'code': 'model_not_found'})

    monkeypatch.setattr(main, 'generate_ai_image', provider)
    fx.session.autonomy = 'autonomous'
    first = rest(fx, 'trigger', {'node_id': 'b'})[1]['task_id']
    assert wait_task(fx, first)['status'] == 'failed'
    fx.session.autonomy = 'standard'
    assert rest(fx, 'retry', {'task_id': first}, actor='second-actor')[0] == 404
    assert rest(fx, 'retry', {'task_id': first}, pid=fx.other, headers={'X-Agent-Project': fx.pid})[0] == 403
    assert not mcp(fx, 'retry', {'task_id': first})[0]
    assert rest(fx, 'retry', {'task_id': first, 'batch_id': 'shortcut'})[0] == 400
    assert rest(fx, 'retry', {'task_id': first, 'model': 'not-approved'})[1]['detail']['code'] == 'MODEL_NOT_APPROVED'
    assert fx.events.empty() and len(calls) == 1

    receipt = rest(fx, 'retry', {'task_id': first})[1]
    assert receipt['code'] == 'CONFIRM_PENDING' and len(calls) == 1
    event = fx.events.get(timeout=2)
    assert event['tool'] == 'generate.retry'
    patch_node(fx, 'b', prompt='Edited user prompt')
    confirm(fx, event)
    failure = operation_results.get(timeout=5)['error']
    assert failure['code'] in ('RETRY_STALE', 'CONFIRM_STALE'), failure
    assert fx.events.get(timeout=2)['type'] == 'confirm_resolved'
    assert agent._GENERATION_COUNTS[fx.pid, 'actor'] == 1

    receipts = [rest(fx, 'retry', {'task_id': first})[1] for _ in range(2)]
    assert all(r['code'] == 'CONFIRM_PENDING' for r in receipts)
    events = [fx.events.get(timeout=2) for _ in range(2)]
    confirm(fx, events[0]); confirm(fx, events[1])
    outcomes = [operation_results.get(timeout=5) for _ in range(2)]
    assert sum('error' in result for result in outcomes) == 1
    tip = next(result['task_id'] for result in outcomes if 'task_id' in result)
    assert wait_task(fx, tip)['status'] == 'failed'
    assert calls == ['Minimalist art'] * 2
    assert [fx.events.get(timeout=2)['type'] for _ in range(2)] == ['confirm_resolved'] * 2
    assert rest(fx, 'retry', {'task_id': first})[1]['detail']['code'] == 'RETRY_LIMIT'
    monkeypatch.setattr(agent, 'GENERATION_SESSION_LIMIT', 2)
    assert rest(fx, 'retry', {'node_id': 'b', 'use_snapshot': False})[1]['code'] == 'CONFIRM_PENDING'
    actual = fx.events.get(timeout=2)
    confirm(fx, events[0])
    assert operation_results.empty() and len(calls) == 2
    confirm(fx, actual)
    assert operation_results.get(timeout=5)['error']['code'] == 'BUDGET_EXCEEDED'
    assert fx.events.get(timeout=2)['type'] == 'confirm_resolved'
    monkeypatch.setattr(agent, 'GENERATION_SESSION_LIMIT', 200)
    assert rest(fx, 'retry', {'node_id': 'b', 'use_snapshot': False})[1]['code'] == 'CONFIRM_PENDING'
    confirm(fx, fx.events.get(timeout=2))
    result = operation_results.get(timeout=5)
    assert wait_task(fx, result['task_id'])['status'] == 'failed'
    assert calls[-1] == 'Edited user prompt'
    assert rest(fx, 'retry', {'task_id': result['task_id']})[1]['detail']['code'] == 'RETRY_LIMIT'
    assert agent._GENERATION_COUNTS[fx.pid, 'actor'] == len(calls) == 3


@pytest.mark.parametrize('edit', ['node', 'reference', 'none'])
def test_failure_cleanup_is_one_reversible_write_and_does_not_remove_user_data(fx, monkeypatch, edit):
    from app.services.agent_ops_store import list_operations
    from app.services.agent_ops_undo import undo_ops
    from firefly_opsdb import OpsDb

    release = threading.Event()

    async def provider(*args, **kw):
        import asyncio
        assert await asyncio.to_thread(release.wait, 3)
        raise upstream(400, 'unsupported size')

    monkeypatch.setattr(main, 'generate_ai_image', provider)
    fx.session.autonomy = 'autonomous'
    result = rest(fx, 'trigger', {'node_id': 'b'})[1]
    result_id = result['result_node_id']
    try:
        if edit == 'node':
            patch_node(fx, result_id, title='Keep my caption')
            patch_node(fx, 'b', title='Keep my source title')
        if edit == 'reference':
            patch_node(fx, 'a', inputNodeIds=[result_id])
    finally:
        release.set()
    assert wait_task(fx, result['task_id'])['failure']['code'] == 'INVALID_PARAMS'
    canvas = fx.store.canvas(fx.pid)
    node = next((n for n in canvas['nodes'] if n['id'] == result_id), None)
    source = fx.store.find_canvas_node(fx.pid, 'b')
    assert source['lastFailure']['code'] == 'INVALID_PARAMS'
    if edit == 'node':
        assert node['title'] == 'Keep my caption' and source['title'] == 'Keep my source title'
    elif edit == 'reference':
        assert node and fx.store.find_canvas_node(fx.pid, 'a')['inputNodeIds'] == [result_id]
    else:
        assert node is None
        ops = list_operations(OpsDb(fx.store.root), fx.pid)
        cleanup, = [op for op in ops if any(c['id'] == result_id and c['path'] == [] and not c['after']['present']
                                            for c in op['after'])]
        assert undo_ops(fx.store, fx.pid, op_id=cleanup['op_id'])['status'] == 'reverted'
        assert not fx.store.find_canvas_node(fx.pid, result_id).get('pendingTasks')
        assert undo_ops(fx.store, fx.pid, op_id=result['op_id'])['status'] == 'reverted'
        assert fx.store.find_canvas_node(fx.pid, result_id) is None
        assert rest(fx, 'retry', {'task_id': result['task_id']})[1]['detail']['code'] == 'RETRY_STALE'
    assert fx.store.canvas(fx.pid)['history'] == canvas['history']


def test_partial_output_prevents_replay_and_quota_still_pauses(fx, monkeypatch):
    patch_node(fx, 'b', runSettings={'model': 'fixture', 'provider_id': 'offline', 'routingMode': 'manual', 'count': 2})
    offline = main.generate_ai_image
    calls = []

    async def provider(*args, **kw):
        calls.append(args[0])
        if len(calls) == 2:
            raise upstream(403, 'quota exhausted')
        return await offline(*args, **kw)

    monkeypatch.setattr(main, 'generate_ai_image', provider)
    batch = start_batch(fx, ('b', 'a'))
    paused = status_until(fx, batch, lambda d: d['status'] == 'paused')
    row = paused['items'][0]
    assert len(calls) == 2 and row['attempts'] == 1 and paused['items'][1]['status'] == 'queued'
    history, = fx.store.canvas(fx.pid)['history']
    assert len(history['outputs']) == 1 and history['failure']['code'] == 'QUOTA_EXHAUSTED'
    assert fx.store.find_canvas_node(fx.pid, row['result_node_id'])
    assert rest(fx, 'retry', {'task_id': row['task_id']})[1]['detail']['code'] == 'RETRY_UNSAFE'


def test_actual_openai_adapter_preserves_upstream_codes_and_retry_after(fx, monkeypatch):
    from app.services import generation

    attempts = []
    original_client = httpx.AsyncClient

    def offline(request):
        attempts.append((request.method, request.url.path))
        return httpx.Response(429, headers={'Retry-After': '0'}, json={'error': {'code': 'rate_limit_exceeded'}})

    monkeypatch.setattr(generation.httpx, 'AsyncClient',
                        lambda **kw: original_client(transport=httpx.MockTransport(offline), **kw))
    monkeypatch.setattr(main, 'api_headers', lambda **kw: {'Authorization': 'Bearer fixture-only'})
    monkeypatch.setattr(main, 'generate_ai_image', generation.generate_ai_image)
    batch = start_batch(fx, max_retries=1)
    row, = status_until(fx, batch, lambda d: d['status'] == 'done')['items']
    assert row['attempts'] == 2 and row['failure']['code'] == 'RATE_LIMITED'
    assert attempts == [('POST', '/v1/images/generations')] * 2


def test_external_mcp_retry_keeps_the_same_actor_and_session(fx, monkeypatch, operation_results):
    async def fail(*args, **kw):
        raise upstream(429, 'busy')

    monkeypatch.setattr(main, 'generate_ai_image', fail)
    fx.session.autonomy = 'autonomous'
    ok, task = mcp(fx, 'trigger', {'node_id': 'b'}, sid='same')
    assert ok and wait_task(fx, task['task_id'])['status'] == 'failed'
    fx.session.autonomy = 'standard'
    args = {'task_id': task['task_id']}
    assert not mcp(fx, 'retry', args, sid='different')[0]
    assert fx.events.empty()
    ok, receipt = mcp(fx, 'retry', args, sid='same')
    assert ok and receipt['code'] == 'CONFIRM_PENDING'
    event = fx.events.get(timeout=2)
    assert event['tool'] == 'generate.retry'
    confirm(fx, event)
    retried = operation_results.get(timeout=5)
    assert wait_task(fx, retried['task_id'])['status'] == 'failed'
