"""REST/MCP admission tests using the real confirmation and durable task paths."""
import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
import io
import json
from queue import Queue
import threading
import time
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

import main
from app.project_sessions import register_project_session, unregister_project_session
from app.routers import api_agent_tools as agent
from app.services.agent_host import AgentSession, agent_host_manager as host
from app.services.agent_ops_store import list_operations
from firefly_opsdb import OpsDb
from firefly_projects import ProjectStore


@pytest.fixture
def fx(tmp_path, monkeypatch):
    store = ProjectStore(tmp_path / 'projects', snapshot_delay=0)
    monkeypatch.setattr(main, 'FIREFLY_PROJECT_STORE', store)
    providers = [{'id': 'offline', 'enabled': True, 'primary': True, 'protocol': 'openai',
                  'base_url': 'https://example.invalid/v1', 'api_key': 'stub', 'image_models': ['fixture'],
                  'image_model_capabilities': {'fixture': {'max_references': 20, 'arbitrary_size': True,
                                                               'resolutions': ['1k', '2k'], 'aspect_ratios': ['1:1', '16:9']}}}]
    monkeypatch.setattr(main, 'load_api_providers', lambda **kw: providers)
    buffer = io.BytesIO()
    Image.new('RGB', (8, 8), '#336699').save(buffer, 'PNG')
    data = buffer.getvalue()
    calls, events = Queue(), Queue()
    release = threading.Event()
    release.set()

    async def provider(prompt, size, quality, model, refs, provider_id, **kw):
        calls.put((prompt, size, model, provider_id, [r['url'] for r in refs], kw))
        assert await asyncio.to_thread(release.wait, 5)
        return {'type': 'b64', 'value': base64.b64encode(data).decode(), 'mime_type': 'image/png'}, {}

    monkeypatch.setattr(main, 'generate_ai_image', provider)
    pid = store.create_project('400 fixture')['id']
    other = store.create_project('400 other')['id']
    settings = {'model': 'fixture', 'provider_id': 'offline', 'routingMode': 'manual', 'ratio': 'landscape169', 'count': 1}
    nodes = [{'id': id_, 'type': 'smart-agent'} for id_ in ('actor', 'second-actor')]
    nodes += [{'id': id_, 'type': 'smart-image', 'prompt': prompt, 'runSettings': settings}
              for id_, prompt in (('a', 'A sculpture; overlay text “Firefly”'), ('b', 'Minimalist art'), ('blank', ''))]
    ids = [store.import_media(pid, data + bytes([i]), f'ref{i}.png', 'image/png')['asset_id'] for i in range(9)]
    nodes += [{'id': 'ref', 'type': 'smart-image', 'images': [{'asset_id': asset} for asset in ids]},
              {'id': 'frame', 'type': 'smart-group', 'layoutFrame': True, 'items': ['a', 'b', 'ref']}]
    edges = [{'id': f'e{i}', 'kind': 'input', 'agentRef': True, 'from': 'ref', 'to': 'a',
              'order': i + 1, 'role': 'product' if i == 0 else 'detail', 'enabled': True, 'asset_id': asset}
             for i, asset in enumerate(ids)]
    store.apply_mutations(pid, [{'op': 'upsert_node', 'node': n} for n in nodes] +
                          [{'op': 'upsert_edge', 'edge': e} for e in edges], base_revision=store.canvas(pid)['revision'])
    store.apply_mutations(other, [{'op': 'upsert_node', 'node': nodes[0]}], base_revision=0)
    tab, tab_other = object(), object()
    register_project_session(tab, pid)
    register_project_session(tab_other, other)
    session = AgentSession(project_id=pid, node_id='actor', autonomy='autonomous')
    session._current_turn_id = 'initiating-turn'

    async def event_handler(session, event):
        events.put(event)

    session.event_handlers.append(event_handler)
    monkeypatch.setitem(host.sessions, f'{pid}:actor', session)
    try:
        with TestClient(main.app, base_url='http://localhost', headers={'Origin': 'http://localhost'}) as client:
            yield SimpleNamespace(client=client, store=store, pid=pid, other=other, calls=calls, events=events,
                                  providers=providers, session=session, ids=ids, release=release)
            release.set()
    finally:
        unregister_project_session(tab)
        unregister_project_session(tab_other)
        agent.reset_generation_counts(pid)
        store.close()


