"""Agent plan approvals resume their original execution through public routes (#475)."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import time

import pytest

# Routers bind the application module while importing the shared route fixture.
import main
from app.routers import api_agent_tools as tools
from tests.test_agent_plan_399 import env as plan_environment, png
from tests.test_agent_plan_apply_484 import ApplyHTTP, drain, headers, track_lifetime


@pytest.fixture
def plan_http(tmp_path, monkeypatch, request):
    monkeypatch.setenv('FIREFLY_PROJECTS_ROOT', str(tmp_path / 'projects'))
    fixture = plan_environment.__wrapped__(tmp_path, monkeypatch)
    resource = next(fixture)
    try:
        yield ApplyHTTP(resource, mcp=getattr(request, 'param', False))
    finally:
        try:
            next(fixture)
        except StopIteration:
            pass


def public_state(http):
    result = {}
    for name, path in (('nodes', 'canvas/nodes'), ('ops', 'ops')):
        response = http.client.get(f'/api/projects/{http.project}/{path}', headers=headers(http.project))
        assert response.status_code == 200, response.text
        result[name] = response.json()
    return result


def agent_apply(http, **args):
    return http.client.post(f'/api/projects/{http.project}/plan/apply',
        params={'agent_node_id': 'actor'}, headers=headers(http.project),
        json={'plan_id': http.draft['plan_id'], 'generation': http.draft['generation'], **args})


def external_call(http, operation, arguments, *, turn_id=None, sid='plan-test'):
    params = {'project': http.project, 'node': 'actor', 'caller': 'external'}
    if turn_id is not None:
        params['turn_id'] = turn_id
    response = http.client.post('/api/mcp', params=params, headers={'Mcp-Session-Id': sid}, json={
        'jsonrpc': '2.0', 'id': 475, 'method': 'tools/call',
        'params': {'name': operation, 'arguments': arguments}})
    assert response.status_code == 200, response.text
    envelope = response.json()
    assert envelope['id'] == 475 and 'result' in envelope, envelope
    return envelope['result']


def external_apply(http, *, turn_id=None):
    return external_call(http, 'plan.apply',
        {'plan_id': http.draft['plan_id'], 'generation': http.draft['generation']}, turn_id=turn_id)


def set_autonomy(http, autonomy):
    response = http.client.post(f'/api/projects/{http.project}/agent-nodes/actor/autonomy',
        headers=headers(http.project), json={'autonomy': autonomy})
    assert response.status_code == 200, response.text


@pytest.mark.parametrize('autonomy', ['cautious', 'standard', 'autonomous'])
@pytest.mark.parametrize('window', [0, .01], ids=['zero', 'positive'])
def test_R01_rest_late_approval_persists_original_application_and_real_undo(plan_http, monkeypatch, autonomy, window):
    http = plan_http
    set_autonomy(http, autonomy)
    http.select(0)
    tasks = track_lifetime(monkeypatch)
    monkeypatch.setattr(tools, 'AGENT_WRITE_CONFIRM_TIMEOUT_SECONDS', window)
    before = public_state(http)
    response = agent_apply(http)
    event = http.events.get(timeout=10)
    try:
        assert response.status_code == 200, response.text
        pending = response.json()
        assert (pending['code'], pending['status'], pending['confirm_id']) == (
            'CONFIRM_PENDING', 'awaiting_confirmation', event['confirm_id'])
        assert http.read()['application'] == {
            'state': 'awaiting_confirmation', 'generation': 2,
            'receipt': None, 'confirm_id': event['confirm_id']}
        assert public_state(http) == before

        http.confirm(event)
        drain(http.client, tasks)
        application = http.read()['application']
        assert application['state'] == 'applied'
        receipt = application['receipt']
        assert receipt['applied_by'] == 'agent' and receipt['generation'] == 2
        result = receipt['result']
        assert [task['task_id'] for task in result['tasks']] == [http.draft['preview']['tasks'][0]['task_id']]
        after = public_state(http)
        assert after['nodes']['count'] == 5
        operations = after['ops']['operations']
        assert len(operations) == 1
        assert (operations[0]['op_id'], operations[0]['tool'], operations[0]['agent_node_id']) == (
            result['op_id'], 'plan.apply', 'actor')
        assert set(operations[0]['touched_node_ids']) == set(result['touched_node_ids'])
        undo = http.client.post(f'/api/projects/{http.project}/ops/undo',
            headers=headers(http.project), json={'op_id': result['op_id']})
        assert undo.status_code == 200, undo.text
        assert undo.json()['reverted'] == [result['op_id']]
        assert public_state(http)['nodes'] == before['nodes']
        assert http.read()['application']['receipt'] == receipt
    finally:
        http.confirm(event, False)
        drain(http.client, tasks)


@pytest.mark.parametrize('plan_http', [True], indirect=True, ids=['external-original-SID'])
@pytest.mark.parametrize('autonomy', ['cautious', 'standard', 'autonomous'])
@pytest.mark.parametrize('turn_id', [None, 'original-475'], ids=['omitted-turn', 'explicit-turn'])
def test_R02_external_late_approval_keeps_the_original_sid_and_turn(plan_http, monkeypatch, autonomy, turn_id):
    http = plan_http
    set_autonomy(http, autonomy)
    http.select(0)
    tasks = track_lifetime(monkeypatch)
    monkeypatch.setattr(tools, 'AGENT_WRITE_CONFIRM_TIMEOUT_SECONDS', .01)
    before = public_state(http)
    result = external_apply(http, turn_id=turn_id)
    event = http.events.get(timeout=10)
    try:
        assert result['isError'] is False, result
        pending = json.loads(result['content'][0]['text'])
        assert pending['code'] == 'CONFIRM_PENDING' and pending['confirm_id'] == event['confirm_id']
        assert event['turn_id'] == turn_id
        assert http.read()['application']['state'] == 'awaiting_confirmation'
        assert public_state(http) == before
        http.confirm(event)
        drain(http.client, tasks)
        application = http.read()['application']
        assert application['state'] == 'applied'
        receipt = application['receipt']
        assert receipt['applied_by'] == 'agent'
        after = public_state(http)
        assert after['nodes']['count'] == 5
        assert len(after['ops']['operations']) == 1
        operation = after['ops']['operations'][0]
        assert operation['turn_id'] == turn_id
        assert (operation['op_id'], operation['tool'], operation['agent_node_id']) == (
            receipt['result']['op_id'], 'plan.apply', 'actor')
        imported = external_call(http, 'plan.import_excel', {'attachment_id': http.source['id']})
        assert imported['isError'] is False, imported
        assert json.loads(imported['content'][0]['text'])['application'] == application
        undo = http.client.post(f'/api/projects/{http.project}/ops/undo',
            headers=headers(http.project), json={'op_id': operation['op_id']})
        assert undo.status_code == 200 and undo.json()['reverted'] == [operation['op_id']], undo.text
        assert public_state(http)['nodes'] == before['nodes']
    finally:
        http.confirm(event, False)
        drain(http.client, tasks)


@pytest.mark.parametrize('plan_http', [True], indirect=True, ids=['external-original-SID'])
def test_R11_public_stop_retires_processless_external_pending_application(plan_http, monkeypatch):
    http = plan_http
    http.select(0)
    tasks = track_lifetime(monkeypatch)
    monkeypatch.setattr(tools, 'AGENT_WRITE_CONFIRM_TIMEOUT_SECONDS', .01)
    status = http.client.get(f'/api/projects/{http.project}/agent-nodes/actor/status')
    assert status.status_code == 200 and status.json()['is_running'] is False, status.text
    before = public_state(http)
    result = external_apply(http)
    event = http.events.get(timeout=10)
    try:
        assert result['isError'] is False, result
        assert json.loads(result['content'][0]['text'])['code'] == 'CONFIRM_PENDING'
        assert http.read()['application']['state'] == 'awaiting_confirmation'
        stop = http.client.delete(f'/api/projects/{http.project}/agent-nodes/actor', headers=headers(http.project))
        assert stop.status_code == 200 and stop.json()['status'] == 'stopped', stop.text
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            application = http.read()['application']
            if application['state'] != 'awaiting_confirmation':
                break
            time.sleep(.01)
        assert application['state'] == 'ready', application
        assert application['receipt'] is None
        drain(http.client, tasks)
        http.confirm(event)
        drain(http.client, tasks)
        assert public_state(http) == before
        assert not http.session.confirm_handlers
    finally:
        http.confirm(event, False)
        drain(http.client, tasks)


@pytest.mark.parametrize('plan_http', [True], indirect=True, ids=['external-original-SID'])
def test_R11_processless_stop_retires_external_apply_admitted_before_gate_publication(plan_http, monkeypatch):
    http = plan_http
    http.select(0)
    before = public_state(http)
    tasks = track_lifetime(monkeypatch)
    monkeypatch.setattr(tools, 'AGENT_WRITE_CONFIRM_TIMEOUT_SECONDS', .2)
    portal = http.client.portal
    lock = http.host._lock

    async def wait_for_lock_waiters(count):
        async def wait():
            while sum(not future.done() for future in (lock._waiters or ())) < count:
                await asyncio.sleep(0)
        await asyncio.wait_for(wait(), timeout=5)

    # Queue the real DELETE ahead of the confirmation's session lookup without replacing either route.
    with ThreadPoolExecutor(max_workers=2) as pool:
        portal.call(lock.acquire)
        try:
            stop = pool.submit(http.client.delete, f'/api/projects/{http.project}/agent-nodes/actor',
                               headers=headers(http.project))
            portal.call(wait_for_lock_waiters, 1)
            apply = pool.submit(external_apply, http)
            portal.call(wait_for_lock_waiters, 2)
            assert http.events.empty()
            assert http.read()['application']['state'] == 'awaiting_confirmation'
        finally:
            portal.call(lock.release)
        stopped = stop.result(timeout=10)
        assert stopped.status_code == 200 and stopped.json()['status'] == 'stopped', stopped.text
        result = apply.result(timeout=10)
    try:
        assert result['isError'] is True, result
        assert result['structuredContent']['code'] in ('PLAN_STALE', 'CONFIRM_STALE'), result
        drain(http.client, tasks)
        assert http.read()['application']['state'] == 'ready'
        assert public_state(http) == before
        status = http.client.get(f'/api/projects/{http.project}/agent-nodes/actor/status')
        assert status.status_code == 200 and status.json()['is_running'] is False, status.text
    finally:
        if not http.events.empty():
            http.confirm(http.events.get_nowait(), False)
        drain(http.client, tasks)


@pytest.mark.parametrize('plan_http', [True], indirect=True, ids=['external-original-SID'])
@pytest.mark.parametrize('decision', ['approve', 'stop'])
def test_R29_external_pending_turn_releases_exact_expiry_protection_after_settlement(
        plan_http, monkeypatch, decision):
    from types import SimpleNamespace
    from app.services import agent_ops_store

    http = plan_http
    http.select(0)
    tasks = track_lifetime(monkeypatch)
    monkeypatch.setattr(tools, 'AGENT_WRITE_CONFIRM_TIMEOUT_SECONDS', .01)
    clock = [10000.0]
    monkeypatch.setattr(agent_ops_store, 'time', SimpleNamespace(time=lambda: clock[0]))
    monkeypatch.setattr(agent_ops_store, 'MAX_OPERATIONS', 1)
    before_seed = public_state(http)['nodes']
    seed = external_call(http, 'canvas.create_node',
        {'node': {'type': 'prompt', 'x': 800, 'y': 800, 'text': 'original turn'}}, turn_id='original-475')
    assert seed['isError'] is False, seed
    seed_result = json.loads(seed['content'][0]['text'])
    before_apply = public_state(http)['nodes']
    result = external_apply(http, turn_id='original-475')
    event = http.events.get(timeout=10)

    def original_turn():
        response = http.client.get(f'/api/projects/{http.project}/ops',
            params={'turn_id': 'original-475'}, headers=headers(http.project))
        assert response.status_code == 200, response.text
        return response.json()['operations']

    def pressure(name, color):
        path = http.store.root / http.project / (name + '.png')
        path.write_bytes(png(color))
        response = external_call(http, 'media.upload', {'file_path': str(path)},
            turn_id=name, sid='pressure-475')
        assert response['isError'] is False, response
        assert public_state(http)['nodes'] == before_apply
        assert http.read()['application']['state'] == 'awaiting_confirmation'

    try:
        assert result['isError'] is False, result
        assert json.loads(result['content'][0]['text'])['code'] == 'CONFIRM_PENDING'
        assert [op['op_id'] for op in original_turn()] == [seed_result['op_id']]
        clock[0] += agent_ops_store.EXTERNAL_TURN_INACTIVITY_SECONDS - .001
        pressure('before-boundary', 'red')
        assert [op['op_id'] for op in original_turn()] == [seed_result['op_id']]
        clock[0] += .001
        pressure('at-boundary', 'blue')
        assert [op['op_id'] for op in original_turn()] == [seed_result['op_id']]

        if decision == 'stop':
            stop = http.client.delete(f'/api/projects/{http.project}/agent-nodes/actor', headers=headers(http.project))
            assert stop.status_code == 200 and stop.json()['status'] == 'stopped', stop.text
            drain(http.client, tasks)
            application = http.read()['application']
            assert application['state'] == 'ready' and application['receipt'] is None, application
            assert public_state(http)['nodes'] == before_apply
            assert [op['op_id'] for op in original_turn()] == [seed_result['op_id']]
        else:
            http.confirm(event)
            drain(http.client, tasks)
            application = http.read()['application']
            assert application['state'] == 'applied', application
            receipt = application['receipt']
            records = original_turn()
            assert {op['op_id'] for op in records} == {seed_result['op_id'], receipt['result']['op_id']}
            assert all(op['turn_id'] == 'original-475' and op['agent_node_id'] == 'actor' for op in records)
            undo = http.client.post(f'/api/projects/{http.project}/ops/undo',
                headers=headers(http.project), json={'turn_id': 'original-475'})
            assert undo.status_code == 200, undo.text
            assert set(undo.json()['reverted']) == {op['op_id'] for op in records}
            assert public_state(http)['nodes'] == before_seed
        clock[0] += agent_ops_store.EXTERNAL_TURN_INACTIVITY_SECONDS
        path = http.store.root / http.project / 'after-release.png'
        path.write_bytes(png('green'))
        released = external_call(http, 'media.upload', {'file_path': str(path)},
            turn_id='after-release', sid='pressure-475')
        assert released['isError'] is False, released
        assert original_turn() == []
    finally:
        http.confirm(event, False)
        drain(http.client, tasks)


def test_R17_changed_attachment_retires_rest_pending_without_user_response(plan_http, monkeypatch):
    http = plan_http
    http.select(0)
    tasks = track_lifetime(monkeypatch)
    monkeypatch.setattr(tools, 'AGENT_WRITE_CONFIRM_TIMEOUT_SECONDS', .01)
    before = public_state(http)
    response = agent_apply(http)
    event = http.events.get(timeout=10)
    try:
        assert response.status_code == 200 and response.json()['code'] == 'CONFIRM_PENDING', response.text
        Path(http.source['path']).write_bytes(b'x' * http.source['size'])
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            application = http.read()['application']
            if application['state'] != 'awaiting_confirmation':
                break
            time.sleep(.01)
        assert application['state'] == 'ready', application
        assert application['receipt'] is None and application['error']['code'] == 'PLAN_STALE', application
        drain(http.client, tasks)
        assert not http.session.confirm_handlers
        http.confirm(event)
        assert public_state(http) == before
        records = http.client.get(f'/api/projects/{http.project}/agent-nodes/actor/events',
            params={'host': 'pi'})
        assert records.status_code == 200, records.text
        assert any(record['event']['type'] == 'confirm_resolved' and
            record['event']['confirm_id'] == event['confirm_id'] and not record['event']['approved']
            for record in records.json()['events'])
    finally:
        http.confirm(event, False)
        drain(http.client, tasks)
