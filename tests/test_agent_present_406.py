"""Shared presentation contract through in-process transports and real input/event storage."""
import asyncio
from copy import deepcopy
from io import StringIO
import json
from queue import Queue
from pathlib import Path
import re
import subprocess
import sys
from types import SimpleNamespace

import httpx
import pytest

import main
from app.constants import NODE_TYPE_AGENT
from app.project_sessions import register_project_session, unregister_project_session
from app.services.agent_host import agent_host_manager as manager
from firefly_projects import ProjectStore


@pytest.fixture(name='env')
def agent_present_env(tmp_path, monkeypatch):
    store = ProjectStore(tmp_path / 'projects', snapshot_delay=0)
    monkeypatch.setattr(main, 'FIREFLY_PROJECT_STORE', store)
    from app.routers import api_agent_tools, api_mcp
    monkeypatch.setattr(api_agent_tools, '_JOURNALS', {})
    monkeypatch.setattr(api_mcp, 'mcp_event_recorder', api_mcp.McpEventRecorder())
    for key, value in [('sessions', {}), ('_node_locks', {}), ('_lock', asyncio.Lock())]:
        monkeypatch.setattr(manager, key, value)
    project = store.create_project('406 synthetic')['id']
    store.apply_mutations(project, [{'op': 'upsert_node', 'node': {
        'id': 'actor', 'type': NODE_TYPE_AGENT, 'agentCli': 'pi', 'title': 'Agent'}},
        {'op': 'upsert_node', 'node': {'id': 'text', 'type': 'smart-prompt', 'text': 'before'}}], base_revision=0)
    store.flush(project)
    tab = object()
    register_project_session(tab, project)
    state = SimpleNamespace(store=store, project=project, live=[])

    async def start():
        session = await manager.get_or_create_session(project, 'actor')
        # Only the process/pipe boundary is replaced; input admission, turns and logs stay real.
        session.process = SimpleNamespace(stdin=StringIO(), poll=lambda: None)
        session.status = 'running'
        session.event_handlers.append(lambda _s, event: state.live.append(deepcopy(event)))
        state.session = session
        state.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://127.0.0.1:3146')
        await state.client.post(f'/api/projects/{project}/agent-nodes/actor/prompt', json={'message': 'start', 'request_id': 'start'})
        return state

    state.start = start
    try:
        yield state
    finally:
        unregister_project_session(tab)
        store.close()


async def present(env, args, **kwargs):
    return await env.client.post(f'/api/projects/{env.project}/agent/present?agent_node_id=actor',
                                 headers={'Content-Type': 'application/json', **kwargs.pop('headers', {'X-Agent-Project': env.project})},
                                 content=json.dumps(args), **kwargs)


async def agent_present_events(env, suffix='events'):
    response = await env.client.get(f'/api/projects/{env.project}/agent-nodes/actor/{suffix}', params={'host': 'pi'})
    assert response.status_code == 200, response.text
    return response.json()['events']


async def agent_present_mcp(env, args, *, caller='external', sid='external-406', node='actor', project=None, tool='agent.present', turn=None):
    response = await env.client.post('/api/mcp', params={'project': project or env.project, 'node': node, 'caller': caller,
                                                       **({'turn_id': turn} if turn is not None else {})},
        headers={'Mcp-Session-Id': sid, 'Content-Type': 'application/json'},
        content=json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
        'params': {'name': tool, 'arguments': args}}))
    assert response.status_code == 200, response.text
    result = response.json()['result']
    return result if result['isError'] else json.loads(result['content'][0]['text'])


def test_external_mcp_answers_are_only_read_by_original_binding_and_never_injected(env):
    async def scenario():
        await env.start()
        try:
            before = env.store.canvas(env.project)
            pipe_before = env.session.process.stdin.getvalue()
            result = await agent_present_mcp(env, QUESTION)
            assert 'presentation' in result, result
            p = result['presentation']
            assert p['state'] == 'pending'
            path = f'/api/projects/{env.project}/agent-nodes/actor/questions/{p["id"]}'
            answer = await env.client.post(path + '/answers', json={'answers': {'style': 'b'}})
            assert answer.status_code == 200, answer.text
            assert answer.json()['presentation']['answers'] == {'style': 'b'} and answer.json()['input'] is None
            read = await agent_present_mcp(env, {'action': 'get', 'presentation_id': p['id']})
            assert read['presentation'] == answer.json()['presentation']
            foreign = await agent_present_mcp(env, {'action': 'get', 'presentation_id': p['id']}, sid='other-session')
            assert foreign['isError']
            assert (await present(env, {'action': 'get', 'presentation_id': p['id']})).status_code == 409
            assert env.session.process.stdin.getvalue() == pipe_before
            assert env.store.canvas(env.project) == before and len(manager.sessions) == 1
            live = [e for e in env.live if e['type'] == 'question']
            assert len(live) == 2 and all(e['turn_id'] is None for e in live)
            assert [r['event'] for r in await agent_present_events(env) if r['event']['type'] == 'question'] == live
        finally:
            await env.client.aclose()
    asyncio.run(scenario())


