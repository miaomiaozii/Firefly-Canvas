import base64
import copy
import io
import json
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import main
from app.project_sessions import register_project_session, unregister_project_session
from app.routers import api_agent_tools, api_mcp
from app.services.agent_ops_undo import undo_ops
from firefly_projects import ProjectStore


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def run(tmp_path, monkeypatch):
    store = ProjectStore(tmp_path / "projects", snapshot_delay=0)
    monkeypatch.setattr(main, "FIREFLY_PROJECT_STORE", store)
    caps = {"aspect_ratios": ["1:1", "16:9", "9:21", "21:9"], "resolutions": ["1k", "2k"],
            "arbitrary_size": True, "max_references": 20}
    providers = [{"id": "mock", "name": "Mock", "protocol": "openai", "enabled": True,
                  "base_url": "https://example.invalid/v1", "api_key": "mock-only",
                  "image_models": ["fallback", "picked"],
                  "image_model_capabilities": {"fallback": caps, "picked": caps}}]
    monkeypatch.setattr(main, "load_api_providers", lambda *, read_only=False: providers)
    buf = io.BytesIO()
    Image.new("RGB", (8, 8)).save(buf, "PNG")
    png = buf.getvalue()
    calls = []

    async def provider(prompt, size, quality, model, refs, provider_id, **kwargs):
        calls.append({"prompt": prompt, "size": size, "quality": quality, "model": model,
                      "refs": [r["url"] for r in refs], "provider": provider_id,
                      "parameters": kwargs.get("image_parameters"), "preserve_model": kwargs.get("preserve_model")})
        return {"type": "b64", "value": base64.b64encode(png).decode(), "mime_type": "image/png"}, {}

    monkeypatch.setattr(main, "generate_ai_image", provider)
    pid = store.create_project("A6")['id']
    ids = [store.import_media(pid, png, f"ref{i}.png", "image/png")["asset_id"] for i in range(3)]
    # Importing identical bytes deduplicates on some store implementations.
    if len(set(ids)) != 3:
        ids = [store.import_media(pid, png + bytes([i]), f"ref{i}.png", "image/png")["asset_id"] for i in range(3)]
    settings = {"engine": "api", "provider_id": "mock", "model": "picked", "routingMode": "manual",
                "ratio": "portrait14", "resolution": "2k", "count": "2", "quality": "auto"}
    source = {"id": "source", "type": "smart-image", "prompt": "A firefly",
              "createdBy": "agent", "agentNodeId": "actor", "runSettings": settings,
              "reference_asset_ids": [ids[2]]}
    nodes = [source, {"id": "actor", "type": "smart-agent"}, {"id": "browser-result", "type": "smart-image"},
             {"id": "ref", "type": "smart-image", "images": [{"asset_id": asset} for asset in ids]}]
    edges = [{"id": f"ref{i}", "kind": "input", "agentRef": True, "from": "ref", "to": "source",
              "order": 2-i, "role": role, "enabled": i != 2, "asset_id": ids[i]}
             for i, role in enumerate(("detail", "product", "layout"))]
    store.apply_mutations(pid, [{"op": "upsert_node", "node": n} for n in nodes] +
                          [{"op": "upsert_edge", "edge": e} for e in edges], base_revision=0)
    token = object()
    register_project_session(token, pid)
    try:
        with TestClient(main.app, base_url="http://localhost", headers={"origin": "http://localhost"}) as client:
            yield client, store, pid, calls, ids, providers
    finally:
        unregister_project_session(token)
        api_agent_tools.reset_generation_counts(pid)
        store.close()


def rest(client, pid, suffix, args, method="POST"):
    return client.request(method, f"/api/projects/{pid}/{suffix}?agent_node_id=actor", json=args,
                          headers={"X-Agent-Project": pid})


def mcp(client, pid, tool, args):
    response = client.post(f"/api/mcp?project={pid}&node=actor", json={"jsonrpc": "2.0", "id": 1,
                           "method": "tools/call", "params": {"name": tool, "arguments": args}})
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    return (True, result["structuredContent"]) if result["isError"] else (False, json.loads(result["content"][0]["text"]))


