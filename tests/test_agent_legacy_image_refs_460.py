import asyncio
import base64
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from fastapi.testclient import TestClient
from PIL import Image
import pytest

import main
from app.project_sessions import register_project_session, unregister_project_session
from app.routers import api_agent_tools as agent, api_project_library as library
from app.services.agent_ops_context import agent_ops_scope
from app.services.agent_ops_store import list_operations
from app.services.agent_ops_undo import undo_ops
from firefly_opsdb import OpsDb
from firefly_projects import ProjectStore
from firefly_withdrawn_refs import (
    AUTO_ARCHIVED_REFS, REATTACH_HINT, WITHDRAWN_HISTORY_REFS, WITHDRAWN_REFS, historical_image_retry_hint,
)
from tests.agent_image_task_fakes import route_stub, stub_providers


URL = '/api/storage-files/upload/reference.png'


def png(color):
    with io.BytesIO() as output:
        Image.new('RGB', (4, 4), color).save(output, 'PNG')
        return output.getvalue()


def mention(url, name='Ref'):
    return f'<span class="mention-image-token" data-name="{name}" data-url="{url}"></span>'


@pytest.fixture
def project(tmp_path, monkeypatch):
    store = ProjectStore(tmp_path / 'projects', snapshot_delay=0)
    pid = store.create_project('Synthetic #460')['id']
    runtime = tmp_path / 'runtime'
    roots = {'RUNTIME_ROOT': runtime, 'ASSETS_DIR': runtime / 'assets', 'OUTPUT_DIR': runtime / 'output',
             'OUTPUT_INPUT_DIR': runtime / 'assets/input', 'OUTPUT_OUTPUT_DIR': runtime / 'assets/output',
             'LOCAL_UPLOAD_DIR': runtime / 'assets/uploads'}
    for key, path in roots.items():
        path.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(main, key, str(path))
    source = roots['OUTPUT_INPUT_DIR'] / 'reference.png'
    source.write_bytes(png('red'))
    asset = hashlib.sha256(source.read_bytes()).hexdigest()
    monkeypatch.setattr(main, 'FIREFLY_PROJECT_STORE', store)
    monkeypatch.setattr(main, 'load_api_providers', stub_providers)
    monkeypatch.setattr(main, 'route_image_generation', lambda *a, **kw: route_stub())
    monkeypatch.setattr(main.manager, 'broadcast_canvas_updated', AsyncMock())
    calls = AsyncMock(return_value=({'type': 'b64', 'value': base64.b64encode(png('green')).decode(),
                                     'mime_type': 'image/png'}, {}))
    monkeypatch.setattr(library, 'run_routed_image_candidate', calls)
    store.apply_mutations(pid, [{'op': 'upsert_node', 'node': n} for n in (
        {'id': 'source', 'type': 'smart-image', 'promptDraftHtml': 'Draw ' + mention(URL),
         'runSettings': {'provider_id': 'mock'}},
        {'id': 'actor', 'type': 'smart-agent'},
    )], base_revision=0)
    store.flush(pid)
    token = object()
    register_project_session(token, pid)
    try:
        yield SimpleNamespace(store=store, pid=pid, directory=store.root / pid, calls=calls,
                              asset=asset, source=source, runtime=runtime)
    finally:
        unregister_project_session(token)
        agent.reset_generation_counts(pid)
        with main.CANVAS_TASK_LOCK:
            for task, record in list(main.CANVAS_TASKS.items()):
                if record.get('project_id') == pid:
                    main.CANVAS_TASKS.pop(task, None)
        store.close()


def mutate(project, *ops):
    project.store.apply_mutations(project.pid, ops, base_revision=project.store.canvas(project.pid)['revision'])
    project.store.flush(project.pid)


def source_fields(project, **fields):
    node = project.store.find_canvas_node(project.pid, 'source')
    mutate(project, {'op': 'upsert_node', 'node': {**node, **fields}})


