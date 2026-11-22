"""Real 31xx HTTP smoke; offline host I/O is not native/model or #411 visual proof."""
import asyncio
import json
import socket

import httpx
import pytest
import uvicorn

import main
from tests.test_agent_plan_399 import env, submit, upload, workbook


def _plan_preview_loopback_socket():
    for port in range(3190, 3200):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            sock.bind(('127.0.0.1', port))
            sock.listen(128)
            sock.setblocking(False)
            return sock
        except OSError:
            sock.close()
    pytest.fail('No owned 3190..3199 port available; existing listeners were left alone')


def test_plan_reference_and_human_cas_use_real_loopback_http(env, request):
    client, store, _, (project, _) = env
    source = upload(client, project, 'loopback.xlsx', workbook(''))
    submit(client, project, source)
    before = store.canvas(project)

    async def scenario():
        sock = _plan_preview_loopback_socket()
        port = sock.getsockname()[1]
        origin = f'http://127.0.0.1:{port}'
        request.node.user_properties.append(('loopback_port', port))
        request.node.user_properties.append(('limits', 'real HTTP; fixture-owned lifespan; offline native I/O; no browser/model'))
        # The TestClient fixture already owns startup/lifespan for this application.
        server = uvicorn.Server(uvicorn.Config(main.app, host='127.0.0.1', port=port,
            loop='asyncio', lifespan='off', access_log=False, log_level='error', timeout_graceful_shutdown=3))
        task = asyncio.create_task(server.serve(sockets=[sock]))
        try:
            async def ready():
                while not server.started:
                    if task.done():
                        await task
                        pytest.fail('Owned HTTP server exited before startup')
                    await asyncio.sleep(0.01)
            await asyncio.wait_for(ready(), timeout=10)
            async with httpx.AsyncClient(base_url=origin, trust_env=False, timeout=20) as http:
                async def mcp(name, args):
                    response = await http.post('/api/mcp', params={'project': project, 'node': 'actor', 'caller': 'external'},
                        headers={'Mcp-Session-Id': 'loopback-plan-482'}, json={
                            'jsonrpc': '2.0', 'id': 482, 'method': 'tools/call',
                            'params': {'name': name, 'arguments': args}})
                    assert response.status_code == 200, response.text
                    return response.json()['result']

                imported = await mcp('plan.import_excel', {'attachment_id': source['id']})
                assert not imported['isError'], imported
                draft = json.loads(imported['content'][0]['text'])
                path = f'/api/projects/{project}/plan/previews/{draft["plan_id"]}'
                params, headers = {'agent_node_id': 'actor'}, {'Origin': origin, 'X-Agent-Project': project}
                denied = await http.get(path, params=params, headers={'X-Agent-Project': project})
                assert denied.status_code == 403, denied.text
                read = await http.get(path, params=params, headers=headers)
                assert read.status_code == 200 and read.json() == draft, read.text
                first = draft['preview']['tasks'][0]['task_id']
                body = {'generation': 1, 'ops': [{'task_id': first, 'select': True}]}
                edited = await http.post(path + '/edits', params=params, headers=headers, json=body)
                assert edited.status_code == 200, edited.text
                latest = edited.json()
                assert latest['generation'] == 2 and latest['preview']['tasks'][0]['selected'] is True
                stale = await http.post(path + '/edits', params=params, headers=headers, json=body)
                assert stale.status_code == 409 and stale.json()['detail']['code'] == 'PLAN_STALE', stale.text
                get = await http.get(path, params={**params, 'generation': 2}, headers=headers)
                assert get.status_code == 200 and get.json() == latest, get.text
                args = {'action': 'show', 'kind': 'plan', 'title': 'Loopback plan', 'plan_id': draft['plan_id'], 'generation': 1}
                outdated = await mcp('agent.present', args)
                assert outdated['isError'] and outdated['structuredContent']['code'] == 'PLAN_STALE', outdated
                shown = await mcp('agent.present', {**args, 'generation': 2})
                assert not shown['isError'], shown
                presentation = json.loads(shown['content'][0]['text'])['presentation']
                assert presentation['data'] == {'source': 'plan_preview', 'plan_id': draft['plan_id'], 'generation': 2}
                replay = []
                for suffix in ('events', 'events/page'):
                    events = await http.get(f'/api/projects/{project}/agent-nodes/actor/{suffix}', params={'host': 'pi'})
                    assert events.status_code == 200, events.text
                    plans = [record['event'] for record in events.json()['events'] if record['event']['type'] == 'plan']
                    assert len(plans) == 1 and plans[0]['presentation'] == presentation
                    replay.append(plans)
                assert replay[0] == replay[1]
                invalid_apply = await http.post(path + '/apply', params=params, headers=headers,
                                                json={'generation': 2, 'approved': True})
                assert invalid_apply.status_code == 400, invalid_apply.text
                assert invalid_apply.json()['detail']['code'] == 'INVALID_ARGUMENT'
                stale_apply = await http.post(path + '/apply', params=params, headers=headers, json={'generation': 1})
                assert stale_apply.status_code == 409, stale_apply.text
                assert stale_apply.json()['detail']['code'] == 'PLAN_STALE'
                assert latest['apply_via'] == 'human_apply' and latest['agent_node_id'] == 'actor'
                assert latest['application'] == {'state': 'ready', 'generation': 2, 'receipt': None}
                assert store.canvas(project) == before and store.list_media(project) == []
                request.node.user_properties.append(('receipt', json.dumps({
                    'origin': origin, 'plan_id': draft['plan_id'], 'generation': 2, 'presentation_id': presentation['id'],
                    'same_origin_denial': denied.status_code, 'stale_cas': stale.status_code,
                    'human_apply_validation': invalid_apply.status_code,
                    'human_apply_stale': stale_apply.status_code, 'event_seq': replay[0][0]['seq']})))
        finally:
            server.should_exit = True
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=10)
            except asyncio.TimeoutError:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                pytest.fail('Owned #482 HTTP server did not shut down')
            finally:
                sock.close()
        assert task.done() and not task.cancelled()

    asyncio.run(scenario())