def rest(fx, name, args, *, actor='actor', pid=None, headers=None):
    pid = pid or fx.pid
    response = fx.client.post(f'/api/projects/{pid}/generate/{name}?agent_node_id={actor}', json=args,
                              headers={'X-Agent-Project': pid} if headers is None else headers)
    return response.status_code, response.json()


def mcp(fx, name, args, *, sid='external-400', actor='actor', hosted=False, pid=None):
    response = fx.client.post(f'/api/mcp?project={pid or fx.pid}&node={actor}' + ('&caller=hosted' if hosted else ''),
                              headers={'Mcp-Session-Id': sid}, json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                              'params': {'name': 'generate.' + name, 'arguments': args}})
    result = response.json()['result']
    return ((False, result['structuredContent']) if result['isError'] else
            (True, json.loads(result['content'][0]['text'])))


def confirm(fx, event, approved=True, *, actor='actor'):
    response = fx.client.post(f'/api/projects/{fx.pid}/agent-nodes/{actor}/confirm',
                              json={'confirm_id': event['confirm_id'], 'approved': approved})
    assert response.is_success, response.text


def tree(fx):
    return {p.relative_to(fx.store.root): p.read_bytes() for p in fx.store.root.rglob('*') if p.is_file()}


def status_until(fx, batch, pred):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        status, data = rest(fx, 'batch_status', {'batch_id': batch, 'wait_seconds': .1})
        assert status == 200, data
        if pred(data):
            return data
    pytest.fail(f'Batch did not settle: {data}')


def patch_node(fx, ident, **changes):
    fx.store.apply_mutations(fx.pid, [{'op': 'upsert_node', 'node': {**fx.store.find_canvas_node(fx.pid, ident), **changes}}],
                             base_revision=fx.store.canvas(fx.pid)['revision'])


def test_read_only_preview_matches_executed_provider_and_journals_after_turn(fx):
    before = tree(fx)
    tasks = set(main.CANVAS_TASKS)
    preview = rest(fx, 'preflight', {'group_id': 'frame'})[1]
    assert [r['node_id'] for r in preview['items']] == ['a', 'b']
    assert preview['total_images'] == 2 and preview['models_used'] == ['fixture'] and not preview['blockers']
    assert {w['code'] for w in preview['warnings']} == {'REFS_MANY', 'COPY_IN_PROMPT'}
    a = preview['items'][0]
    assert a['user_prompt'] == 'A sculpture; overlay text “Firefly”'
    assert a['refs'][0]['role'] == 'product' and a['refs'][0]['n'] == 1
    assert a['final_prompt'].startswith('参考图说明：图1：产品主体')
    options = {'preflight_id': preview['preflight_id'], 'fallback_models': ['alternate', 'last'], 'max_retries': 3}
    with ThreadPoolExecutor(max_workers=6) as pool:
        replies = list(pool.map(lambda _: rest(fx, 'request_batch', options), range(6)))
    status, receipt = replies[0]
    assert status == 200 and receipt['status'] == 'awaiting_confirmation' and replies == [replies[0]] * 6
    batch = receipt['batch_id']
    event = fx.events.get(timeout=2)
    assert event['type'] == 'confirm' and event['params'] == {
        'batch_id': batch, 'fallback_models': ['alternate', 'last'], 'max_retries': 3, 'total_images': 2}
    assert 'alternate' in event['message'] and event['message'].endswith('2')
    assert tree(fx) == before and set(main.CANVAS_TASKS) == tasks and fx.calls.empty()
    assert not agent._GENERATION_COUNTS.get((fx.pid, 'actor'))
    assert rest(fx, 'trigger', {'node_id': 'a', 'batch_id': batch})[0] == 400
    assert rest(fx, 'request_batch', {**options, 'fallback_models': ['unapproved']}) == (200, receipt)

    fx.session._current_turn_id = None
    confirm(fx, event)
    final = status_until(fx, batch, lambda d: d['status'] == 'done')
    assert final['approved_count'] == final['spent'] == 2 and final['concurrency'] == 1
    for entry, expected in zip(final['items'], preview['items']):
        prompt, size, model, provider, refs, kw = fx.calls.get(timeout=2)
        assert (prompt, size, model, provider, refs) == (expected['final_prompt'], expected['size'], expected['model'],
                                                       expected['provider'], [r['thumb'] for r in expected['refs']])
        assert kw['image_parameters']['aspect_ratio'] == expected['aspect_ratio']
        assert kw['approved_provider']['base_url'] == 'https://example.invalid/v1'
        assert entry['attempts'] == 1 and entry['status'] == 'succeeded'
        assert fx.store.find_canvas_node(fx.pid, entry['result_node_id'])['images']
    assert fx.calls.empty()
    assert fx.events.get(timeout=2)['type'] == 'confirm_resolved' and fx.events.empty()
    assert agent._GENERATION_COUNTS[(fx.pid, 'actor')] == 2
    ops = list_operations(OpsDb(fx.store.root), fx.pid)
    family = [row for row in ops if row['tool'] == 'generate.request_batch']
    root, = [row for row in family if not row.get('parent_op_id')]
    assert len(family) >= 5 and not root['turn_id'] and root['args']['fallback_models'] == options['fallback_models']
    assert rest(fx, 'request_batch', options) == (200, {'batch_id': batch, 'status': 'approved'})