def approve(monkeypatch, fn):
    monkeypatch.setattr(api_agent_tools, "request_confirmation_core", fn)
    monkeypatch.setattr(api_mcp, "request_confirmation_core", fn)


def wait(client, url):
    for _ in range(200):
        response = client.get(url)
        assert response.is_success, response.text
        if response.json()["status"] in ("succeeded", "failed"):
            assert response.json()["status"] == "succeeded", response.text
            return
        time.sleep(.02)
    pytest.fail("generation did not finish")


def browser_payload(client, pid, fixture):
    command = ['node', str(ROOT / 'tests/canvas_module_units/generation-payload-396.mjs')]
    raw = subprocess.check_output([*command, '--resolution-input'], cwd=ROOT, input=json.dumps(fixture).encode())
    resolution = client.post(f'/api/project-library/{pid}/image-reference-resolution', json=json.loads(raw))
    assert resolution.is_success, resolution.text
    data = resolution.json()
    raw = subprocess.check_output(command, cwd=ROOT, input=json.dumps({**fixture, 'resolution': data}).encode())
    return json.loads(raw), data


@pytest.mark.parametrize('mixed', [False, True], ids=['managed-only', 'mixed-graph-and-outpaint'])
def test_actual_browser_payload_and_agent_provider_request_match(run, monkeypatch, mixed):
    client, store, pid, calls, ids, providers = run
    if not shutil.which("node"):
        pytest.fail("Node is required to check the actual browser payload")
    async def yes(*args):
        return {"approved": True}
    approve(monkeypatch, yes)
    expected_ids, expected_roles = [ids[1], ids[0]], ['product', 'detail']
    expected_prompt = 'A firefly'
    expected_ratio = '9:21'
    if mixed:
        def extra(name):
            return store.import_media(pid, b'fake-image-' + name.encode(), name + '.png', 'image/png')['asset_id']
        ordinary, manual, mention, base = map(extra, ('ordinary', 'manual', 'mention', 'base'))
        url = f'/api/project-library/{pid}/media/'
        source = store.find_canvas_node(pid, 'source')
        html = (f'<div>Compose <span class="mention-image-token" data-url="{url}{ids[1]}" data-name="Product">'
                f'<img src="https://example.invalid/never-fetch"><span>opaque</span></span> &amp; '
                f'<span class="mention-image-token" data-url="{url}{mention}" data-name="Extra"></span> with '
                f'<span class="mention-image-token" data-url="{url}{ids[2]}" data-node-id="ordinary" '
                'data-image-index="0" data-name="Blocked">not prompt text</span>.</div>')
        source.update(promptDraftHtml=html, promptDraftText='stale fallback', images=[{'asset_id': base, 'name': 'base'}],
                      manualInputRefs=[{'asset_id': manual, 'name': 'manual'}], blockedInputRefs=['ordinary|0'],
                      outpaintSize={'width': 168, 'height': 72, 'aspectRatio': '21:9'})
        store.apply_mutations(pid, [
            {'op': 'upsert_node', 'node': source},
            {'op': 'upsert_node', 'node': {'id': 'ordinary', 'type': 'smart-image', 'images': [
                {'asset_id': ids[2], 'name': 'exclude'}, {'asset_id': ordinary, 'name': 'ordinary'}]}},
            {'op': 'upsert_edge', 'edge': {'id': 'ordinary-edge', 'from': 'ordinary', 'to': 'source', 'kind': 'input'}},
        ], base_revision=store.canvas(pid)['revision'])
        expected_ids += [ordinary, manual, base, mention]
        expected_roles += [None] * 4
        expected_prompt = ('下面是参考图编号：\n图1：图1\n图2：图2\n图3：ordinary\n图4：manual\n图5：base\n图6：Extra'
                           '\n\n用户需求：\nCompose 图1 & 图6 with @Blocked.')
        expected_ratio = '21:9'
    fixture = {"project": pid, "canvas": store.canvas(pid), "providers": providers}
    payload, graph_result = browser_payload(client, pid, fixture)
    assert payload['reference_asset_ids'] == expected_ids
    assert payload['prompt'] == expected_prompt
    assert payload['reference_roles'] == expected_roles
    assert (payload['aspect_ratio'], payload['n'], payload['model']) == (expected_ratio, 2, 'picked')
    assert [item['role'] for item in graph_result['refs']] == [f'image_{n}' for n in range(1, len(expected_ids) + 1)]
    browser = client.post(f"/api/project-library/{pid}/image-generation-tasks", json=payload)
    assert browser.status_code == 202, browser.text
    wait(client, f"/api/project-library/{pid}/image-generation-tasks/{browser.json()['task_id']}")
    agent = rest(client, pid, "generate/trigger", {"node_id": "source"})
    assert agent.status_code == 200, agent.text
    task_id = agent.json()['task_id']
    wait(client, f"/api/projects/{pid}/generate/tasks/{task_id}")
    shared = client.get(f"/api/project-library/{pid}/image-generation-tasks/{task_id}").json()
    task = client.get(f"/api/projects/{pid}/generate/tasks/{task_id}/wait?timeout=1").json()
    assert task['type'] == 'agent-image'
    assert task['op_id'] == agent.json()['op_id']
    assert task['result']['project_asset_ids'] == [image['asset_id'] for image in shared['result']['image_items']]
    assert shared['request_payload']['reference_asset_ids'] == expected_ids
    assert shared['request_payload']['reference_roles'] == expected_roles
    assert shared['request_payload']['prompt'] == expected_prompt
    assert shared['request_payload']['n'] == 2
    assert shared['idempotency_key'] == task_id
    assert shared['result']['result_node_id'] != payload['result_node_id']
    assert len(calls) == 4
    assert calls == [calls[0]] * 4
    assert calls[0]['prompt'] == '参考图说明：图1：产品主体；图2：细节。\n\n' + expected_prompt
    assert calls[0]['refs'] == [f'/api/project-library/{pid}/media/{asset}' for asset in expected_ids]
    assert store.find_canvas_node(pid, 'source')['reference_asset_ids'] == [ids[2]]
    if mixed:
        assert store.find_canvas_node(pid, 'source')['promptDraftHtml'] == html


