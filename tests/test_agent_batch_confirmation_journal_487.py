"""Batch audit receipts use the actual project JSONL, never live delivery as proof (#487)."""
import builtins

import pytest
from pathlib import Path
import json
from copy import deepcopy
import os
import subprocess
import sys

import main

from app.services.agent_ops_store import list_operations
from firefly_opsdb import OpsDb

from tests.test_agent_batch_400 import fx, patch_node, rest, status_until
from tests.test_agent_batch_confirmation_transports_487 import dispatch, read_gate, _transports_start_batch_487


def journal_path(fx):
    return Path(fx.store.root) / fx.pid / 'agent-events' / 'actor.jsonl'


def fail_journal_open(fx, monkeypatch):
    original = builtins.open
    hits = []
    target = journal_path(fx).resolve()

    def open_file(file, mode='r', *args, **kwargs):
        if isinstance(file, (str, bytes, Path)) and Path(file).resolve() == target and 'a' in mode:
            hits.append(str(file))
            raise OSError('private audit append unavailable')
        return original(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, 'open', open_file)
    return hits


def fail_journal_write(fx, monkeypatch, *, match=lambda event: True):
    original = builtins.open
    hits = []
    target = journal_path(fx).resolve()

    class FailingAppend:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            self.stream.__enter__()
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def __getattr__(self, name):
            return getattr(self.stream, name)

        def write(self, data):
            events = [json.loads(line)['event'] for line in data.splitlines()]
            if any(match(event) for event in events):
                hits.extend(event['type'] for event in events if match(event))
                raise OSError('private audit write unavailable')
            return self.stream.write(data)

    def open_file(file, mode='r', *args, **kwargs):
        stream = original(file, mode, *args, **kwargs)
        if isinstance(file, (str, bytes, Path)) and Path(file).resolve() == target and 'a' in mode:
            return FailingAppend(stream)
        return stream

    monkeypatch.setattr(builtins, 'open', open_file)
    return hits


def test_pending_audit_failure_returns_500_without_installing_gate(fx, monkeypatch):
    code, preview = rest(fx, 'preflight', {'node_ids': ['b']})
    assert code == 200
    before = fx.store.canvas(fx.pid)
    hits = fail_journal_open(fx, monkeypatch)
    code, result = rest(fx, 'request_batch', {'preflight_id': preview['preflight_id']})
    assert code == 500 and result['detail']['code'] == 'JOURNAL_WRITE_FAILED', result
    assert hits and not fx.session.confirm_handlers and fx.events.empty() and fx.calls.empty()
    assert fx.store.canvas(fx.pid) == before
    assert not journal_path(fx).exists()


def test_approval_audit_failure_returns_500_and_keeps_original_gate(fx, monkeypatch):
    _, receipt, event = _transports_start_batch_487(fx, ['b'])
    original = read_gate(fx, receipt)
    before = fx.store.canvas(fx.pid)
    saved = journal_path(fx).read_bytes()
    with monkeypatch.context() as patch:
        hits = fail_journal_open(fx, patch)
        code, result = dispatch(fx, 'http', {'confirm_id': event['confirm_id'], 'approved': True})
        assert code == 500 and result['code'] == 'JOURNAL_WRITE_FAILED', result
        assert hits and read_gate(fx, receipt) == original and fx.calls.empty()
        assert fx.store.canvas(fx.pid) == before and journal_path(fx).read_bytes() == saved
        _, waiting = rest(fx, 'batch_status', {'batch_id': receipt['batch_id']})
        assert waiting['spent'] == waiting['approved_count'] == 0 and not any(r['task_id'] for r in waiting['items'])
    assert dispatch(fx, 'http', {'confirm_id': event['confirm_id'], 'approved': True})[0] == 200
    final = status_until(fx, receipt['batch_id'], lambda row: row['status'] == 'done')
    assert final['spent'] == final['approved_count'] == 1 and fx.calls.qsize() == 1