@pytest.mark.parametrize('ending', ['stop', 'reject', 'timeout', 'source', 'ref', 'provider'])
def test_pending_terminal_no_charge_or_submission(fx, monkeypatch, ending):
    if ending == 'timeout':
        monkeypatch.setattr(agent, 'AGENT_WRITE_CONFIRM_TIMEOUT_SECONDS', .25)
    before = tree(fx)
    ok, preview = mcp(fx, 'preflight', {'node_ids': ['a']})
    assert ok
    ok, receipt = mcp(fx, 'request_batch', {'preflight_id': preview['preflight_id']})
    assert ok and receipt['status'] == 'awaiting_confirmation'
    batch = receipt['batch_id']
    event = fx.events.get(timeout=2)
    if ending == 'stop':
        assert mcp(fx, 'stop_batch', {'batch_id': batch})[1]['status'] == 'stopped'
        confirm(fx, event)  # Late approval is a no-op.
    elif ending == 'reject':
        confirm(fx, event, False)
    elif ending in ('source', 'ref', 'provider'):
        if ending == 'source':
            patch_node(fx, 'a', prompt='changed')
        elif ending == 'ref':
            canvas = fx.store.canvas(fx.pid)
            edge = next(e for e in canvas['connections'] if e['id'] == 'e0')
            fx.store.apply_mutations(fx.pid, [{'op': 'upsert_edge', 'edge': {**edge, 'enabled': False}}],
                                     base_revision=canvas['revision'])
        else:
            fx.providers[0]['base_url'] = 'https://changed.invalid'
        before = tree(fx)
        confirm(fx, event)
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        ok, final = mcp(fx, 'batch_status', {'batch_id': batch, 'wait_seconds': .1})
        assert ok
        if final['status'] in ('rejected', 'stopped'):
            break
    assert final['reason'] == {'stop': 'stopped', 'reject': 'rejected', 'timeout': 'timeout'}.get(ending, 'PREFLIGHT_STALE')
    assert final['approved_count'] == final['spent'] == 0 and fx.calls.empty()
    assert not agent._GENERATION_COUNTS.get((fx.pid, 'actor'))
    assert fx.events.get(timeout=2)['type'] == 'confirm_resolved'
    assert tree(fx) == before
    assert mcp(fx, 'request_batch', {'preflight_id': preview['preflight_id']})[1]['batch_id'] == batch


def test_external_mcp_approved_matches_preview_and_wait_is_bounded(fx):
    fx.release.clear()
    ok, pre = mcp(fx, 'preflight', {'node_ids': ['b']})
    assert ok and not pre['blockers']
    args = {'preflight_id': pre['preflight_id']}
    ok, receipt = mcp(fx, 'request_batch', args)
    assert ok
    batch = {'batch_id': receipt['batch_id']}
    event = fx.events.get(timeout=2)
    begin = time.monotonic()
    assert mcp(fx, 'batch_status', {**batch, 'wait_seconds': .05})[1]['status'] == 'awaiting_confirmation'
    assert .04 <= time.monotonic() - begin < 1
    assert mcp(fx, 'batch_status', {**batch, 'wait_seconds': 601})[1]['code'] == 'INVALID_ARGUMENT'
    confirm(fx, event)
    prompt, size, model, provider, refs, _ = fx.calls.get(timeout=4)
    expected = pre['items'][0]
    assert (prompt, size, model, provider, refs) == (expected['final_prompt'], expected['size'],
                                                     expected['model'], expected['provider'], [])
    assert mcp(fx, 'request_batch', args) == (True, {**batch, 'status': 'approved'})
    fx.release.set()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        ok, final = mcp(fx, 'batch_status', {**batch, 'wait_seconds': .1})
        if ok and final['status'] == 'done':
            break
    assert final['status'] == 'done' and final['items'][0]['status'] == 'succeeded'
    assert fx.calls.empty() and fx.events.get(timeout=2)['type'] == 'confirm_resolved' and fx.events.empty()