@pytest.mark.parametrize('url', ['/api/asset-library/old.png', 'D:/must-not-read.png', 'https://example.invalid/no-fetch.png'])
def test_legacy_mention_rejected_before_any_generation_writes(run, monkeypatch, url):
    client, store, pid, calls, ids, providers = run
    source = store.find_canvas_node(pid, 'source')
    source['promptDraftHtml'] = ('Use <span class="mention-image-token" data-name="Legacy" '
                                 f'data-url="{url}"><img src="{url}">opaque</span>')
    store.apply_mutations(pid, [{'op': 'upsert_node', 'node': source}], base_revision=store.canvas(pid)['revision'])
    store.wait_for_snapshot(pid)
    fixture = {'project': pid, 'canvas': store.canvas(pid), 'providers': providers,
               'legacyImportId': ids[2], 'legacySource': url}
    before = {p.relative_to(store.root): p.read_bytes() for p in store.root.rglob('*') if p.is_file()}
    browser, selection = browser_payload(client, pid, fixture)
    assert selection['refs'][-1]['url'] == url
    assert browser['reference_asset_ids'] == [ids[1], ids[0], ids[2]]  # Browser import remains downstream of resolution.
    assert browser['prompt'].endswith('Use 图3')

    def unexpected(*args, **kwargs):
        pytest.fail('Rejected generation attempted a confirmation, provider lookup or write')

    approve(monkeypatch, unexpected)
    monkeypatch.setattr(main, 'load_api_providers', unexpected)
    monkeypatch.setattr(store, 'open_project', unexpected)  # Even implicit repair must not run.
    initial_tasks = set(main.CANVAS_TASKS)
    response = rest(client, pid, 'generate/trigger', {'node_id': 'source'})
    assert response.status_code == 400, response.text
    detail = response.json()['detail']
    assert detail['code'] == 'REF_NOT_IN_PROJECT'
    assert '导入或归档' in detail['hint'] and detail['retryable'] is False
    # Unbound MCP (with a turn) must reject before materializing a carrier or reserving an op.
    response = client.post(f'/api/mcp?project={pid}&turn_id=unbound-test', json={
        'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
        'params': {'name': 'generate.trigger', 'arguments': {'node_id': 'source'}},
    })
    result = response.json()['result']
    assert result['isError'] and result['structuredContent'] == detail
    assert set(main.CANVAS_TASKS) == initial_tasks
    assert calls == []
    assert {p.relative_to(store.root): p.read_bytes() for p in store.root.rglob('*') if p.is_file()} == before