@pytest.mark.parametrize('action', ['remove', 'remove-last', 'new-pending-edit', 'new-pending-remove'])
def test_revision_required_audit_failures_never_install(fx, monkeypatch, action):
    from tests.test_agent_batch_preview_edit_487 import _preview_edit_mutate_487
    _, receipt, _ = _transports_start_batch_487(fx, ['b'] if action == 'remove-last' else None)
    old = read_gate(fx, receipt)
    with monkeypatch.context() as patch:
        hits = fail_journal_write(fx, patch, match=(lambda e: e['type'] == 'confirm')
                                  if action.startswith('new-pending') else (lambda e: True))
        response = _preview_edit_mutate_487(fx, receipt, old, 'edit' if action.endswith('edit') else 'remove')
        assert response.status_code == 500 and response.json()['detail']['code'] == 'JOURNAL_WRITE_FAILED', response.text
        assert hits and read_gate(fx, receipt) == old and fx.calls.empty() and fx.events.empty()
    assert dispatch(fx, 'http', {'confirm_id': old['confirm_id'], 'approved': False})[0] == 200
    assert status_until(fx, receipt['batch_id'], lambda row: row['status'] == 'rejected')['spent'] == 0


@pytest.mark.parametrize('transport', ['http', 'callback', 'ws'])
@pytest.mark.parametrize('approved', [True, False], ids=['approve', 'reject'])
def test_decision_write_failure_is_visible_on_each_boundary(fx, monkeypatch, transport, approved):
    from tests.test_agent_batch_confirmation_transports_487 import gate_handler
    _, receipt, event = _transports_start_batch_487(fx, ['b'])
    old = read_gate(fx, receipt)
    handler = gate_handler(fx, event)
    saved = journal_path(fx).read_bytes()
    with monkeypatch.context() as patch:
        hits = fail_journal_write(fx, patch)
        code, result = dispatch(fx, transport, {'confirm_id': event['confirm_id'], 'approved': approved},
                                handler=handler, token=old['decision_token'])
        assert code == 500 and result['code'] == 'JOURNAL_WRITE_FAILED', result
        assert hits and read_gate(fx, receipt) == old and fx.calls.empty() and fx.events.empty()
        assert journal_path(fx).read_bytes() == saved
    assert dispatch(fx, transport, {'confirm_id': event['confirm_id'], 'approved': False},
                    handler=handler, token=old['decision_token'])[0] == 200
    assert status_until(fx, receipt['batch_id'], lambda row: row['status'] == 'rejected')['spent'] == 0


def test_failed_replacement_append_cannot_leave_a_durable_replaced_card(fx, monkeypatch):
    from tests.test_agent_batch_preview_edit_487 import _preview_edit_mutate_487
    _, receipt, _ = _transports_start_batch_487(fx)
    old = read_gate(fx, receipt)
    saved = journal_path(fx).read_bytes()
    with monkeypatch.context() as patch:
        hits = fail_journal_write(fx, patch, match=lambda e: e['type'] == 'confirm')
        response = _preview_edit_mutate_487(fx, receipt, old)
        assert response.status_code == 500 and hits
        assert read_gate(fx, receipt) == old
        assert journal_path(fx).read_bytes() == saved, 'failed replacement must not durably close the still-current gate'
    assert dispatch(fx, 'http', {'confirm_id': old['confirm_id'], 'approved': False})[0] == 200


def test_stop_audit_failure_returns_500_but_revokes_authorization(fx, monkeypatch):
    _, receipt, event = _transports_start_batch_487(fx, ['b'])
    with monkeypatch.context() as patch:
        hits = fail_journal_open(fx, patch)
        code, result = rest(fx, 'stop_batch', {'batch_id': receipt['batch_id']})
        assert code == 500 and result['detail']['code'] == 'JOURNAL_WRITE_FAILED', result
        assert hits and fx.calls.empty()
        assert dispatch(fx, 'http', {'confirm_id': event['confirm_id'], 'approved': True})[0] == 409
    _, final = rest(fx, 'batch_status', {'batch_id': receipt['batch_id']})
    assert final['status'] == 'stopped' and final['spent'] == final['approved_count'] == 0