def disk(project):
    return {p.relative_to(project.store.root): p.read_bytes() for p in project.store.root.rglob('*') if p.is_file()}


def post(project, payload):
    return TestClient(main.app, base_url='http://localhost').post(
        f'/api/projects/{project.pid}/generate/trigger?agent_node_id=actor', json=payload,
        headers={'origin': 'http://localhost', 'X-Agent-Project': project.pid},
    )


async def generate(project, *, turn='turn', fail=False):
    original = project.calls.return_value

    async def candidate(*args, **kwargs):
        opened = project.store.open_project(project.pid)['canvas']
        task, = [task for node in opened['nodes'] for task in node.get('pendingTasks', [])]
        assert task['referenceAssetIds'] == [project.asset]
        project.pending = deepcopy(task)
        project.received_refs = args[5]
        if fail:
            raise RuntimeError('synthetic generation failure')
        return original

    project.calls.side_effect = candidate
    with agent_ops_scope(project_id=project.pid, agent_node_id='actor', turn_id=turn,
                         tool='generate.trigger', args={'node_id': 'source'}):
        result = await agent.trigger_generation_core(project.pid, 'source', agent_node_id='actor')
    future = main.PROJECT_IMAGE_GENERATION_FUTURES.get(result['task_id'])
    if future is not None:
        await asyncio.wait_for(future, 5)
    assert (await agent.get_task_status(project.pid, result['task_id']))['status'] == ('failed' if fail else 'succeeded')
    return result


@pytest.mark.parametrize('locator,relative', [(URL, 'assets/input/reference.png'),
    ('/assets/uploads/reference.png', 'assets/uploads/reference.png'),
    ('/output/reference.png', 'output/reference.png')])
def test_saved_local_mention_archives_after_read_only_plan(project, monkeypatch, locator, relative):
    (project.runtime / relative).write_bytes(project.source.read_bytes())
    source_fields(project, promptDraftHtml='Draw ' + mention(locator) + mention('/assets/never.png', 'Skip'),
                  blockedInputRefs=['url|/assets/never.png'])
    resolver = Mock(wraps=main.output_file_from_url)
    monkeypatch.setattr(main, 'output_file_from_url', resolver)
    before = disk(project)
    plan = asyncio.run(agent.prepare_generation(project.pid, 'source'))
    assert list(plan.payload.reference_asset_ids) == [project.asset]
    assert plan.payload.prompt.endswith('Draw 图1@Skip')
    assert disk(project) == before
    assert resolver.call_args_list[0].args == (locator,)
    result = asyncio.run(generate(project))
    canvas = project.store.canvas(project.pid)
    history, = canvas['history']
    assert history['task_id'] == result['task_id']
    assert history[AUTO_ARCHIVED_REFS] == project.pending[AUTO_ARCHIVED_REFS] == [project.asset]
    assert [item['asset_id'] for item in project.received_refs] == [project.asset]
    assert set(call.args[0] for call in resolver.call_args_list) == {locator}
    assert project.store.media_record(project.pid, project.asset)['source']['type'] == 'agent_legacy_reference'


@pytest.mark.parametrize('invalid', ['remote', 'file', 'absolute', 'cross-project', 'traversal', 'configured',
                                    'not-image', 'manual-only', 'override', 'caller-path'])