@pytest.mark.parametrize("transport", ["rest", "mcp", "rest-batch", "mcp-batch"])
@pytest.mark.parametrize("change_during_confirmation", [False, True], ids=["stable-inputs", "changed-inputs"])
def test_gate_budget_and_dispatch_share_count_snapshot(run, monkeypatch, transport, change_during_confirmation):
    client, store, pid, calls, _, _ = run
    confirmations = []

    def change_settings():
        node = store.find_canvas_node(pid, "source")
        node["runSettings"] = {**node["runSettings"], "count": 8, "routingMode": "auto", "ratio": "square"}
        store.apply_mutations(pid, [{"op": "upsert_node", "node": node}], base_revision=store.canvas(pid)["revision"])

    async def yes(project, actor, tool, params, message, timeout):
        assert (project, actor) == (pid, "actor")
        confirmations.append((params, message))
        if change_during_confirmation:
            change_settings()
        return {"approved": True}
    approve(monkeypatch, yes)
    monkeypatch.setattr(api_agent_tools, "GENERATION_SESSION_LIMIT", 2)
    batch = transport.endswith("batch")
    args = {"node_ids": ["source"], "prompt": "Batch"} if batch else {"node_id": "source"}
    def trigger():
        if transport.startswith("rest"):
            response = rest(client, pid, "canvas/batch_edit" if batch else "generate/trigger", args)
            return not response.is_success, response.json()
        return mcp(client, pid, "canvas.batch_edit" if batch else "generate.trigger", args)
    error, result = trigger()
    assert "2 张" in confirmations[0][1]
    preflight = confirmations[0][0]["preflight"]
    assert (preflight[0] if batch else preflight)["count"] == 2
    assert confirmations[0][0]["n"] == 2
    if batch and change_during_confirmation:
        # #402: a confirmed batch cannot silently rebase changed inputs at child admission.
        assert error, result
        detail = result["detail"] if transport.startswith("rest") else result
        assert detail["code"] == "CANVAS_CONFLICT"
        assert detail["details"]["tasks"] == []
        assert calls == []
        expected_calls = 0
    else:
        assert not error, result
        task = result["tasks"][0] if batch else result
        wait(client, f"/api/projects/{pid}/generate/tasks/{task['task_id']}")
        assert len(calls) == 2 and all(c["model"] == "picked" for c in calls)
        expected_calls = 2
    assert api_agent_tools._GENERATION_COUNTS[(pid, "actor")] == 2
    change_settings()
    error, result = trigger()
    assert error, result
    detail = result["detail"] if transport.startswith("rest") else result
    assert detail["code"] == "BUDGET_EXCEEDED"
    assert len(confirmations) == 2 and confirmations[1][0]["n"] == 8
    assert api_agent_tools._GENERATION_COUNTS[(pid, "actor")] == 2
    assert len(calls) == expected_calls
    node = store.find_canvas_node(pid, "source")
    assert node["runSettings"]["count"] == 8
    assert node["runSettings"]["routingMode"] == "auto"