QUESTION = {'action': 'show', 'kind': 'question', 'title': 'Choose once', 'questions': [
    {'id': 'style', 'text': 'Which style?', 'options': [{'id': 'a', 'text': 'Bright'}, {'id': 'b', 'text': 'Dark'}],
     'recommended': 'a'}]}


def test_pending_question_answer_queues_once_and_becomes_a_real_new_user_turn(env):
    async def scenario():
        await env.start()
        try:
            before = env.store.canvas(env.project)
            original = env.session._current_turn_id
            response = await present(env, QUESTION)
            assert response.status_code == 200, response.text
            p = response.json()['presentation']
            assert p['state'] == 'pending' and p['data'] == {'questions': QUESTION['questions']}
            path = f'/api/projects/{env.project}/agent-nodes/actor/questions/{p["id"]}'
            read = await env.client.get(path)
            assert read.json() == {'presentation': p, 'answerable': True}
            body = {'answers': {'style': 'a'}, 'request_id': 'human-answer'}
            answered = await env.client.post(path + '/answers', json=body)
            assert answered.status_code == 200, answered.text
            receipt = answered.json()
            assert receipt['presentation'] == {**p, 'state': 'answered', 'answers': {'style': 'a'}}
            assert receipt['input']['success'] and receipt['input']['request_id'] == 'human-answer'
            duplicate = await env.client.post(path + '/answers', json=body)
            assert duplicate.json() == receipt
            conflict = await env.client.post(path + '/answers', json={'answers': {'style': 'b'}})
            assert conflict.status_code == 409
            records = await agent_present_events(env)
            assert len([r for r in records if r['event']['type'] == 'queued_input']) == 1
            assert len([r for r in records if r['event']['type'] == 'user_prompt']) == 1
            live = [e for e in env.live if e['type'] == 'question']
            assert [e['presentation']['state'] for e in live] == ['pending', 'answered']
            assert all(e['turn_id'] == original for e in live) and live[1]['seq'] > live[0]['seq']
            assert [r['event'] for r in records if r['event']['type'] == 'question'] == live
            assert [r['event'] for r in await agent_present_events(env, 'events/page') if r['event']['type'] == 'question'] == live
            await manager._dispatch_event(env.session, {'type': 'agent_end'})
            prompts = [r['event'] for r in await agent_present_events(env) if r['event']['type'] == 'user_prompt']
            assert len(prompts) == 2 and prompts[1]['turn_id'] not in (None, original)
            assert prompts[1]['request_id'] == 'human-answer' and 'Bright' in prompts[1]['text']
            assert not any(r['event']['type'] == 'system_continuation' for r in await agent_present_events(env))
            read = await present(env, {'action': 'get', 'presentation_id': p['id']})
            assert read.json()['presentation']['answers'] == {'style': 'a'}
            assert env.store.canvas(env.project) == before and not env.session.confirm_handlers
        finally:
            await env.client.aclose()
    asyncio.run(scenario())


def test_diff_resolves_real_bound_preview_without_consuming_or_applying_it(env):
    async def scenario():
        await env.start()
        try:
            preview = await env.client.post(f'/api/projects/{env.project}/canvas/batch_prompt_edit?agent_node_id=actor',
                json={'apply': False, 'edits': [{'node_id': 'text', 'prompt': 'after'}]})
            assert preview.status_code == 200, preview.text
            diff_id = preview.json()['diff_id']
            before = env.store.canvas(env.project)
            response = await present(env, {'action': 'show', 'kind': 'diff', 'title': 'Actual preview', 'diff_id': diff_id})
            assert response.status_code == 200, response.text
            p = response.json()['presentation']
            assert p['state'] == 'presented'
            assert p['data'] == {'source': 'preview', 'diff_id': diff_id, 'items': [
                {'node_id': 'text', 'before': 'before', 'after': 'after', 'protected': False}], 'applied': False}
            again = await present(env, {'action': 'show', 'kind': 'diff', 'title': 'Still preview', 'diff_id': diff_id})
            assert again.status_code == 200
            assert env.store.canvas(env.project) == before and not env.session.confirm_handlers
            live = [e for e in env.live if e['type'] == 'diff']
            assert live[0]['presentation'] == p and live[0]['turn_id'] == env.session._current_turn_id
            assert [r['event'] for r in await agent_present_events(env) if r['event']['type'] == 'diff'] == live
            assert [r['event'] for r in await agent_present_events(env, 'events/page') if r['event']['type'] == 'diff'] == live
            assert (await present(env, {'action': 'show', 'kind': 'diff', 'title': 'Missing', 'diff_id': 'invented'})).status_code == 409
        finally:
            await env.client.aclose()
    asyncio.run(scenario())


