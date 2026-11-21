"""A12 legacy batch approvals are revalidated at each durable child admission."""
import asyncio
from copy import deepcopy
from html import escape
import json
from queue import Queue
import threading
import time

import pytest

import main
from app.routers import api_agent_tools as agent
from app.services.agent_confirmation import Origin
from app.services.agent_host import agent_host_manager as host
from app.services.agent_ops_store import list_operations
from firefly_opsdb import OpsDb
from tests.test_agent_batch_400 import confirm, fx, patch_node, tree  # noqa: F401


PROMPT = 'Approved <legacy> batch prompt'


@pytest.fixture
def outcomes(monkeypatch):
    results = Queue()

    async def finished(origin, result):
        results.put(result)

    monkeypatch.setattr(Origin, 'continue_with', finished)
    return results


def legacy_batch(fx, transport):
    args = {'node_ids': ['a', 'b'], 'prompt': PROMPT}
    if transport == 'rest':
        response = fx.client.post(f'/api/projects/{fx.pid}/canvas/batch_edit?agent_node_id=actor',
                                  headers={'X-Agent-Project': fx.pid}, json=args)
        assert response.status_code == 200, response.text
        return response.json()
    response = fx.client.post(f'/api/mcp?project={fx.pid}&node=actor&caller=hosted',
                              headers={'Mcp-Session-Id': 'legacy-402'}, json={
                                  'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                                  'params': {'name': 'canvas.batch_edit', 'arguments': args}})
    assert response.status_code == 200, response.text
    result = response.json()['result']
    assert not result['isError'], result
    return json.loads(result['content'][0]['text'])


def wait_settled(fx, task_id):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        with main.CANVAS_TASK_LOCK:
            task = deepcopy(main.CANVAS_TASKS.get(task_id, {}))
        if task.get('agent_settled'):
            assert task['status'] == 'succeeded', task
            return task
        time.sleep(.01)
    pytest.fail(f'First paid task did not settle: {task}')


def prepare_sources(fx):
    for ident in ('a', 'b'):
        patch_node(fx, ident, promptDraftText='Old draft', promptDraftHtml='Old draft',
                   promptEditedByUser=True)
    canvas = fx.store.canvas(fx.pid)
    fx.store.apply_mutations(fx.pid, [{'op': 'upsert_edge', 'edge': {
        'id': 'ref-b', 'kind': 'input', 'agentRef': True, 'from': 'ref', 'to': 'b',
        'order': 1, 'role': 'detail', 'enabled': True, 'asset_id': fx.ids[0]}}],
        base_revision=canvas['revision'])


@pytest.fixture
def admissions(fx, monkeypatch):
    before_second, resume = threading.Event(), threading.Event()
    registered, locked = [], []
    original = agent._AgentImageLifecycle.register

    async def register(lifecycle, record):
        registered.append((lifecycle.metadata['source_node_id'], lifecycle.task_id, lifecycle.result_id))
        if lifecycle.metadata['source_node_id'] == 'b':
            before_second.set()
            assert await asyncio.to_thread(resume.wait, 10), 'Second admission was not resumed'
        validate = lifecycle.validate_start
        assert validate is not None

        def validate_locked(*args):
            locked.append(fx.store._lock._is_owned())
            return validate(*args)

        lifecycle.validate_start = validate_locked
        return await original(lifecycle, record)

    monkeypatch.setattr(agent._AgentImageLifecycle, 'register', register)
    yield before_second, resume, registered, locked
    resume.set()


def approve_and_pause(fx, transport, admissions):
    before_second, _, registered, _ = admissions
    prepare_sources(fx)
    receipt = legacy_batch(fx, transport)
    assert receipt['code'] == 'CONFIRM_PENDING' and receipt['status'] == 'awaiting_confirmation'
    event = fx.events.get(timeout=2)
    assert event['tool'] == 'canvas.batch_edit' and event['confirm_id'] == receipt['confirm_id']
    assert event['params']['n'] == 2
    assert [item['node_id'] for item in event['params']['preflight']] == ['a', 'b']
    assert fx.calls.empty() and not agent._GENERATION_COUNTS.get((fx.pid, 'actor'))
    fx.session._current_turn_id = None
    confirm(fx, event)
    assert before_second.wait(5), 'Batch did not reach its second child'
    assert [item[0] for item in registered] == ['a', 'b']
    first = wait_settled(fx, registered[0][1])
    call = fx.calls.get(timeout=2)
    approved = event['params']['preflight'][0]
    assert (call[0], call[2], call[3], call[4]) == (
        approved['final_prompt'], approved['model'], approved['provider'],
        [ref['thumb'] for ref in approved['refs']])
    assert call[5]['approved_provider']['base_url'] == 'https://example.invalid/v1'
    for ident in ('a', 'b'):
        source = fx.store.find_canvas_node(fx.pid, ident)
        assert source['prompt'] == source['promptDraftText'] == PROMPT
        assert source['promptDraftHtml'] == escape(PROMPT) and source['promptEditedByUser'] is True
    assert fx.store.find_canvas_node(fx.pid, registered[1][2]) is None
    return first


def invalidate(fx, change):
    if change == 'prompt':
        patch_node(fx, 'b', prompt='Human replacement')
    elif change == 'draft':
        patch_node(fx, 'b', promptDraftText='Human draft', promptDraftHtml='Human draft')
    elif change == 'identity':
        patch_node(fx, 'b', created_at='replacement-node')
    elif change == 'settings':
        patch_node(fx, 'b', runSettings={**fx.store.find_canvas_node(fx.pid, 'b')['runSettings'], 'count': 2})
    elif change == 'canvas_settings':
        with fx.store._lock:
            fx.store._canvases[fx.pid]['settings'] = {'resolution': '2k'}
    elif change in ('reference', 'role'):
        canvas = fx.store.canvas(fx.pid)
        edge = next(edge for edge in canvas['connections'] if edge['id'] == 'ref-b')
        changes = {'asset_id': fx.ids[1]} if change == 'reference' else {'role': 'product'}
        fx.store.apply_mutations(fx.pid, [{'op': 'upsert_edge', 'edge': {**edge, **changes}}],
                                 base_revision=canvas['revision'])
    elif change == 'provider':
        fx.providers[0]['base_url'] = 'https://changed.invalid/v1'
    elif change == 'abort':
        fx.client.portal.call(host.stop_session, fx.session)
    elif change == 'actor':
        fx.store.apply_mutations(fx.pid, [{'op': 'remove_node', 'node_id': 'actor'}],
                                 base_revision=fx.store.canvas(fx.pid)['revision'])
    else:
        raise AssertionError(change)


@pytest.mark.parametrize('transport', ['rest', 'mcp'])
@pytest.mark.parametrize('change', ['prompt', 'draft', 'identity', 'settings', 'canvas_settings',
                                   'reference', 'role', 'provider', 'abort', 'actor'])
def test_changed_second_admission_preserves_paid_first_but_never_registers_second(
        fx, outcomes, admissions, transport, change):
    _, resume, registered, locked = admissions
    first = approve_and_pause(fx, transport, admissions)
    before_first = deepcopy(fx.store.find_canvas_node(fx.pid, first['result_node_id']))
    try:
        invalidate(fx, change)
    finally:
        resume.set()
    result = outcomes.get(timeout=5)
    error = result['error']
    assert error['code'] == ('CONFIRM_STALE' if change in ('provider', 'abort', 'actor') else 'CANVAS_CONFLICT')
    assert error['retryable'] is False and error['details']['status'] == 'partial-unknown'
    assert error['details']['tasks'] == [{'node_id': 'a', 'task_id': first['id']}]
    assert fx.calls.empty() and registered[1][1] not in main.CANVAS_TASKS
    assert fx.store.find_canvas_node(fx.pid, registered[1][2]) is None
    assert fx.store.find_canvas_node(fx.pid, first['result_node_id']) == before_first
    assert before_first['images'] and locked and all(locked)
    canvas = fx.store.canvas(fx.pid)
    assert [row['task_id'] for row in canvas['history']] == [first['id']]
    family = [row for row in list_operations(OpsDb(fx.store.root), fx.pid) if row['tool'] == 'canvas.batch_edit']
    root, = [row for row in family if not row.get('parent_op_id')]
    assert root['op_id'] == error['details']['op_id'] and root['turn_id'] == 'initiating-turn'
    assert {'a', 'b'} <= set(root['touched_node_ids'])
    assert all(row.get('parent_op_id') == root['op_id'] for row in family if row is not root)
    assert agent._GENERATION_COUNTS[(fx.pid, 'actor')] == 2


@pytest.mark.parametrize('transport', ['rest', 'mcp'])
def test_unchanged_second_admission_allows_first_result_and_prompt_mutations(fx, outcomes, admissions, transport):
    _, resume, registered, locked = admissions
    approve_and_pause(fx, transport, admissions)
    resume.set()
    result = outcomes.get(timeout=5)
    assert result['success'] is True and result['count'] == 2
    assert result['tasks'] == [{'node_id': node, 'task_id': task} for node, task, _ in registered]
    second = wait_settled(fx, registered[1][1])
    assert fx.calls.get(timeout=2)[4] == [f'/api/project-library/{fx.pid}/media/{fx.ids[0]}']
    assert fx.calls.empty() and locked == [True, True]
    assert fx.store.find_canvas_node(fx.pid, second['result_node_id'])['images']
    assert len(fx.store.canvas(fx.pid)['history']) == 2


@pytest.mark.parametrize('transport', ['rest', 'mcp'])
def test_second_input_invalidation_does_not_cancel_or_roll_back_inflight_paid_first(
        fx, outcomes, admissions, transport):
    before_second, resume, registered, locked = admissions
    fx.release.clear()
    try:
        prepare_sources(fx)
        assert legacy_batch(fx, transport)['code'] == 'CONFIRM_PENDING'
        confirm(fx, fx.events.get(timeout=2))
        assert before_second.wait(5)
        fx.calls.get(timeout=2)
        first_task, first_result = registered[0][1:]
        assert fx.store.find_canvas_node(fx.pid, first_result)['pendingTasks']
        invalidate(fx, 'settings')
        resume.set()
        error = outcomes.get(timeout=5)['error']
        assert error['code'] == 'CANVAS_CONFLICT' and error['retryable'] is False
        assert error['details']['tasks'] == [{'node_id': 'a', 'task_id': first_task}]
        assert fx.calls.empty() and registered[1][1] not in main.CANVAS_TASKS
        assert fx.store.find_canvas_node(fx.pid, registered[1][2]) is None
        assert fx.store.find_canvas_node(fx.pid, first_result)['pendingTasks']
    finally:
        resume.set()
        fx.release.set()
    wait_settled(fx, first_task)
    assert fx.store.find_canvas_node(fx.pid, first_result)['images']
    assert fx.calls.empty() and locked == [True, True]
    assert [row['task_id'] for row in fx.store.canvas(fx.pid)['history']] == [first_task]


@pytest.mark.parametrize('change', ['prompt', 'provider', 'actor'])
def test_stale_approval_is_checked_before_legacy_batch_debit_or_prompt_write(fx, outcomes, change):
    prepare_sources(fx)
    receipt = legacy_batch(fx, 'rest')
    event = fx.events.get(timeout=2)
    assert receipt['code'] == 'CONFIRM_PENDING'
    invalidate(fx, change)
    before = tree(fx)
    confirm(fx, event)
    result = outcomes.get(timeout=5)
    assert result['error']['code'] == 'CONFIRM_STALE'
    assert fx.calls.empty() and tree(fx) == before
    assert not agent._GENERATION_COUNTS.get((fx.pid, 'actor'))