def test_stale_before_request_and_session_shutdown_do_not_submit(fx):
    pre = rest(fx, 'preflight', {'node_ids': ['b']})[1]['preflight_id']
    patch_node(fx, 'b', prompt='Changed before requesting approval')
    assert rest(fx, 'request_batch', {'preflight_id': pre})[1]['detail']['code'] == 'PREFLIGHT_STALE'
    assert fx.events.empty()
    pre = rest(fx, 'preflight', {'node_ids': ['b']})[1]['preflight_id']
    batch = {'batch_id': rest(fx, 'request_batch', {'preflight_id': pre})[1]['batch_id']}
    event = fx.events.get(timeout=2)
    fx.client.portal.call(agent.shutdown_agent_ops_lifetime)
    assert not fx.session.confirm_handlers
    confirm(fx, event)
    assert rest(fx, 'batch_status', batch)[1]['detail']['code'] == 'BATCH_NOT_FOUND'
    assert fx.calls.empty() and not agent._GENERATION_COUNTS.get((fx.pid, 'actor'))


def test_session_and_project_isolation_and_guard(fx):
    _, preview = mcp(fx, 'preflight', {'node_ids': ['b']})
    args = {'preflight_id': preview['preflight_id']}
    assert mcp(fx, 'request_batch', args, sid='another')[1]['code'] == 'PREFLIGHT_NOT_FOUND'
    assert mcp(fx, 'request_batch', args, actor='second-actor')[1]['code'] == 'PREFLIGHT_NOT_FOUND'
    assert mcp(fx, 'request_batch', args, pid=fx.other)[1]['code'] == 'PREFLIGHT_NOT_FOUND'
    assert not mcp(fx, 'request_batch', args, sid='z' * 129)[0]
    assert rest(fx, 'request_batch', args, headers={})[0] == 400
    assert rest(fx, 'request_batch', args, headers={'X-Agent-Project': fx.other})[0] == 403
    assert rest(fx, 'request_batch', args, headers={'X-Agent-Project': fx.pid, 'Origin': 'http://evil.invalid'})[0] == 403
    assert rest(fx, 'request_batch', args)[1]['detail']['code'] == 'PREFLIGHT_NOT_FOUND'
    _, receipt = mcp(fx, 'request_batch', args)
    batch = {'batch_id': receipt['batch_id']}
    assert mcp(fx, 'stop_batch', batch, sid='other')[1]['code'] == 'BATCH_NOT_FOUND'
    event = fx.events.get(timeout=2)
    confirm(fx, event, actor='second-actor')
    assert mcp(fx, 'batch_status', batch)[1]['status'] == 'awaiting_confirmation'
    agent.reset_generation_counts(fx.pid, 'actor')
    confirm(fx, event)
    assert mcp(fx, 'batch_status', batch)[1]['code'] == 'BATCH_NOT_FOUND'
    assert fx.calls.empty()


def test_stop_preserves_in_flight_and_shared_approved_budget(fx, monkeypatch):
    monkeypatch.setattr(agent, 'GENERATION_SESSION_LIMIT', 2)
    fx.release.clear()
    p1 = rest(fx, 'preflight', {'node_ids': ['a', 'b']})[1]['preflight_id']
    p2 = rest(fx, 'preflight', {'node_ids': ['b']})[1]['preflight_id']
    one = rest(fx, 'request_batch', {'preflight_id': p1})[1]['batch_id']
    two = rest(fx, 'request_batch', {'preflight_id': p2})[1]['batch_id']
    confirm(fx, fx.events.get(timeout=2))
    fx.calls.get(timeout=4)
    assert fx.events.get(timeout=2)['type'] == 'confirm_resolved'
    confirm(fx, fx.events.get(timeout=2))
    denied = status_until(fx, two, lambda d: d['status'] == 'rejected')
    assert denied['reason'] == 'BUDGET_EXCEEDED' and denied['approved_count'] == 0
    assert fx.calls.empty()
    stopped = rest(fx, 'stop_batch', {'batch_id': one})[1]
    assert stopped['status'] == 'stopped' and stopped['approved_count'] == 2 and stopped['spent'] == 1
    assert stopped['items'][1]['status'] == 'stopped'
    fx.release.set()
    final = status_until(fx, one, lambda d: d['items'][0]['status'] == 'succeeded')
    assert fx.store.find_canvas_node(fx.pid, final['items'][0]['result_node_id'])['images'] and fx.calls.empty()


