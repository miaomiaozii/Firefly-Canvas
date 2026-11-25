"""Public lifecycle characterization; only native pipes, clocks and transport fail (#475)."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
from threading import Event
import time
from uuid import uuid4

import httpx
import pytest

# Shared fixtures import the application before routers bind their main module.
import main
from app.routers import api_agent_tools as tools, api_project_library
from app.services import agent_host
from tests.test_agent_plan_apply_484 import drain, headers, track_lifetime
from tests.test_agent_plan_present_binding_482 import binding_http, ORIGINAL_SID
from tests.test_agent_plan_resumable_hosted_475 import (
    live_http, hosted_imported_475 as imported, apply, hosted_confirm_475 as confirm, read, operations, inputs, wait_record, end_response,
)


HOSTS = [pytest.param('actor', id='Pi'), pytest.param('codex-actor', id='Codex')]
CHANNELS = [pytest.param('hosted', 'actor', id='hosted-Pi'),
            pytest.param('hosted', 'codex-actor', id='hosted-Codex'),
            pytest.param('external', 'actor', id='external-active-Pi')]


def lifecycle_canvas_475(http):
    response = http.client.get(f'/api/project-library/{http.project}')
    assert response.status_code == 200, response.text
    return response.json()['canvas']


def mutate_canvas(http, mutations):
    current = lifecycle_canvas_475(http)
    response = http.client.patch(f'/api/project-library/{http.project}/canvas', headers=headers(http.project),
        json={'base_revision': current['revision'], 'mutations': mutations})
    assert response.status_code == 200, response.text
    return response.json()


def nodes(http):
    response = http.client.get(f'/api/projects/{http.project}/canvas/nodes', headers=headers(http.project))
    assert response.status_code == 200, response.text
    return response.json()['nodes']


def lifecycle_pending_475(http, draft, transport, actor):
    ok, result = apply(http, draft, transport, actor)
    event = http.wait_event(actor, 'confirm')['event']
    assert ok and result['code'] == 'CONFIRM_PENDING', result
    assert result['confirm_id'] == event['confirm_id']
    assert read(http, draft, actor)['state'] == 'awaiting_confirmation'
    assert operations(http) == []
    return event


def retired(http, draft, actor):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        application = read(http, draft, actor)
        if application['state'] != 'awaiting_confirmation':
            assert application['state'] == 'ready' and application['receipt'] is None, application
            return application
        time.sleep(.01)
    pytest.fail('Public preview did not retire its pending execution')


def applied(http, draft, actor):
    application = read(http, draft, actor)
    assert application['state'] == 'applied', application
    receipt = application['receipt']
    assert receipt['applied_by'] == 'agent' and receipt['generation'] == draft['generation']
    result = receipt['result']
    assert [task['task_id'] for task in result['tasks']] == [draft['preview']['tasks'][0]['task_id']]
    ops = operations(http)
    assert len(ops) == 1 and ops[0]['op_id'] == result['op_id']
    assert set(ops[0]['touched_node_ids']) == set(result['touched_node_ids'])
    assert set(result['touched_node_ids']) <= {node['id'] for node in nodes(http)}
    return deepcopy(application)


def no_result(http, actor):
    assert not any(row['event']['type'] == 'system_continuation' for row in inputs(http, actor))


@pytest.mark.parametrize(('transport', 'actor'), CHANNELS)
@pytest.mark.parametrize('admit', [False, True], ids=['queued-only-late-apply-control', 'real-next-admission'])
def test_R10_pending_survives_queue_but_not_actual_next_user_admission(live_http, monkeypatch, transport, actor, admit):
    http = live_http
    _, draft, session = imported(http, transport, actor)
    tasks = track_lifetime(monkeypatch)
    monkeypatch.setattr(tools, 'AGENT_WRITE_CONFIRM_TIMEOUT_SECONDS', .01)
    before_nodes = nodes(http)
    event = lifecycle_pending_475(http, draft, transport, actor)
    original = next(row['event']['turn_id'] for row in reversed(inputs(http, actor))
                    if row['event']['type'] == 'user_prompt')
    request_id = 'next-user-475-' + uuid4().hex
    try:
        response = http.client.post(f'/api/projects/{http.project}/agent-nodes/{actor}/prompt',
            headers=headers(http.project), json={'message': 'A genuinely distinct user request', 'request_id': request_id})
        assert response.status_code == 200, response.text
        wait_record(http, actor, lambda e: e['type'] == 'queued_input' and e['request_id'] == request_id)
        assert not any(row['event']['type'] == 'user_prompt' and row['event'].get('request_id') == request_id
                       for row in inputs(http, actor))
        assert read(http, draft, actor)['state'] == 'awaiting_confirmation'
        assert operations(http) == []
        if admit:
            end_response(http, session, actor)
            user = wait_record(http, actor, lambda e: e['type'] == 'user_prompt' and e.get('request_id') == request_id)
            assert user['turn_id'] != original
            retired(http, draft, actor)
        before_delivery = [process.stdin.getvalue() for process in http.processes]
        confirm(http, actor, event)
        drain(http.client, tasks)
        if admit:
            application = retired(http, draft, actor)
            assert application['error']['code'] in ('PLAN_STALE', 'PLAN_NOT_CONFIRMED'), application
            assert nodes(http) == before_nodes and operations(http) == []
            assert [process.stdin.getvalue() for process in http.processes] == before_delivery
            no_result(http, actor)
        else:
            applied(http, draft, actor)
            if transport == 'external':
                no_result(http, actor)
            else:
                queued = wait_record(http, actor, lambda e: e['type'] == 'system_continuation' and e['delivery'] == 'queued')
                assert queued['turn_id'] == original
    finally:
        confirm(http, actor, event, False)
        drain(http.client, tasks)


@pytest.mark.parametrize(('transport', 'actor'), CHANNELS)
@pytest.mark.parametrize('fault', ['late-apply-control', 'metadata-deleted', 'metadata-path-redirect',
                                 'identical-file-replaced', 'content-unlinked'])
def test_R17_source_metadata_and_path_identity_are_revalidated(live_http, monkeypatch, transport, actor, fault):
    http = live_http
    source, draft, _ = imported(http, transport, actor)
    tasks = track_lifetime(monkeypatch)
    monkeypatch.setattr(tools, 'AGENT_WRITE_CONFIRM_TIMEOUT_SECONDS', .01)
    before = nodes(http)
    event = lifecycle_pending_475(http, draft, transport, actor)
    path = Path(source['path'])
    metadata = path.parent / 'metadata.json'
    try:
        # These are external filesystem faults, not a fabricated plan/session outcome.
        if fault == 'metadata-deleted':
            metadata.unlink()
        elif fault == 'metadata-path-redirect':
            alternate = path.with_name('alternate.xlsx')
            alternate.write_bytes(path.read_bytes())
            record = json.loads(metadata.read_text(encoding='utf-8'))
            metadata.write_text(json.dumps({**record, 'file': alternate.name}), encoding='utf-8')
        elif fault == 'identical-file-replaced':
            alternate = path.with_name('replacement.xlsx')
            alternate.write_bytes(path.read_bytes())
            alternate.replace(path)
        elif fault == 'content-unlinked':
            path.unlink()
        if fault in ('identical-file-replaced', 'content-unlinked'):
            retired(http, draft, actor)
        confirm(http, actor, event)
        drain(http.client, tasks)
        if fault == 'late-apply-control':
            applied(http, draft, actor)
        else:
            application = retired(http, draft, actor)
            assert application['error']['code'] == 'PLAN_STALE', application
            assert nodes(http) == before and operations(http) == []
            if transport == 'external':
                no_result(http, actor)
    finally:
        confirm(http, actor, event, False)
        drain(http.client, tasks)


@pytest.mark.parametrize('actor', HOSTS)
@pytest.mark.parametrize('action', ['late-apply-control', 'stop-before-gate', 'stop-pending',
                                  'abort-pending', 'disconnect-observer'])
def test_R11_R12_public_stop_abort_and_observer_disconnect(live_http, monkeypatch, actor, action):
    http = live_http
    _, draft, session = imported(http, 'hosted', actor)
    tasks = track_lifetime(monkeypatch)
    monkeypatch.setattr(tools, 'AGENT_WRITE_CONFIRM_TIMEOUT_SECONDS', .01)
    before = nodes(http)
    event = None
    try:
        if action == 'stop-before-gate':
            response = http.client.delete(f'/api/projects/{http.project}/agent-nodes/{actor}', headers=headers(http.project))
            assert response.status_code == 200 and response.json()['status'] == 'stopped', response.text
            ok, error = apply(http, draft, 'hosted', actor)
            assert not ok and error['code'] == 'INVALID_ARGUMENT', error
            assert error['message'] == 'hosted write requires an active turn'
            assert not any(row['event']['type'] == 'confirm' for row in http.records(http.project, actor))
        else:
            event = lifecycle_pending_475(http, draft, 'hosted', actor)
            if action == 'stop-pending':
                response = http.client.delete(f'/api/projects/{http.project}/agent-nodes/{actor}', headers=headers(http.project))
                assert response.status_code == 200 and response.json()['status'] == 'stopped', response.text
            elif action == 'abort-pending':
                response = http.client.post(f'/api/projects/{http.project}/agent-nodes/{actor}/abort', headers=headers(http.project))
                assert response.status_code == 200, response.text
                http.wait_event(actor, 'agent_end')
            elif action == 'disconnect-observer':
                with http.client.websocket_connect(
                    f'ws://127.0.0.1:3198/api/projects/{http.project}/agent-nodes/{actor}/stream',
                ) as ws:
                    assert ws.receive_json()['type'] == 'session'
                assert read(http, draft, actor)['state'] == 'awaiting_confirmation'
            if action in ('stop-pending', 'abort-pending'):
                retired(http, draft, actor)
            confirm(http, actor, event)
            drain(http.client, tasks)
        if action in ('late-apply-control', 'disconnect-observer'):
            receipt = applied(http, draft, actor)
            end_response(http, session, actor)
            sent = wait_record(http, actor, lambda e: e['type'] == 'system_continuation' and e['delivery'] == 'sent')
            assert json.loads(sent['text'].split('：', 1)[1])['op_id'] == receipt['receipt']['result']['op_id']
        else:
            assert nodes(http) == before and operations(http) == []
            assert read(http, draft, actor)['receipt'] is None
            no_result(http, actor)
            if event:
                resolved = wait_record(http, actor, lambda e: e['type'] == 'confirm_resolved' and e['confirm_id'] == event['confirm_id'])
                assert resolved['approved'] is False
    finally:
        if event:
            confirm(http, actor, event, False)
        drain(http.client, tasks)


@pytest.mark.parametrize('actor', HOSTS)
@pytest.mark.parametrize('stop', [False, True], ids=['late-apply-control', 'stop-before-body-admission'])
def test_R11_stop_before_inflight_http_body_admission_cannot_publish_a_gate(live_http, monkeypatch, actor, stop):
    http = live_http
    _, draft, _ = imported(http, 'hosted', actor)
    tasks = track_lifetime(monkeypatch)
    monkeypatch.setattr(tools, 'AGENT_WRITE_CONFIRM_TIMEOUT_SECONDS', .01)
    entered, release = Event(), Event()
    body = json.dumps({'jsonrpc': '2.0', 'id': 475, 'method': 'tools/call', 'params': {
        'name': 'plan.apply', 'arguments': {'plan_id': draft['plan_id'], 'generation': draft['generation']}}}).encode()

    class DelayedBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            entered.set()
            assert await asyncio.to_thread(release.wait, 5), 'HTTP body barrier was not released'
            yield body

    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://127.0.0.1:3198') as client:
            return await client.post('/api/mcp', params={'project': http.project, 'node': actor, 'caller': 'hosted'},
                headers={'Mcp-Session-Id': ORIGINAL_SID, 'Content-Type': 'application/json'}, content=DelayedBody())

    future = http.client.portal.start_task_soon(request)
    event = None
    before = nodes(http)
    try:
        assert entered.wait(5), 'Public HTTP request did not reach its body transport barrier'
        assert not any(row['event']['type'] == 'confirm' for row in http.records(http.project, actor))
        if stop:
            response = http.client.delete(f'/api/projects/{http.project}/agent-nodes/{actor}', headers=headers(http.project))
            assert response.status_code == 200 and response.json()['status'] == 'stopped', response.text
        release.set()
        response = future.result(timeout=10)
        assert response.status_code == 200, response.text
        result = response.json()['result']
        if stop:
            assert result['isError'] is True and result['structuredContent']['code'] == 'INVALID_ARGUMENT', result
            assert result['structuredContent']['message'] == 'hosted write requires an active turn'
            assert nodes(http) == before and operations(http) == []
            assert not any(row['event']['type'] == 'confirm' for row in http.records(http.project, actor))
            no_result(http, actor)
        else:
            event = http.wait_event(actor, 'confirm')['event']
            assert result['isError'] is False and json.loads(result['content'][0]['text'])['code'] == 'CONFIRM_PENDING', result
            confirm(http, actor, event)
            drain(http.client, tasks)
            applied(http, draft, actor)
    finally:
        release.set()
        future.result(timeout=10)
        if event:
            confirm(http, actor, event, False)
        drain(http.client, tasks)


@pytest.mark.parametrize(('transport', 'actor'), CHANNELS)
@pytest.mark.parametrize('change', ['late-apply-control', 'deleted', 'same-id-new-agent-session', 'same-id-prompt'])
def test_R14_R20_public_actor_deletion_and_same_id_recreation_retire_old_owner(live_http, monkeypatch, transport, actor, change):
    http = live_http
    # The canvas router must share the fixture's real manager, not the process singleton.
    monkeypatch.setattr(api_project_library, 'agent_host_manager', http.manager)
    _, draft, _ = imported(http, transport, actor)
    tasks = track_lifetime(monkeypatch)
    monkeypatch.setattr(tools, 'AGENT_WRITE_CONFIRM_TIMEOUT_SECONDS', .01)
    event = lifecycle_pending_475(http, draft, transport, actor)
    try:
        if change != 'late-apply-control':
            old_node = next(node for node in lifecycle_canvas_475(http)['nodes'] if node['id'] == actor)
            mutate_canvas(http, [{'op': 'remove_node', 'node_id': actor}])
            if change == 'same-id-new-agent-session':
                mutate_canvas(http, [{'op': 'upsert_node', 'node': old_node}])
                last = max(row['event']['seq'] for row in http.records(http.project, actor))
                http.prime(actor=actor)
                admitted = wait_record(http, actor, lambda e: e['type'] == 'user_prompt' and e['seq'] > last)
                assert admitted['seq'] > last
            elif change == 'same-id-prompt':
                mutate_canvas(http, [{'op': 'upsert_node', 'node': {'id': actor, 'type': 'prompt', 'text': 'Human replacement'}}])
            drain(http.client, tasks)
            after = lifecycle_canvas_475(http)
            count = len(http.processes)
        confirm(http, actor, event)
        drain(http.client, tasks)
        if change == 'late-apply-control':
            applied(http, draft, actor)
        else:
            assert lifecycle_canvas_475(http) == after and operations(http) == []
            assert len(http.processes) == count
            no_result(http, actor)
            response = http.client.get(f'/api/projects/{http.project}/plan/previews/{draft["plan_id"]}',
                params={'agent_node_id': actor}, headers=headers(http.project))
            if change == 'same-id-new-agent-session':
                assert response.status_code == 200 and response.json()['application']['receipt'] is None, response.text
                ok, error = apply(http, draft, transport, actor)
                assert not ok and error['code'] == 'PLAN_STALE', error
            else:
                assert response.status_code == 409 and response.json()['detail']['code'] == 'PLAN_STALE', response.text
    finally:
        confirm(http, actor, event, False)
        drain(http.client, tasks)


@pytest.mark.parametrize('change', ['late-apply-control', 'public-sid-rebind', 'delete-recreate-unbound'])
def test_R14_public_external_carrier_binding_changes_do_not_reuse_old_approval(live_http, monkeypatch, change):
    http, actor = live_http, 'actor'
    monkeypatch.setattr(api_project_library, 'agent_host_manager', http.manager)
    # Prepare an existing server-owned external binding before the tested input.
    initial = http.store.canvas(http.project)
    actor_node = next(node for node in initial['nodes'] if node['id'] == actor)
    http.store.apply_agent_host_mutations(http.project,
        [{'op': 'upsert_node', 'node': {**actor_node, 'mcp_session': ORIGINAL_SID}}], base_revision=initial['revision'])
    http.store.flush(http.project)
    source, draft, _ = imported(http, 'external', actor)
    tasks = track_lifetime(monkeypatch)
    monkeypatch.setattr(tools, 'AGENT_WRITE_CONFIRM_TIMEOUT_SECONDS', .01)
    event = lifecycle_pending_475(http, draft, 'external', actor)
    before_pipes = [process.stdin.getvalue() for process in http.processes]
    before_inputs = deepcopy(inputs(http, actor))
    try:
        if change == 'public-sid-rebind':
            response = http.client.post('/api/mcp', params={'project': http.project, 'caller': 'external'},
                headers={'Mcp-Session-Id': 'replacement-carrier-475'}, json={
                    'jsonrpc': '2.0', 'id': 475, 'method': 'tools/call',
                    'params': {'name': 'canvas.create_node', 'arguments': {'node': {
                        'type': 'prompt', 'text': 'Rebinding positive write', 'x': 900, 'y': 900}}}})
            assert response.status_code == 200 and response.json()['result']['isError'] is False, response.text
            assert next(node for node in lifecycle_canvas_475(http)['nodes'] if node['id'] == actor)['mcp_session'] == 'replacement-carrier-475'
            retired(http, draft, actor)
        elif change == 'delete-recreate-unbound':
            actor_node = next(node for node in lifecycle_canvas_475(http)['nodes'] if node['id'] == actor)
            mutate_canvas(http, [{'op': 'remove_node', 'node_id': actor}])
            mutate_canvas(http, [{'op': 'upsert_node', 'node': actor_node}])
            assert not next(node for node in lifecycle_canvas_475(http)['nodes'] if node['id'] == actor).get('mcp_session')
        after = lifecycle_canvas_475(http)
        before_ops = operations(http)
        confirm(http, actor, event)
        drain(http.client, tasks)
        if change == 'late-apply-control':
            applied(http, draft, actor)
        else:
            assert lifecycle_canvas_475(http) == after and operations(http) == before_ops
            assert all(op['tool'] != 'plan.apply' for op in operations(http))
            assert read(http, draft, actor)['receipt'] is None
            if change == 'public-sid-rebind':
                # Rebinding is not a new SID-revocation protocol: explicit original scope still reads/imports.
                ok, result = http.call('plan.import_excel', {'attachment_id': source['id']}, transport='external', actor=actor)
                assert ok and result['plan_id'] == draft['plan_id'], result
        no_result(http, actor)
        assert [process.stdin.getvalue() for process in http.processes] == before_pipes
        assert inputs(http, actor) == before_inputs
    finally:
        confirm(http, actor, event, False)
        drain(http.client, tasks)


@pytest.mark.parametrize(('transport', 'actor'), CHANNELS)
@pytest.mark.parametrize('replace', [False, True], ids=['late-apply-control', 'environment-store-replacement'])
def test_R20_store_object_replacement_cannot_inherit_old_pending_application(live_http, monkeypatch, transport, actor, replace):
    from firefly_projects import ProjectStore

    http = live_http
    _, draft, _ = imported(http, transport, actor)
    tasks = track_lifetime(monkeypatch)
    monkeypatch.setattr(tools, 'AGENT_WRITE_CONFIRM_TIMEOUT_SECONDS', .01)
    event = lifecycle_pending_475(http, draft, transport, actor)
    replacement = None
    before = nodes(http)
    try:
        if replace:
            # A new Store reads the same persisted project; no fabricated lifecycle result.
            replacement = ProjectStore(http.store.root, snapshot_delay=0)
            monkeypatch.setattr(main, 'FIREFLY_PROJECT_STORE', replacement)
            assert nodes(http) == before
        confirm(http, actor, event)
        drain(http.client, tasks)
        if replace:
            assert nodes(http) == before and operations(http) == []
            if transport == 'external':
                no_result(http, actor)
            else:
                terminal = wait_record(http, actor, lambda e: e['type'] == 'system_continuation' and e['delivery'] == 'queued')
                assert json.loads(terminal['text'].split('：', 1)[1])['error']['code'] == 'PLAN_STALE'
                original = next(row['event']['turn_id'] for row in inputs(http, actor) if row['event']['type'] == 'user_prompt')
                assert terminal['turn_id'] == original
            response = http.client.get(f'/api/projects/{http.project}/plan/previews/{draft["plan_id"]}',
                params={'agent_node_id': actor}, headers=headers(http.project))
            assert response.status_code == 409 and response.json()['detail']['code'] == 'PLAN_STALE', response.text
        else:
            applied(http, draft, actor)
    finally:
        confirm(http, actor, event, False)
        drain(http.client, tasks)
        if replacement:
            monkeypatch.setattr(main, 'FIREFLY_PROJECT_STORE', http.store)
            replacement.close()


@pytest.mark.parametrize('actor', HOSTS)
@pytest.mark.parametrize('fail_write', [False, True], ids=['late-apply-and-sent-control', 'native-write-failure'])
def test_R36_continuation_write_failure_cannot_rewrite_applied_receipt_or_invent_sent(live_http, monkeypatch, actor, fail_write):
    http = live_http
    _, draft, session = imported(http, 'hosted', actor)
    tasks = track_lifetime(monkeypatch)
    monkeypatch.setattr(tools, 'AGENT_WRITE_CONFIRM_TIMEOUT_SECONDS', .01)
    before_inputs = deepcopy(inputs(http, actor))
    event = lifecycle_pending_475(http, draft, 'hosted', actor)
    attempted = []
    try:
        confirm(http, actor, event)
        drain(http.client, tasks)
        saved = applied(http, draft, actor)
        saved_ops = operations(http)
        queued = wait_record(http, actor, lambda e: e['type'] == 'system_continuation' and e['delivery'] == 'queued')
        assert json.loads(queued['text'].split('：', 1)[1])['op_id'] == saved['receipt']['result']['op_id']
        if fail_write:
            if actor == 'actor':
                native_write = session.process.stdin.write

                def broken_write(value):
                    if json.loads(value).get('type') == 'follow_up':
                        attempted.append(json.loads(value)['message'])
                        raise BrokenPipeError('Offline continuation pipe closed')
                    return native_write(value)

                monkeypatch.setattr(session.process.stdin, 'write', broken_write)
            else:
                native = agent_host.subprocess.Popen

                def broken_process(argv, **kwargs):
                    process = native(argv, **kwargs)

                    def broken_write(value):
                        attempted.append(value)
                        raise BrokenPipeError('Offline continuation pipe closed')

                    monkeypatch.setattr(process.stdin, 'write', broken_write)
                    return process

                monkeypatch.setattr(agent_host.subprocess, 'Popen', broken_process)
        end_response(http, session, actor)
        if fail_write:
            terminal = wait_record(http, actor, lambda e: e['type'] == 'input_delivery_terminal' and e['request_id'] == queued['request_id'])
            assert terminal['delivery'] == 'uncertain'
            assert attempted == [queued['text']]
            assert not any(row['event']['type'] == 'system_continuation' and row['event']['delivery'] == 'sent'
                           for row in inputs(http, actor))
        else:
            sent = wait_record(http, actor, lambda e: e['type'] == 'system_continuation' and e['delivery'] == 'sent')
            assert (sent['request_id'], sent['turn_id'], sent['text']) == (queued['request_id'], queued['turn_id'], queued['text'])
            if actor == 'codex-actor':
                assert http.processes[-1].stdin.getvalue() == queued['text']
            else:
                commands = [json.loads(line) for line in session.process.stdin.getvalue().splitlines()]
                assert [command['message'] for command in commands if command['type'] == 'follow_up'] == [queued['text']]
        assert read(http, draft, actor) == saved and operations(http) == saved_ops
        assert [row for row in inputs(http, actor) if row['event']['type'] != 'system_continuation'] == before_inputs
    finally:
        confirm(http, actor, event, False)
        drain(http.client, tasks)


@pytest.mark.parametrize('actor', HOSTS)
@pytest.mark.parametrize('switch', [False, True], ids=['late-apply-control', 'stop-then-switch'])
def test_R13_pending_settings_busy_then_lawful_host_switch_never_revives_old_apply(live_http, monkeypatch, actor, switch):
    http = live_http
    _, draft, session = imported(http, 'hosted', actor)
    tasks = track_lifetime(monkeypatch)
    monkeypatch.setattr(tools, 'AGENT_WRITE_CONFIRM_TIMEOUT_SECONDS', .01)
    event = lifecycle_pending_475(http, draft, 'hosted', actor)
    end_response(http, session, actor)
    assert read(http, draft, actor)['state'] == 'awaiting_confirmation'
    target = 'codex' if actor == 'actor' else 'pi'
    before = nodes(http)
    try:
        response = http.client.post(f'/api/projects/{http.project}/agent-nodes/{actor}/settings',
            headers=headers(http.project), json={'host': target})
        assert response.status_code == 409, response.text
        assert response.json()['detail']['details']['routing']['code'] == 'agent_busy', response.text
        assert nodes(http) == before and operations(http) == []
        assert read(http, draft, actor)['state'] == 'awaiting_confirmation'
        if switch:
            response = http.client.delete(f'/api/projects/{http.project}/agent-nodes/{actor}', headers=headers(http.project))
            assert response.status_code == 200 and response.json()['status'] == 'stopped', response.text
            retired(http, draft, actor)
            count = len(http.processes)
            native = agent_host.subprocess.Popen

            def offline_metadata(argv, **kwargs):
                if 'app-server' in argv or '--no-session' in argv:
                    raise OSError('Offline metadata transport is unavailable')
                return native(argv, **kwargs)

            monkeypatch.setattr(agent_host.subprocess, 'Popen', offline_metadata)
            response = http.client.post(f'/api/projects/{http.project}/agent-nodes/{actor}/settings',
                headers=headers(http.project), json={'host': target})
            assert response.status_code == 200 and response.json()['success'] is True, response.text
            assert response.json()['capabilities']['status'] == 'unavailable'
            assert next(node for node in lifecycle_canvas_475(http)['nodes'] if node['id'] == actor)['agentCli'] == target
            status = http.client.get(f'/api/projects/{http.project}/agent-nodes/{actor}/status')
            assert status.status_code == 200 and status.json()['host'] == target and not status.json()['is_running'], status.text
            after = nodes(http)
        confirm(http, actor, event)
        drain(http.client, tasks)
        if switch:
            assert nodes(http) == after and operations(http) == []
            assert read(http, draft, actor)['receipt'] is None
            assert len(http.processes) == count
            no_result(http, actor)
        else:
            applied(http, draft, actor)
    finally:
        confirm(http, actor, event, False)
        drain(http.client, tasks)
