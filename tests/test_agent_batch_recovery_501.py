"""Public REST/MCP and human controls for a settled, paused batch."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
import time

import pytest

import main
from app.routers import api_agent_tools as agent
from app.services.agent_host import agent_host_manager as host
from tests.test_agent_batch_400 import confirm, fx, mcp, patch_node, rest, status_until
from tests.test_agent_generation_failures_401 import upstream


def human(fx, batch, action='status', body=None, *, actor='actor', pid=None, headers=None):
    pid = pid or fx.pid
    url = f'/api/projects/{pid}/agent-nodes/{actor}/batches/{batch}/{action}'
    kwargs = {'headers': {'X-Agent-Project': pid} if headers is None else headers}
    return fx.client.get(url, **kwargs) if body is None else fx.client.post(url, json=body, **kwargs)


def paused_batch(fx, monkeypatch, *, external=False, cautious=False):
    calls = []

    async def provider(*args, **kw):
        calls.append(args[3])
        raise upstream(403, 'saldo insuficiente; token=sensitive-501')

    monkeypatch.setattr(main, 'generate_ai_image', provider)
    if cautious:
        fx.session.autonomy = 'cautious'

    def tool(name, args):
        code, data = (mcp if external else rest)(fx, name, args)
        assert code == (True if external else 200), data
        return data

    preflight = tool('preflight', {'node_ids': ['a', 'b']})
    receipt = tool('request_batch', {'preflight_id': preflight['preflight_id']})
    batch = receipt['batch_id']
    confirm(fx, fx.events.get(timeout=2))
    assert fx.events.get(timeout=2)['type'] == 'confirm_resolved'
    if cautious:
        gate = fx.events.get(timeout=2)
        assert gate['params']['approval_stage'] == 'pay_item'
        assert gate['params']['preflight']['items'][0]['node_id'] == 'a'
        confirm(fx, gate)
        assert fx.events.get(timeout=2)['type'] == 'confirm_resolved'
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        status = tool('batch_status', {'batch_id': batch, 'wait_seconds': .1})
        if status['status'] == 'paused':
            assert calls == ['fixture']
            return batch, status, calls
    pytest.fail(f'Expected paused batch, got {status}')


@pytest.mark.parametrize('external', [False, True])
def test_structured_failure_status_stays_in_original_scope(fx, monkeypatch, external):
    batch, status, calls = paused_batch(fx, monkeypatch, external=external)
    args = {'batch_id': batch}
    response = human(fx, batch)
    assert response.status_code == 200, response.text
    assert response.json() == status
    failed, queued = status['items']
    assert failed['status'] == 'paused' and failed['failure']['code'] == 'QUOTA_EXHAUSTED'
    assert failed['attempts'] == 1 and failed['task_id'] and failed['can_skip'] is True
    assert queued['status'] == 'queued' and queued['task_id'] is None and queued['can_skip'] is True
    assert 'sensitive-501' not in json.dumps(status) and 'details' not in failed['failure']
    assert human(fx, batch, actor='second-actor').status_code == 409
    assert human(fx, batch, pid=fx.other).status_code == 409
    assert human(fx, batch, headers={}).status_code == 403
    assert human(fx, batch, headers={'X-Agent-Project': fx.pid, 'Origin': 'http://other.test'}).status_code == 403
    assert human(fx, batch, 'status?sid=external-400').status_code == 400
    assert mcp(fx, 'batch_status', args, sid='different-501')[0] is False
    if external:
        assert rest(fx, 'batch_status', args)[0] == 404
    else:
        assert mcp(fx, 'batch_status', args)[0] is False
    assert rest(fx, 'batch_status', args, actor='second-actor')[0] == 404
    assert rest(fx, 'batch_status', args, pid=fx.other)[0] == 404
    assert calls == ['fixture']


@pytest.mark.parametrize('index', [0, 1])
def test_skip_is_idempotent_and_preserves_payment_and_failure_history(fx, monkeypatch, index):
    batch, status, calls = paused_batch(fx, monkeypatch, external=True)
    before = fx.store.canvas(fx.pid)
    debit = agent._GENERATION_COUNTS[fx.pid, 'actor']
    item = status['items'][index]
    body = {'item_id': item['item_id'], 'snapshot_sha256': status['snapshot_sha256']}
    assert human(fx, batch, 'skip', {**body, 'sid': 'external-400'}).status_code == 400
    assert human(fx, batch, 'skip', {**body, 'snapshot_sha256': 'a' * 64}).status_code == 409
    assert human(fx, batch, 'skip', body, actor='second-actor').status_code == 409
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(lambda _: human(fx, batch, 'skip', body), range(2)))
    assert all(reply.status_code == 200 for reply in replies), [r.text for r in replies]
    final = replies[0].json()
    assert final == replies[1].json() == mcp(fx, 'batch_status', {'batch_id': batch})[1]
    assert {k: v for k, v in final.items() if k != 'items'} == {
        **{k: v for k, v in status.items() if k != 'items'}, 'can_resume': index == 0,
    }
    assert final['items'][1 - index] == status['items'][1 - index]
    assert final['items'][index] == {**item, 'status': 'paused' if index == 0 else 'skipped',
                                    'user_skipped': True, 'can_skip': False, 'can_retry': False}
    event = fx.events.get(timeout=2)
    assert event['type'] == 'batch_item_skipped'
    assert event['batch_id'] == batch and event['item_id'] == item['item_id'] and event['source'] == 'mcp'
    events = fx.client.get(f'/api/projects/{fx.pid}/agent-nodes/actor/events').json()
    assert [row['event'] for row in events['events'] if row['event']['type'] == 'batch_item_skipped'] == [event]
    assert calls == ['fixture'] and agent._GENERATION_COUNTS[fx.pid, 'actor'] == debit
    assert fx.store.canvas(fx.pid) == before


def test_skip_audit_failure_or_stale_origin_does_not_change_row(fx, monkeypatch):
    batch, status, calls = paused_batch(fx, monkeypatch)
    body = {'item_id': status['items'][1]['item_id'], 'snapshot_sha256': status['snapshot_sha256']}
    with monkeypatch.context() as patch:
        patch.setattr(host, '_persist_event', lambda *args, **kw: False)
        reply = human(fx, batch, 'skip', body)
    assert reply.status_code == 500 and reply.json()['detail']['code'] == 'JOURNAL_WRITE_FAILED'
    assert human(fx, batch).json() == status
    fx.session.input_epoch += '-changed'
    assert human(fx, batch).status_code == 409
    assert human(fx, batch, 'skip', body).status_code == 409
    code, final = rest(fx, 'batch_status', {'batch_id': batch})
    assert code == 200 and final['items'][1]['status'] == 'queued'
    assert final['items'][1]['can_skip'] is False and calls == ['fixture']


def test_skip_cannot_bypass_pending_approval(fx):
    preview = rest(fx, 'preflight', {'node_ids': ['b']})[1]
    receipt = rest(fx, 'request_batch', {'preflight_id': preview['preflight_id']})[1]
    gate = fx.events.get(timeout=2)
    body = {'item_id': preview['items'][0]['item_id'], 'snapshot_sha256': gate['params']['preflight']['snapshot_sha256']}
    response = human(fx, receipt['batch_id'], 'skip', body)
    assert response.status_code == 409
    confirm(fx, gate, False)
    final = status_until(fx, receipt['batch_id'], lambda data: data['status'] == 'rejected')
    assert final['spent'] == 0 and fx.calls.empty()


def recovery_body(status, row=None):
    return {'snapshot_sha256': status['snapshot_sha256'], **({'item_id': row['item_id']} if row else {})}


def test_resume_uses_only_original_unadmitted_queue_without_second_debit(fx, monkeypatch):
    good = main.generate_ai_image
    batch, status, calls = paused_batch(fx, monkeypatch)
    body = recovery_body(status)
    assert status['can_resume'] is True
    assert human(fx, batch, 'resume', {**body, 'snapshot_sha256': '0' * 64}).status_code == 409
    assert human(fx, batch, 'resume', body, actor='second-actor').status_code == 409
    with monkeypatch.context() as patch:
        patch.setattr(host, '_persist_event', lambda *args, **kw: False)
        assert human(fx, batch, 'resume', body).status_code == 500
    assert human(fx, batch).json() == status
    monkeypatch.setattr(main, 'generate_ai_image', good)
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(lambda _: human(fx, batch, 'resume', body), range(2)))
    assert sorted(reply.status_code for reply in replies) == [200, 409]
    data = status_until(fx, batch, lambda s: s['status'] == 'done')
    assert [row['status'] for row in data['items']] == ['paused', 'succeeded']
    assert fx.calls.qsize() == 1 and fx.calls.get()[0] == 'Minimalist art'
    assert calls == ['fixture'] and data['approved_item_ids'] == status['approved_item_ids']
    assert data['approval_sha256'] == status['approval_sha256'] and data['spent'] == 2
    assert agent._GENERATION_COUNTS[fx.pid, 'actor'] == 2
    assert human(fx, batch, 'resume', body).status_code == 409


def test_cautious_resume_waits_for_the_queued_items_own_paid_gate(fx, monkeypatch):
    good = main.generate_ai_image
    batch, status, calls = paused_batch(fx, monkeypatch, cautious=True)
    assert status['approved_count'] == status['spent'] == agent._GENERATION_COUNTS[fx.pid, 'actor'] == 1
    monkeypatch.setattr(main, 'generate_ai_image', good)
    response = human(fx, batch, 'resume', recovery_body(status))
    assert response.status_code == 200, response.text
    resumed = fx.events.get(timeout=2)
    assert resumed['type'] == 'batch_resumed' and resumed['item_ids'] == [status['items'][1]['item_id']]
    gate = fx.events.get(timeout=2)
    assert gate['params']['approval_stage'] == 'pay_item'
    assert [row['item_id'] for row in gate['params']['preflight']['items']] == [status['items'][1]['item_id']]
    waiting = human(fx, batch).json()
    assert waiting['items'][1]['attempts'] == 0 and waiting['items'][1]['task_id'] is None
    assert waiting['approved_count'] == waiting['spent'] == agent._GENERATION_COUNTS[fx.pid, 'actor'] == 1
    assert fx.calls.empty() and calls == ['fixture']
    confirm(fx, gate)
    final = status_until(fx, batch, lambda item: item['status'] == 'done')
    assert [row['status'] for row in final['items']] == ['paused', 'succeeded']
    assert final['approved_count'] == final['spent'] == agent._GENERATION_COUNTS[fx.pid, 'actor'] == 2
    assert fx.calls.qsize() == 1 and fx.calls.get()[0] == 'Minimalist art'


def test_resume_revalidates_sources_and_skipped_queue_is_not_resumed(fx, monkeypatch):
    batch, status, calls = paused_batch(fx, monkeypatch)
    body = recovery_body(status)
    patch_node(fx, 'b', prompt='different')
    assert human(fx, batch, 'resume', body).json()['detail']['code'] == 'PREFLIGHT_STALE'
    assert human(fx, batch, 'skip', recovery_body(status, status['items'][1])).status_code == 200
    assert human(fx, batch).json()['can_resume'] is False
    assert human(fx, batch, 'resume', body).status_code == 409
    assert calls == ['fixture'] and agent._GENERATION_COUNTS[fx.pid, 'actor'] == 2


def test_paid_retry_is_separate_from_resume_in_original_mcp_scope(fx, monkeypatch):
    good = main.generate_ai_image
    batch, status, calls = paused_batch(fx, monkeypatch, external=True)
    body = recovery_body(status, status['items'][0])
    assert status['items'][0]['can_retry'] is True
    assert human(fx, batch, 'retry', body, headers={}).status_code == 403
    assert human(fx, batch, 'retry', recovery_body(status, status['items'][1])).status_code == 409
    reply = human(fx, batch, 'retry', body)
    assert reply.status_code == 200, reply.text
    gate = fx.events.get(timeout=2)
    assert gate['type'] == 'confirm' and gate['tool'] == 'generate.retry'
    assert gate['batch_id'] == batch and gate['item_id'] == body['item_id']
    assert gate['confirm_id'] == reply.json()['confirm_id'] and gate['params']['task_id'] == status['items'][0]['task_id']
    assert human(fx, batch, 'retry', body).json() == reply.json()
    assert human(fx, batch, 'resume', recovery_body(status)).status_code == 409
    path = f'/api/projects/{fx.pid}/agent-nodes/actor/confirm'
    missing_scope = fx.client.post(path, json={'confirm_id': gate['confirm_id'], 'approved': True})
    assert missing_scope.status_code == 403
    assert calls == ['fixture'] and agent._GENERATION_COUNTS[fx.pid, 'actor'] == 2
    confirm(fx, gate, False)
    assert fx.events.get(timeout=2)['type'] == 'confirm_resolved'
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and human(fx, batch).json()['items'][0]['retry_pending']:
        time.sleep(.02)
    assert calls == ['fixture'] and agent._GENERATION_COUNTS[fx.pid, 'actor'] == 2
    monkeypatch.setattr(main, 'generate_ai_image', good)
    assert human(fx, batch, 'retry', body).status_code == 200
    gate = fx.events.get(timeout=2)
    confirm(fx, gate)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result = human(fx, batch).json()
        if not result['items'][0]['retry_pending']:
            break
        time.sleep(.02)
    assert result['status'] == 'paused' and result['items'][1]['status'] == 'queued'
    still_failed = result['items'][0]
    assert still_failed['task_id'] == status['items'][0]['task_id'] and still_failed['failure'] == status['items'][0]['failure']
    assert still_failed['retry_result']['status'] == 'succeeded'
    assert result['spent'] == status['spent'] == 1 and agent._GENERATION_COUNTS[fx.pid, 'actor'] == 3
    assert calls == ['fixture'] and fx.calls.qsize() == 1
    assert mcp(fx, 'batch_status', {'batch_id': batch})[1] == result


@pytest.mark.parametrize('attestation,partial,final', [
    ('independent', False, 'done'), ('shared', False, 'paused'), ('unknown', False, 'paused'),
    ('independent', True, 'paused'), ('rotated', False, 'paused'),
])
def test_quota_continuation_requires_durable_rejection_and_independent_route(fx, monkeypatch, attestation, partial, final):
    fx.providers[0]['image_request_mode'] = 'openai-json'
    other = {**deepcopy(fx.providers[0]), 'id': 'offline-b', 'primary': False, 'api_key': 'stub-b'}
    keys = {'offline': 'offline-test-a', 'offline-b': 'offline-test-b'}
    monkeypatch.setattr(main, 'provider_env_key_value', keys.get)
    fx.providers.append(other)
    patch_node(fx, 'b', runSettings={**fx.store.find_canvas_node(fx.pid, 'b')['runSettings'], 'provider_id': 'offline-b'})
    if partial:
        patch_node(fx, 'a', runSettings={**fx.store.find_canvas_node(fx.pid, 'a')['runSettings'], 'count': 2})
    pools = {}
    if attestation != 'unknown':
        for provider, pool in zip(fx.providers, ('pool-a', 'pool-a' if attestation == 'shared' else 'pool-b')):
            fingerprint = hashlib.sha256(json.dumps((provider, 'fixture', 'Bearer ' + keys[provider['id']]), sort_keys=True,
                                                   ensure_ascii=False, default=str).encode()).hexdigest()
            pools[fingerprint] = pool
    monkeypatch.setenv('FIREFLY_AGENT_BATCH_QUOTA_POOLS', json.dumps(pools))
    good = main.generate_ai_image
    submitted = []

    async def provider(*args, **kw):
        submitted.append(args[5])
        if attestation == 'rotated' and args[5] == 'offline':
            keys['offline-b'] = 'offline-rotated'
        assert main.api_headers(provider=kw['approved_provider'], model='fixture')['Authorization'] == 'Bearer ' + keys[args[5]]
        if args[5] == 'offline' and not (partial and submitted.count('offline') == 1):
            raise upstream(403, 'saldo insuficiente')
        return await good(*args, **kw)

    monkeypatch.setattr(main, 'generate_ai_image', provider)
    preview = rest(fx, 'preflight', {'node_ids': ['a', 'b']})[1]
    receipt = rest(fx, 'request_batch', {'preflight_id': preview['preflight_id']})[1]
    confirm(fx, fx.events.get(timeout=2))
    result = status_until(fx, receipt['batch_id'], lambda s: s['status'] in ('paused', 'done'))
    assert result['status'] == final
    failed, remaining = result['items']
    assert failed['failure']['code'] == 'QUOTA_EXHAUSTED'
    if final == 'done':
        assert failed['status'] == 'failed' and remaining['status'] == 'succeeded' and submitted == ['offline', 'offline-b']
    else:
        assert failed['status'] == 'paused' and remaining['status'] == 'queued' and 'offline-b' not in submitted
        if partial:
            assert failed['can_retry'] is False