def test_update_run_settings_merge_prev_and_journal_noop(run):
    client, store, pid, _, _, _ = run
    before = copy.deepcopy(store.find_canvas_node(pid, "source")["runSettings"])
    args = {"runSettings": {"count": 3, "aspect_ratio": "16:9"}}
    response = rest(client, pid, "canvas/nodes/source", args, method="PATCH")
    assert response.is_success, response.text
    result = response.json()
    assert result["prev"]["runSettings"]["count"] == before["count"]
    assert result["prev"]["runSettings"]["aspect_ratio"] == "1:4"
    assert result["op_id"]
    changed = store.find_canvas_node(pid, "source")["runSettings"]
    assert {k: changed[k] for k in ("engine", "model", "provider_id", "resolution", "quality")} == {
        k: before[k] for k in ("engine", "model", "provider_id", "resolution", "quality")}
    assert (changed["ratio"], changed["count"]) == ("wide", 3)
    revision = store.canvas(pid)["revision"]
    error, again = mcp(client, pid, "canvas.update_node", {"node_id": "source", **args})
    assert not error and "op_id" not in again
    for value in ({"apiKey": "no"}, {"count": True}, {"routingMode": "unknown"}, None):
        assert rest(client, pid, "canvas/nodes/source", {"runSettings": value}, method="PATCH").status_code == 400
        assert mcp(client, pid, "canvas.update_node", {"node_id": "source", "runSettings": value})[0]
    assert store.canvas(pid)["revision"] == revision


def test_paid_arguments_cannot_override_settings_or_gate(run, monkeypatch):
    client, store, pid, calls, _, _ = run
    async def no(*args):
        return {"approved": False, "reason": "rejected"}
    approve(monkeypatch, no)
    args = {"node_id": "source", "n": 1, "approved": True, "routingMode": "auto", "batch_id": "forged"}
    assert rest(client, pid, "generate/trigger", args).status_code in (400, 409)
    assert mcp(client, pid, "generate.trigger", args)[0]
    assert not calls and store.find_canvas_node(pid, "source")["runSettings"]["count"] == "2"


@pytest.mark.parametrize('cancel', [False, True])
def test_shared_agent_terminal_retains_history_and_guards(run, monkeypatch, cancel):
    import asyncio

    client, store, pid, *_ = run
    entered, release = threading.Event(), threading.Event()
    calls = []

    async def yes(*args):
        return {'approved': True}

    async def provider(*args, **kwargs):
        calls.append(args)
        entered.set()
        while not release.is_set():
            await asyncio.sleep(.01)
        raise RuntimeError('mock failure')

    approve(monkeypatch, yes)
    monkeypatch.setattr(main, 'generate_ai_image', provider)
    response = rest(client, pid, 'generate/trigger', {'node_id': 'source'})
    assert response.is_success, response.text
    task_id, op_id, result_id = (response.json()[key] for key in ('task_id', 'op_id', 'result_node_id'))
    browser_url = f'/api/project-library/{pid}/image-generation-tasks/{task_id}'
    agent_url = f'/api/projects/{pid}/generate/tasks/{task_id}'
    try:
        assert entered.wait(3)
        assert undo_ops(store, pid, op_id=op_id)['status'] == 'generating'
        pending = store.find_canvas_node(pid, result_id)['pendingTasks'][0]
        assert pending['taskId'] == task_id and pending['count'] == 2
        assert pending['route']['model'] == 'picked'
        replay = client.post(f'/api/project-library/{pid}/image-generation-tasks', json=pending['requestPayload'])
        assert replay.status_code == 202 and replay.json()['task_id'] == task_id
        if cancel:
            cancelled = client.post(f'{browser_url}/cancel')
            assert cancelled.is_success, cancelled.text
        else:
            release.set()
        status = client.get(f'{agent_url}/wait?timeout=4').json()
        expected = 'cancelled' if cancel else 'failed'
        assert status['status'] == expected, status
        assert client.get(browser_url).json()['status'] == expected
        result_node = store.find_canvas_node(pid, result_id)
        assert result_node is None if not cancel else not result_node.get('pendingTasks')
        undone = undo_ops(store, pid, op_id=op_id)
        assert undone['status'] == 'reverted', undone
        assert undone['irreversible_effects']
        assert store.find_canvas_node(pid, result_id) is None
        records = [r for r in store.canvas(pid)['history'] if r['task_id'] == task_id]
        assert [(r['status'], r['idempotency_key']) for r in records] == [(expected, task_id)]
        assert client.post(f'{browser_url}/retry').status_code == 409
        assert len(calls) <= 2
    finally:
        release.set()


