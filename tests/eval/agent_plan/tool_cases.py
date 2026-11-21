"""Explicit real attachment -> conversation -> REST/MCP tool evaluation; not default collection."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil
from unittest.mock import AsyncMock

import pytest

from tests.eval.agent_plan.evaluator import evaluate, load_gold, write_report

PARAMETERS = [(gold['case'], variant['id'], 'REST') for gold in load_gold() for variant in gold['variants']]
PARAMETERS.append(('E0', 'original', 'MCP'))


@pytest.fixture
def transport_env(tmp_path, monkeypatch):
    from tools.copy_eval_fixtures import REPO, SOURCE_ROOT, overlaps

    roots = [Path(os.environ[key]).resolve() for key in ('FIREFLY_RUNTIME_ROOT', 'FIREFLY_PROJECTS_ROOT')]
    if any(overlaps(root, forbidden) for root in roots for forbidden in (REPO, SOURCE_ROOT)) or overlaps(*roots):
        pytest.fail('Use the isolated external run_pytest.py wrapper, not live runtime/projects roots')
    import main
    from app import core
    from app.project_sessions import register_project_session, unregister_project_session
    from app.routers import api_agent_nodes
    from app.services import agent_host, agent_plan
    from app.services.agent_host import AgentHostManager
    from fastapi.testclient import TestClient
    from firefly_projects import ProjectStore

    previous_policy = asyncio.get_event_loop_policy()
    asyncio.set_event_loop_policy(asyncio.DefaultEventLoopPolicy())
    store = ProjectStore(roots[1] / f'e2e-tabbit-404-{tmp_path.name}', snapshot_delay=0)
    host = AgentHostManager()

    async def spawn(session, **kwargs):
        session.status = 'running'
        return True

    monkeypatch.setattr(host, 'spawn_session', spawn)
    monkeypatch.setattr(host, 'send_command', AsyncMock())
    monkeypatch.setattr(main, 'FIREFLY_PROJECT_STORE', store)
    monkeypatch.setattr(agent_host, 'agent_host_manager', host)
    monkeypatch.setattr(api_agent_nodes, 'agent_host_manager', host)
    monkeypatch.setenv('FIREFLY_AGENT_API_BASE', 'http://127.0.0.1:3194')
    monkeypatch.setattr(main, 'GLOBAL_LOOP', None)
    monkeypatch.setattr(core, 'GLOBAL_LOOP', None)
    project = store.create_project('e2e-tabbit-404-literal-preview')['id']
    token = object()
    register_project_session(token, project)
    store.apply_mutations(project, [{'op': 'upsert_node', 'node': {
        'id': 'actor', 'type': 'smart-agent', 'autonomy': 'autonomous'}}], base_revision=0)
    store.flush(project)
    try:
        # TestClient owns the ASGI loop; no listening server or external model exists.
        with TestClient(main.app, base_url='http://127.0.0.1:3194', raise_server_exceptions=False) as client:
            yield client, store, host, project
    finally:
        unregister_project_session(token)
        with agent_plan._LOCK:
            agent_plan._CACHE.pop(store, None)
        store.close()
        shutil.rmtree(store.root)
        asyncio.set_event_loop_policy(previous_policy)
        assert not store.root.exists()


def projection(preview):
    return {key: value for key, value in preview.items() if key not in {'source', 'tasks'}} | {
        'tasks': [{key: value for key, value in task.items() if key not in {'task_id', 'linked_task_id'}}
                  for task in preview['tasks']]}


@pytest.mark.parametrize('case_id,variant_id,transport', PARAMETERS,
                         ids=[f'{case}-{variant}-{transport}' for case, variant, transport in PARAMETERS])
def test_import_excel(case_id, variant_id, transport, dataset, transport_env):
    client, store, host, project = transport_env
    gold = next(gold for gold in dataset['gold'] if gold['case'] == case_id)
    variant = next(variant for variant in gold['variants'] if variant['id'] == variant_id)
    content = (dataset['root'] / variant['fixture']).read_bytes()
    report = dataset['report']
    before = store.canvas(project)
    response_body, preview, error = None, None, None
    try:
        response = client.post(f'/api/projects/{project}/agent-nodes/actor/attachments', content=content,
                               headers={'X-Attachment-Name': f'{case_id}-{variant_id}.xlsx',
                                        'Content-Type': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'})
        assert response.status_code == 200, f'attachment API {response.status_code}: {response.text}'
        attachment = response.json()
        response = client.post(f'/api/projects/{project}/agent-nodes/actor/prompt', json={
            'message': '请逐行预览这份图片方案；不拆分，不挂图，不连线，不执行生成。',
            'attachments': [attachment['id']],
        })
        assert response.status_code == 200, f'conversation API {response.status_code}: {response.text}'
        arguments = {'attachment_id': attachment['id']}
        if transport == 'REST':
            response = client.post(f'/api/projects/{project}/plan/import_excel?agent_node_id=actor',
                                   headers={'X-Agent-Project': project}, json=arguments)
            response_body = response.json()
            assert response.status_code == 200, f'import REST {response.status_code}: {response.text}'
        else:
            response = client.post(f'/api/mcp?project={project}&node=actor', json={
                'jsonrpc': '2.0', 'id': 404, 'method': 'tools/call',
                'params': {'name': 'plan.import_excel', 'arguments': arguments},
            }, headers={'Mcp-Session-Id': f'e2e-tabbit-404-{project}'})
            envelope = response.json()
            response_body = envelope
            assert response.status_code == 200 and 'result' in envelope, envelope
            result = envelope['result']
            assert result.get('isError') is False, result
            response_body = json.loads(result['content'][0]['text'])
        preview = response_body['preview']
        assert store.canvas(project) == before, 'Import mutated the canvas'
        assert store.list_media(project) == [], 'Import implicitly archived a workbook/render image'
        host.send_command.assert_awaited()
    except Exception as exc:
        error = f'{type(exc).__name__}: {exc}'
    result = evaluate(gold, variant, preview, error=error) | {'transport': transport, 'project': project,
                                                           'transport_status': 'success' if error is None else 'error'}
    if transport == 'MCP' and preview is not None and error is None:
        rest = json.loads((report / 'E0-original-REST-actual.json').read_text(encoding='utf-8'))
        result['rest_mcp_parity'] = projection(preview) == projection(rest['response']['preview'])
    (report / f'{case_id}-{variant_id}-{transport}-actual.json').write_text(
        json.dumps({'response': response_body, 'evaluation': result}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    dataset['records'].append(result)
    write_report(report, dataset['records'], dataset['hashes'])
    assert error is None, f'Transport unavailable (NOT semantic pass): {error}'
    if transport == 'MCP':
        assert result['rest_mcp_parity'], 'REST/MCP literal-preview mismatch'
    if case_id == 'E0':
        failed = [item for item in result['criteria'] if not item['passed']]
        assert not failed, f'E0 gate FAILED; frozen gold is unchanged: {failed}'
