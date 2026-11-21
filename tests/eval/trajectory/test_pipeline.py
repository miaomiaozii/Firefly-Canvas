"""Actual tools + plan/confirmation/jobs/media + HTTP seam. Host is OFFLINE, not B2 live evidence."""
import asyncio
from io import BytesIO
import os
import json
from pathlib import Path
import shutil
import time
from unittest.mock import AsyncMock
from zipfile import ZipFile

import pytest

from tests.eval.trajectory.safety import REPO, overlaps
from tests.eval.trajectory.stub import Journal, MODELS, ProviderFence, providers, tiny_png


def workbook():
    from excel_importer import NS_MAIN, NS_PKG_REL, NS_REL
    buffer = BytesIO()
    with ZipFile(buffer, 'w') as archive:
        archive.writestr('xl/workbook.xml', f'<workbook xmlns="{NS_MAIN}" xmlns:r="{NS_REL}"><sheets>'
                         '<sheet name="图片方案" r:id="rId1"/></sheets></workbook>')
        archive.writestr('xl/_rels/workbook.xml.rels', f'<Relationships xmlns="{NS_PKG_REL}">'
                         '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
        rows = []
        for index, cells in enumerate([['名称', '设计需求', '文案'], ['主图1', '白底产品摄影，无字底图', 'Overlay only']], 1):
            rows.append(f'<row r="{index}">' + ''.join(
                f'<c r="{chr(65+column)}{index}" t="inlineStr"><is><t>{cell}</t></is></c>'
                for column, cell in enumerate(cells)) + '</row>')
        archive.writestr('xl/worksheets/sheet1.xml', f'<worksheet xmlns="{NS_MAIN}"><sheetData>' + ''.join(rows) + '</sheetData></worksheet>')
    return buffer.getvalue()


@pytest.fixture
def application(tmp_path, monkeypatch):
    roots = [Path(os.environ[key]).resolve() for key in ('FIREFLY_RUNTIME_ROOT', 'FIREFLY_PROJECTS_ROOT')]
    assert not overlaps(*roots) and not any(overlaps(root, REPO) for root in roots)
    previous_policy = asyncio.get_event_loop_policy()
    asyncio.set_event_loop_policy(asyncio.DefaultEventLoopPolicy())
    import main
    from app import core
    from app.project_sessions import register_project_session, unregister_project_session
    from app.routers import api_agent_nodes, api_agent_tools
    from app.services import agent_host, agent_plan, agent_batch
    from tests.eval.trajectory.backend import ToolObserver
    from fastapi.testclient import TestClient
    from firefly_projects import ProjectStore
    store = ProjectStore(roots[1] / f'e2e-tabbit-405-{tmp_path.name}', snapshot_delay=0)
    manager = agent_host.AgentHostManager()

    async def offline_spawn(session, **kwargs):
        session.status = 'running'
        return True

    monkeypatch.setattr(main, 'FIREFLY_PROJECT_STORE', store)
    monkeypatch.setattr(main, 'load_api_providers', lambda **_: providers())
    monkeypatch.setattr(main, 'GLOBAL_LOOP', None)
    monkeypatch.setattr(core, 'GLOBAL_LOOP', None)
    monkeypatch.setattr(agent_host, 'agent_host_manager', manager)
    monkeypatch.setattr(agent_host, '_app_main', main)
    monkeypatch.setattr(api_agent_nodes, 'agent_host_manager', manager)
    monkeypatch.setattr(manager, 'spawn_session', offline_spawn)
    monkeypatch.setattr(manager, 'send_command', AsyncMock())
    monkeypatch.setenv('FIREFLY_AGENT_API_BASE', 'http://127.0.0.1:3194')
    monkeypatch.setenv('API_PROVIDER_TRAJECTORY_STUB_KEY', 'inert-not-a-provider-key')
    pid = store.create_project('e2e-tabbit-405-synthetic-plan')['id']
    store.apply_mutations(pid, [{'op': 'upsert_node', 'node': {'id': 'actor', 'type': 'smart-agent', 'autonomy': 'autonomous'}}], base_revision=0)
    asset = store.import_media(pid, tiny_png(), 'explicit-product.png', 'image/png')['asset_id']
    token = object()
    register_project_session(token, pid)
    journal = Journal()
    confirmations = []
    try:
        observed = ToolObserver(main.app, journal=journal, store=store, project=pid)
        with TestClient(observed, base_url='http://127.0.0.1:3194') as client:
            async def subscribe():
                session = await manager.get_or_create_session(pid, 'actor')
                session.autonomy = 'autonomous'

                async def approve(_, event):
                    if event['type'] == 'confirm':
                        confirmations.append(event)
                        for handler in list(session.confirm_handlers):
                            await handler(event['confirm_id'], True)
                session.event_handlers.append(approve)
            client.portal.call(subscribe)
            try:
                yield client, store, manager, pid, asset, journal, confirmations
            finally:
                client.portal.call(agent_batch.shutdown, client.portal.call(asyncio.get_running_loop))
                session = client.portal.call(manager.get_or_create_session, pid, 'actor')
                client.portal.call(manager._dispatch_event, session, {'type': 'agent_end'})
                client.portal.call(manager.stop_session, session)
    finally:
        api_agent_tools.reset_generation_counts(pid)
        unregister_project_session(token)
        with agent_plan._LOCK:
            agent_plan._CACHE.pop(store, None)
        store.close()
        shutil.rmtree(store.root)
        asyncio.set_event_loop_policy(previous_policy)
        assert not store.root.exists()


@pytest.mark.parametrize('scenario,expected_attempts,code,expected_status', [
    ('normal', 1, None, 'succeeded'), ('rate_limit', 2, 'RATE_LIMITED', 'succeeded'),
    ('no_channel', 2, 'MODEL_UNAVAILABLE', 'succeeded'), ('size_mismatch', 2, 'INVALID_PARAMS', 'succeeded'),
    ('transient', 2, 'TRANSIENT', 'succeeded'), ('quota_exhausted', 1, 'QUOTA_EXHAUSTED', 'paused'),
    ('content_refusal', 1, 'CONTENT_REJECTED', 'failed')])
def test_real_plan_tools_batch_provider_adapter_and_media_pipeline(application, scenario, expected_attempts, code, expected_status):
    import main
    from app.services import generation
    client, store, manager, pid, asset, journal, confirmations = application
    assert main.generate_ai_image is generation.generate_ai_image
    response = client.post(f'/api/projects/{pid}/agent-nodes/actor/attachments', content=workbook(),
                           headers={'X-Attachment-Name': 'synthetic.xlsx'})
    assert response.status_code == 200, response.text
    attachment = response.json()
    response = client.post(f'/api/projects/{pid}/agent-nodes/actor/prompt', json={
        'message': 'Synthetic tool test; NOT an actual native model trajectory.', 'attachments': [attachment['id']]})
    assert response.status_code == 200, response.text

    def tool(name, args):
        response = client.post(f'/api/mcp?project={pid}&node=actor', headers={'Mcp-Session-Id': 'offline-405'}, json={
            'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': name, 'arguments': args}})
        assert response.status_code == 200, response.text
        result = response.json()['result']
        assert not result['isError'], result
        return json.loads(result['content'][0]['text'])

    draft = tool('plan.import_excel', {'attachment_id': attachment['id']})
    assert len(draft['preview']['tasks']) == 1 and not draft['preview']['tasks'][0]['selected']
    assert not store.list_media(pid)[-1].get('source_attachment_id')
    edited = tool('plan.edit_preview', {'plan_id': draft['plan_id'], 'generation': draft['generation'],
                                     'ops': [{'task_id': draft['preview']['tasks'][0]['task_id'], 'select': True}]})
    applied = tool('plan.apply', {'plan_id': draft['plan_id'], 'generation': edited['generation']})
    assert len(confirmations) == 1 and confirmations[0]['tool'] == 'plan.apply'
    assert applied['tasks'][0]['ref_nodes'] == []
    node = applied['tasks'][0]['gen_node']
    tool('canvas.attach_refs', {'node_id': node, 'refs': [{'source': {'asset_id': asset}, 'role': 'product'}], 'mode': 'replace'})
    tool('canvas.update_node', {'node_id': node, 'runSettings': {'model': MODELS[0], 'provider_id': 'trajectory-stub',
                                                              'routingMode': 'manual', 'aspect_ratio': '16:9', 'count': 1}})
    with ProviderFence(scenario, journal=journal):
        preview = tool('generate.preflight', {'node_ids': [node]})
        assert preview['blockers'] == []
        receipt = tool('generate.request_batch', {'preflight_id': preview['preflight_id'],
                                                'fallback_models': [MODELS[1]], 'max_retries': 2})
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            final = tool('generate.batch_status', {'batch_id': receipt['batch_id'], 'wait_seconds': .1})
            if final['status'] in {'done', 'paused', 'stopped'}:
                break
        else:
            pytest.fail('Real generation jobs failed to settle under stub')
    row, = final['items']
    if row['status'] != expected_status:
        pytest.fail(json.dumps({'row': row, 'seam': journal.rows, 'history': store.canvas(pid)['history']}, ensure_ascii=False))
    assert row['attempts'] == expected_attempts
    if code:
        failure = row['last_failure'] if expected_status == 'succeeded' else row['failure']
        assert failure['code'] == code
    assert len([r for r in journal.rows if r['kind'] == 'stub_request']) == expected_attempts
    if expected_status == 'succeeded':
        assert store.find_canvas_node(pid, row['result_node_id'])['images']
    else:
        assert store.find_canvas_node(pid, row['result_node_id']) is None
        assert store.find_canvas_node(pid, node)['lastFailure']['code'] == code
    assert [r['tool'] for r in journal.rows if r['kind'] == 'tool_start'][:3] == [
        'plan.import_excel', 'plan.edit_preview', 'plan.apply']
    from PIL import Image
    for item in store.list_media(pid):
        assert item['filename'].endswith('.png')
        with Image.open(store.root / pid / 'media' / item['filename']) as image:
            image.load()
            assert image.size == (8, 8)
    assert len(confirmations) == 2 and confirmations[1]['tool'] == 'generate.request_batch'