def test_diff_resolves_real_operation_journal_and_rejects_another_agent_receipt(env):
    async def scenario():
        await env.start()
        try:
            created = await env.client.post(f'/api/projects/{env.project}/canvas/nodes',
                headers={'X-Agent-Project': env.project},
                json={'agent_node_id': 'actor', 'node': {'type': 'smart-prompt', 'text': 'journal text'}})
            assert created.status_code == 200, created.text
            op_id = created.json()['op_id']
            before = env.store.canvas(env.project)
            response = await present(env, {'action': 'show', 'kind': 'diff', 'title': 'Recorded change', 'op_id': op_id})
            assert response.status_code == 200, response.text
            data = response.json()['presentation']['data']
            assert data['source'] == 'operation' and data['op_id'] == op_id
            assert data['turn_id'] == env.session._current_turn_id and data['tool'] == 'canvas.create_node'
            assert data['undone'] is False and data['before'] != data['after']
            assert 'journal text' in json.dumps(data['after'])
            other = await agent_present_mcp(env, {'action': 'show', 'kind': 'diff', 'title': 'Not external ownership', 'op_id': op_id})
            assert other['isError']
            missing = await present(env, {'action': 'show', 'kind': 'diff', 'title': 'Unknown op', 'op_id': 'invented'})
            assert missing.status_code == 409
            assert env.store.canvas(env.project) == before
        finally:
            await env.client.aclose()
    asyncio.run(scenario())


def test_hosted_receipts_cannot_be_read_from_a_replaced_runtime(env):
    async def scenario():
        await env.start()
        try:
            result = await present(env, QUESTION)
            p = result.json()['presentation']
            env.session._generation += 1
            read = await present(env, {'action': 'get', 'presentation_id': p['id']})
            assert read.status_code == 409, read.text
            path = f'/api/projects/{env.project}/agent-nodes/actor/questions/{p["id"]}'
            status = await env.client.get(path)
            assert status.json()['presentation']['state'] == 'expired' and not status.json()['answerable']
            before = env.session.process.stdin.getvalue()
            answer = await env.client.post(path + '/answers', json={'answers': {'style': 'a'}})
            assert answer.status_code == 409 and env.session.process.stdin.getvalue() == before
        finally:
            await env.client.aclose()
    asyncio.run(scenario())