def test_running_stop_is_audited_and_failure_remains_visible(fx, monkeypatch):
    fx.release.clear()
    _, receipt, event = _transports_start_batch_487(fx, ['b'])
    assert dispatch(fx, 'http', {'confirm_id': event['confirm_id'], 'approved': True})[0] == 200
    fx.calls.get(timeout=3)
    running = status_until(fx, receipt['batch_id'], lambda row: row['spent'] == 1)
    try:
        with monkeypatch.context() as patch:
            hits = fail_journal_write(fx, patch)
            code, result = rest(fx, 'stop_batch', {'batch_id': receipt['batch_id']})
            assert code == 500 and result['detail']['code'] == 'JOURNAL_WRITE_FAILED', result
            assert hits
    finally:
        fx.release.set()
    settled = status_until(fx, receipt['batch_id'], lambda row: row['items'][0]['status'] == 'succeeded')
    assert settled['status'] == 'stopped' and settled['spent'] == settled['approved_count'] == 1
    task_id = running['items'][0]['task_id']
    assert fx.client.get(f'/api/projects/{fx.pid}/generate/tasks/{task_id}').json()['status'] == 'succeeded'


def test_running_stop_receipt_is_replayed_with_original_grant(fx):
    fx.release.clear()
    _, receipt, event = _transports_start_batch_487(fx, ['b'])
    assert dispatch(fx, 'http', {'confirm_id': event['confirm_id'], 'approved': True})[0] == 200
    fx.calls.get(timeout=3)
    status_until(fx, receipt['batch_id'], lambda row: row['spent'] == 1)
    resolved = fx.events.get(timeout=3)
    assert resolved['type'] == 'confirm_resolved'
    try:
        assert rest(fx, 'stop_batch', {'batch_id': receipt['batch_id']})[0] == 200
        stopped = fx.events.get(timeout=3)
        assert stopped['type'] == 'batch_stopped' and stopped['turn_id'] == 'initiating-turn'
        assert stopped['approval_sha256'] == resolved['approval_sha256']
        assert stopped['approved_item_ids'] == resolved['approved_item_ids']
        assert stopped['approved_count'] == stopped['spent'] == 1
        for suffix in ('events', 'events/page'):
            response = fx.client.get(f'/api/projects/{fx.pid}/agent-nodes/actor/{suffix}', params={'host': 'pi'})
            assert response.status_code == 200 and response.json()['events'][-1]['event'] == stopped
        saved = json.loads(journal_path(fx).read_text(encoding='utf-8').splitlines()[-1])
        assert saved['event'] == stopped
    finally:
        fx.release.set()
    assert status_until(fx, receipt['batch_id'], lambda row: row['items'][0]['status'] == 'succeeded')['spent'] == 1


@pytest.mark.parametrize('action', ['approve', 'edit'])
def test_origin_is_revalidated_after_successful_audit_io(fx, monkeypatch, action):
    from tests.test_agent_batch_preview_edit_487 import _preview_edit_mutate_487
    _, receipt, event = _transports_start_batch_487(fx, ['b'])
    old = read_gate(fx, receipt)

    def change_during_write(event):
        fx.session._last_user_turn_id = 'new-user-during-file-io'
        return False

    with monkeypatch.context() as patch:
        fail_journal_write(fx, patch, match=change_during_write)
        if action == 'edit':
            response = _preview_edit_mutate_487(fx, receipt, old)
            code, result = response.status_code, response.json().get('detail')
        else:
            code, result = dispatch(fx, 'http', {'confirm_id': event['confirm_id'], 'approved': True})
        assert code == 409 and result['code'] == 'CONFIRM_STALE', result
    final = status_until(fx, receipt['batch_id'], lambda row: row['status'] == 'rejected')
    assert final['spent'] == final['approved_count'] == 0 and not any(r['task_id'] for r in final['items'])
    assert final['preview_revision'] == 1 and final['snapshot_sha256'] == old['preflight']['snapshot_sha256']
    assert fx.calls.empty()
    assert dispatch(fx, 'http', {'confirm_id': event['confirm_id'], 'approved': True})[0] == 409


def test_node_stop_audit_failure_returns_500_without_leaving_old_gate(fx, monkeypatch):
    _, receipt, event = _transports_start_batch_487(fx, ['b'])
    with monkeypatch.context() as patch:
        hits = fail_journal_open(fx, patch)
        response = fx.client.delete(f'/api/projects/{fx.pid}/agent-nodes/actor')
        assert response.status_code == 500 and response.json()['detail']['code'] == 'JOURNAL_WRITE_FAILED', response.text
        assert hits and fx.calls.empty()
        assert dispatch(fx, 'http', {'confirm_id': event['confirm_id'], 'approved': True})[0] == 409