def test_batch_429_with_no_approved_retries_does_not_use_legacy_backoff(fx, monkeypatch):
    async def offline_429(*args, **kwargs):
        fx.calls.put(args[3])
        request = httpx.Request('POST', 'https://example.invalid/v1/images/generations')
        raise httpx.HTTPStatusError('Fixture rate limit', request=request, response=httpx.Response(429))

    monkeypatch.setattr(main, 'generate_ai_image', offline_429)
    monkeypatch.setenv('FIREFLY_PROVIDER_429_RETRY_ATTEMPTS', '3')
    monkeypatch.setenv('FIREFLY_PROVIDER_429_BACKOFF_SECONDS', '.001')
    pre = rest(fx, 'preflight', {'node_ids': ['b']})[1]['preflight_id']
    batch = rest(fx, 'request_batch', {'preflight_id': pre, 'fallback_models': ['other'], 'max_retries': 0})[1]['batch_id']
    confirm(fx, fx.events.get(timeout=2))
    final = status_until(fx, batch, lambda d: d['status'] == 'done')
    assert final['items'][0]['status'] == 'failed' and final['items'][0]['attempts'] == 1
    assert fx.calls.get(timeout=1) == 'fixture' and fx.calls.empty()


def test_product_preset_metadata_and_allowed_unclassified_art(fx, tmp_path, monkeypatch):
    from app.routers import api_prompt_libraries
    library = tmp_path / 'presets.json'
    library.write_text(json.dumps({'libraries': [{'items': [{'id': 'photo', 'category': 'product'}]}]}))
    monkeypatch.setattr(api_prompt_libraries, 'PROMPT_LIBRARY_PATH', library)
    patch_node(fx, 'b', promptPresetId='photo')
    assert rest(fx, 'preflight', {'node_ids': ['b']})[1]['blockers'] == [
        {'node_id': 'b', 'code': 'MISSING_PRODUCT_REF'}]
    patch_node(fx, 'b', promptPresetId='art')
    assert rest(fx, 'preflight', {'node_ids': ['b']})[1]['blockers'] == []


def test_blockers_and_legacy_batch_keeps_its_own_gate(fx):
    patch_node(fx, 'b', excel_task_id='plan-row', planLineage={'source_row': 'Sheet!1'})
    patch_node(fx, 'a', runSettings={'routingMode': 'manual', 'provider_id': 'missing'})
    _, preview = rest(fx, 'preflight', {'node_ids': ['a', 'b', 'blank']})
    assert {(row['node_id'], row['code']) for row in preview['blockers']} == {
        ('a', 'NO_ROUTE'), ('b', 'MISSING_PRODUCT_REF'), ('blank', 'NO_PROMPT')}
    assert rest(fx, 'request_batch', {'preflight_id': preview['preflight_id']})[1]['detail']['code'] == 'PREFLIGHT_BLOCKED'
    assert fx.events.empty()
    with ThreadPoolExecutor(max_workers=1) as pool:
        call = pool.submit(fx.client.post, f'/api/projects/{fx.pid}/canvas/batch_edit?agent_node_id=actor',
                           json={'node_ids': ['b'], 'prompt': 'Legacy batch prompt'}, headers={'X-Agent-Project': fx.pid})
        event = fx.events.get(timeout=2)
        assert event['tool'] == 'canvas.batch_edit' and not call.done()
        assert fx.store.find_canvas_node(fx.pid, 'b')['prompt'] == 'Minimalist art'
        confirm(fx, event)
        response = call.result(5)
    assert response.is_success and response.json()['count'] == 1
    assert fx.store.find_canvas_node(fx.pid, 'b')['prompt'] == 'Legacy batch prompt'
    assert fx.events.get(timeout=2)['type'] == 'confirm_resolved' and fx.events.empty()
    task = response.json()['tasks'][0]['task_id']
    reply = fx.client.get(f'/api/projects/{fx.pid}/generate/tasks/{task}/wait?timeout=5')
    assert reply.json()['status'] == 'succeeded'
