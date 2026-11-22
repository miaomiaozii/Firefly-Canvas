"""Real 31xx human apply/approval/receipt/undo; no browser or native/model claim."""
import asyncio
from copy import deepcopy
import json

import httpx
import pytest
import uvicorn

import main
from app.routers import api_agent_tools as tools
from tests.test_agent_plan_399 import env, submit, upload, workbook
from tests.test_agent_plan_apply_484 import no_agent_delivery
from tests.test_agent_plan_preview_loopback_482 import _plan_preview_loopback_socket


@pytest.mark.parametrize('late', [False, True], ids=['within-window', 'after-pending'])
def test_external_original_sid_human_apply_and_undo_over_real_loopback(env, request, monkeypatch, late):
    client, store, host, (project, foreign) = env
    source = upload(client, project, 'real-http-apply.xlsx', workbook(''))
    submit(client, project, source)
    session = host.conversation_session(project, 'actor')
    spies = no_agent_delivery(monkeypatch, host)
    before = {pid: store.canvas(pid) for pid in (project, foreign)}
    monkeypatch.setattr(tools, 'AGENT_WRITE_CONFIRM_TIMEOUT_SECONDS', 0 if late else 90)

    async def scenario():
        events = asyncio.Queue()
        lifetime = []
        normal_track = tools._track_lifetime_task

        def track(task):
            lifetime.append(task)
            return normal_track(task)

        monkeypatch.setattr(tools, '_track_lifetime_task', track)

        async def observe(_session, event):
            if event['type'] == 'confirm' and event['tool'] == 'plan.apply':
                events.put_nowait(deepcopy(event))

        session.event_handlers.append(observe)
        sock = _plan_preview_loopback_socket()
        port = sock.getsockname()[1]
        origin = f'http://127.0.0.1:{port}'
        request.node.user_properties.append(('loopback_port', port))
        request.node.user_properties.append(('limits', 'real HTTP; fixture-owned lifespan; offline host I/O; no browser/model'))
        # The env TestClient owns application startup/lifespan; this server owns only its socket.
        server = uvicorn.Server(uvicorn.Config(main.app, host='127.0.0.1', port=port,
            loop='asyncio', lifespan='off', access_log=False, log_level='error', timeout_graceful_shutdown=3))
        serving = asyncio.create_task(server.serve(sockets=[sock]))
        execution = None
        pending = None
        confirm_id = None
        try:
            async def ready():
                while not server.started:
                    if serving.done():
                        await serving
                        pytest.fail('Owned #484 HTTP server exited before startup')
                    await asyncio.sleep(0.01)
            await asyncio.wait_for(ready(), timeout=10)
            async with httpx.AsyncClient(base_url=origin, trust_env=False, timeout=20) as http:
                headers = {'Origin': origin, 'X-Agent-Project': project}
                params = {'agent_node_id': 'actor'}

                async def mcp(name, args, *, sid='original-external-484-loopback'):
                    response = await http.post('/api/mcp',
                        params={'project': project, 'node': 'actor', 'caller': 'external'},
                        headers={'Mcp-Session-Id': sid}, json={'jsonrpc': '2.0', 'id': 484,
                            'method': 'tools/call', 'params': {'name': name, 'arguments': args}})
                    assert response.status_code == 200, response.text
                    return response.json()['result']

                imported = await mcp('plan.import_excel', {'attachment_id': source['id']})
                assert not imported['isError'], imported
                draft = json.loads(imported['content'][0]['text'])
                path = f'/api/projects/{project}/plan/previews/{draft["plan_id"]}'
                first = draft['preview']['tasks'][0]['task_id']
                edited = await http.post(path + '/edits', params=params, headers=headers,
                    json={'generation': 1, 'ops': [{'task_id': first, 'select': True}]})
                assert edited.status_code == 200 and edited.json()['generation'] == 2, edited.text
                execution = asyncio.create_task(http.post(path + '/apply', params=params, headers=headers,
                                                          json={'generation': 2}))
                event = await asyncio.wait_for(events.get(), timeout=10)
                confirm_id = event['confirm_id']
                assert [task['task_id'] for task in event['params']['tasks']] == [first]
                if late:
                    pending = await execution
                    assert pending.status_code == 200 and pending.json()['code'] == 'CONFIRM_PENDING', pending.text
                    assert pending.json()['confirm_id'] == confirm_id
                waiting = await http.get(path, params=params, headers=headers)
                assert waiting.status_code == 200, waiting.text
                assert waiting.json()['application'] == {'state': 'awaiting_confirmation', 'generation': 2,
                                                         'receipt': None, 'confirm_id': confirm_id}
                assert {pid: store.canvas(pid) for pid in (project, foreign)} == before
                competitor = await http.post(path + '/apply', params=params, headers=headers, json={'generation': 2})
                assert competitor.status_code == 409 and competitor.json()['detail']['code'] == 'CONFLICT', competitor.text
                approved = await http.post(f'/api/projects/{project}/agent-nodes/actor/confirm', headers=headers,
                                           json={'confirm_id': confirm_id, 'approved': True})
                assert approved.status_code == 200 and approved.json()['approved'] is True, approved.text
                response = await execution
                if not late:
                    assert response.status_code == 200 and response.json()['application']['state'] == 'applied', response.text
                outcomes = await asyncio.wait_for(asyncio.gather(*list(lifetime), return_exceptions=True), timeout=10)
                rejected = [outcome for outcome in outcomes if isinstance(outcome, BaseException)]
                assert len(rejected) == 1
                assert getattr(rejected[0], 'status_code', None) == 409
                assert rejected[0].detail['code'] == 'CONFLICT'
                latest = await http.get(path, params=params, headers=headers)
                assert latest.status_code == 200, latest.text
                applied = latest.json()
                assert applied['application']['state'] == 'applied'
                receipt = applied['application']['receipt']
                assert receipt['generation'] == 2 and receipt['applied_by'] == 'human'
                result = receipt['result']
                assert [task['task_id'] for task in result['tasks']] == [first]
                assert result['op_id'] and result['touched_node_ids']
                assert store.canvas(project)['revision'] == before[project]['revision'] + 1
                disk = json.loads((store.root / project / 'canvas.json').read_text(encoding='utf-8'))
                assert disk['nodes'] == store.canvas(project)['nodes'] and disk['revision'] == store.canvas(project)['revision']
                queried = await mcp('plan.import_excel', {'attachment_id': source['id']})
                assert not queried['isError'] and json.loads(queried['content'][0]['text']) == applied, queried
                duplicate = await mcp('plan.apply', {'plan_id': draft['plan_id'], 'generation': 2})
                assert duplicate['isError'] and duplicate['structuredContent']['code'] == 'PLAN_ALREADY_APPLIED', duplicate
                assert events.empty() and not session.confirm_handlers
                for spy in spies:
                    spy.assert_not_awaited()
                undo = await http.post(f'/api/projects/{project}/ops/undo', headers=headers, json={'op_id': result['op_id']})
                assert undo.status_code == 200 and undo.json()['reverted'] == [result['op_id']], undo.text
                assert store.canvas(project)['nodes'] == before[project]['nodes']
                assert store.canvas(project)['connections'] == before[project]['connections']
                assert store.canvas(foreign) == before[foreign] and store.list_media(project) == []
                denied = await http.post(path + '/apply', params=params, headers=headers, json={'generation': 2})
                assert denied.status_code == 409 and denied.json()['detail']['code'] == 'PLAN_ALREADY_APPLIED', denied.text
                request.node.user_properties.append(('receipt', json.dumps({'origin': origin, 'late': late,
                    'confirm_id': confirm_id, 'op_id': result['op_id'], 'generation': 2,
                    'applied_by': receipt['applied_by'], 'reverted': undo.json()['reverted']})))
        finally:
            if execution is not None and not execution.done():
                execution.cancel()
                await asyncio.gather(execution, return_exceptions=True)
            # Do not let a failed assertion leave a detached approval waiter alive.
            for task in lifetime:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*lifetime, return_exceptions=True)
            session.event_handlers.remove(observe)
            server.should_exit = True
            try:
                await asyncio.wait_for(asyncio.shield(serving), timeout=10)
            except asyncio.TimeoutError:
                serving.cancel()
                await asyncio.gather(serving, return_exceptions=True)
                pytest.fail('Owned #484 HTTP server did not shut down')
            finally:
                sock.close()
        assert serving.done() and not serving.cancelled()

    asyncio.run(scenario())