def test_idle_codex_answer_keeps_original_card_envelope_and_starts_a_new_real_turn(env, monkeypatch):
    from app.services import agent_host
    monkeypatch.setenv('FIREFLY_CODEX_WARMUP', 'off')
    processes = []

    class Pipe(StringIO):
        def close(self):
            pass

    class Output:
        def __init__(self):
            self.lines = Queue()

        def readline(self):
            return self.lines.get(timeout=10)

    class Process:
        def __init__(self, *args, **kwargs):
            self.stdin, self.stdout, self.stderr = Pipe(), Output(), StringIO()
            self.returncode = None
            processes.append(self)

        def poll(self):
            return self.returncode

        def wait(self):
            return 0

        def finish(self):
            self.returncode = 0
            self.stdout.lines.put('{"type":"turn.completed"}\n')
            self.stdout.lines.put('')

    original_which = agent_host.shutil.which
    monkeypatch.setattr(agent_host.shutil, 'which', lambda name: sys.executable if name == 'codex' else original_which(name))
    monkeypatch.setattr(agent_host.subprocess, 'Popen', Process)

    async def scenario():
        await env.start()
        try:
            await manager._dispatch_event(env.session, {'type': 'agent_end'})
            env.session.process = None
            node = next(n for n in env.store.canvas(env.project)['nodes'] if n['id'] == 'actor')
            env.store.apply_agent_host_mutations(env.project, [{'op': 'upsert_node', 'node': {**node, 'agentCli': 'codex'}}],
                                                 base_revision=env.store.canvas(env.project)['revision'])
            manager.select_runtime(env.session, 'codex')
            sent = await env.client.post(f'/api/projects/{env.project}/agent-nodes/actor/prompt', json={'message': 'codex question'})
            assert sent.status_code == 200, sent.text
            response = await agent_present_mcp(env, QUESTION, caller='hosted')
            assert 'presentation' in response, response
            p = response['presentation']
            original_turn, original_gen = env.session._current_turn_id, env.session.generation
            processes[0].finish()
            await env.session._reader_task
            path = f'/api/projects/{env.project}/agent-nodes/actor/questions/{p["id"]}/answers'
            answer = await env.client.post(path, json={'answers': {'style': 'a'}})
            assert answer.status_code == 200, answer.text
            assert answer.json()['input']['success'] and len(processes) == 2
            question_events = [e for e in env.live if e['type'] == 'question']
            assert len(question_events) == 2 and all(e['turn_id'] == original_turn and e['generation'] == original_gen
                                                   for e in question_events)
            assert env.session._current_turn_id not in (None, original_turn)
            assert 'Bright' in processes[1].stdin.getvalue()
        finally:
            for process in processes:
                if process.poll() is None:
                    process.finish()
            if env.session._reader_task:
                await env.session._reader_task
            await env.client.aclose()
    asyncio.run(scenario())


def test_answer_request_id_cannot_silently_alias_an_existing_user_input(env):
    async def scenario():
        await env.start()
        try:
            result = await present(env, QUESTION)
            p = result.json()['presentation']
            path = f'/api/projects/{env.project}/agent-nodes/actor/questions/{p["id"]}'
            response = await env.client.post(path + '/answers', json={'answers': {'style': 'a'}, 'request_id': 'start'})
            assert response.status_code == 409, response.text
            status = await env.client.get(path)
            assert status.json()['presentation']['state'] == 'pending' and status.json()['answerable']
            assert len([r for r in await agent_present_events(env) if r['event']['type'] == 'user_prompt']) == 1
        finally:
            await env.client.aclose()
    asyncio.run(scenario())


def test_external_diff_preview_keeps_original_mcp_binding_even_on_shared_node(env):
    async def scenario():
        await env.start()
        try:
            preview = await agent_present_mcp(env, {'apply': False, 'edits': [{'node_id': 'text', 'prompt': 'external edit'}]},
                                tool='canvas.batch_prompt_edit')
            assert 'diff_id' in preview, preview
            args = {'action': 'show', 'kind': 'diff', 'title': 'External preview', 'diff_id': preview['diff_id']}
            result = await agent_present_mcp(env, args)
            assert 'presentation' in result, result
            assert result['presentation']['data']['items'][0]['after'] == 'external edit'
            assert (await agent_present_mcp(env, args, sid='foreign'))['isError']
            assert (await present(env, args)).status_code == 409
            assert next(n for n in env.store.canvas(env.project)['nodes'] if n['id'] == 'text')['text'] == 'before'
        finally:
            await env.client.aclose()
    asyncio.run(scenario())


@pytest.mark.parametrize('args', [
    None, [], False, 1, 'show', {}, {'action': 'show'},
    {'action': 'get', 'presentation_id': 'missing', 'turn_id': 'forged'},
    {'action': 'get', 'presentation_id': 'x' * 129},
    {**QUESTION, 'host': 'codex'}, {**QUESTION, 'generation': 999}, {**QUESTION, 'seq': 99},
    {**QUESTION, 'project_id': 'foreign'}, {**QUESTION, 'agent_node_id': 'foreign'},
    {**QUESTION, 'title': 'x' * 201}, {**QUESTION, 'title': ' '}, {**QUESTION, 'title': '\ud800'},
    {**QUESTION, 'questions': []}, {**QUESTION, 'questions': QUESTION['questions'] * 2},
    {**QUESTION, 'questions': [{**QUESTION['questions'][0], 'recommended': 'invented'}]},
    {**QUESTION, 'questions': [{**QUESTION['questions'][0], 'options': [{'id': 'a', 'text': 'A'}] * 2}]},
    {'action': 'show', 'kind': 'plan', 'title': 'P', 'steps': [{'id': 'x', 'text': 'X'}] * 2},
    {'action': 'show', 'kind': 'plan', 'title': 'P', 'steps': [{'id': 'x', 'text': 'X'}] * 51},
    {'action': 'show', 'kind': 'plan', 'title': 'P', 'steps': [{'id': str(i), 'text': '界' * 2000} for i in range(12)]},
    {'action': 'show', 'kind': 'diff', 'title': 'D', 'diff_id': 'x', 'op_id': 'y'},
    {'action': 'show', 'kind': 'diff', 'title': 'D', 'before': 'fiction', 'after': 'fiction', 'applied': True},
])
def test_strict_bounded_arguments_are_identical_on_rest_and_mcp_without_effects(env, args):
    async def scenario():
        await env.start()
        try:
            before = env.store.canvas(env.project)
            records = await agent_present_events(env)
            rest = await present(env, args)
            external = await agent_present_mcp(env, args)
            assert rest.status_code == 400 and rest.json()['detail']['code'] == 'INVALID_ARGUMENT'
            assert external['isError'] and external['structuredContent'] == rest.json()['detail']
            assert env.store.canvas(env.project) == before and await agent_present_events(env) == records
        finally:
            await env.client.aclose()
    asyncio.run(scenario())