def test_pending_write_failure_is_visible_from_request(fx, monkeypatch):
    code, preview = rest(fx, 'preflight', {'node_ids': ['b']})
    assert code == 200
    with monkeypatch.context() as patch:
        hits = fail_journal_write(fx, patch)
        code, result = rest(fx, 'request_batch', {'preflight_id': preview['preflight_id']})
        assert code == 500 and result['detail']['code'] == 'JOURNAL_WRITE_FAILED', result
        assert hits and not fx.session.confirm_handlers and fx.calls.empty() and fx.events.empty()
    code, receipt = rest(fx, 'request_batch', {'preflight_id': preview['preflight_id']})
    assert code == 200
    event = fx.events.get(timeout=3)
    assert dispatch(fx, 'http', {'confirm_id': event['confirm_id'], 'approved': False})[0] == 200
    assert status_until(fx, receipt['batch_id'], lambda row: row['status'] == 'rejected')['spent'] == 0


def test_live_and_replayed_journal_bind_revisions_and_approved_subset(fx):
    from tests.test_agent_batch_preview_edit_487 import _preview_edit_mutate_487
    _, receipt, initial = _transports_start_batch_487(fx)
    live = [initial]
    old = read_gate(fx, receipt)
    response = _preview_edit_mutate_487(fx, receipt, old, patch={'prompt': 'Audited selected body'})
    assert response.status_code == 200, response.text
    edited = response.json()
    live += [fx.events.get(timeout=3), fx.events.get(timeout=3)]
    response = _preview_edit_mutate_487(fx, receipt, edited, 'remove', item=edited['preflight']['items'][1]['item_id'])
    assert response.status_code == 200, response.text
    current = response.json()
    live += [fx.events.get(timeout=3), fx.events.get(timeout=3)]
    assert list_operations(OpsDb(fx.store.root), fx.pid) == []
    assert dispatch(fx, 'http', {'confirm_id': current['confirm_id'], 'approved': True,
        'approved_item_ids': [current['preflight']['items'][0]['item_id']],
        'snapshot_sha256': current['preflight']['snapshot_sha256']})[0] == 200
    final = status_until(fx, receipt['batch_id'], lambda row: row['status'] == 'done')
    live.append(fx.events.get(timeout=3))
    records = [json.loads(line) for line in journal_path(fx).read_text(encoding='utf-8').splitlines()]
    assert [row['event'] for row in records] == live
    for suffix in ('events', 'events/page'):
        replay = fx.client.get(f'/api/projects/{fx.pid}/agent-nodes/actor/{suffix}', params={'host': 'pi'})
        assert replay.status_code == 200, replay.text
        assert replay.json()['events'] == records
    assert all(row['project_id'] == fx.pid and row['node_id'] == 'actor' for row in records)
    assert all(e['agent_node_id'] == 'actor' and e['source'] == 'rest' and e['turn_id'] == 'initiating-turn'
               and e['host'] == 'pi' and e['generation'] == fx.session.generation for e in live)
    for pair, before, after, action in ((live[1:3], old, edited, 'edit'), (live[3:5], edited, current, 'remove')):
        assert pair[0]['reason'] == 'replaced' and pair[1]['type'] == 'confirm'
        for e in pair:
            assert e['action'] == action
            assert e['old_snapshot_sha256'] == before['preflight']['snapshot_sha256']
            assert e['snapshot_sha256'] == after['preflight']['snapshot_sha256']
            assert e['old_preview_revision'] == before['preflight']['preview_revision']
            assert e['preview_revision'] == after['preflight']['preview_revision']
    assert live[-1]['approved_item_ids'] == final['approved_item_ids']
    assert live[-1]['approval_sha256'] == final['approval_sha256']
    assert 'decision_token' not in json.dumps(records)
    assert old['decision_token'] not in json.dumps(records) and current['decision_token'] not in json.dumps(records)
    assert final['spent'] == final['approved_count'] == 1 and fx.calls.qsize() == 1
    ops = list_operations(OpsDb(fx.store.root), fx.pid)
    assert ops and {op['turn_id'] for op in ops} == {'initiating-turn'}
    assert all('Minimalist art' not in str(op) for op in ops)