def test_restart_reconciles_shared_pending_without_dispatch(run, monkeypatch):
    import asyncio

    client, store, pid, calls, *_ = run

    async def yes(*args):
        return {'approved': True}

    def leave_pending(lifecycle, run):
        # Crash boundary: the registered request and its journal are on disk,
        # but no in-memory worker survives to tell us the provider outcome.
        done = asyncio.get_running_loop().create_future()
        done.set_result(None)
        return done

    approve(monkeypatch, yes)
    monkeypatch.setattr(api_agent_tools._AgentImageLifecycle, 'schedule', leave_pending)
    response = rest(client, pid, 'generate/trigger', {'node_id': 'source'})
    assert response.is_success, response.text
    task_id, op_id, result_id = (response.json()[key] for key in ('task_id', 'op_id', 'result_node_id'))
    with api_agent_tools.CANVAS_TASK_LOCK:
        api_agent_tools.CANVAS_TASKS.pop(task_id)
    reopened = ProjectStore(store.root, snapshot_delay=0)
    try:
        with monkeypatch.context() as restart:
            restart.setattr(main, 'FIREFLY_PROJECT_STORE', reopened)
            pending = reopened.find_canvas_node(pid, result_id)['pendingTasks'][0]
            assert pending['requestPayload']['n'] == 2
            assert pending['route']['model'] == 'picked'
            assert undo_ops(reopened, pid, op_id=op_id)['status'] == 'generating'
            from app.services import agent_ops_store
            with monkeypatch.context() as unavailable:
                unavailable.setattr(agent_ops_store, 'list_operation_tree', lambda *args: [])
                url = f'/api/project-library/{pid}/image-generation-tasks/{task_id}'
                assert client.get(url).json()['status'] == 'cleanup_pending'
                assert reopened.find_canvas_node(pid, result_id)['pendingTasks'][0] == pending
                assert not reopened.canvas(pid)['history']
            status = client.get(f'/api/projects/{pid}/generate/tasks/{task_id}').json()
            assert status['status'] == 'failed', status
            assert status['result']['history_record']['termination_reason'] == 'service_restart_unknown'
            assert status['result']['history_record']['request_payload'] == pending['requestPayload']
            route = f'/api/project-library/{pid}/image-generation-tasks'
            replay = client.post(route, json=pending['requestPayload'])
            assert replay.status_code == 202 and replay.json()['task_id'] == task_id
            assert undo_ops(reopened, pid, op_id=op_id)['status'] == 'reverted'
            assert reopened.find_canvas_node(pid, result_id) is None
            assert len([r for r in reopened.canvas(pid)['history'] if r['task_id'] == task_id]) == 1
            assert calls == []
    finally:
        reopened.close()