@pytest.mark.parametrize('body', [None, [], {}, {'answers': {}}, {'answers': {'style': 'missing'}},
    {'answers': {'style': 'a', 'extra': 'a'}}, {'answers': {'style': ['a']}},
    {'answers': {'style': 'a'}, 'host': 'pi'}, {'answers': {'style': 'a'}, 'request_id': 'x' * 129}])
def test_question_answers_require_the_exact_option_set_and_no_authority_overrides(env, body):
    async def scenario():
        await env.start()
        try:
            p = (await present(env, QUESTION)).json()['presentation']
            path = f'/api/projects/{env.project}/agent-nodes/actor/questions/{p["id"]}'
            response = await env.client.post(path + '/answers', json=body)
            assert response.status_code == 400, response.text
            assert (await env.client.get(path)).json() == {'presentation': p, 'answerable': True}
            assert not any(r['event']['type'] == 'queued_input' for r in await agent_present_events(env))
        finally:
            await env.client.aclose()
    asyncio.run(scenario())


def test_stop_expires_question_without_resuming_or_creating_a_host(env):
    async def scenario():
        await env.start()
        try:
            p = (await present(env, QUESTION)).json()['presentation']
            process = env.session.process
            process.returncode = None
            process.poll = lambda: process.returncode
            process.terminate = lambda: setattr(process, 'returncode', 0)
            process.wait = lambda: 0
            stopped = await env.client.delete(f'/api/projects/{env.project}/agent-nodes/actor')
            assert stopped.status_code == 200, stopped.text
            path = f'/api/projects/{env.project}/agent-nodes/actor/questions/{p["id"]}'
            assert (await env.client.get(path)).json()['presentation']['state'] == 'expired'
            rejected = await env.client.post(path + '/answers', json={'answers': {'style': 'a'}})
            assert rejected.status_code == 409 and env.session.process is None
            assert len(manager.sessions) == 1
            replayed = [r['event']['presentation'] for r in await agent_present_events(env) if r['event']['type'] == 'question']
            assert replayed == [p]
        finally:
            await env.client.aclose()
    asyncio.run(scenario())


def test_read_guards_and_missing_explicit_mcp_targets_never_create_carriers(env):
    async def scenario():
        await env.start()
        try:
            before = env.store.canvas(env.project)
            for headers, code in [({}, 400), ({'X-Agent-Project': 'foreign'}, 403),
                                  ({'X-Agent-Project': env.project, 'Origin': 'http://evil.example'}, 403)]:
                assert (await present(env, QUESTION, headers=headers)).status_code == code
            for node in ('', 'missing', 'text'):
                assert (await agent_present_mcp(env, QUESTION, node=node))['isError']
            assert (await agent_present_mcp(env, QUESTION, sid=''))['isError']
            assert env.store.canvas(env.project) == before and len(manager.sessions) == 1
            p = (await present(env, QUESTION)).json()['presentation']
            path = f'/api/projects/{env.project}/agent-nodes/actor/questions/{p["id"]}'
            for method, suffix in [('GET', ''), ('POST', '/answers')]:
                response = await env.client.request(method, path + suffix, headers={'Origin': 'http://evil.example'},
                                                    json={'answers': {'style': 'a'}} if method == 'POST' else None)
                assert response.status_code == 403
            assert (await env.client.get(path)).json()['answerable']
        finally:
            await env.client.aclose()
    asyncio.run(scenario())


