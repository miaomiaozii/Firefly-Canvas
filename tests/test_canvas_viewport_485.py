"""Public HTTP/MCP + real ASGI WebSocket protocol checks; not browser geometry proof."""
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import main
from firefly_projects import ProjectStore


@asynccontextmanager
async def quiet_lifetime(app):
    # These protocol tests must not start native hosts, recovery or provider workers.
    yield


@pytest.fixture(name='env')
def viewport_protocol_env(tmp_path):
    store = ProjectStore(tmp_path / "projects", snapshot_delay=0)
    pid = store.create_project("Viewport protocol")['id']
    store.apply_mutations(pid, [{'op': 'upsert_node', 'node': {
        'id': 'prompt-1', 'type': 'smart-prompt', 'text': 'offline', 'x': 1200, 'y': 900,
    }}], base_revision=0)
    with patch.object(main, 'FIREFLY_PROJECT_STORE', store), patch.object(
        main.app.router, 'lifespan_context', quiet_lifetime
    ), patch('subprocess.Popen', side_effect=AssertionError('native startup forbidden')):
        with TestClient(main.app, base_url='http://127.0.0.1', raise_server_exceptions=False) as client:
            with ThreadPoolExecutor(max_workers=4) as pool:
                yield client, pid, store, pool
    store.close()


def viewport_rest(client, pid, command, args=None, **kwargs):
    if command == 'state':
        return client.get(f'/api/projects/{pid}/viewport/state', **kwargs)
    return client.post(f'/api/projects/{pid}/viewport/{command}',
                       headers={'X-Agent-Project': pid}, json=args or {}, **kwargs)


def open_socket(client, pid, client_id='canvas_protocol'):
    return client.websocket_connect(f'/ws/stats?client_id={client_id}&project_id={pid}',
                                    headers={'host': '127.0.0.1', 'origin': 'http://127.0.0.1'})


def frame(ws):
    import anyio

    async def bounded_receive():
        with anyio.move_on_after(4):
            return await ws._send_rx.receive()
        return None

    while True:
        wire = ws.portal.call(bounded_receive)
        assert wire is not None, 'WebSocket did not produce the expected protocol frame'
        assert wire['type'] == 'websocket.send', wire
        message = json.loads(wire['text'])
        if message.get('type') != 'stats':
            return message


def ready(ws, pid):
    ws.send_json({'type': 'canvas_viewport_ready', 'project_id': pid})
    ws.send_text('ping')
    message = frame(ws)
    assert message['type'] == 'canvas_viewport_ready_ack'
    assert message['project_id'] == pid
    assert isinstance(message['generation'], str) and message['generation']
    assert frame(ws) == {'type': 'pong'}
    return message['generation']


def test_ready_has_idempotent_server_generation(env):
    client, pid, _, _ = env
    with open_socket(client, pid) as ws:
        generation = ready(ws, pid)
        assert ready(ws, pid) == generation


def acknowledge(ws, wire, viewport=None, **overrides):
    message = {**wire, 'type': 'canvas_viewport_ack',
               'status': 'observed' if wire['command'] == 'state' else 'applied',
               'viewport': viewport or {'x': -420, 'y': 73.5, 'scale': 1.75}}
    message.pop('node_id', None)
    message.pop('level', None)
    message.update(overrides)
    ws.send_json(message)


def assert_waiting(future):
    from concurrent.futures import TimeoutError
    with pytest.raises(TimeoutError):
        future.result(timeout=0.08)


def test_state_waits_for_fresh_observed_ack(env):
    client, pid, _, pool = env
    with open_socket(client, pid) as ws:
        generation = ready(ws, pid)
        future = pool.submit(viewport_rest, client, pid, 'state')
        assert_waiting(future)
        command = frame(ws)
        assert command['type'] == 'canvas_viewport_command'
        assert command['generation'] == generation
        assert command['command'] == 'state'
        acknowledge(ws, command)
        response = future.result(timeout=2)
        assert response.status_code == 200, response.text
        body = response.json()
        assert (body['x'], body['y'], body['scale']) == (-420, 73.5, 1.75)
        assert body['command_id'] == command['command_id']
        assert body['updated_at'] > 0
        assert 'focus_node_id' not in body


@pytest.mark.parametrize('command,args,key,value', [
    ('focus', {'node_id': 'prompt-1'}, 'node_id', 'prompt-1'),
    ('zoom', {'level': 1.75}, 'level', 1.75),
], ids=['focus', 'zoom'])
def test_rest_success_requires_applied_ack(env, command, args, key, value):
    client, pid, _, pool = env
    with open_socket(client, pid) as ws:
        ready(ws, pid)
        future = pool.submit(viewport_rest, client, pid, command, args)
        assert_waiting(future)
        message = frame(ws)
        assert message['command'] == command
        assert message[key] == value
        acknowledge(ws, message)
        response = future.result(timeout=2)
        assert response.status_code == 200, response.text
        assert response.json() == {'success': True, key: value, 'dispatched': True,
            'command_id': message['command_id'], 'viewport': {'x': -420, 'y': 73.5, 'scale': 1.75}}


def viewport_mcp(client, pid, command, args=None, **kwargs):
    return client.post(f'/api/mcp?project={pid}', json={
        'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
        'params': {'name': f'viewport.{command}', 'arguments': args or {}}}, **kwargs)


def payload(response, transport):
    if transport == 'rest':
        return response.json()
    result = response.json()['result']
    assert not result['isError'], result
    return json.loads(result['content'][0]['text'])


def error(response, transport):
    if transport == 'rest':
        assert response.status_code >= 400, response.text
        return response.json()['detail']
    result = response.json()['result']
    assert result['isError'], result
    return result['structuredContent']


@pytest.mark.parametrize('command,args', [('focus', {'node_id': 'prompt-1'}),
    ('zoom', {'level': 1.75}), ('state', {})], ids=['focus', 'zoom', 'state'])
def test_mcp_ack_contract(env, command, args):
    client, pid, _, pool = env
    with open_socket(client, pid) as ws:
        ready(ws, pid)
        future = pool.submit(viewport_mcp, client, pid, command, args)
        assert_waiting(future)
        message = frame(ws)
        assert message['command'] == command
        acknowledge(ws, message)
        body = payload(future.result(timeout=2), 'mcp')
        observed = body if command == 'state' else body['viewport']
        assert {key: observed[key] for key in ('x', 'y', 'scale')} == {
            'x': -420, 'y': 73.5, 'scale': 1.75}