def test_cancel_after_archive_keeps_output_in_history_without_partial_canvas(run, monkeypatch):
    import asyncio
    from app.routers import api_project_library

    client, store, pid, _, ids, _ = run
    archive_released, first_archived, second_started = threading.Event(), threading.Event(), threading.Event()
    candidate = api_project_library.run_routed_image_candidate
    calls = 0

    async def yes(*args):
        return {'approved': True}

    async def provider(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return await candidate(*args, **kwargs)
        second_started.set()
        while not archive_released.is_set():
            await asyncio.sleep(.01)
        raise RuntimeError('should not finish before cancellation')

    async def archive(*args):
        first_archived.set()
        return {'asset_id': ids[0], 'kind': 'image'}

    approve(monkeypatch, yes)
    monkeypatch.setattr(api_project_library, 'run_routed_image_candidate', provider)
    monkeypatch.setattr(api_project_library, 'archive_project_generated_image', archive)
    result = rest(client, pid, 'generate/trigger', {'node_id': 'source'}).json()
    try:
        assert first_archived.wait(3) and second_started.wait(3)
        assert not store.find_canvas_node(pid, result['result_node_id']).get('images')
        url = f"/api/project-library/{pid}/image-generation-tasks/{result['task_id']}/cancel"
        response = client.post(url)
        assert response.is_success, response.text
        assert undo_ops(store, pid, op_id=result['op_id'])['status'] == 'reverted'
        record, = [r for r in store.canvas(pid)['history'] if r['task_id'] == result['task_id']]
        assert record['status'] == 'cancelled'
        assert [item['asset_id'] for item in record['outputs']] == [ids[0]]
        assert store.media_record(pid, ids[0])
    finally:
        archive_released.set()


def test_archive_finishing_at_deadline_is_recorded_before_shared_task_settles(run, monkeypatch):
    import asyncio
    from app.routers import api_project_library

    client, store, pid, _, ids, _ = run
    archive_cancelled, settlement_started, allow_settlement = (threading.Event() for _ in range(3))
    original_archive = api_agent_tools._AgentImageLifecycle.archive
    original_event = api_agent_tools._generation_event
    dispatches = []

    async def provider(*args, **kwargs):
        dispatches.append(args)
        return {'type': 'b64', 'value': 'not-used'}, {}

    async def archive(*args):
        while not archive_cancelled.is_set():
            await asyncio.sleep(.001)
        return {'asset_id': ids[0], 'kind': 'image'}

    async def observe_archive(lifecycle, fn, *args):
        # Signal the archive only after wait_for actually cancels its candidate.
        future = asyncio.create_task(original_archive(lifecycle, fn, *args))
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            archive_cancelled.set()
            await future
            raise

    async def event(project_id, lifecycle, phase):
        if phase == 'end':
            settlement_started.set()
            while not allow_settlement.is_set():
                await asyncio.sleep(.01)
        return await original_event(project_id, lifecycle, phase)

    async def yes(*args):
        return {'approved': True}

    approve(monkeypatch, yes)
    monkeypatch.setattr(api_project_library, 'image_generation_task_timeout_seconds', lambda: .25)
    monkeypatch.setattr(api_project_library, 'run_routed_image_candidate', provider)
    monkeypatch.setattr(api_project_library, 'archive_project_generated_image', archive)
    monkeypatch.setattr(api_agent_tools._AgentImageLifecycle, 'archive', observe_archive)
    monkeypatch.setattr(api_agent_tools, '_generation_event', event)
    node = store.find_canvas_node(pid, 'source')
    node['runSettings']['count'] = '1'
    store.apply_mutations(pid, [{'op': 'upsert_node', 'node': node}], base_revision=store.canvas(pid)['revision'])
    response = rest(client, pid, 'generate/trigger', {'node_id': 'source'})
    assert response.is_success, response.text
    task_id, result_id, op_id = (response.json()[key] for key in ('task_id', 'result_node_id', 'op_id'))
    browser = f'/api/project-library/{pid}/image-generation-tasks/{task_id}'
    agent = f'/api/projects/{pid}/generate/tasks/{task_id}'
    try:
        assert settlement_started.wait(5)
        for path in (browser, agent):
            status = client.get(path).json()
            assert status['status'] == 'cleanup_pending' and status['result'] is None, status
        assert undo_ops(store, pid, op_id=op_id, is_running=lambda pid, op: api_agent_tools.agent_generation_is_running(store, pid, op))['status'] == 'generating'
        assert not store.find_canvas_node(pid, result_id).get('images')
        record, = [r for r in store.canvas(pid)['history'] if r['task_id'] == task_id]
        assert record['status'] == 'timeout'
        assert [item['asset_id'] for item in record['outputs']] == [ids[0]]
        assert len(dispatches) == 1
    finally:
        allow_settlement.set()
    assert client.get(f'{agent}/wait?timeout=4').json()['status'] == 'timeout'
    assert client.get(browser).json()['status'] == 'timeout'
    assert undo_ops(store, pid, op_id=op_id)['status'] == 'reverted'