def test_untrusted_input_rejected_before_confirmation_or_import(project, monkeypatch, tmp_path, invalid):
    locator = {'remote': 'https://example.invalid/no.png', 'file': 'file:///D:/no.png', 'absolute': 'D:/no.png',
               'cross-project': '/api/project-library/other/media/test', 'traversal': '/assets/%2e%2e/no.png'}.get(invalid, URL)
    if invalid == 'configured':
        monkeypatch.setattr(main, 'OUTPUT_INPUT_DIR', str(tmp_path / 'not-app-storage'))
    if invalid == 'not-image':
        project.source.write_bytes(b'not a PNG')
    if invalid in {'manual-only', 'override'}:
        # Current mutation rules disallow these legacy fields; simulate a saved old canvas.
        path = project.directory / 'canvas.json'
        canvas = json.loads(path.read_bytes())
        canvas['nodes'][0].update(manualInputRefs=[{'url': URL}],
                                  promptDraftHtml='Draw' if invalid == 'manual-only' else 'Draw ' + mention(URL))
        path.write_text(json.dumps(canvas), encoding='utf-8')
        project.store = ProjectStore(project.store.root, snapshot_delay=0)
        monkeypatch.setattr(main, 'FIREFLY_PROJECT_STORE', project.store)
    else:
        source_fields(project, promptDraftHtml='Draw ' + mention(locator))
    forbidden = Mock(side_effect=AssertionError('an untrusted request reached confirmation, routing or import'))
    monkeypatch.setattr(agent, 'request_confirmation_core', forbidden)
    monkeypatch.setattr(main, 'load_api_providers', forbidden)
    monkeypatch.setattr(project.store, 'import_media_file', forbidden)
    before = disk(project)
    if invalid == 'override':
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as error:
            asyncio.run(agent.prepare_generation(project.pid, 'source', prompt_override='override'))
        assert error.value.detail['code'] == 'REF_NOT_IN_PROJECT'
    else:
        response = post(project, {'node_id': 'source', **({'source_path': str(project.source)} if invalid == 'caller-path' else {})})
        assert response.status_code == 400, response.text
        assert response.json()['detail']['code'] == ('INVALID_ARGUMENT' if invalid == 'caller-path' else 'REF_NOT_IN_PROJECT')
    forbidden.assert_not_called()
    project.calls.assert_not_called()
    assert disk(project) == before


def test_denied_confirmation_does_not_archive_or_debit(project, monkeypatch):
    confirm = AsyncMock(return_value={'approved': False, 'reason': 'rejected'})
    monkeypatch.setattr(agent, 'request_confirmation_core', confirm)
    debit = Mock(side_effect=AssertionError('denied request reached quota'))
    monkeypatch.setattr(agent, 'enforce_generation_limit', debit)
    before = disk(project)
    response = post(project, {'node_id': 'source'})
    assert response.status_code == 409, response.text
    assert response.json()['detail']['code'] == 'CONFIRM_REJECTED'
    confirm.assert_awaited_once()
    debit.assert_not_called()
    project.calls.assert_not_called()
    assert disk(project) == before


def test_changed_bytes_after_confirmation_do_not_archive(project, monkeypatch):
    async def confirm(*args):
        project.source.write_bytes(png('blue'))
        return {'approved': True}
    monkeypatch.setattr(agent, 'request_confirmation_core', confirm)
    before = disk(project)
    response = post(project, {'node_id': 'source'})
    assert response.status_code == 400, response.text
    project.calls.assert_not_called()
    assert disk(project) == before


def test_dedup_existing_digest_not_tagged_or_withdrawn(project):
    prior = project.store.import_media(project.pid, project.source.read_bytes(), 'owned.png', 'image/png')
    result = asyncio.run(generate(project))
    history, = project.store.canvas(project.pid)['history']
    assert AUTO_ARCHIVED_REFS not in history and AUTO_ARCHIVED_REFS not in project.pending
    assert undo_ops(project.store, project.pid, op_id=result['op_id'])['status'] == 'reverted'
    assert WITHDRAWN_REFS not in project.store.canvas(project.pid)
    current = project.store.media_record(project.pid, project.asset)
    current.pop('path')
    assert current == prior