@pytest.mark.parametrize('ending', ['reject', 'stale', 'stop'])
def test_external_terminal_audit_keeps_original_source_and_turn(fx, ending):
    from tests.test_agent_batch_confirmation_transports_487 import initialize_mcp, mcp_call
    sid = initialize_mcp(fx)
    ok, preview = mcp_call(fx, sid, 'preflight', {'node_ids': ['b']})
    assert ok
    ok, receipt = mcp_call(fx, sid, 'request_batch', {'preflight_id': preview['preflight_id']})
    assert ok
    initial = fx.events.get(timeout=3)
    generation = fx.session.generation
    if ending == 'reject':
        assert dispatch(fx, 'http', {'confirm_id': initial['confirm_id'], 'approved': False})[0] == 200
    elif ending == 'stale':
        fx.session._generation += 1
    else:
        assert mcp_call(fx, sid, 'stop_batch', {'batch_id': receipt['batch_id']})[0]
    resolved = fx.events.get(timeout=3)
    assert resolved['type'] == 'confirm_resolved' and not resolved['approved']
    assert resolved['reason'] == {'reject': 'rejected', 'stale': 'session_ended', 'stop': 'stopped'}[ending]
    records = [json.loads(line) for line in journal_path(fx).read_text(encoding='utf-8').splitlines()]
    assert [r['event'] for r in records] == [initial, resolved]
    assert all(e['source'] == 'mcp' and e['agent_node_id'] == 'actor' and e['turn_id'] is None
               and e['host'] == 'pi' and e['generation'] == generation
               and e['snapshot_sha256'] == preview['snapshot_sha256'] for e in [initial, resolved])
    for suffix in ('events', 'events/page'):
        response = fx.client.get(f'/api/projects/{fx.pid}/agent-nodes/actor/{suffix}', params={'host': 'pi'})
        assert response.status_code == 200 and response.json()['events'] == records
    assert fx.calls.empty() and list_operations(OpsDb(fx.store.root), fx.pid) == []


@pytest.mark.parametrize('change', ['generation', 'turn', 'host', 'stop-batch'])
def test_admitted_selected_task_settles_after_origin_is_invalidated(fx, change):
    from tests.test_agent_batch_confirmation_transports_487 import invalidate_fixture_origin
    fx.release.clear()
    _, receipt, event = _transports_start_batch_487(fx)
    current = read_gate(fx, receipt)
    assert dispatch(fx, 'http', {'confirm_id': event['confirm_id'], 'approved': True,
        'approved_item_ids': [current['preflight']['items'][1]['item_id']],
        'snapshot_sha256': current['preflight']['snapshot_sha256']})[0] == 200
    fx.calls.get(timeout=3)
    running = status_until(fx, receipt['batch_id'], lambda row: any(i['task_id'] for i in row['items']))
    task_id = running['items'][1]['task_id']
    try:
        if change == 'stop-batch':
            assert rest(fx, 'stop_batch', {'batch_id': receipt['batch_id']})[0] == 200
        else:
            invalidate_fixture_origin(fx, change)
    finally:
        fx.release.set()
    response = fx.client.get(f'/api/projects/{fx.pid}/generate/tasks/{task_id}/wait?timeout=5')
    assert response.status_code == 200 and response.json()['status'] == 'succeeded', response.text
    result = response.json()['result']
    assert result['history_record']['outputs'] and result['prompt'] == 'Minimalist art'
    assert fx.store.find_canvas_node(fx.pid, running['items'][1]['result_node_id'])['images']
    final = status_until(fx, receipt['batch_id'], lambda row: row['items'][1]['status'] == 'succeeded')
    assert final['spent'] == final['approved_count'] == 1 and final['items'][0]['status'] == 'skipped'
    assert fx.calls.empty()
    ops = list_operations(OpsDb(fx.store.root), fx.pid)
    assert ops and {op['turn_id'] for op in ops} == {'initiating-turn'}