def test_no_old_turn_guess_and_new_user_turn_expires_original_question(env):
    async def scenario():
        await env.start()
        try:
            p = (await present(env, QUESTION)).json()['presentation']
            original = env.session._current_turn_id
            await manager._dispatch_event(env.session, {'type': 'agent_end'})
            assert env.session._current_turn_id is None
            assert (await present(env, QUESTION)).status_code == 409
            sent = await env.client.post(f'/api/projects/{env.project}/agent-nodes/actor/prompt',
                                        json={'message': 'A different goal', 'request_id': 'different'})
            assert sent.status_code == 200
            assert env.session._current_turn_id not in (None, original)
            path = f'/api/projects/{env.project}/agent-nodes/actor/questions/{p["id"]}'
            status = (await env.client.get(path)).json()
            assert status['presentation']['state'] == 'expired' and not status['answerable']
            assert (await env.client.post(path + '/answers', json={'answers': {'style': 'a'}})).status_code == 409
        finally:
            await env.client.aclose()
    asyncio.run(scenario())


def test_external_mcp_with_no_runtime_does_not_spawn_and_get_never_creates_a_session(env):
    async def scenario():
        await env.start()
        try:
            await manager._dispatch_event(env.session, {'type': 'agent_end'})
            manager.sessions.pop(f'{env.project}:actor')
            before = env.store.canvas(env.project)
            assert (await agent_present_mcp(env, {'action': 'get', 'presentation_id': 'missing'}))['isError']
            assert not manager.sessions
            p = (await agent_present_mcp(env, QUESTION))['presentation']
            canonical = manager.conversation_session(env.project, 'actor')
            assert canonical is not None and canonical.process is None
            path = f'/api/projects/{env.project}/agent-nodes/actor/questions/{p["id"]}/answers'
            result = await env.client.post(path, json={'answers': {'style': 'a'}})
            assert result.status_code == 200 and result.json()['input'] is None
            assert (await agent_present_mcp(env, {'action': 'get', 'presentation_id': p['id']}))['presentation']['state'] == 'answered'
            assert canonical.process is None and env.store.canvas(env.project) == before
        finally:
            await env.client.aclose()
    asyncio.run(scenario())


def test_answer_pipe_failure_is_honest_and_never_retried_by_duplicate_post(env):
    class BrokenPipe:
        writes = 0

        def write(self, line):
            self.writes += 1
            raise OSError('synthetic pipe failure')

    async def scenario():
        await env.start()
        try:
            p = (await present(env, QUESTION)).json()['presentation']
            await manager._dispatch_event(env.session, {'type': 'agent_end'})
            pipe = BrokenPipe()
            env.session.process.stdin = pipe
            path = f'/api/projects/{env.project}/agent-nodes/actor/questions/{p["id"]}'
            for _ in range(2):
                failed = await env.client.post(path + '/answers', json={'answers': {'style': 'a'}})
                assert failed.status_code == 502, failed.text
            assert pipe.writes == 1
            status = (await env.client.get(path)).json()
            assert status['presentation']['state'] == 'answered' and not status['answerable']
            assert status['delivery_error']['code'] == 'UPSTREAM_MODEL_ERROR'
            questions = [r['event'] for r in await agent_present_events(env) if r['event']['type'] == 'question']
            assert questions[-1]['delivery_error'] == status['delivery_error']
            assert questions[-1]['presentation'] == status['presentation']
            assert [e for e in env.live if e['type'] == 'question'] == questions
            assert [r['event'] for r in await agent_present_events(env, 'events/page') if r['event']['type'] == 'question'] == questions
            assert len([r for r in await agent_present_events(env) if r['event']['type'] == 'user_prompt']) == 1
        finally:
            await env.client.aclose()
    asyncio.run(scenario())


def test_concurrent_same_answers_have_one_receipt_and_one_real_queue_entry(env):
    async def scenario():
        await env.start()
        try:
            p = (await present(env, QUESTION)).json()['presentation']
            path = f'/api/projects/{env.project}/agent-nodes/actor/questions/{p["id"]}/answers'
            responses = await asyncio.gather(*[env.client.post(path, json={'answers': {'style': 'a'}}) for _ in range(2)])
            assert all(r.status_code == 200 for r in responses) and responses[0].json() == responses[1].json()
            assert len([r for r in await agent_present_events(env) if r['event']['type'] == 'queued_input']) == 1
        finally:
            await env.client.aclose()
    asyncio.run(scenario())