ZOOM_VALUES = [True, False, '1.5', None, float('nan'), float('inf'), -float('inf'), 0.09, 8.01, {}, [], 10 ** 400]
ZOOM_IDS = ['true', 'false', 'string', 'null', 'nan', 'infinity', 'negative-infinity', 'low', 'high', 'object', 'array', 'huge']
@pytest.mark.parametrize('transport', ['rest', 'mcp'])
@pytest.mark.parametrize('level', ZOOM_VALUES, ids=ZOOM_IDS)
def test_zoom_strict_input(env, transport, level):
    client, pid, _, _ = env
    with open_socket(client, pid) as ws:
        ready(ws, pid)
        # Raw JSON preserves non-finite wire tokens that HTTP libraries refuse to encode.
        args = {'level': level}
        if transport == 'rest':
            response = client.post(f'/api/projects/{pid}/viewport/zoom',
                headers={'X-Agent-Project': pid, 'Content-Type': 'application/json'},
                content=json.dumps(args))
        else:
            response = client.post(f'/api/mcp?project={pid}', content=json.dumps({
                'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                'params': {'name': 'viewport.zoom', 'arguments': args}}),
                headers={'Content-Type': 'application/json'})
        if transport == 'rest':
            assert response.status_code == 400, response.text
        assert error(response, transport)['code'] == 'INVALID_ARGUMENT'
        ws.send_text('ping')
        assert frame(ws) == {'type': 'pong'}


@pytest.mark.parametrize('transport', ['rest', 'mcp'])
@pytest.mark.parametrize('node_id', [1, True, None, '', '   ', {}, []],
                         ids=['number', 'bool', 'null', 'empty', 'blank', 'object', 'array'])
def test_focus_strict_input(env, transport, node_id):
    client, pid, _, _ = env
    with open_socket(client, pid) as ws:
        ready(ws, pid)
        response = (viewport_rest if transport == 'rest' else viewport_mcp)(client, pid, 'focus', {'node_id': node_id})
        assert error(response, transport)['code'] == 'INVALID_ARGUMENT'
        ws.send_text('ping')
        assert frame(ws) == {'type': 'pong'}


@pytest.mark.parametrize('transport', ['rest', 'mcp'])
def test_timeout_is_unknown_and_not_retryable(env, transport):
    client, pid, _, pool = env
    with open_socket(client, pid) as ws:
        ready(ws, pid)
        future = pool.submit(viewport_rest if transport == 'rest' else viewport_mcp, client, pid, 'zoom', {'level': 2})
        assert_waiting(future)
        command = frame(ws)
        response = future.result(timeout=5)
        if transport == 'rest':
            assert response.status_code == 504, response.text
        detail = error(response, transport)
        assert detail['code'] == 'TOOL_TIMEOUT'
        assert detail['retryable'] is False
        assert detail['details']['outcome'] == 'unknown'
        assert detail['details']['command_id'] == command['command_id']
        assert 'state' in detail['hint']


def test_multiple_ready_tabs_fail_closed(env):
    client, pid, _, _ = env
    with open_socket(client, pid, 'canvas_one') as one, open_socket(client, pid, 'canvas_two') as two:
        ready(one, pid)
        ready(two, pid)
        response = viewport_rest(client, pid, 'zoom', {'level': 2})
        assert response.status_code == 409, response.text
        detail = response.json()['detail']
        assert detail['code'] == 'CONFLICT'
        assert detail['message'] == '检测到 2 个打开的画布窗口，请关闭多余窗口后重试'
        for ws in (one, two):
            ws.send_text('ping')
            assert frame(ws) == {'type': 'pong'}


@pytest.mark.parametrize('dimension', ['generation', 'command', 'status', 'project_id', 'command_id', 'socket'])
def test_ack_correlation_rejects_each_wrong_dimension(env, dimension):
    client, pid, _, pool = env
    with open_socket(client, pid) as ws, open_socket(client, pid, 'canvas_unready') as other:
        ready(ws, pid)
        future = pool.submit(viewport_rest, client, pid, 'state')
        assert_waiting(future)
        command = frame(ws)
        changes = {'generation': 'old', 'command': 'zoom', 'status': 'applied',
                   'project_id': 'foreign', 'command_id': 'unknown'}
        destination = other if dimension == 'socket' else ws
        acknowledge(destination, command, **({dimension: changes[dimension]} if dimension != 'socket' else {}))
        destination.send_text('ping')
        assert frame(destination) == {'type': 'pong'}
        assert_waiting(future)
        acknowledge(ws, command)
        assert future.result(timeout=2).status_code == 200


@pytest.mark.parametrize('snapshot', [
    {'x': 1, 'y': 2}, {'x': True, 'y': 2, 'scale': 1}, {'x': '1', 'y': 2, 'scale': 1},
    {'x': float('nan'), 'y': 2, 'scale': 1}, {'x': 1, 'y': float('inf'), 'scale': 1},
    {'x': 1, 'y': 2, 'scale': 0}, {'x': 1, 'y': 2, 'scale': -1},
    {'x': 1, 'y': 2, 'scale': float('inf')}, {'x': 1, 'y': 2, 'scale': 1, '__proto__': {}},
    {'x': 10 ** 400, 'y': 2, 'scale': 1},
], ids=['missing', 'bool', 'string', 'nan', 'infinity', 'zero-scale', 'negative-scale', 'infinite-scale', 'extra', 'huge'])
def test_snapshot_is_strict_and_cannot_complete_observation(env, snapshot):
    client, pid, _, pool = env
    with open_socket(client, pid) as ws:
        ready(ws, pid)
        future = pool.submit(viewport_rest, client, pid, 'state')
        assert_waiting(future)
        command = frame(ws)
        acknowledge(ws, command, snapshot)
        ws.send_text('ping')
        assert frame(ws) == {'type': 'pong'}
        assert_waiting(future)
        acknowledge(ws, command)
        assert future.result(timeout=2).status_code == 200


@pytest.mark.parametrize('second,args', [('focus', {'node_id': 'prompt-1'}),
    ('zoom', {'level': 2}), ('state', {})], ids=['focus', 'zoom', 'state'])
def test_one_inflight_per_connection_busy_is_not_queued(env, second, args):
    client, pid, _, pool = env
    with open_socket(client, pid) as ws:
        ready(ws, pid)
        first = pool.submit(viewport_rest, client, pid, 'state')
        assert_waiting(first)
        command = frame(ws)
        response = viewport_rest(client, pid, second, args)
        assert response.status_code == 409, response.text
        assert response.json()['detail']['details']['reason'] == 'VIEWPORT_BUSY'
        acknowledge(ws, command)
        assert first.result(timeout=2).status_code == 200
        ws.send_text('ping')
        assert frame(ws) == {'type': 'pong'}


@pytest.mark.parametrize('reason,code', [('NODE_NOT_FOUND', 'NODE_NOT_FOUND'),
    ('DOCKED_TARGET', 'INVALID_ARGUMENT'), ('TARGET_NOT_RENDERABLE', 'INVALID_ARGUMENT'),
    ('INVALID_ARGUMENT', 'INVALID_ARGUMENT'), ('NOT_READY', 'SERVER_NOT_READY')])
@pytest.mark.parametrize('transport', ['rest', 'mcp'])
def test_browser_rejection_is_whitelisted(env, reason, code, transport):
    client, pid, _, pool = env
    with open_socket(client, pid) as ws:
        ready(ws, pid)
        future = pool.submit(viewport_rest if transport == 'rest' else viewport_mcp, client, pid, 'focus', {'node_id': 'prompt-1'})
        assert_waiting(future)
        command = frame(ws)
        acknowledge(ws, command, status='rejected', reason=reason)
        detail = error(future.result(timeout=4), transport)
        assert detail['code'] == code
        assert detail['details']['reason'] == reason
        if reason == 'DOCKED_TARGET':
            assert '停靠' in detail['message']


def test_arbitrary_browser_error_is_not_forwarded(env):
    client, pid, _, pool = env
    with open_socket(client, pid) as ws:
        ready(ws, pid)
        future = pool.submit(viewport_rest, client, pid, 'focus', {'node_id': 'prompt-1'})
        assert_waiting(future)
        command = frame(ws)
        acknowledge(ws, command, status='rejected', reason='INTERNAL_SECRET_STACK')
        ws.send_text('ping')
        assert frame(ws) == {'type': 'pong'}
        assert_waiting(future)
        acknowledge(ws, command)
        assert 'INTERNAL_SECRET_STACK' not in future.result(timeout=2).text


def test_missing_target_prevalidation_keeps_node_not_found(env):
    client, pid, _, _ = env
    response = viewport_rest(client, pid, 'focus', {'node_id': 'deleted'})
    assert response.status_code == 404
    assert response.json()['detail']['code'] == 'NODE_NOT_FOUND'


@pytest.mark.parametrize('body', ['[]', 'null', 'true', '"text"', '{bad'], ids=['array', 'null', 'bool', 'string', 'malformed'])
@pytest.mark.parametrize('command', ['focus', 'zoom'])
def test_rest_requires_json_object(env, body, command):
    client, pid, _, _ = env
    response = client.post(f'/api/projects/{pid}/viewport/{command}', content=body,
                          headers={'X-Agent-Project': pid, 'Content-Type': 'application/json'})
    assert response.status_code == 400, response.text
    assert response.json()['detail']['code'] == 'INVALID_ARGUMENT'


@pytest.mark.parametrize('command', ['focus', 'zoom', 'state'])
@pytest.mark.parametrize('transport', ['rest', 'mcp'])
def test_project_header_must_match(env, command, transport):
    client, pid, _, _ = env
    with open_socket(client, pid) as ws:
        ready(ws, pid)
        args = {'node_id': 'prompt-1'} if command == 'focus' else {'level': 2} if command == 'zoom' else {}
        if transport == 'mcp':
            response = viewport_mcp(client, pid, command, args, headers={'X-Agent-Project': 'foreign'})
        elif command == 'state':
            response = viewport_rest(client, pid, command, headers={'X-Agent-Project': 'foreign'})
        else:
            response = client.post(f'/api/projects/{pid}/viewport/{command}', json=args,
                                   headers={'X-Agent-Project': 'foreign'})
        if transport == 'rest':
            assert response.status_code == 403, response.text
        assert error(response, transport)['code'] == 'PROJECT_MISMATCH'
        ws.send_text('ping')
        assert frame(ws) == {'type': 'pong'}


def test_disconnect_releases_pending_without_waiting_for_timeout(env):
    client, pid, _, pool = env
    with open_socket(client, pid) as ws:
        ready(ws, pid)
        future = pool.submit(viewport_rest, client, pid, 'zoom', {'level': 2})
        assert_waiting(future)
        command = frame(ws)
    import time
    time.sleep(0.15)
    assert future.done(), 'closed socket must settle its pending request promptly'
    detail = error(future.result(timeout=1), 'rest')
    assert detail['retryable'] is False
    assert detail['details'] == {'outcome': 'unknown', 'command_id': command['command_id']}
    assert viewport_rest(client, pid, 'state').status_code == 503


def test_project_deletion_settles_pending_and_invalidates_ready(env):
    client, pid, _, pool = env
    with open_socket(client, pid) as ws:
        ready(ws, pid)
        future = pool.submit(viewport_rest, client, pid, 'state')
        assert_waiting(future)
        command = frame(ws)
        deleted = client.delete(f'/api/project-library/{pid}', headers={'Origin': 'http://127.0.0.1'})
        assert deleted.status_code == 200, deleted.text
        import time
        time.sleep(0.2)
        assert future.done(), 'deleting the project must invalidate its pending viewport observation'
        detail = error(future.result(timeout=1), 'rest')
        assert detail['details']['outcome'] == 'unknown'
        acknowledge(ws, command)
        assert viewport_rest(client, pid, 'state').status_code == 404


def test_requests_on_another_loop_are_delivered_on_socket_owner(env):
    import asyncio
    from starlette.websockets import WebSocket
    client, pid, _, pool = env
    original = WebSocket.send_json
    owner = {}

    async def loop_affine_send(socket, data, *args, **kwargs):
        if data.get('type') == 'canvas_viewport_ready_ack':
            owner[socket] = asyncio.get_running_loop()
        if data.get('type') == 'canvas_viewport_command' and owner[socket] is not asyncio.get_running_loop():
            raise RuntimeError('transport belongs to a different event loop')
        return await original(socket, data, *args, **kwargs)

    with patch.object(WebSocket, 'send_json', loop_affine_send), open_socket(client, pid) as ws, TestClient(
        main.app, base_url='http://127.0.0.1', raise_server_exceptions=False
    ) as other:
        ready(ws, pid)
        future = pool.submit(viewport_rest, other, pid, 'state')
        assert_waiting(future)
        command = frame(ws)
        acknowledge(ws, command)
        response = future.result(timeout=5)
        assert response.status_code == 200, response.text
        assert response.json()['x'] == -420


@pytest.mark.parametrize('fault', ['value-error', 'stall', 'after-send'])
def test_external_transport_failure_has_unknown_outcome(env, fault):
    import asyncio
    from starlette.websockets import WebSocket
    client, pid, _, _ = env
    original = WebSocket.send_json

    async def faulty_send(socket, data, *args, **kwargs):
        if data.get('type') == 'canvas_viewport_command':
            if fault == 'stall':
                await asyncio.sleep(10)
            if fault == 'after-send':
                await original(socket, data, *args, **kwargs)
            raise ValueError('external transport failed')
        return await original(socket, data, *args, **kwargs)

    with patch.object(WebSocket, 'send_json', faulty_send), open_socket(client, pid) as ws:
        ready(ws, pid)
        import time
        started = time.monotonic()
        response = viewport_rest(client, pid, 'zoom', {'level': 2})
        assert time.monotonic() - started < 4.5, 'fixed timeout also bounds a stalled send'
        assert response.status_code == 504, response.text
        detail = error(response, 'rest')
        assert detail['details']['outcome'] == 'unknown'
        assert detail['retryable'] is False
        assert 'state' in detail['hint']


@pytest.mark.parametrize('message', ['binary', 'bad-json', 'array', 'deep', 'oversize', 'unknown', 'wrong-project-ready'],
                         ids=['binary', 'bad-json', 'array', 'deep', 'oversize', 'unknown', 'wrong-project-ready'])
def test_malformed_ws_frames_keep_ping_and_do_not_register(env, message):
    client, pid, _, _ = env
    with open_socket(client, pid) as ws:
        if message == 'binary':
            ws.send_bytes(b'not text')
        elif message == 'bad-json':
            ws.send_text('{bad')
        elif message == 'array':
            ws.send_json([])
        elif message == 'deep':
            ws.send_text('[' * 1500 + ']' * 1500)
        elif message == 'oversize':
            ws.send_text(' ' * 5000 + json.dumps({'type': 'canvas_viewport_ready', 'project_id': pid}))
        elif message == 'unknown':
            ws.send_json({'type': 'execute_code', 'project_id': pid})
        else:
            ws.send_json({'type': 'canvas_viewport_ready', 'project_id': 'foreign'})
        ws.send_text('ping')
        assert frame(ws) == {'type': 'pong'}
        assert viewport_rest(client, pid, 'state').status_code == 503
        ready(ws, pid)


def test_ack_unknown_fields_cannot_smuggle_error_structures(env):
    client, pid, _, pool = env
    with open_socket(client, pid) as ws:
        ready(ws, pid)
        future = pool.submit(viewport_rest, client, pid, 'state')
        assert_waiting(future)
        command = frame(ws)
        acknowledge(ws, command, __proto__={'code': 'SECRET'})
        ws.send_text('ping')
        assert frame(ws) == {'type': 'pong'}
        assert_waiting(future)
        acknowledge(ws, command)
        assert future.result(timeout=2).status_code == 200


@pytest.mark.parametrize('client_id', ['canvas_a', 'canvas_duplicate'])
def test_cross_project_socket_targeting_ignores_personal_client_id(env, client_id):
    client, pid, store, pool = env
    other_pid = store.create_project('Other')['id']
    other_id = client_id if client_id == 'canvas_duplicate' else 'canvas_b'
    with open_socket(client, pid, client_id) as one, open_socket(client, other_pid, other_id) as two:
        ready(one, pid)
        ready(two, other_pid)
        first = pool.submit(viewport_rest, client, pid, 'state')
        assert_waiting(first)
        command = frame(one)
        assert command['project_id'] == pid
        two.send_text('ping')
        assert frame(two) == {'type': 'pong'}
        acknowledge(one, command, {'x': -11, 'y': 22, 'scale': 3})
        assert first.result(timeout=2).json()['x'] == -11
        second = pool.submit(viewport_rest, client, other_pid, 'state')
        assert_waiting(second)
        command = frame(two)
        assert command['project_id'] == other_pid
        acknowledge(two, command, {'x': 99, 'y': -88, 'scale': 0.2})
        assert second.result(timeout=2).json()['x'] == 99


@pytest.mark.parametrize('scale', [0.01, 9, 1.2], ids=['below-tool-bound', 'above-tool-bound', 'normal'])
def test_every_state_is_fresh_and_observed_scale_is_not_input_clamped(env, scale):
    client, pid, _, pool = env
    ids = []
    with open_socket(client, pid) as ws:
        ready(ws, pid)
        for snapshot in ({'x': 15, 'y': -9, 'scale': scale}, {'x': -81, 'y': 66, 'scale': scale}):
            future = pool.submit(viewport_rest, client, pid, 'state')
            assert_waiting(future)
            command = frame(ws)
            ids.append(command['command_id'])
            acknowledge(ws, command, snapshot)
            response = future.result(timeout=2)
            assert response.status_code == 200, response.text
            body = response.json()
            assert {key: body[key] for key in ('x', 'y', 'scale')} == snapshot
        assert ids[0] != ids[1]


@pytest.mark.parametrize('history', ['unknown', 'duplicate', 'expired', 'out-of-order'])
def test_old_receipts_cannot_complete_next_operation(env, history):
    client, pid, _, pool = env
    with open_socket(client, pid) as ws:
        ready(ws, pid)
        previous = pool.submit(viewport_rest, client, pid, 'state')
        assert_waiting(previous)
        old = frame(ws)
        if history == 'expired':
            assert previous.result(timeout=5).status_code == 504
        else:
            acknowledge(ws, old)
            assert previous.result(timeout=2).status_code == 200
        if history == 'duplicate':
            acknowledge(ws, old)
        current = pool.submit(viewport_rest, client, pid, 'state')
        assert_waiting(current)
        command = frame(ws)
        bad = {**old, 'command_id': 'never-issued'} if history == 'unknown' else old
        acknowledge(ws, bad, {'x': 999, 'y': 999, 'scale': 7})
        ws.send_text('ping')
        assert frame(ws) == {'type': 'pong'}
        assert_waiting(current)
        acknowledge(ws, command)
        assert current.result(timeout=2).json()['x'] == -420


def test_reconnection_has_new_generation_and_no_replay(env):
    client, pid, _, pool = env
    with open_socket(client, pid) as old:
        old_generation = ready(old, pid)
        first = pool.submit(viewport_rest, client, pid, 'state')
        assert_waiting(first)
        old_command = frame(old)
    assert error(first.result(timeout=2), 'rest')['details']['outcome'] == 'unknown'
    with open_socket(client, pid) as ws:
        ws.send_text('ping')
        assert frame(ws) == {'type': 'pong'}
        assert viewport_rest(client, pid, 'state').status_code == 503
        assert ready(ws, pid) != old_generation
        current = pool.submit(viewport_rest, client, pid, 'state')
        assert_waiting(current)
        command = frame(ws)
        acknowledge(ws, old_command)
        ws.send_text('ping')
        assert frame(ws) == {'type': 'pong'}
        assert_waiting(current)
        acknowledge(ws, command)
        assert current.result(timeout=2).status_code == 200


def test_cancelled_public_request_frees_slot_and_does_not_replay(env):
    import asyncio
    import httpx
    client, pid, _, pool = env

    async def public_request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://127.0.0.1') as http:
            return await http.get(f'/api/projects/{pid}/viewport/state')

    with open_socket(client, pid) as ws:
        ready(ws, pid)
        cancelled = client.portal.start_task_soon(public_request)
        old = frame(ws)
        assert cancelled.cancel()
        client.portal.call(asyncio.sleep, 0.05)
        current = pool.submit(viewport_rest, client, pid, 'state')
        assert_waiting(current)
        command = frame(ws)
        acknowledge(ws, old)
        ws.send_text('ping')
        assert frame(ws) == {'type': 'pong'}
        assert_waiting(current)
        acknowledge(ws, command)
        assert current.result(timeout=2).status_code == 200


@pytest.mark.parametrize('transport', ['rest', 'mcp'])
@pytest.mark.parametrize('command,args', [('focus', {'node_id': 'prompt-1'}), ('zoom', {'level': 2}), ('state', {})],
                         ids=['focus', 'zoom', 'state'])
def test_viewport_has_no_document_or_operation_side_effects(env, transport, command, args):
    client, pid, _, pool = env
    before = client.get(f'/api/project-library/{pid}').json()
    assert client.get(f'/api/projects/{pid}/ops').json() == {'operations': []}
    with open_socket(client, pid) as ws:
        ready(ws, pid)
        future = pool.submit(viewport_rest if transport == 'rest' else viewport_mcp, client, pid, command, args)
        assert_waiting(future)
        message = frame(ws)
        acknowledge(ws, message)
        payload(future.result(timeout=2), transport)
    assert client.get(f'/api/project-library/{pid}').json() == before
    assert client.get(f'/api/projects/{pid}/ops').json() == {'operations': []}


def test_deleted_idle_connection_cannot_reuse_ready_after_restore(env):
    client, pid, _, _ = env
    with open_socket(client, pid) as ws:
        generation = ready(ws, pid)
        origin = {'Origin': 'http://127.0.0.1'}
        assert client.delete(f'/api/project-library/{pid}', headers=origin).status_code == 200
        import time
        time.sleep(0.15)
        assert client.post(f'/api/project-library/trash/{pid}/restore', headers=origin).status_code == 200
        assert viewport_rest(client, pid, 'state').status_code == 503
        assert ready(ws, pid) != generation


def test_delete_restore_before_next_liveness_tick_cannot_reuse_old_ready(env):
    import asyncio
    client, pid, _, _ = env
    original_sleep = asyncio.sleep

    async def delayed_liveness_tick(delay, *args, **kwargs):
        if delay == 0.05:
            # Control the time boundary, not the viewport helper or readiness gate.
            await asyncio.Event().wait()
        else:
            await original_sleep(delay, *args, **kwargs)

    with patch.object(asyncio, 'sleep', delayed_liveness_tick), open_socket(client, pid) as ws:
        ready(ws, pid)
        headers = {'Origin': 'http://127.0.0.1'}
        assert client.delete(f'/api/project-library/{pid}', headers=headers).status_code == 200
        assert client.post(f'/api/project-library/trash/{pid}/restore', headers=headers).status_code == 200
        response = viewport_rest(client, pid, 'state')
        assert response.status_code == 503, 'restore must not resurrect the previous ready generation: ' + response.text


@pytest.mark.parametrize('failure,status', [('trash-conflict', 409), ('move-error', 500)])
def test_failed_trash_preserves_live_ready_generation_and_pending(env, failure, status):
    import os
    client, pid, store, pool = env
    live = store.root / pid
    target = store.project_trash_root / pid
    original_replace = os.replace
    if failure == 'trash-conflict':
        target.mkdir(parents=True)

    def replace(source, destination, *args, **kwargs):
        if failure == 'move-error' and Path(source) == live and Path(destination) == target:
            raise OSError('external project move failed')
        return original_replace(source, destination, *args, **kwargs)

    with open_socket(client, pid) as ws:
        generation = ready(ws, pid)
        future = pool.submit(viewport_rest, client, pid, 'state')
        assert_waiting(future)
        command = frame(ws)
        with patch.object(os, 'replace', replace):
            response = client.delete(f'/api/project-library/{pid}', headers={'Origin': 'http://127.0.0.1'})
        assert response.status_code == status, response.text
        assert client.get(f'/api/project-library/{pid}').status_code == 200
        assert ready(ws, pid) == generation
        acknowledge(ws, command)
        assert future.result(timeout=2).status_code == 200


def test_trash_retires_only_the_store_that_performed_the_move(env, tmp_path):
    import asyncio
    import os
    client, pid, store, pool = env
    other_root = tmp_path / 'other-projects'
    store.copy_library_to(other_root)
    other_store = ProjectStore(other_root, snapshot_delay=0)
    original_sleep = asyncio.sleep
    original_replace = os.replace

    async def delayed_liveness_tick(delay, *args, **kwargs):
        if delay == 0.05:
            # Polling must not hide a lifecycle notification aimed at the wrong store.
            await asyncio.Event().wait()
        else:
            await original_sleep(delay, *args, **kwargs)

    def replace_and_select_other_store(source, destination, *args, **kwargs):
        result = original_replace(source, destination, *args, **kwargs)
        if Path(source) == store.root / pid and Path(destination) == store.project_trash_root / pid:
            # A post-move library selection must not redirect retirement to another live project.
            main.FIREFLY_PROJECT_STORE = other_store
        return result

    try:
        with patch.object(asyncio, 'sleep', delayed_liveness_tick), open_socket(client, pid) as ws:
            generation = ready(ws, pid)
            with patch.object(main, 'FIREFLY_PROJECT_STORE', other_store), open_socket(
                client, pid, 'canvas_other_store'
            ) as other_ws:
                other_generation = ready(other_ws, pid)
                with patch.object(main, 'FIREFLY_PROJECT_STORE', store), TestClient(
                    main.app, base_url='http://127.0.0.1', raise_server_exceptions=False
                ) as deleting_client:
                    headers = {'Origin': 'http://127.0.0.1'}
                    with patch.object(os, 'replace', replace_and_select_other_store):
                        deleted = deleting_client.delete(f'/api/project-library/{pid}', headers=headers)
                    assert deleted.status_code == 200, deleted.text
                    future = pool.submit(viewport_rest, client, pid, 'state')
                    assert_waiting(future)
                    command = frame(other_ws)
                    acknowledge(other_ws, command)
                    assert future.result(timeout=2).status_code == 200
                    assert ready(other_ws, pid) == other_generation
                    with patch.object(main, 'FIREFLY_PROJECT_STORE', store):
                        restored = client.post(f'/api/project-library/trash/{pid}/restore', headers=headers)
                        assert restored.status_code == 200, restored.text
                        assert viewport_rest(client, pid, 'state').status_code == 503
                        assert ready(ws, pid) != generation
    finally:
        other_store.close()


def test_nonexistent_project_cannot_become_ready(env):
    client, _, _, _ = env
    with open_socket(client, 'nonexistent') as ws:
        ws.send_json({'type': 'canvas_viewport_ready', 'project_id': 'nonexistent'})
        ws.send_text('ping')
        assert frame(ws) == {'type': 'pong'}


@pytest.mark.parametrize('kind', ['no-ws', 'ordinary-stats', 'old-canvas', 'not-loaded'])
@pytest.mark.parametrize('command,args', [('focus', {'node_id': 'prompt-1'}), ('zoom', {'level': 2}), ('state', {})],
                         ids=['focus', 'zoom', 'state'])
def test_not_ready_does_not_dispatch_or_queue(env, kind, command, args):
    from contextlib import ExitStack
    client, pid, _, _ = env
    with ExitStack() as stack:
        ws = None
        if kind != 'no-ws':
            ws = stack.enter_context(open_socket(client, pid, 'ordinary' if kind == 'ordinary-stats' else 'canvas_old'))
        response = viewport_rest(client, pid, command, args)
        assert response.status_code == 503, response.text
        assert response.json()['detail']['details']['dispatched'] is False
        if ws:
            ws.send_text('ping')
            assert frame(ws) == {'type': 'pong'}
            if kind != 'ordinary-stats':
                ready(ws, pid)
                ws.send_text('ping')
                assert frame(ws) == {'type': 'pong'}


@pytest.mark.parametrize('transport', ['rest-state', 'rest-zoom', 'mcp', 'ws'])
@pytest.mark.parametrize('guard', ['origin', 'host'])
def test_existing_origin_and_host_guards_still_reject(env, transport, guard):
    from starlette.testclient import WebSocketDenialResponse
    client, pid, _, _ = env
    headers = {'host': '127.0.0.1', 'origin': 'http://127.0.0.1', 'X-Agent-Project': pid}
    headers[guard] = 'http://evil.invalid' if guard == 'origin' else 'evil.invalid'
    if transport == 'ws':
        with pytest.raises(WebSocketDenialResponse) as exc:
            with client.websocket_connect(f'/ws/stats?client_id=canvas_guard&project_id={pid}', headers=headers):
                pass
        assert exc.value.status_code == 403
    elif transport == 'mcp':
        assert viewport_mcp(client, pid, 'state', headers=headers).status_code == 403
    elif transport == 'rest-state':
        assert viewport_rest(client, pid, 'state', headers=headers).status_code == 403
    else:
        assert client.post(f'/api/projects/{pid}/viewport/zoom', json={'level': 2}, headers=headers).status_code == 403


@pytest.mark.parametrize('header', [None, '', '   '], ids=['absent', 'empty', 'blank'])
@pytest.mark.parametrize('command', ['focus', 'zoom'])
def test_rest_posts_still_require_project_header(env, header, command):
    client, pid, _, _ = env
    headers = {} if header is None else {'X-Agent-Project': header}
    args = {'node_id': 'prompt-1'} if command == 'focus' else {'level': 2}
    response = client.post(f'/api/projects/{pid}/viewport/{command}', json=args, headers=headers)
    assert response.status_code == 400
    assert response.json()['detail']['code'] == 'INVALID_ARGUMENT'


@pytest.mark.parametrize('transport', ['rest', 'mcp'])
@pytest.mark.parametrize('command', ['focus', 'zoom'])
def test_missing_tool_argument_is_invalid(env, transport, command):
    client, pid, _, _ = env
    with open_socket(client, pid) as ws:
        ready(ws, pid)
        assert error((viewport_rest if transport == 'rest' else viewport_mcp)(client, pid, command, {}), transport)['code'] == 'INVALID_ARGUMENT'
        ws.send_text('ping')
        assert frame(ws) == {'type': 'pong'}


@pytest.mark.parametrize('level', [0.1, 8, 1.75], ids=['minimum', 'maximum', 'interior'])
@pytest.mark.parametrize('transport', ['rest', 'mcp'])
def test_zoom_valid_input_bounds(env, level, transport):
    client, pid, _, pool = env
    response = protocol_roundtrip(client, pool, pid, 'zoom', {'level': level}, transport,
                                 snapshot={'x': 10, 'y': -20, 'scale': level})
    body = payload(response, transport)
    assert body['level'] == level
    assert body['viewport']['scale'] == level


def test_mcp_explicit_project_without_socket_has_same_not_ready_error(env):
    client, pid, _, _ = env
    detail = error(viewport_mcp(client, pid, 'state'), 'mcp')
    assert detail['code'] == 'SERVER_NOT_READY'
    assert detail['details']['reason'] == 'VIEWPORT_NOT_READY'


@pytest.mark.parametrize('args', [[], None, True, 'text'], ids=['array', 'null', 'bool', 'string'])
def test_mcp_state_requires_object_arguments(env, args):
    client, pid, _, _ = env
    with open_socket(client, pid) as ws:
        ready(ws, pid)
        response = client.post(f'/api/mcp?project={pid}', json={
            'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
            'params': {'name': 'viewport.state', 'arguments': args}})
        detail = error(response, 'mcp')
        assert detail['code'] == 'INVALID_ARGUMENT'
        ws.send_text('ping')
        assert frame(ws) == {'type': 'pong'}


def test_ready_connection_records_are_bounded(env):
    from contextlib import ExitStack
    client, pid, _, _ = env
    with ExitStack() as stack:
        for index in range(256):
            ws = stack.enter_context(open_socket(client, pid, f'canvas_bound_{index}'))
            ready(ws, pid)
        overflow = stack.enter_context(open_socket(client, pid, 'canvas_overflow'))
        overflow.send_json({'type': 'canvas_viewport_ready', 'project_id': pid})
        overflow.send_text('ping')
        assert frame(overflow) == {'type': 'pong'}
        response = viewport_rest(client, pid, 'state')
        assert response.status_code == 409
        assert response.json()['detail']['message'] == '检测到 256 个打开的画布窗口，请关闭多余窗口后重试'


def test_target_deleted_after_dispatch_returns_browser_node_not_found(env):
    client, pid, _, pool = env
    with open_socket(client, pid) as ws:
        ready(ws, pid)
        future = pool.submit(viewport_rest, client, pid, 'focus', {'node_id': 'prompt-1'})
        assert_waiting(future)
        command = frame(ws)
        deleted = client.patch(f'/api/project-library/{pid}/canvas', json={
            'base_revision': 1, 'force_empty': True, 'mutations': [{'op': 'remove_node', 'node_id': 'prompt-1'}]})
        assert deleted.status_code == 200, deleted.text
        assert frame(ws)['type'] == 'canvas_updated'
        acknowledge(ws, command, status='rejected', reason='NODE_NOT_FOUND')
        detail = error(future.result(timeout=4), 'rest')
        assert detail['code'] == 'NODE_NOT_FOUND'
        assert detail['details']['command_id'] == command['command_id']


def test_normal_canvas_update_does_not_emit_viewport_rpc(env):
    client, pid, _, _ = env
    with open_socket(client, pid) as ws:
        ready(ws, pid)
        updated = client.patch(f'/api/project-library/{pid}/canvas', json={
            'base_revision': 1, 'mutations': [{'op': 'upsert_node', 'node': {
                'id': 'prompt-1', 'type': 'smart-prompt', 'text': 'human edit', 'x': 1200, 'y': 900}}]})
        assert updated.status_code == 200, updated.text
        assert frame(ws)['type'] == 'canvas_updated'
        ws.send_text('ping')
        assert frame(ws) == {'type': 'pong'}


def protocol_roundtrip(client, pool, pid, command, args=None, transport='rest', snapshot=None):
    with open_socket(client, pid) as ws:
        ready(ws, pid)
        future = pool.submit(viewport_rest if transport == 'rest' else viewport_mcp, client, pid, command, args)
        assert_waiting(future)
        message = frame(ws)
        acknowledge(ws, message, snapshot)
        return future.result(timeout=2)


def test_no_ready_never_fabricates_state(env):
    client, pid, _, _ = env
    response = viewport_rest(client, pid, 'state')
    assert response.status_code == 503
    assert response.json()['detail']['details']['reason'] == 'VIEWPORT_NOT_READY'


@pytest.mark.parametrize('transport', ['rest', 'mcp'])
@pytest.mark.parametrize('command,args', [('focus', {'node_id': 'prompt-1'}), ('zoom', {'level': 2}), ('state', {})],
                         ids=['focus', 'zoom', 'state'])
@pytest.mark.parametrize('legacy', ['metadata-backup', 'retired-generation'])
def test_unready_viewport_never_repairs_or_migrates_documents(env, transport, command, args, legacy):
    client, pid, store, _ = env
    store.flush(pid)
    store.close()
    directory = store.root / pid
    if legacy == 'metadata-backup':
        metadata = directory / 'project.json'
        (directory / 'project.json.bak').write_bytes(metadata.read_bytes())
        metadata.write_text('{broken', encoding='utf-8')
    else:
        path = directory / 'canvas.json'
        document = json.loads(path.read_bytes())
        document['nodes'][0]['jimengPending'] = {'submitId': 'retired-offline-task'}
        path.write_text(json.dumps(document), encoding='utf-8')
    cold = ProjectStore(store.root, snapshot_delay=0)
    try:
        with patch.object(main, 'FIREFLY_PROJECT_STORE', cold):
            before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in directory.glob('*.json*')}
            response = (viewport_rest if transport == 'rest' else viewport_mcp)(client, pid, command, args)
            assert error(response, transport)['code'] == 'SERVER_NOT_READY'
            assert {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in directory.glob('*.json*')} == before
    finally:
        cold.close()


@pytest.mark.parametrize('contender', ['ready', 'rest', 'mcp'])
def test_snapshot_during_migration_does_not_block_pending_timeout(env, tmp_path, contender):
    import shutil
    import threading
    import time
    from app.services import canvas_viewport
    client, pid, store, pool = env
    entered, release = threading.Event(), threading.Event()
    original_copy = shutil.copytree

    def held_copy(*args, **kwargs):
        entered.set()
        release.wait(timeout=3)
        return original_copy(*args, **kwargs)

    try:
        with patch.object(main, 'update_env_values'), patch.object(canvas_viewport, 'TIMEOUT_MS', 300), \
                patch.object(shutil, 'copytree', side_effect=held_copy):
            with open_socket(client, pid) as ws, open_socket(client, pid, 'canvas_contender') as cold:
                ready(ws, pid)
                pending = pool.submit(viewport_rest, client, pid, 'state')
                sent = frame(ws)
                migrating = pool.submit(client.post, '/api/global-settings/project-library', json={
                    'path': str(tmp_path / 'migration-contention'), 'move_existing': True})
                followup = None
                try:
                    assert entered.wait(timeout=2), 'Migration must hold the real project-store lock'
                    assert not pending.done(), 'The pending deadline must still be active'
                    started = time.monotonic()
                    if contender == 'ready':
                        cold.send_json({'type': 'canvas_viewport_ready', 'project_id': pid})
                    else:
                        followup = pool.submit(viewport_rest if contender == 'rest' else viewport_mcp, client, pid, 'state')
                    detail = error(pending.result(timeout=1), 'rest')
                    assert time.monotonic() - started < 1, 'Snapshot I/O must not stall the event-loop deadline'
                    assert detail['details'] == {'outcome': 'unknown', 'command_id': sent['command_id']}
                finally:
                    release.set()
                    assert migrating.result(timeout=5).status_code == 200
                    if followup is not None:
                        followup.result(timeout=5)
    finally:
        current = main.FIREFLY_PROJECT_STORE
        main.FIREFLY_PROJECT_STORE = store
        if current is not store:
            current.close()


@pytest.mark.parametrize('transport', ['rest', 'mcp'])
def test_store_replacement_rejects_old_ack_before_watcher_tick(env, tmp_path, transport):
    import shutil
    client, pid, store, pool = env
    store.flush(pid)
    target = tmp_path / 'replacement-projects'
    shutil.copytree(store.root, target)
    replacement = ProjectStore(target, snapshot_delay=0)
    try:
        with open_socket(client, pid) as ws:
            ready(ws, pid)
            pending = pool.submit(viewport_rest if transport == 'rest' else viewport_mcp, client, pid, 'state')
            sent = frame(ws)
            ack = {**sent, 'type': 'canvas_viewport_ack', 'status': 'observed',
                   'viewport': {'x': 123, 'y': 456, 'scale': 2}}

            def publish_store_and_deliver_ack():
                # A library publication and an already-arriving frame can precede the next timer tick.
                main.FIREFLY_PROJECT_STORE = replacement
                ws._receive_tx.send_nowait({'type': 'websocket.receive', 'text': json.dumps(ack)})

            ws.portal.call(publish_store_and_deliver_ack)
            detail = error(pending.result(timeout=2), transport)
            assert detail['details'] == {'outcome': 'unknown', 'command_id': sent['command_id']}
            assert detail['retryable'] is False
    finally:
        main.FIREFLY_PROJECT_STORE = store
        replacement.close()


@pytest.mark.parametrize('transport', ['rest', 'mcp'])
@pytest.mark.parametrize('command,args', [('idle', {}), ('focus', {'node_id': 'prompt-1'}),
                                         ('zoom', {'level': 2}), ('state', {})],
                         ids=['idle', 'focus', 'zoom', 'state'])
def test_library_migration_retires_old_socket_and_allows_fresh_readiness(env, tmp_path, transport, command, args):
    import anyio
    client, pid, store, pool = env
    with open_socket(client, pid) as ws:
        generation = ready(ws, pid)
        pending = None
        if command != 'idle':
            pending = pool.submit(viewport_rest if transport == 'rest' else viewport_mcp, client, pid, command, args)
            assert_waiting(pending)
            sent = frame(ws)
        with patch.object(main, 'update_env_values') as persist_environment:
            switched = client.post('/api/global-settings/project-library', json={
                'path': str(tmp_path / 'migrated-projects'), 'move_existing': True})
            assert switched.status_code == 200, switched.text
            persist_environment.assert_called_once()
        current = main.FIREFLY_PROJECT_STORE
        try:
            assert current is not store
            assert (store.root / pid / 'project.json').is_file(), 'Migration retains the old files'
            async def close_frame():
                with anyio.move_on_after(1):
                    return await ws._send_rx.receive()
                return None
            closed = ws.portal.call(close_frame)
            assert closed is not None, 'Store replacement must close stale transport for a fresh handshake'
            assert closed['type'] == 'websocket.close' and closed['code'] == 1012, closed
            if pending:
                detail = error(pending.result(timeout=2), transport)
                assert detail['details'] == {'outcome': 'unknown', 'command_id': sent['command_id']}
                assert detail['retryable'] is False
            assert error(viewport_rest(client, pid, 'state'), 'rest')['code'] == 'SERVER_NOT_READY'
            with open_socket(client, pid, 'canvas_after_migration') as fresh:
                assert ready(fresh, pid) != generation
                observed = pool.submit(viewport_rest, client, pid, 'state')
                acknowledge(fresh, frame(fresh), {'x': 150, 'y': -90, 'scale': 1.5})
                assert observed.result(timeout=2).json()['x'] == 150
        finally:
            current.close()


@pytest.mark.parametrize('previously_ready', [False, True], ids=['initial-ready', 'repeated-ready'])
def test_trash_restore_retires_inflight_ready_completion(env, previously_ready):
    import asyncio
    from threading import Event
    from unittest.mock import patch

    client, pid, store, pool = env
    real_to_thread = asyncio.to_thread
    snapshot_finished, resume = Event(), Event()
    armed = True

    async def delay_completed_worker(func, *args, **kwargs):
        nonlocal armed
        # Delay worker delivery only after the real disk read has released the store lock.
        result = await real_to_thread(func, *args, **kwargs)
        if armed and func == store.agent_inverse_snapshot:
            armed = False
            snapshot_finished.set()
            assert await real_to_thread(resume.wait, 5), 'probe release timed out'
        return result

    headers = {'Origin': 'http://127.0.0.1'}
    with open_socket(client, pid) as ws:
        old_generation = ready(ws, pid) if previously_ready else None
        with patch('asyncio.to_thread', delay_completed_worker):
            try:
                ws.send_json({'type': 'canvas_viewport_ready', 'project_id': pid})
                assert snapshot_finished.wait(3), 'real store snapshot did not finish'
                if not previously_ready:
                    assert viewport_rest(client, pid, 'state').status_code == 503
                deleted = client.delete(f'/api/project-library/{pid}', headers=headers)
                assert deleted.status_code == 200, deleted.text
                restored = client.post(f'/api/project-library/trash/{pid}/restore', headers=headers)
                assert restored.status_code == 200, restored.text
                ws.send_text('ping')
                resume.set()
                messages = []
                while True:
                    message = frame(ws)
                    if message == {'type': 'pong'}:
                        break
                    messages.append(message)
                assert not any(m.get('type') == 'canvas_viewport_ready_ack' for m in messages), (
                    'A ready handshake started before project deletion must not admit the old '
                    'page after restore: ' + repr(messages))
                response = viewport_rest(client, pid, 'state')
                assert response.status_code == 503, response.text
                fresh = ready(ws, pid)
                if old_generation is not None:
                    assert fresh != old_generation
                future = pool.submit(viewport_rest, client, pid, 'state')
                command = frame(ws)
                assert command['generation'] == fresh
                acknowledge(ws, command)
                assert future.result(timeout=2).status_code == 200
            finally:
                resume.set()