def test_selected_replay_safe_fallback_preserves_budget_ops_and_undo_truth(fx, monkeypatch):
    from app.routers import api_agent_tools as agent
    from tests.test_agent_batch_preview_edit_487 import _preview_edit_mutate_487, next_gate
    from tests.test_agent_generation_failures_401 import upstream
    monkeypatch.setattr(agent, 'GENERATION_SESSION_LIMIT', 3)
    fx.providers[0]['image_models'].append('fallback')
    fx.providers[0]['image_model_capabilities']['fallback'] = deepcopy(
        fx.providers[0]['image_model_capabilities']['fixture'])
    patch_node(fx, 'b', runSettings={'model': 'fixture', 'provider_id': 'offline', 'routingMode': 'manual', 'count': 2})
    offline = main.generate_ai_image
    observed = []

    async def provider(*args, **kwargs):
        observed.append((args[0], args[3]))
        if args[3] == 'fixture':
            raise upstream(503, {'code': 'model_not_found'})
        return await offline(*args, **kwargs)

    monkeypatch.setattr(main, 'generate_ai_image', provider)
    code, preview = rest(fx, 'preflight', {'node_ids': ['a', 'b']})
    assert code == 200
    code, receipt = rest(fx, 'request_batch', {'preflight_id': preview['preflight_id'],
        'fallback_models': ['fallback'], 'max_retries': 1})
    assert code == 200
    next_gate(fx)
    old = read_gate(fx, receipt)
    assert old['preflight']['snapshot_sha256'] != preview['snapshot_sha256']
    response = _preview_edit_mutate_487(fx, receipt, old, item=old['preflight']['items'][1]['item_id'],
                      patch={'runSettings': {'count': 3}})
    assert response.status_code == 200, response.text
    next_gate(fx)
    current = response.json()
    assert current['preflight']['snapshot_sha256'] != old['preflight']['snapshot_sha256']
    body = {'confirm_id': current['confirm_id'], 'approved': True,
        'approved_item_ids': [current['preflight']['items'][1]['item_id']],
        'snapshot_sha256': current['preflight']['snapshot_sha256']}
    assert dispatch(fx, 'http', {**body, 'snapshot_sha256': preview['snapshot_sha256']})[0] == 409
    assert dispatch(fx, 'http', body)[0] == 200
    final = status_until(fx, receipt['batch_id'], lambda row: row['status'] == 'done')
    assert final['spent'] == final['approved_count'] == 3
    assert [row['status'] for row in final['items']] == ['skipped', 'succeeded']
    row = final['items'][1]
    assert row['attempts'] == 2 and row['model'] == 'fallback'
    assert observed == [('Minimalist art', 'fixture')] * 3 + [('Minimalist art', 'fallback')] * 3
    task = fx.client.get(f'/api/projects/{fx.pid}/generate/tasks/{row["task_id"]}').json()
    record = task['result']['history_record']
    assert task['status'] == 'succeeded' and record['retry_index'] == 1 and len(record['outputs']) == 3
    failed = fx.client.get(f'/api/projects/{fx.pid}/generate/tasks/{record["retry_parent_id"]}').json()
    assert failed['status'] == 'failed' and failed['result']['history_record']['automatic_retry'] is True
    ops = list_operations(OpsDb(fx.store.root), fx.pid)
    root, = [op for op in ops if not op.get('parent_op_id')]
    assert root['args']['fallback_models'] == ['fallback'] and root['args']['max_retries'] == 1
    assert all('A sculpture' not in str(op) for op in ops)
    undo = fx.client.post(f'/api/projects/{fx.pid}/ops/undo', headers={'X-Agent-Project': fx.pid},
                          json={'op_id': root['op_id']})
    assert undo.status_code == 200 and undo.json()['status'] == 'reverted', undo.text
    assert undo.json()['irreversible_effects'] and undo.json()['reverted']
    assert fx.store.find_canvas_node(fx.pid, row['result_node_id']) is None
    assert len(fx.store.canvas(fx.pid)['history']) == 2
    _, second, event = _transports_start_batch_487(fx, ['b'])
    assert dispatch(fx, 'http', {'confirm_id': event['confirm_id'], 'approved': True})[0] == 200
    denied = status_until(fx, second['batch_id'], lambda s: s['status'] == 'rejected')
    assert denied['reason'] == 'BUDGET_EXCEEDED' and denied['approved_count'] == denied['spent'] == 0
    assert len(observed) == 6