def test_presentation_turn_is_captured_before_live_subscribers_advance_fifo(env):
    async def scenario():
        await env.start()
        try:
            original = env.session._current_turn_id
            await env.client.post(f'/api/projects/{env.project}/agent-nodes/actor/follow_up',
                                  json={'message': 'next user', 'request_id': 'next-user'})

            async def finish_on_card(session, event):
                if event['type'] == 'plan':
                    await manager._dispatch_event(session, {'type': 'agent_end'})

            env.session.event_handlers.append(finish_on_card)
            result = await present(env, {'action': 'show', 'kind': 'plan', 'title': 'Plan',
                                         'steps': [{'id': 'one', 'text': 'Inspect'}]})
            assert result.status_code == 200
            assert env.session._current_turn_id not in (None, original)
            saved = [r['event'] for r in await agent_present_events(env) if r['event']['type'] == 'plan']
            assert len(saved) == 1 and saved[0]['turn_id'] == original
            assert saved == [e for e in env.live if e['type'] == 'plan']
        finally:
            await env.client.aclose()
    asyncio.run(scenario())


def test_receipts_are_bounded_and_expire_without_replaying_inputs(env, monkeypatch):
    from app.services import agent_present
    now = agent_present.monotonic()
    monkeypatch.setattr(agent_present, 'monotonic', lambda: now)

    async def scenario():
        nonlocal now
        await env.start()
        try:
            first = last = None
            for i in range(257):
                result = await present(env, {'action': 'show', 'kind': 'plan', 'title': f'Plan {i}',
                                             'steps': [{'id': 'one', 'text': 'Inspect'}]})
                assert result.status_code == 200
                last = result.json()['presentation']['id']
                if first is None:
                    first = last
            assert (await present(env, {'action': 'get', 'presentation_id': first})).status_code == 409
            assert (await present(env, {'action': 'get', 'presentation_id': last})).status_code == 200
            now += 1801
            assert (await present(env, {'action': 'get', 'presentation_id': last})).status_code == 409
            assert len([r for r in await agent_present_events(env) if r['event']['type'] == 'user_prompt']) == 1
            assert len(manager.sessions) == 1
        finally:
            await env.client.aclose()
    asyncio.run(scenario())


def test_dead_pi_pipe_cannot_be_revived_by_answering_a_retained_question(env):
    async def scenario():
        await env.start()
        try:
            p = (await present(env, QUESTION)).json()['presentation']
            env.session.process.poll = lambda: 0
            path = f'/api/projects/{env.project}/agent-nodes/actor/questions/{p["id"]}'
            status = (await env.client.get(path)).json()
            assert status['presentation']['state'] == 'expired' and not status['answerable']
            answer = await env.client.post(path + '/answers', json={'answers': {'style': 'a'}})
            assert answer.status_code == 409
            assert env.session.process.poll() == 0
        finally:
            await env.client.aclose()
    asyncio.run(scenario())


def test_external_operation_diff_requires_real_original_mcp_turn_owner(env):
    async def scenario():
        await env.start()
        try:
            created = await agent_present_mcp(env, {'type': 'smart-prompt', 'text': 'External journal'},
                                tool='canvas.create_node', turn='external-original')
            assert 'op_id' in created, created
            args = {'action': 'show', 'kind': 'diff', 'title': 'Journal', 'op_id': created['op_id']}
            shown = await agent_present_mcp(env, args)
            assert 'presentation' in shown, shown
            data = shown['presentation']['data']
            assert data['source'] == 'operation' and data['turn_id'] == 'external-original'
            assert 'External journal' in json.dumps(data['after'])
            assert (await agent_present_mcp(env, args, sid='foreign-owner'))['isError']
            assert (await present(env, args)).status_code == 409
        finally:
            await env.client.aclose()
    asyncio.run(scenario())


def test_canonical_host_mismatch_is_not_authorized_by_hosted_caller_label(env):
    async def scenario():
        await env.start()
        try:
            node = next(n for n in env.store.canvas(env.project)['nodes'] if n['id'] == 'actor')
            env.store.apply_agent_host_mutations(env.project, [{'op': 'upsert_node', 'node': {**node, 'agentCli': 'codex'}}],
                                                 base_revision=env.store.canvas(env.project)['revision'])
            before = env.store.canvas(env.project)
            response = await agent_present_mcp(env, QUESTION, caller='hosted')
            assert response['isError'] and response['structuredContent']['code'] == 'CONFLICT'
            assert (await present(env, QUESTION)).status_code == 409
            assert env.session.agent_cli == 'pi' and env.store.canvas(env.project) == before
            assert not any(r['event']['type'] == 'question' for r in await agent_present_events(env))
        finally:
            await env.client.aclose()
    asyncio.run(scenario())


