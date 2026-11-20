"""#468: real browser request composition and Agent generation consume the same prompt."""
import base64
import io
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
from PIL import Image
import pytest

import main
from app.project_sessions import register_project_session, unregister_project_session
from app.routers import api_agent_tools as agent, api_project_library as library
from firefly_projects import ProjectStore
from tests.test_agent_generation_settings_396 import browser_payload, rest, wait


@pytest.fixture
def project(tmp_path, monkeypatch):
    store = ProjectStore(tmp_path / 'projects', snapshot_delay=0)
    monkeypatch.setattr(main, 'FIREFLY_PROJECT_STORE', store)
    providers = [{'id':'mock', 'name':'Mock', 'protocol':'openai', 'enabled':True,
                  'base_url':'https://example.invalid/v1', 'image_models':['picked']}]
    monkeypatch.setattr(main, 'load_api_providers', lambda: providers)
    monkeypatch.setattr(agent, 'request_confirmation_core', AsyncMock(return_value={'approved':True}))
    monkeypatch.setattr(main.manager, 'broadcast_canvas_updated', AsyncMock())
    with io.BytesIO() as output:
        Image.new('RGB', (4, 4), 'green').save(output, 'PNG')
        png = output.getvalue()
    calls = AsyncMock(return_value=({'type':'b64', 'value':base64.b64encode(png).decode(), 'mime_type':'image/png'}, {}))
    monkeypatch.setattr(library, 'run_routed_image_candidate', calls)
    pid = store.create_project('Synthetic #468')['id']
    asset = store.import_media(pid, png, 'reference.png', 'image/png')['asset_id']
    settings = {'engine':'api', 'apiKind':'image', 'provider_id':'mock', 'model':'picked',
                'routingMode':'manual', 'ratio':'square', 'resolution':'1k', 'count':'1'}
    nodes = [{'id':'source', 'type':'smart-image', 'runSettings':settings},
             {'id':'prompt', 'type':'smart-prompt', 'text':'Connected instruction'},
             {'id':'actor', 'type':'smart-agent'}, {'id':'browser-result', 'type':'smart-image'}]
    store.apply_mutations(pid, [{'op':'upsert_node', 'node':node} for node in nodes], base_revision=0)
    token = object()
    register_project_session(token, pid)
    try:
        with TestClient(main.app, base_url='http://localhost', headers={'origin':'http://localhost'}) as client:
            yield client, store, pid, calls, providers, asset
    finally:
        unregister_project_session(token)
        agent.reset_generation_counts(pid)
        with main.CANVAS_TASK_LOCK:
            for task, record in list(main.CANVAS_TASKS.items()):
                if record.get('project_id') == pid:
                    main.CANVAS_TASKS.pop(task, None)
        store.close()


def arrange(project, *, kind='flow', connected=True, text='Connected instruction', own='', mention=False):
    client, store, pid, calls, providers, asset = project
    node = store.find_canvas_node(pid, 'source')
    node['promptDraftHtml'] = (own + f'<span class="mention-image-token" data-name="Reference" '
                               f'data-url="/api/project-library/{pid}/media/{asset}"></span>') if mention else own
    prompt = store.find_canvas_node(pid, 'prompt')
    prompt['text'] = text
    ops = [{'op':'upsert_node', 'node':node}, {'op':'upsert_node', 'node':prompt}]
    if connected:
        ops.append({'op':'upsert_edge', 'edge':{'id':'prompt-edge', 'from':'prompt', 'to':'source', 'kind':kind}})
    store.apply_mutations(pid, ops, base_revision=store.canvas(pid)['revision'])
    store.flush(pid)
    return browser_payload(client, pid, {'project':pid, 'canvas':store.canvas(pid), 'providers':providers})


def assert_agent_prompt(project, expected, refs=(), *, executed=None):
    client, store, pid, calls, providers, asset = project
    response = rest(client, pid, 'generate/trigger', {'node_id':'source'})
    assert response.status_code == 200, response.text
    task_id = response.json()['task_id']
    wait(client, f'/api/projects/{pid}/generate/tasks/{task_id}')
    task = client.get(f'/api/project-library/{pid}/image-generation-tasks/{task_id}').json()
    assert task['request_payload']['prompt'] == expected
    assert task['request_payload']['reference_asset_ids'] == list(refs)
    calls.assert_awaited_once()
    assert calls.await_args.args[2] == (expected if executed is None else executed)


@pytest.mark.parametrize('kind', ['input', 'flow'])
@pytest.mark.parametrize('own', ['', 'Discard own instruction'])
def test_browser_request_and_agent_generation_use_connected_prompt(project, kind, own):
    payload, resolution = arrange(project, kind=kind, own=own)
    assert payload['prompt'] == resolution['prompt'] == 'Connected instruction'
    assert resolution['displayPrompt'] == 'Connected instruction'
    assert_agent_prompt(project, 'Connected instruction')


@pytest.mark.parametrize('own', ['', 'Discard own instruction'])
def test_agent_trigger_independently_consumes_flow_prompt(project, own):
    arrange(project, kind='flow', own=own)
    assert_agent_prompt(project, 'Connected instruction')


@pytest.mark.parametrize('connected', [False, True])
@pytest.mark.parametrize('own', ['', 'Own fallback'])
def test_empty_connected_prompt_keeps_existing_own_fallback_or_rejects_empty(project, connected, own):
    payload, resolution = arrange(project, connected=connected, text=' \n ', own=own)
    assert payload['prompt'] == resolution['prompt'] == own
    if own:
        assert_agent_prompt(project, own)
    else:
        client, store, pid, calls, providers, asset = project
        response = rest(client, pid, 'generate/trigger', {'node_id':'source'})
        assert response.status_code == 400, response.text
        assert response.json()['detail']['code'] == 'NODE_NOT_GENERATABLE'
        calls.assert_not_called()


def test_reference_map_survives_connected_prompt_replacement_in_browser_and_agent(project):
    payload, resolution = arrange(project, own='Discard own ', mention=True)
    expected = '下面是参考图编号：\n图1：Reference\n\n用户需求：\nConnected instruction'
    assert payload['prompt'] == resolution['prompt'] == expected
    assert resolution['displayPrompt'] == 'Connected instruction'
    asset = project[-1]
    assert payload['reference_asset_ids'] == [asset]
    assert_agent_prompt(project, expected, [asset])


def test_managed_reference_role_prefix_survives_connected_prompt_replacement(project):
    client, store, pid, calls, providers, asset = project
    store.apply_mutations(pid, [
        {'op':'upsert_node', 'node':{'id':'ref', 'type':'smart-image', 'images':[{'asset_id':asset}]}},
        {'op':'upsert_edge', 'edge':{'id':'ref-edge', 'from':'ref', 'to':'source', 'kind':'input',
                                   'agentRef':True, 'asset_id':asset, 'role':'product', 'enabled':True, 'order':1}},
    ], base_revision=store.canvas(pid)['revision'])
    payload, resolution = arrange(project, own='Discard own instruction')
    assert payload['prompt'] == resolution['prompt'] == 'Connected instruction'
    assert payload['reference_asset_ids'] == [asset]
    assert payload['reference_roles'] == ['product']
    assert_agent_prompt(project, 'Connected instruction', [asset],
                        executed='参考图说明：图1：产品主体。\n\nConnected instruction')