@pytest.mark.parametrize('failure', ['uncertain', 'partial'])
def test_selected_uncertain_or_partial_effects_are_never_retried(fx, monkeypatch, failure):
    import httpx
    from tests.test_agent_generation_failures_401 import upstream
    count = 2 if failure == 'partial' else 1
    patch_node(fx, 'b', runSettings={'model': 'fixture', 'provider_id': 'offline', 'routingMode': 'manual', 'count': count})
    offline = main.generate_ai_image
    observed = []

    async def provider(*args, **kwargs):
        observed.append(args[0])
        if failure == 'partial' and len(observed) == 1:
            return await offline(*args, **kwargs)
        if failure == 'partial':
            raise upstream(503, {'code': 'model_not_found'})
        raise httpx.ReadTimeout('request may have reached remote generation')

    monkeypatch.setattr(main, 'generate_ai_image', provider)
    preview, receipt, event = _transports_start_batch_487(fx)
    assert dispatch(fx, 'http', {'confirm_id': event['confirm_id'], 'approved': True,
        'approved_item_ids': [preview['items'][1]['item_id']], 'snapshot_sha256': preview['snapshot_sha256']})[0] == 200
    final = status_until(fx, receipt['batch_id'], lambda row: row['status'] == 'done')
    row = final['items'][1]
    assert final['spent'] == final['approved_count'] == count and final['items'][0]['status'] == 'skipped'
    assert row['status'] == 'failed' and row['attempts'] == 1 and observed == ['Minimalist art'] * count
    task = fx.client.get(f'/api/projects/{fx.pid}/generate/tasks/{row["task_id"]}').json()
    record = task['result']['history_record']
    assert not record.get('automatic_retry')
    assert len(record['outputs']) == (1 if failure == 'partial' else 0)