@pytest.mark.parametrize('selection', ['family', 'turn'])
def test_undo_marks_history_on_read_and_keeps_bytes(project, selection):
    result = asyncio.run(generate(project))
    raw_path = project.directory / 'history.json'
    raw = raw_path.read_bytes()
    canvas_before = (project.directory / 'canvas.json').read_bytes()
    original, = project.store.canvas(project.pid)['history']
    target = {'op_id': result['op_id']} if selection == 'family' else {'turn_id': 'turn'}
    assert undo_ops(project.store, project.pid, **target)['status'] == 'reverted'
    persisted = json.loads((project.directory / 'canvas.json').read_bytes())
    assert persisted[WITHDRAWN_REFS] == {result['task_id']: [project.asset]}
    assert result['result_node_id'] not in {n['id'] for n in persisted['nodes']}
    assert (project.directory / 'canvas.json.bak').read_bytes() == canvas_before
    assert raw_path.read_bytes() == raw
    reopened = ProjectStore(project.store.root)
    history, = reopened.canvas(project.pid)['history']
    assert history.pop(WITHDRAWN_HISTORY_REFS) == [project.asset]
    assert history == original
    for asset in [project.asset, original['outputs'][0]['asset_id']]:
        assert Path(reopened.media_record(project.pid, asset)['path']).is_file()
    with pytest.raises(library.HTTPException) as error:
        asyncio.run(agent.prepare_generation(project.pid, 'source'))
    assert error.value.status_code == 409
    assert error.value.detail['hint'] == REATTACH_HINT
    owned = f'/api/project-library/{project.pid}/media/{project.asset}'
    source_fields(project, promptDraftHtml='Draw ' + mention(owned))
    plan = asyncio.run(agent.prepare_generation(project.pid, 'source'))
    assert plan.payload.reference_asset_ids == [project.asset] and not plan.legacy_images
    reopened.close()


@pytest.mark.parametrize('reference,target', [('canvas', 'family'), ('history', 'turn'),
                                             ('output', 'family'), ('pending', 'child')])
def test_other_references_conflict_with_zero_writes(project, reference, target):
    result = asyncio.run(generate(project))
    if reference == 'canvas':
        url = f'/api/project-library/{project.pid}/media/{project.asset}'
        mutate(project, {'op': 'upsert_node', 'node': {'id': 'consumer', 'type': 'smart-image',
                                                       'promptDraftHtml': mention(url)}})
    elif reference == 'history':
        mutate(project, {'op': 'set_canvas_meta', 'history': [{'id': 'other',
                         'request_payload': {'reference_asset_ids': [project.asset]}}]})
    elif reference == 'output':
        # The input and an independent output can share a content digest.
        with project.store._lock:
            project.store._histories[project.pid][0]['outputs'].append({'asset_id': project.asset})
            project.store.flush(project.pid)
    else:
        mutate(project, {'op': 'upsert_node', 'node': {'id': 'consumer', 'type': 'smart-image'}})
        project.store.begin_image_generation(project.pid, 'consumer', {'taskId': 'durable-other', 'count': 1,
            'referenceAssetIds': [project.asset]}, base_revision=project.store.canvas(project.pid)['revision'])
        project.store.flush(project.pid)
        project.store = ProjectStore(project.store.root, snapshot_delay=0)
    operations = list_operations(OpsDb(project.store.root), project.pid)
    child = next(op for op in operations if op.get('parent_op_id') == result['op_id']
                 and any(change['path'] == ['pendingTasks'] and change['before'].get('value') for change in op['before']))
    selector = {'family': {'op_id': result['op_id']}, 'turn': {'turn_id': 'turn'}, 'child': {'op_id': child['op_id']}}[target]
    before = disk(project)
    opened = project.store.canvas(project.pid)
    undo = undo_ops(project.store, project.pid, **selector)
    assert undo['status'] == 'conflict'
    assert undo['conflicts'][0]['asset_id'] == project.asset
    assert project.store.canvas(project.pid) == opened
    assert list_operations(OpsDb(project.store.root), project.pid) == operations
    assert disk(project) == before


def retry(project, task_id):
    return TestClient(main.app, base_url='http://localhost').post(
        f'/api/project-library/{project.pid}/image-generation-tasks/{task_id}/retry',
        headers={'origin': 'http://localhost'},
    )


