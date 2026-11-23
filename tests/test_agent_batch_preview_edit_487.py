"""Batch-only preview revisions through real human routes and confirmation waiters (#487)."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor, TimeoutError
import asyncio
import threading
from types import SimpleNamespace

import main

import pytest

from app.services.agent_ops_store import list_operations
from firefly_opsdb import OpsDb
from tests.test_agent_batch_400 import fx, mcp, patch_node, rest, status_until
from tests.test_agent_batch_confirmation_transports_487 import (
    dispatch, gate_handler, initialize_mcp, preview_url, read_gate, _transports_start_batch_487,
)


def _preview_edit_mutate_487(fx, receipt, current, action='edit', *, item=None, patch=None, **extra):
    prepared = current['preflight']
    body = {'preview_revision': prepared['preview_revision'], 'snapshot_sha256': prepared['snapshot_sha256'],
            'item_id': item or prepared['items'][0]['item_id'], **extra}
    if action == 'edit':
        body['patch'] = patch if patch is not None else {'prompt': 'Revised body'}
    return fx.client.post(preview_url(fx, receipt) + '/' + action,
                          headers={'X-Agent-Project': fx.pid}, json=body)


def next_gate(fx):
    while True:
        event = fx.events.get(timeout=3)
        if event['type'] == 'confirm':
            return event


def approve_current(fx, receipt):
    current = read_gate(fx, receipt)
    code, result = dispatch(fx, 'http', {'confirm_id': current['confirm_id'], 'approved': True})
    assert code == 200, result
    return status_until(fx, receipt['batch_id'], lambda row: row['status'] in ('done', 'rejected', 'stopped'))


def test_prompt_override_repreflights_effective_text_and_preserves_refs(fx):
    canvas = fx.store.canvas(fx.pid)
    fx.store.apply_mutations(fx.pid, [
        {'op': 'upsert_node', 'node': {'id': 'upstream', 'type': 'smart-prompt', 'text': 'OLD upstream body'}},
        {'op': 'upsert_edge', 'edge': {'id': 'prompt-edge', 'from': 'upstream', 'to': 'a', 'kind': 'flow'}},
    ], base_revision=canvas['revision'])
    preview, receipt, event = _transports_start_batch_487(fx, ['a'])
    assert preview['items'][0]['user_prompt'] == 'OLD upstream body'
    old = read_gate(fx, receipt)
    response = _preview_edit_mutate_487(fx, receipt, old, patch={'prompt': 'NEW effective body'})
    assert response.status_code == 200, response.text
    new = response.json()
    assert new['preflight']['items'][0]['user_prompt'] == 'NEW effective body'
    assert new['preflight']['items'][0]['final_prompt'].startswith('参考图说明：图1：产品主体')
    assert new['preflight']['items'][0]['final_prompt'].endswith('NEW effective body')
    assert 'OLD upstream body' not in new['preflight']['items'][0]['final_prompt']
    assert new['preflight']['items'][0]['refs'] == preview['items'][0]['refs']
    assert next_gate(fx)['confirm_id'] == new['confirm_id'] != event['confirm_id']
    final = approve_current(fx, receipt)
    assert final['status'] == 'done' and final['spent'] == 1, final
    assert fx.calls.get(timeout=3)[0] == new['preflight']['items'][0]['final_prompt']


def test_parameter_edit_revalidates_route_ratio_count_and_cost(fx):
    second = deepcopy(fx.providers[0])
    second.update(id='other-provider', primary=False, image_models=['other-model'],
                  image_model_capabilities={'other-model': {'max_references': 20, 'arbitrary_size': True,
                     'resolutions': ['1k'], 'aspect_ratios': ['1:1', '16:9']}})
    fx.providers.append(second)
    _, receipt, _ = _transports_start_batch_487(fx, ['b'])
    old = read_gate(fx, receipt)
    response = _preview_edit_mutate_487(fx, receipt, old, patch={'runSettings': {
        'model': 'other-model', 'provider_id': 'other-provider', 'routingMode': 'manual',
        'aspect_ratio': '1:1', 'count': 3}})
    assert response.status_code == 200, response.text
    current = response.json()
    row, = current['preflight']['items']
    assert (row['model'], row['provider'], row['aspect_ratio'], row['count']) == (
        'other-model', 'other-provider', '1:1', 3)
    assert current['preflight']['total_images'] == 3
    assert current['preflight']['models_used'] == ['other-model']
    assert current['preflight']['snapshot_sha256'] != old['preflight']['snapshot_sha256']
    final = approve_current(fx, receipt)
    assert final['spent'] == final['approved_count'] == 3 and final['status'] == 'done', final
    calls = [fx.calls.get(timeout=3) for _ in range(3)]
    assert all(call[2:4] == ('other-model', 'other-provider') for call in calls)
    assert all(call[5]['image_parameters']['aspect_ratio'] == '1:1' for call in calls)


def test_remove_one_item_revises_snapshot_not_canvas(fx):
    preview, receipt, event = _transports_start_batch_487(fx)
    old = read_gate(fx, receipt)
    before = fx.store.canvas(fx.pid)
    response = _preview_edit_mutate_487(fx, receipt, old, 'remove')
    assert response.status_code == 200, response.text
    current = response.json()
    assert current['preflight']['preview_revision'] == 2
    assert current['preflight']['snapshot_sha256'] != preview['snapshot_sha256']
    assert current['preflight']['items'] == preview['items'][1:]
    assert current['preflight']['total_images'] == 1
    assert current['confirm_id'] != event['confirm_id']
    assert next_gate(fx)['confirm_id'] == current['confirm_id']
    assert fx.store.canvas(fx.pid) == before and fx.calls.empty()
    assert dispatch(fx, 'http', {'confirm_id': event['confirm_id'], 'approved': True})[0] == 409
    final = approve_current(fx, receipt)
    assert final['status'] == 'done' and final['spent'] == final['approved_count'] == 1, final
    assert [row['item_id'] for row in final['items']] == [preview['items'][1]['item_id']]
    assert fx.calls.get(timeout=3)[0] == 'Minimalist art' and fx.calls.empty()


def test_preview_edits_and_removals_never_write_canvas(fx):
    patch_node(fx, 'b', promptEditedByUser=True, userTouchedAt=42)
    _, receipt, _ = _transports_start_batch_487(fx)
    before = fx.store.canvas(fx.pid)
    operations = list_operations(OpsDb(fx.store.root), fx.pid)
    old = read_gate(fx, receipt)
    edited = _preview_edit_mutate_487(fx, receipt, old, patch={'prompt': 'Batch body', 'runSettings': {'count': 2}})
    assert edited.status_code == 200, edited.text
    removed = _preview_edit_mutate_487(fx, receipt, edited.json(), 'remove')
    assert removed.status_code == 200, removed.text
    assert fx.store.canvas(fx.pid) == before
    assert list_operations(OpsDb(fx.store.root), fx.pid) == operations == []
    _, status = rest(fx, 'batch_status', {'batch_id': receipt['batch_id']})
    assert status['spent'] == status['approved_count'] == 0
    assert not any(row['task_id'] for row in status['items']) and fx.calls.empty()
    assert rest(fx, 'stop_batch', {'batch_id': receipt['batch_id']})[0] == 200


@pytest.mark.parametrize('patch,expected', [
    ({'prompt': 'valid part', 'forbidden': 1}, 400),
    ({'prompt': 'valid part', 'runSettings': {'size': '512x512'}}, 400),
    ({'prompt': 'valid part', 'runSettings': {'count': True}}, 400),
    ({'runSettings': {'count': 1.5}}, 400),
    ({'runSettings': {'count': 0}}, 400),
    ({'runSettings': {'aspect_ratio': 'not-a-ratio'}}, 400),
    ({'prompt': ''}, 400),
    ({'prompt': ['not text']}, 400),
    ({'runSettings': {'model': 'not-available', 'routingMode': 'manual'}}, 409),
    ({'runSettings': {'provider_id': 'missing', 'routingMode': 'manual'}}, 409),
    ({'runSettings': {'routingMode': True}}, 400),
    ({'runSettings': {'provider_id': {'id': 'offline'}}}, 400),
], ids=['unknown-patch', 'unknown-setting', 'bool-count', 'float-count', 'zero-count', 'ratio',
        'empty-prompt', 'prompt-type', 'model-blocker', 'provider-blocker', 'routing-type', 'provider-type'])
def test_invalid_patch_is_atomic_and_keeps_original_confirmation(fx, patch, expected):
    _, receipt, _ = _transports_start_batch_487(fx, ['b'])
    old = read_gate(fx, receipt)
    before = fx.store.canvas(fx.pid)
    response = _preview_edit_mutate_487(fx, receipt, old, patch=patch)
    assert response.status_code == expected, response.text
    assert response.json()['detail']['code'] == ('INVALID_ARGUMENT' if expected == 400 else 'PREFLIGHT_BLOCKED')
    assert read_gate(fx, receipt) == old
    assert fx.store.canvas(fx.pid) == before and fx.calls.empty() and fx.events.empty()
    final = approve_current(fx, receipt)
    assert final['status'] == 'done' and final['spent'] == 1, final
    assert fx.calls.get(timeout=3)[0] == 'Minimalist art'


def test_noop_edit_keeps_snapshot_confirmation_and_expiry(fx):
    _, receipt, _ = _transports_start_batch_487(fx, ['b'])
    old = read_gate(fx, receipt)
    response = _preview_edit_mutate_487(fx, receipt, old, patch={'prompt': 'Minimalist art', 'runSettings': {
        'model': 'fixture', 'provider_id': 'offline', 'routingMode': 'manual', 'aspect_ratio': '1:1', 'count': 1}})
    assert response.status_code == 200, response.text
    assert response.json() == old
    assert fx.events.empty() and fx.calls.empty()
    assert approve_current(fx, receipt)['spent'] == 1


def test_remove_last_item_stops_empty_batch_without_approvable_gate(fx):
    _, receipt, event = _transports_start_batch_487(fx, ['b'])
    old = read_gate(fx, receipt)
    response = _preview_edit_mutate_487(fx, receipt, old, 'remove')
    assert response.status_code == 200, response.text
    current = response.json()
    assert current['status'] == 'stopped' and current['preflight']['items'] == []
    assert current['preflight']['total_images'] == 0 and current['preflight']['preview_revision'] == 2
    assert current['confirm_id'] is None and current['decision_token'] is None and not current['actionable']
    final = status_until(fx, receipt['batch_id'], lambda row: row['status'] == 'stopped')
    assert final['reason'] == 'empty_batch' and final['spent'] == final['approved_count'] == 0
    assert dispatch(fx, 'http', {'confirm_id': event['confirm_id'], 'approved': True})[0] == 409
    assert fx.calls.empty()
    assert fx.store.find_canvas_node(fx.pid, 'b')['prompt'] == 'Minimalist art'
    assert not any(e['type'] == 'confirm' for e in list(fx.events.queue))


def provider_io_barrier(fx, monkeypatch, call_number=2, parties=1):
    """Pause configuration IO, not the resolver, freshness check or snapshot CAS."""
    entered = threading.Event()
    release = threading.Event()
    lock = threading.Lock()
    calls, waiting = [], []

    def load(**kwargs):
        snapshot = deepcopy(fx.providers)
        ident = threading.get_ident()
        with lock:
            calls.append(ident)
            block = (call_number - 1) * parties < len(calls) <= call_number * parties
            if block:
                waiting.append(ident)
                if len(waiting) == parties:
                    entered.set()
        if block:
            assert release.wait(5), 'test did not release provider configuration IO'
        return snapshot

    monkeypatch.setattr(main, 'load_api_providers', load)
    return entered, release


def test_two_edits_compete_on_one_preview_cas(fx, monkeypatch):
    _, receipt, _ = _transports_start_batch_487(fx, ['b'])
    old = read_gate(fx, receipt)
    entered, release = provider_io_barrier(fx, monkeypatch, parties=2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(_preview_edit_mutate_487, fx, receipt, old, patch=patch) for patch in (
            {'prompt': 'Winner body'}, {'runSettings': {'count': 2}})]
        try:
            assert entered.wait(3), 'both actual preflights must reach external configuration IO'
        finally:
            release.set()
        responses = [job.result(5) for job in jobs]
    assert sorted(r.status_code for r in responses) == [200, 409], [r.text for r in responses]
    current = read_gate(fx, receipt)
    assert current['preflight']['preview_revision'] == 2
    row, = current['preflight']['items']
    assert (row['user_prompt'], row['count']) in [('Winner body', 1), ('Minimalist art', 2)]
    final = approve_current(fx, receipt)
    assert final['status'] == 'done' and final['spent'] == row['count'], final


def test_repreflight_install_does_not_hold_locks_across_external_io(fx, monkeypatch):
    _, receipt, _ = _transports_start_batch_487(fx, ['b'])
    old = read_gate(fx, receipt)
    entered, release = provider_io_barrier(fx, monkeypatch, call_number=3)
    responsive = False
    with ThreadPoolExecutor(max_workers=2) as pool:
        change = pool.submit(_preview_edit_mutate_487, fx, receipt, old)
        try:
            assert entered.wait(3), 'final original-input validation must reach real provider IO'
            read = pool.submit(read_gate, fx, receipt)
            try:
                responsive = read.result(.5) == old
            except TimeoutError:
                pass
        finally:
            release.set()
        response = change.result(5)
        read.result(5)
    assert responsive, 'current preview must remain readable while external preflight IO is paused'
    assert response.status_code == 200, response.text
    assert approve_current(fx, receipt)['spent'] == 1


@pytest.mark.parametrize('change', ['provider', 'source', 'group', 'origin'])
def test_source_or_provider_changes_during_repreflight_invalidate_install(fx, monkeypatch, change):
    if change == 'group':
        code, preview = rest(fx, 'preflight', {'group_id': 'frame'})
        assert code == 200 and not preview['blockers']
        code, receipt = rest(fx, 'request_batch', {'preflight_id': preview['preflight_id']})
        assert code == 200
        next_gate(fx)
    else:
        _, receipt, _ = _transports_start_batch_487(fx, ['b'])
    old = read_gate(fx, receipt)
    entered, release = provider_io_barrier(fx, monkeypatch, call_number=3)
    with ThreadPoolExecutor(max_workers=1) as pool:
        job = pool.submit(_preview_edit_mutate_487, fx, receipt, old)
        try:
            assert entered.wait(3)
            if change == 'provider':
                fx.providers[0]['base_url'] = 'https://changed.invalid/v1'
            elif change == 'source':
                patch_node(fx, 'b', prompt='Actual human change')
            elif change == 'group':
                patch_node(fx, 'frame', items=['b'])
            else:
                fx.session.input_epoch = 'new-input-epoch'
        finally:
            release.set()
        response = job.result(5)
    assert response.status_code == 409, response.text
    if change != 'origin':
        current = read_gate(fx, receipt)
        assert current['preflight'] == old['preflight'] and current['confirm_id'] == old['confirm_id']
    else:
        response = fx.client.get(preview_url(fx, receipt), headers={'X-Agent-Project': fx.pid})
        assert response.status_code == 409
    assert fx.calls.empty()
    assert dispatch(fx, 'http', {'confirm_id': old['confirm_id'], 'approved': True})[0] == 409
    assert rest(fx, 'stop_batch', {'batch_id': receipt['batch_id']})[0] == 200


@pytest.mark.parametrize('action,winner', [('edit', 'mutation'), ('remove', 'mutation'),
    ('edit', 'approval'), ('remove', 'approval')], ids=['edit-mutation', 'remove-mutation', 'edit-approval', 'remove-approval'])
def test_edit_approve_remove_races_cannot_execute_stale_plan(fx, monkeypatch, action, winner):
    preview, receipt, event = _transports_start_batch_487(fx)
    old = read_gate(fx, receipt)
    entered, release = provider_io_barrier(fx, monkeypatch, call_number=1 if winner == 'mutation' else 3)
    decision = {'confirm_id': event['confirm_id'], 'approved': True, 'snapshot_sha256': preview['snapshot_sha256']}
    responsive = False
    with ThreadPoolExecutor(max_workers=2) as pool:
        if winner == 'mutation':
            pending = pool.submit(dispatch, fx, 'http', decision)
        else:
            pending = pool.submit(_preview_edit_mutate_487, fx, receipt, old, action)
        try:
            assert entered.wait(3)
            if winner == 'mutation':
                competing = pool.submit(_preview_edit_mutate_487, fx, receipt, old, action)
                try:
                    installed = competing.result(1)
                    responsive = installed.status_code == 200
                except TimeoutError:
                    pass
            else:
                competing = pool.submit(dispatch, fx, 'http', decision)
                assert competing.result(3)[0] == 200
        finally:
            release.set()
        loser, other = pending.result(5), competing.result(5)
    if winner == 'mutation':
        assert responsive, 'snapshot install must not wait for another decision external IO or its locks'
        assert loser[0] == 409, loser
        final = approve_current(fx, receipt)
        assert final['spent'] == (1 if action == 'remove' else 2)
        calls = [fx.calls.get(timeout=3)[0] for _ in range(final['spent'])]
        if action == 'remove':
            assert calls == ['Minimalist art']
        else:
            assert calls[0].startswith('参考图说明：图1：产品主体') and calls[0].endswith('Revised body')
            assert 'A sculpture' not in calls[0] and calls[1] == 'Minimalist art'
    else:
        assert loser.status_code == 409, loser.text
        final = status_until(fx, receipt['batch_id'], lambda row: row['status'] == 'done')
        assert final['spent'] == final['approved_count'] == 2
        assert [fx.calls.get(timeout=3)[0] for _ in range(2)] == [r['final_prompt'] for r in preview['items']]
    assert final['status'] == 'done' and fx.calls.empty()


@pytest.mark.parametrize('mode', ['standard', 'autonomous', 'cautious'])
@pytest.mark.parametrize('action', ['edit', 'remove'])
def test_replaced_waiter_waits_for_new_confirmation_without_deadlock(fx, mode, action):
    fx.session.autonomy = mode
    _, receipt, event = _transports_start_batch_487(fx)
    handler = gate_handler(fx, event)
    old = read_gate(fx, receipt)
    response = _preview_edit_mutate_487(fx, receipt, old, action)
    assert response.status_code == 200, response.text
    resolved = fx.events.get(timeout=3)
    assert resolved['type'] == 'confirm_resolved' and resolved['confirm_id'] == event['confirm_id']
    assert resolved['reason'] == 'replaced' and resolved['approved'] is False
    gate = fx.events.get(timeout=3)
    current = response.json()
    assert gate['type'] == 'confirm' and gate['confirm_id'] == current['confirm_id'] != event['confirm_id']
    assert gate['params']['preflight'] == current['preflight']
    assert current['decision_token'] != old['decision_token']
    assert dispatch(fx, 'callback', {'confirm_id': event['confirm_id'], 'approved': True}, handler=handler)[0] == 409
    _, pending = rest(fx, 'batch_status', {'batch_id': receipt['batch_id']})
    assert pending['status'] == 'awaiting_confirmation' and pending['spent'] == pending['approved_count'] == 0
    assert fx.calls.empty()
    code, result = dispatch(fx, 'http', {'confirm_id': current['confirm_id'], 'approved': True})
    assert code == 200, result
    if mode == 'cautious':
        for row in current['preflight']['items']:
            spending_gate = next_gate(fx)
            assert spending_gate['params']['preflight']['items'] == [row]
            assert dispatch(fx, 'http', {'confirm_id': spending_gate['confirm_id'], 'approved': True})[0] == 200
    final = status_until(fx, receipt['batch_id'], lambda row: row['status'] == 'done')
    assert final['spent'] == final['approved_count'] == len(current['preflight']['items'])
    assert fx.calls.qsize() == final['spent']
    assert len({row['task_id'] for row in final['items']}) == final['spent']


@pytest.mark.parametrize('action', ['edit', 'remove'])
def test_hosted_replacement_queues_one_real_continuation(fx, action):
    async def begin_reader():
        return asyncio.create_task(asyncio.Event().wait())

    async def end_reader(reader):
        reader.cancel()
        try:
            await reader
        except asyncio.CancelledError:
            pass

    def history():
        response = fx.client.get(f'/api/projects/{fx.pid}/agent-nodes/actor/events')
        assert response.status_code == 200, response.text
        return [row['event'] for row in response.json()['events']]

    def continuations():
        return [event for event in history() if event['type'] == 'system_continuation']

    # A live reader and busy turn exercise the real hosted queue without starting a native process.
    reader = fx.client.portal.call(begin_reader)
    fx.session._reader_task = reader
    fx.session.status = 'busy'
    fx.session._last_user_turn_id = 'initiating-turn'
    identity = (fx.session.agent_cli, fx.session.generation, fx.session.input_epoch,
                fx.session._current_turn_id, fx.session._last_user_turn_id)
    try:
        sid = initialize_mcp(fx)
        ok, preview = mcp(fx, 'preflight', {'node_ids': ['a', 'b']}, sid=sid, hosted=True)
        assert ok, preview
        ok, receipt = mcp(fx, 'request_batch', {'preflight_id': preview['preflight_id']},
                          sid=sid, hosted=True)
        assert ok, receipt
        original = next_gate(fx)
        handler = gate_handler(fx, original)
        assert not continuations() and not fx.session._follow_ups
        revised = _preview_edit_mutate_487(fx, receipt, read_gate(fx, receipt), action)
        assert revised.status_code == 200, revised.text
        current = revised.json()
        assert next_gate(fx)['confirm_id'] == current['confirm_id'] != original['confirm_id']
        assert dispatch(fx, 'callback', {'confirm_id': original['confirm_id'], 'approved': True},
                        handler=handler)[0] == 409
        assert not continuations() and not fx.session._follow_ups and fx.calls.empty()
        assert dispatch(fx, 'http', {'confirm_id': current['confirm_id'], 'approved': True})[0] == 200
        final = status_until(fx, receipt['batch_id'],
                             lambda row: row['status'] == 'done' and bool(continuations()))
        continuation, = continuations()
        queued, = fx.session._follow_ups
        assert continuation['delivery'] == 'queued'
        assert continuation['turn_id'] == queued['turn_id'] == 'initiating-turn'
        assert continuation['request_id'] == queued['request_id'] and queued['_system'] is True
        assert (queued['host'], queued['generation'], queued['epoch']) == identity[:3]
        assert continuation['text'] == queued['message'] and receipt['batch_id'] in queued['message']
        assert final['spent'] == final['approved_count'] == len(current['preflight']['items'])
        assert fx.calls.qsize() == final['spent']
        assert (fx.session.agent_cli, fx.session.generation, fx.session.input_epoch,
                fx.session._current_turn_id, fx.session._last_user_turn_id) == identity
        recorded = history()
        assert not any(event['type'] in ('user_prompt', 'queued_input') for event in recorded)
        assert all(event['turn_id'] == identity[3] for event in recorded
                   if event['type'] in ('confirm', 'confirm_resolved', 'system_continuation'))
    finally:
        fx.client.portal.call(end_reader, reader)
        fx.session._reader_task = None


@pytest.mark.parametrize('kind', ['unknown', 'foreign', 'removed', 'revision', 'hash'])
def test_remove_unknown_foreign_or_stale_item_is_atomic(fx, kind):
    _, receipt, _ = _transports_start_batch_487(fx)
    old = read_gate(fx, receipt)
    current = old
    ident = 'item-never-issued'
    if kind == 'foreign':
        code, foreign = rest(fx, 'preflight', {'node_ids': ['b']})
        assert code == 200
        ident = foreign['items'][0]['item_id']
    elif kind in ('removed', 'revision', 'hash'):
        response = _preview_edit_mutate_487(fx, receipt, old, 'remove')
        assert response.status_code == 200, response.text
        current = response.json()
        next_gate(fx)
        ident = old['preflight']['items'][0]['item_id'] if kind == 'removed' else current['preflight']['items'][0]['item_id']
    body = {'preview_revision': current['preflight']['preview_revision'],
            'snapshot_sha256': current['preflight']['snapshot_sha256'], 'item_id': ident}
    if kind == 'revision':
        body['preview_revision'] = old['preflight']['preview_revision']
    elif kind == 'hash':
        body['snapshot_sha256'] = old['preflight']['snapshot_sha256']
    before = fx.store.canvas(fx.pid)
    response = fx.client.post(preview_url(fx, receipt) + '/remove', json=body, headers={'X-Agent-Project': fx.pid})
    assert response.status_code == 409 and response.json()['detail']['code'] == 'CONFIRM_STALE', response.text
    assert read_gate(fx, receipt) == current and fx.events.empty()
    assert fx.store.canvas(fx.pid) == before and fx.calls.empty()
    assert approve_current(fx, receipt)['status'] == 'done'


def test_two_removals_compete_on_one_preview_cas(fx, monkeypatch):
    _, receipt, _ = _transports_start_batch_487(fx)
    old = read_gate(fx, receipt)
    entered, release = provider_io_barrier(fx, monkeypatch, parties=2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(_preview_edit_mutate_487, fx, receipt, old, 'remove', item=row['item_id'])
                for row in old['preflight']['items']]
        try:
            assert entered.wait(3)
        finally:
            release.set()
        results = [job.result(5) for job in jobs]
    assert sorted(r.status_code for r in results) == [200, 409], [r.text for r in results]
    current = read_gate(fx, receipt)
    assert len(current['preflight']['items']) == 1 and current['preflight']['preview_revision'] == 2
    assert approve_current(fx, receipt)['spent'] == 1


@pytest.mark.parametrize('action', ['edit', 'remove'])
def test_running_items_use_existing_cancel_not_preview_mutation(fx, action):
    fx.release.clear()
    _, receipt, event = _transports_start_batch_487(fx, ['b'])
    old = read_gate(fx, receipt)
    assert dispatch(fx, 'http', {'confirm_id': event['confirm_id'], 'approved': True})[0] == 200
    fx.calls.get(timeout=3)
    running = status_until(fx, receipt['batch_id'], lambda row: row['items'][0]['status'] == 'running')
    before = fx.store.canvas(fx.pid)
    response = _preview_edit_mutate_487(fx, receipt, old, action)
    assert response.status_code == 409, response.text
    assert fx.store.canvas(fx.pid) == before
    task_id = running['items'][0]['task_id']
    cancelled = fx.client.post(f'/api/project-library/{fx.pid}/image-generation-tasks/{task_id}/cancel')
    assert cancelled.status_code == 200, cancelled.text
    fx.release.set()
    final = status_until(fx, receipt['batch_id'], lambda row: row['status'] == 'done')
    assert final['items'][0]['task_id'] == task_id and final['items'][0]['status'] == 'cancelled'
    assert final['spent'] == final['approved_count'] == 1 and fx.calls.empty()
    task = fx.client.get(f'/api/projects/{fx.pid}/generate/tasks/{task_id}')
    assert task.status_code == 200 and task.json()['status'] == 'cancelled', task.text
    assert _preview_edit_mutate_487(fx, receipt, old, action).status_code == 409


@pytest.mark.parametrize('action', ['edit', 'remove'])
@pytest.mark.parametrize('kind', ['bool-revision', 'float-revision', 'missing', 'hash-type', 'hash-format',
                                  'item-type', 'extra', 'null', 'list'])
def test_preview_mutation_envelope_is_strict_and_atomic(fx, action, kind):
    from tests.test_agent_batch_confirmation_transports_487 import preview_mutation_body
    _, receipt, _ = _transports_start_batch_487(fx)
    old = read_gate(fx, receipt)
    body = preview_mutation_body(old, action)
    if kind == 'bool-revision':
        body['preview_revision'] = True
    elif kind == 'float-revision':
        body['preview_revision'] = 1.0
    elif kind == 'missing':
        del body['snapshot_sha256']
    elif kind == 'hash-type':
        body['snapshot_sha256'] = True
    elif kind == 'hash-format':
        body['snapshot_sha256'] = 'not-a-digest'
    elif kind == 'item-type':
        body['item_id'] = 3
    elif kind == 'extra':
        body['unknown'] = 'not permitted'
    else:
        body = None if kind == 'null' else []
    response = fx.client.post(preview_url(fx, receipt) + '/' + action, headers={'X-Agent-Project': fx.pid}, json=body)
    assert response.status_code == 400 and response.json()['detail']['code'] == 'INVALID_ARGUMENT', response.text
    assert read_gate(fx, receipt) == old and fx.events.empty() and fx.calls.empty()
    assert approve_current(fx, receipt)['spent'] == 2


@pytest.mark.parametrize('case', ['expiry', 'fresh'])
def test_noop_still_checks_freshness_and_retains_original_deadline(fx, monkeypatch, case):
    from app.services import agent_batch
    clock = [100.0]
    monkeypatch.setattr(agent_batch, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    _, receipt, event = _transports_start_batch_487(fx, ['b'])
    old = read_gate(fx, receipt)
    clock[0] = 3699.0
    if case == 'fresh':
        patch_node(fx, 'b', prompt='Changed on the actual canvas')
    response = _preview_edit_mutate_487(fx, receipt, old, patch={'prompt': 'Minimalist art'})
    assert response.status_code == (409 if case == 'fresh' else 200), response.text
    assert read_gate(fx, receipt) == old and fx.events.empty() and fx.calls.empty()
    assert dispatch(fx, 'http', {'confirm_id': event['confirm_id'], 'approved': False})[0] == 200
    status_until(fx, receipt['batch_id'], lambda row: row['status'] == 'rejected')
    clock[0] = 3701.0
    expired = fx.client.get(preview_url(fx, receipt), headers={'X-Agent-Project': fx.pid})
    assert expired.status_code == 409, expired.text


@pytest.mark.parametrize('action', ['edit', 'remove'])
def test_cautious_admitted_selection_is_not_preview_mutable(fx, action):
    fx.session.autonomy = 'cautious'
    _, receipt, event = _transports_start_batch_487(fx, ['b'])
    old = read_gate(fx, receipt)
    assert dispatch(fx, 'http', {'confirm_id': event['confirm_id'], 'approved': True})[0] == 200
    spending = next_gate(fx)
    current = read_gate(fx, receipt)
    assert current['confirm_id'] == spending['confirm_id'] != old['confirm_id']
    response = _preview_edit_mutate_487(fx, receipt, current, action)
    assert response.status_code == 409, response.text
    assert read_gate(fx, receipt) == current and fx.calls.empty()
    assert dispatch(fx, 'http', {'confirm_id': spending['confirm_id'], 'approved': True})[0] == 200
    final = status_until(fx, receipt['batch_id'], lambda row: row['status'] == 'done')
    assert final['spent'] == final['approved_count'] == 1


def test_candidate_from_transient_source_cannot_be_installed_as_fresh(fx, monkeypatch):
    _, receipt, _ = _transports_start_batch_487(fx, ['b'])
    old = read_gate(fx, receipt)
    original_node = fx.store.find_canvas_node(fx.pid, 'b')
    entered = [threading.Event(), threading.Event()]
    release = [threading.Event(), threading.Event()]
    calls = []

    def load(**kwargs):
        index = len(calls)
        calls.append(index)
        if index < 2:
            entered[index].set()
            assert release[index].wait(5)
        return deepcopy(fx.providers)

    monkeypatch.setattr(main, 'load_api_providers', load)
    with ThreadPoolExecutor(max_workers=1) as pool:
        job = pool.submit(_preview_edit_mutate_487, fx, receipt, old, patch={'runSettings': {'count': 2}})
        try:
            assert entered[0].wait(3)
            patch_node(fx, 'b', prompt='Transient input never approved')
            release[0].set()
            assert entered[1].wait(3)
            fx.store.apply_mutations(fx.pid, [{'op': 'upsert_node', 'node': original_node}],
                                     base_revision=fx.store.canvas(fx.pid)['revision'])
            assert fx.store.find_canvas_node(fx.pid, 'b') == original_node
        finally:
            for signal in release:
                signal.set()
        response = job.result(5)
    assert response.status_code == 409, response.text
    assert read_gate(fx, receipt) == old and fx.calls.empty() and fx.events.empty()
    assert approve_current(fx, receipt)['spent'] == 1
    assert fx.calls.get(timeout=3)[0] == 'Minimalist art'


def test_candidate_from_transient_provider_cannot_be_installed_as_fresh(fx, monkeypatch):
    _, receipt, _ = _transports_start_batch_487(fx, ['b'])
    old = read_gate(fx, receipt)
    entered = [threading.Event(), threading.Event()]
    release = [threading.Event(), threading.Event()]
    calls = []
    original_url = fx.providers[0]['base_url']

    def load(**kwargs):
        snapshot = deepcopy(fx.providers)
        index = len(calls)
        calls.append(index)
        if index < 2:
            entered[index].set()
            assert release[index].wait(5)
        return snapshot

    monkeypatch.setattr(main, 'load_api_providers', load)
    with ThreadPoolExecutor(max_workers=1) as pool:
        job = pool.submit(_preview_edit_mutate_487, fx, receipt, old, patch={'runSettings': {'count': 2}})
        try:
            assert entered[0].wait(3)
            fx.providers[0]['base_url'] = 'https://transient.invalid/v1'
            release[0].set()
            assert entered[1].wait(3)
            fx.providers[0]['base_url'] = original_url
        finally:
            for signal in release:
                signal.set()
        response = job.result(5)
    assert response.status_code == 409, response.text
    assert read_gate(fx, receipt) == old and fx.calls.empty() and fx.events.empty()
    assert approve_current(fx, receipt)['spent'] == 1


def test_prompt_body_override_keeps_saved_mention_reference_map(fx):
    url = f'/api/project-library/{fx.pid}/media/{fx.ids[0]}'
    patch_node(fx, 'a', promptDraftHtml='Original <span class="mention-image-token" '
               f'data-url="{url}" data-asset-id="{fx.ids[0]}" data-name="subject">opaque</span> body')
    preview, receipt, _ = _transports_start_batch_487(fx, ['a'])
    old = read_gate(fx, receipt)
    assert '下面是参考图编号：' in preview['items'][0]['final_prompt']
    response = _preview_edit_mutate_487(fx, receipt, old, patch={'prompt': 'Replacement body only'})
    assert response.status_code == 200, response.text
    revised = response.json()['preflight']['items'][0]
    assert revised['final_prompt'].startswith('参考图说明：图1：产品主体')
    assert '下面是参考图编号：\n图1：' in revised['final_prompt']
    assert revised['final_prompt'].endswith('用户需求：\nReplacement body only')
    assert revised['user_prompt'] == 'Replacement body only' and revised['refs'] == preview['items'][0]['refs']
    assert approve_current(fx, receipt)['spent'] == 1
    call = fx.calls.get(timeout=3)
    assert call[0] == revised['final_prompt'] and len(call[4]) == 9


@pytest.mark.parametrize('stale', ['revision', 'hash'])
def test_edit_cannot_reuse_an_old_revision_or_hash(fx, stale):
    from tests.test_agent_batch_confirmation_transports_487 import revise_gate, preview_mutation_body
    _, receipt, _ = _transports_start_batch_487(fx, ['b'])
    old, current = revise_gate(fx, receipt, 'edit')
    body = preview_mutation_body(current, 'edit')
    key = 'preview_revision' if stale == 'revision' else 'snapshot_sha256'
    body[key] = old['preflight'][key]
    response = fx.client.post(preview_url(fx, receipt) + '/edit', headers={'X-Agent-Project': fx.pid}, json=body)
    assert response.status_code == 409 and response.json()['detail']['code'] == 'CONFIRM_STALE', response.text
    assert read_gate(fx, receipt) == current and fx.events.empty() and fx.calls.empty()
    assert approve_current(fx, receipt)['spent'] == 1