@pytest.mark.parametrize('ending', ['pending', 'approved'])
def test_fresh_process_and_store_cannot_execute_old_audit_or_token(fx, ending):
    from tests.test_agent_batch_confirmation_transports_487 import preview_url
    _, receipt, event = _transports_start_batch_487(fx, ['b'])
    old = read_gate(fx, receipt)
    if ending == 'approved':
        assert dispatch(fx, 'http', {'confirm_id': event['confirm_id'], 'approved': True})[0] == 200
        assert status_until(fx, receipt['batch_id'], lambda row: row['status'] == 'done')['spent'] == 1
    fx.store.flush(fx.pid)
    expected = fx.store.canvas(fx.pid)
    script = r'''
import json, sys
from pathlib import Path
from fastapi.testclient import TestClient
import main
from firefly_projects import ProjectStore
from app.services.agent_host import agent_host_manager as host
payload = json.loads(sys.stdin.read())
store = ProjectStore(Path(payload['root']), snapshot_delay=0)
main.FIREFLY_PROJECT_STORE = store
calls = []
async def offline(*args, **kwargs):
    calls.append(args)
    raise AssertionError('old audit must not submit generation')
main.generate_ai_image = offline
try:
    with TestClient(main.app, base_url='http://localhost', headers={'Origin': 'http://localhost'}) as client:
        pid, batch, cid = payload['pid'], payload['batch'], payload['cid']
        url = f'/api/projects/{pid}/agent-nodes/actor'
        assert client.get(url + f'/batches/{batch}/preview', headers={'X-Agent-Project': pid}).status_code == 409
        response = client.post(url + '/confirm', headers={'X-Agent-Project': pid}, json={'confirm_id': cid, 'approved': True})
        assert response.status_code == 409, response.text
        with client.websocket_connect(f'ws://localhost' + url + '/stream') as ws:
            assert ws.receive_json()['type'] == 'session'
            ws.send_json({'type': 'confirm', 'confirm_id': cid, 'approved': True, 'decision_token': payload['token']})
            while True:
                event = ws.receive_json()
                if event['type'] in ('error', 'confirm_result'):
                    break
            assert event['type'] == 'error' and event['status'] == 409, event
        for suffix in ('events', 'events/page'):
            replay = client.get(url + '/' + suffix, params={'host': 'pi'})
            assert replay.status_code == 200 and replay.json()['events'], replay.text
        assert store.canvas(pid) == payload['canvas'] and not calls
        session = host.conversation_session(pid, 'actor')
        assert session is None or not session.is_running and not session.confirm_handlers
    print('RECONSTRUCTION_REJECTED_OLD_GATE_AND_TOKEN')
finally:
    store.close()
'''
    result = subprocess.run([sys.executable, '-B', '-c', script], input=json.dumps({
        'root': str(fx.store.root), 'pid': fx.pid, 'batch': receipt['batch_id'], 'cid': event['confirm_id'],
        'token': old['decision_token'], 'canvas': expected}), text=True, encoding='utf-8',
        env={**os.environ, 'FIREFLY_PROJECTS_ROOT': str(fx.store.root)}, capture_output=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'RECONSTRUCTION_REJECTED_OLD_GATE_AND_TOKEN' in result.stdout
    assert fx.store.canvas(fx.pid) == expected
    if ending == 'pending':
        assert dispatch(fx, 'http', {'confirm_id': event['confirm_id'], 'approved': False})[0] == 200
        assert status_until(fx, receipt['batch_id'], lambda row: row['status'] == 'rejected')['spent'] == 0


def test_replacement_journal_targets_only_surviving_items(fx):
    from tests.test_agent_batch_preview_edit_487 import _preview_edit_mutate_487
    _, receipt, _ = _transports_start_batch_487(fx)
    response = _preview_edit_mutate_487(fx, receipt, read_gate(fx, receipt), 'remove')
    assert response.status_code == 200, response.text
    old_resolution, new_pending = fx.events.get(timeout=3), fx.events.get(timeout=3)
    assert old_resolution['target_node_ids'] == ['a', 'b']
    assert new_pending['target_node_ids'] == ['b']
    assert dispatch(fx, 'http', {'confirm_id': new_pending['confirm_id'], 'approved': False})[0] == 200
    resolved = fx.events.get(timeout=3)
    assert resolved['target_node_ids'] == ['b']


def test_empty_removal_journal_never_publishes_an_approvable_empty_gate(fx):
    from tests.test_agent_batch_preview_edit_487 import _preview_edit_mutate_487
    _, receipt, event = _transports_start_batch_487(fx, ['b'])
    old = read_gate(fx, receipt)
    response = _preview_edit_mutate_487(fx, receipt, old, 'remove')
    assert response.status_code == 200 and response.json()['status'] == 'stopped', response.text
    resolved = fx.events.get(timeout=3)
    records = [json.loads(line)['event'] for line in journal_path(fx).read_text(encoding='utf-8').splitlines()]
    assert [e['type'] for e in records] == ['confirm', 'confirm_resolved'], records
    assert records[-1] == resolved and resolved['reason'] == 'empty_batch'
    assert resolved['preview_revision'] == 2 and resolved['old_preview_revision'] == 1
    assert resolved['snapshot_sha256'] == response.json()['preflight']['snapshot_sha256']
    assert fx.calls.empty() and fx.events.empty()


def test_edit_audit_failure_returns_500_before_install(fx, monkeypatch):
    from tests.test_agent_batch_preview_edit_487 import _preview_edit_mutate_487
    _, receipt, _ = _transports_start_batch_487(fx)
    old = read_gate(fx, receipt)
    before = fx.store.canvas(fx.pid)
    saved = journal_path(fx).read_bytes()
    with monkeypatch.context() as patch:
        hits = fail_journal_open(fx, patch)
        response = _preview_edit_mutate_487(fx, receipt, old)
        assert response.status_code == 500 and response.json()['detail']['code'] == 'JOURNAL_WRITE_FAILED', response.text
        assert hits and read_gate(fx, receipt) == old
        assert fx.calls.empty() and fx.events.empty() and fx.store.canvas(fx.pid) == before
        assert journal_path(fx).read_bytes() == saved
    assert dispatch(fx, 'http', {'confirm_id': old['confirm_id'], 'approved': False})[0] == 200
    assert status_until(fx, receipt['batch_id'], lambda row: row['status'] == 'rejected')['spent'] == 0