def test_generated_pi_registration_preserves_the_same_strict_capability_schema():
    from app.services.agent_present import SCHEMA
    from app.services.agent_tools_manifest import TOOLS
    root = Path(__file__).resolve().parents[1]
    generated = (root / 'app/agent_extensions/canvas-tools.generated.ts').read_text(encoding='utf-8')
    tool = next(t for t in TOOLS if t['name'] == 'agent.present')
    assert tool['inputSchema'] is SCHEMA
    entry = re.search(r'name: "agent.present",.*?parameters: Type.Unsafe\((.*?)\),\n', generated, re.S)
    assert entry, 'Pi registration must retain conditional and nested strict schemas, not only field types'
    assert json.loads(entry[1]) == SCHEMA


def test_pi_executor_calls_real_rest_twin_with_gate_identity_and_unchanged_payload():
    root = Path(__file__).resolve().parents[1]
    source = (root / 'app/agent_extensions/canvas-tools.ts').read_text(encoding='utf-8')
    executor = re.search(r'    "agent.present": async.*?(?=\n    "[^\"]+": async)', source, re.S)
    assert executor, 'agent.present must have a real Pi executor'
    call_api = source[source.index('async function callApi'):source.index('// ── #284')]
    call_api = (call_api.replace('path: string, method: string, body?: any', 'path, method, body')
                .replace('options: RequestInit', 'options').replace('(err as any)', 'err').replace('e: any', 'e'))
    text_result = source[source.index('function textResult'):source.index('async function readChat')]
    text_result = text_result.replace('payload: any', 'payload')
    script = '''
const API_BASE = 'http://127.0.0.1:3146', PROJECT_ID = 'project-406', NODE_ID = 'actor /406';
const calls = [];
globalThis.fetch = async (url, options) => {
    calls.push({url, options});
    return {ok:true, json:async () => ({presentation:{schema:1,id:'server',kind:'plan',state:'presented'}})};
};
''' + call_api + text_result + 'const EXECUTORS = {\n' + executor.group() + '\n};\n' + '''
const args = {action:'show', kind:'plan', title:'Proposal', steps:[{id:'one',text:'Keep exact'}]};
const result = await EXECUTORS['agent.present']('untrusted-call-id', args);
if (calls.length !== 1) throw Error('must send exactly one REST call');
const {url, options} = calls[0], parsed = new URL(url);
if (parsed.pathname !== '/api/projects/project-406/agent/present'
    || parsed.searchParams.get('agent_node_id') !== NODE_ID || options.method !== 'POST'
    || options.headers['X-Agent-Project'] !== PROJECT_ID || options.body !== JSON.stringify(args)
    || JSON.parse(result.content[0].text).presentation.id !== 'server') throw Error('Pi contract drift');
'''
    result = subprocess.run(['node', '--input-type=module', '-e', script], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_plan_is_a_real_persisted_live_and_paged_event_without_canvas_writes(env):
    async def scenario():
        await env.start()
        try:
            before = env.store.canvas(env.project)
            turn = env.session._current_turn_id
            response = await present(env, {'action': 'show', 'kind': 'plan', 'title': 'Proposal',
                                           'steps': [{'id': 'one', 'text': 'Inspect canvas'}]})
            assert response.status_code == 200, response.text
            p = response.json()['presentation']
            assert p == {'schema': 1, 'id': p['id'], 'kind': 'plan', 'title': 'Proposal', 'state': 'presented',
                         'data': {'steps': [{'id': 'one', 'text': 'Inspect canvas'}]}}
            live, = [e for e in env.live if e['type'] == 'plan']
            assert live['presentation'] == p and live['turn_id'] == turn
            assert live['host'] == 'pi' and live['generation'] == env.session.generation and live['seq'] > 1
            saved = [r['event'] for r in await agent_present_events(env) if r['event']['type'] == 'plan']
            paged = [r['event'] for r in await agent_present_events(env, 'events/page') if r['event']['type'] == 'plan']
            assert saved == paged == [live]
            read = await present(env, {'action': 'get', 'presentation_id': p['id']})
            assert read.json() == {'presentation': p}
            assert len([e for e in env.live if e['type'] == 'plan']) == 1
            assert env.store.canvas(env.project) == before and not env.session.confirm_handlers
        finally:
            await env.client.aclose()
    asyncio.run(scenario())
