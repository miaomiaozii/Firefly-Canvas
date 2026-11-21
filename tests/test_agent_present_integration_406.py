"""#406: real loopback REST, persisted events, TUI controls and input admission."""
import asyncio
import json
from pathlib import Path
import socket

import pytest
import uvicorn

import main
from app.services.agent_host import agent_host_manager as manager
from tests.test_agent_present_406 import env


ROOT = Path(__file__).resolve().parents[1]


def _loopback_socket():
    for port in range(3146, 3150):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            # Windows SO_REUSEADDR can steal another listener's port.
            if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            sock.bind(('127.0.0.1', port))
            sock.listen(128)
            sock.setblocking(False)
            return sock
        except OSError:
            sock.close()
    pytest.fail('No dedicated loopback port available in 3146..3149; existing listeners were left alone')


def test_real_present_events_tui_recommendation_and_queued_input(env, request):
    def evidence(name, value):
        request.node.user_properties.append((name, value))

    async def scenario():
        await env.start()
        sock = _loopback_socket()
        port = sock.getsockname()[1]
        evidence('loopback_port', port)
        evidence('boundaries', 'native process/pipe and browser DOM only; no model or browser engine')
        server = uvicorn.Server(uvicorn.Config(
            main.app, host='127.0.0.1', port=port, loop='asyncio', lifespan='off',
            access_log=False, log_level='error', timeout_graceful_shutdown=3,
        ))
        server_task = asyncio.create_task(server.serve(sockets=[sock]))
        node = None
        collected = None
        try:
            async def ready():
                while not server.started:
                    if server_task.done():
                        await server_task
                        pytest.fail('Loopback server exited before startup')
                    await asyncio.sleep(0.01)
            await asyncio.wait_for(ready(), timeout=10)
            before = env.store.canvas(env.project)
            pipe_before = env.session.process.stdin.getvalue()
            turn, generation = env.session._current_turn_id, env.session.generation
            assert turn and env.session.is_busy

            node = await asyncio.create_subprocess_exec(
                'node', str(ROOT / 'tests/agent-present-integration-406.mjs'),
                f'http://127.0.0.1:{port}', env.project,
                cwd=ROOT, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            collected = asyncio.create_task(node.communicate())
            try:
                stdout, stderr = await asyncio.wait_for(asyncio.shield(collected), timeout=45)
            except asyncio.TimeoutError:
                node.kill()
                stdout, stderr = await asyncio.wait_for(collected, timeout=5)
                pytest.fail(f'Node timed out on port {port}: {stdout.decode(errors="replace")}\n{stderr.decode(errors="replace")}')
            assert node.returncode == 0, (
                f'Node exit {node.returncode}, port {port}:\n'
                f'{stdout.decode(errors="replace")}\n{stderr.decode(errors="replace")}')
            result = json.loads(stdout)
            assert not stderr, stderr.decode(errors='replace')
            assert result['origin'] == f'http://127.0.0.1:{port}'

            journal = env.store.root / env.project / 'agent-events/actor.jsonl'
            records = [json.loads(line) for line in journal.read_text(encoding='utf-8').splitlines()]
            assert records == result['records'], 'Node must consume the actual persisted journal through REST'
            assert all(r['project_id'] == env.project and r['node_id'] == 'actor'
                       and r['host'] == 'pi' and r['generation'] == generation for r in records)
            assert [r['seq'] for r in records] == sorted({r['seq'] for r in records})
            cards = [r['event'] for r in records if r['event']['type'] in ('plan', 'question')]
            assert [e['type'] for e in cards] == ['plan', 'question', 'question']
            assert [e for e in env.live if e['type'] in ('plan', 'question')] == cards
            assert all(e['turn_id'] == turn and e['host'] == 'pi' and e['generation'] == generation for e in cards)
            assert cards[0]['presentation']['id'] == result['plan_id']
            pending, answered = cards[1]['presentation'], cards[2]['presentation']
            assert pending['id'] == answered['id'] == result['question_id']
            assert pending['state'] == 'pending' and answered['state'] == 'answered'
            expected_answers = {q['id']: q['recommended'] for q in pending['data']['questions']}
            assert answered == {**pending, 'state': 'answered', 'answers': expected_answers}
            receipt = result['receipt']
            assert receipt['presentation'] == answered
            request_id = 'presentation-' + pending['id']
            assert receipt['input'] == {'success': True, 'node_id': 'actor', 'input_type': 'prompt',
                                        'node_refs': [], 'request_id': request_id}
            expected_text = '\n'.join([pending['title'], *[
                q['text'] + ': ' + next(o['text'] for o in q['options'] if o['id'] == q['recommended'])
                for q in pending['data']['questions']]])
            queued = [r['event'] for r in records if r['event']['type'] == 'queued_input']
            assert len(queued) == 1 and queued == [e for e in env.live if e['type'] == 'queued_input']
            assert queued[0]['input_type'] == 'follow_up' and queued[0]['request_id'] == request_id
            assert queued[0]['text'] == expected_text
            assert len(env.session._follow_ups) == 1
            item, = env.session._follow_ups
            assert item['request_id'] == request_id and item['message'] == expected_text
            prompts = [r['event'] for r in records if r['event']['type'] == 'user_prompt']
            assert len(prompts) == 1 and prompts[0]['request_id'] == 'start' and prompts[0]['turn_id'] == turn
            assert env.session._current_turn_id == env.session._last_user_turn_id == turn
            assert not any(r['event']['type'] == 'system_continuation' for r in records)
            assert env.session.process.stdin.getvalue() == pipe_before, 'queued is not proof of native delivery'
            assert env.store.canvas(env.project) == before and not env.session.confirm_handlers
            assert len(manager.sessions) == 1
            evidence('node_receipt', json.dumps({k: v for k, v in result.items() if k != 'records'}))
        finally:
            try:
                if node is not None:
                    if node.returncode is None:
                        node.kill()
                    if collected is not None:
                        await asyncio.wait_for(asyncio.shield(collected), timeout=5)
            finally:
                server.should_exit = True
                try:
                    await asyncio.wait_for(asyncio.shield(server_task), timeout=10)
                except asyncio.TimeoutError:
                    server_task.cancel()
                    await asyncio.gather(server_task, return_exceptions=True)
                    pytest.fail('Owned loopback server failed to shut down')
                finally:
                    sock.close()
                    await env.client.aclose()
        assert server_task.done() and not server_task.cancelled()

    asyncio.run(scenario())