def test_browser_still_cannot_retry_ordinary_agent_history(project):
    project.store.import_media(project.pid, project.source.read_bytes(), 'owned.png', 'image/png')
    result = asyncio.run(generate(project, fail=True))
    task_id = result['task_id']
    with main.CANVAS_TASK_LOCK:
        main.CANVAS_TASKS.pop(task_id)
    project.calls.reset_mock()
    response = retry(project, task_id)
    assert response.status_code == 409
    assert response.json()['detail'] == '请从 Agent 重新发起生成并确认'
    project.calls.assert_not_called()


def test_real_backup_recovery_without_marker_rejects_retry_before_billing(project, monkeypatch):
    result = asyncio.run(generate(project, fail=True))
    task_id = result['task_id']
    assert undo_ops(project.store, project.pid, op_id=result['op_id'])['status'] == 'reverted'
    assert WITHDRAWN_REFS in json.loads((project.directory / 'canvas.json').read_bytes())
    (project.directory / 'canvas.json').write_bytes(b'{broken')
    with main.CANVAS_TASK_LOCK:
        main.CANVAS_TASKS.pop(task_id)
    reopened = ProjectStore(project.store.root, snapshot_delay=0)
    monkeypatch.setattr(main, 'FIREFLY_PROJECT_STORE', reopened)
    canvas = reopened.open_project(project.pid)['canvas']
    assert canvas['_recovery']['restored_from'] == 'canvas.json.bak'
    assert result['result_node_id'] not in {n['id'] for n in canvas['nodes']}
    assert WITHDRAWN_REFS not in canvas
    history, = canvas['history']
    assert history[AUTO_ARCHIVED_REFS] == history['request_payload']['reference_asset_ids'] == [project.asset]
    assert Path(reopened.media_record(project.pid, project.asset)['path']).is_file()
    assert historical_image_retry_hint(canvas, task_id) == REATTACH_HINT
    forbidden = Mock(side_effect=AssertionError('retry reached quota, admission or billing'))
    monkeypatch.setattr(agent, 'enforce_generation_limit', forbidden)
    monkeypatch.setattr(library, 'reserve_project_image_task', forbidden)
    monkeypatch.setattr(main, 'load_api_providers', forbidden)
    monkeypatch.setattr(agent, 'request_confirmation_core', forbidden)
    project.calls.reset_mock()
    before = disk(project)
    response = retry(project, task_id)
    assert response.status_code == 409, response.text
    assert response.json()['detail'] == REATTACH_HINT
    response = TestClient(main.app, base_url='http://localhost').post(
        f'/api/projects/{project.pid}/generate/retry?agent_node_id=actor', json={'task_id': task_id},
        headers={'origin': 'http://localhost', 'X-Agent-Project': project.pid},
    )
    assert response.status_code == 409 and response.json()['detail']['hint'] == REATTACH_HINT
    forbidden.assert_not_called()
    project.calls.assert_not_called()
    assert disk(project) == before
    reopened.close()


@pytest.mark.parametrize('restore', ['version', 'backup', 'package'])
def test_withdrawal_survives_supported_restore(project, tmp_path, restore):
    result = asyncio.run(generate(project))
    previous = project.store.capture_project_version(project.pid, project.store.canvas(project.pid), label='before undo')
    assert undo_ops(project.store, project.pid, op_id=result['op_id'])['status'] == 'reverted'
    store, pid = project.store, project.pid
    if restore == 'version':
        store.restore_project_version(pid, previous['id'])
    elif restore == 'backup':
        store.restore_from_backup(pid, 'canvas.json', 'canvas.json.bak')
    else:
        target = tmp_path / 'package.fly'
        store.export_fly_to(pid, target)
        pid = store.import_fly(target)['id']
    canvas = store.canvas(pid)
    assert canvas[WITHDRAWN_REFS] == {result['task_id']: [project.asset]}
    assert canvas['history'][0][WITHDRAWN_HISTORY_REFS] == [project.asset]
