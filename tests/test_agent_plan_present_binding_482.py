"""Original Agent bindings survive human edits; plan cards never grant authority (#482)."""
from copy import deepcopy
from io import StringIO
import json
import os
from pathlib import Path
from queue import Queue
import sys
import time
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

import main
from app.project_sessions import register_project_session, unregister_project_session
from app.routers import api_agent_nodes
from app.services import agent_host
from firefly_projects import ProjectStore
from tests.test_agent_plan_399 import submit, upload, workbook


ORIGIN = 'http://127.0.0.1:3198'
ORIGINAL_SID = 'original-plan-present-482'
ACTORS = {'actor': 'pi', 'other': 'pi', 'codex-actor': 'codex'}
TRANSPORTS = ['rest', 'external', 'hosted']


class Output:
    def __init__(self):
        self.lines = Queue()

    def readline(self):
        return self.lines.get(timeout=30)


class Pipe(StringIO):
    def __init__(self, process):
        super().__init__()
        self.process = process

    def close(self):
        # Codex closes stdin after admission; retain the bytes as delivery evidence.
        pass

    def write(self, value):
        count = super().write(value)
        if self.process.host == 'pi':
            command = json.loads(value)
            if command['type'] == 'get_state':
                self.process.emit({'type': 'response', 'command': 'get_state', 'id': command['id'],
                                   'success': True, 'data': self.process.native_state})
            elif command['type'] == 'abort':
                self.process.emit({'type': 'agent_end'})
        return count


class PlanBindingHTTP:
    def __init__(self, client, store, manager, projects, processes):
        self.client, self.store, self.manager = client, store, manager
        self.project, self.foreign = projects
        self.processes, self.live, self.observed = processes, [], set()

    def call(self, operation, args, *, transport='rest', project=None, actor='actor', sid=ORIGINAL_SID):
        project = self.project if project is None else project
        if transport == 'rest':
            namespace, name = operation.split('.')
            response = self.client.post(f'/api/projects/{project}/{namespace}/{name}',
                params={'agent_node_id': actor}, headers={'X-Agent-Project': project}, json=args)
            if response.is_error:
                assert response.status_code in (400, 409), response.text
                return False, response.json()['detail']
            assert response.status_code == 200, response.text
            return True, response.json()
        response = self.client.post('/api/mcp',
            params={'project': project, 'node': actor, 'caller': transport},
            headers={'Mcp-Session-Id': sid}, json={
                'jsonrpc': '2.0', 'id': 482, 'method': 'tools/call',
                'params': {'name': operation, 'arguments': args},
            })
        assert response.status_code == 200, response.text
        envelope = response.json()
        assert envelope['id'] == 482 and 'result' in envelope, envelope
        result = envelope['result']
        if result['isError']:
            return False, result['structuredContent']
        return True, json.loads(result['content'][0]['text'])

    def prime(self, *, project=None, actor='actor', source=None):
        project = self.project if project is None else project
        submit(self.client, project, *([source] if source else []), actor=actor)
        session = self.manager.conversation_session(project, actor)
        assert session is not None and session.is_running
        if id(session) not in self.observed:
            session.event_handlers.append(lambda _s, event: self.live.append((project, actor, deepcopy(event))))
            self.observed.add(id(session))
        return session

    def imported(self, transport='rest', *, actor='actor'):
        source = upload(self.client, self.project, 'binding.xlsx', workbook(''), actor=actor)
        self.prime(actor=actor, source=source)
        ok, draft = self.call('plan.import_excel', {'attachment_id': source['id']}, transport=transport, actor=actor)
        assert ok and draft['generation'] == 1, draft
        return source, draft

    def human_read(self, draft, *, actor='actor'):
        response = self.client.get(f'/api/projects/{self.project}/plan/previews/{draft["plan_id"]}',
            params={'agent_node_id': actor}, headers={'Origin': ORIGIN, 'X-Agent-Project': self.project})
        assert response.status_code == 200, response.text
        return response.json()

    def human_merge(self, draft, *, actor='actor'):
        first, second = draft['preview']['tasks'][:2]
        assert first['source_rows'] == ['图片方案!3'] and second['source_rows'] == ['图片方案!5']
        before = self.state()
        response = self.client.post(f'/api/projects/{self.project}/plan/previews/{draft["plan_id"]}/edits',
            params={'agent_node_id': actor}, headers={'Origin': ORIGIN, 'X-Agent-Project': self.project},
            json={'generation': 1, 'ops': [
                {'task_id': first['task_id'], 'select': True},
                {'task_id': second['task_id'], 'merge_into': first['task_id']},
            ]})
        assert response.status_code == 200, response.text
        edited = response.json()
        assert edited['plan_id'] == draft['plan_id'] and edited['generation'] == 2
        merged = edited['preview']['tasks'][0]
        assert merged['task_id'] == first['task_id']
        assert merged['source_rows'] == ['图片方案!3', '图片方案!5']
        assert [int(row.rsplit('!', 1)[1]) for row in merged['source_rows']] == [3, 5]
        assert merged['selected'] is True and len(edited['preview']['tasks']) == 2
        assert draft['generation'] == 1 and len(draft['preview']['tasks']) == 3
        assert self.human_read(edited, actor=actor) == edited
        assert self.state() == before
        return edited

    def records(self, project, actor, suffix='events'):
        response = self.client.get(f'/api/projects/{project}/agent-nodes/{actor}/{suffix}',
                                   params={'host': ACTORS[actor]})
        assert response.status_code == 200, response.text
        return response.json()['events']

    def state(self):
        state = {'live': deepcopy(self.live),
                 'pipes': [process.stdin.getvalue() for process in self.processes], 'projects': {}}
        for project in (self.project, self.foreign):
            opened = self.client.get(f'/api/project-library/{project}')
            media = self.client.get(f'/api/project-library/{project}/media')
            assert opened.status_code == 200 and media.status_code == 200
            state['projects'][project] = {'canvas': opened.json()['canvas'], 'media': media.json(),
                'events': {(actor, suffix): self.records(project, actor, suffix)
                           for actor in ACTORS for suffix in ('events', 'events/page')}}
        return state

    @staticmethod
    def reference(draft):
        return {'action': 'show', 'kind': 'plan', 'title': 'Review current imported plan',
                'plan_id': draft['plan_id'], 'generation': draft['generation']}

    def reject(self, args, *, code='PLAN_STALE', **kwargs):
        before = self.state()
        ok, error = self.call('agent.present', args, **kwargs)
        assert not ok and error['code'] == code, error
        assert self.state() == before

    def show(self, draft, *, transport='rest', actor='actor', sid=ORIGINAL_SID):
        before = self.state()
        ok, result = self.call('agent.present', self.reference(draft), transport=transport, actor=actor, sid=sid)
        assert ok and 'presentation' in result, result
        presentation = result['presentation']
        assert presentation == {'schema': 1, 'id': presentation['id'], 'kind': 'plan',
            'title': 'Review current imported plan', 'state': 'presented', 'data': {
                'source': 'plan_preview', 'plan_id': draft['plan_id'], 'generation': draft['generation']}}
        after = self.state()
        assert after['pipes'] == before['pipes']
        assert len(after['live']) == len(before['live']) + 1
        project, node, live = after['live'][-1]
        assert (project, node) == (self.project, actor)
        assert live['type'] == 'plan' and live['presentation'] == presentation
        assert live['host'] == ACTORS[actor] and live['seq'] > 0
        original = before['projects'][self.project]['events'][(actor, 'events')]
        prompt = next(record for record in reversed(original) if record['event']['type'] == 'user_prompt')
        assert live['generation'] == prompt['generation']
        assert live['turn_id'] == (None if transport == 'external' else prompt['event']['turn_id'])
        for pid in (self.project, self.foreign):
            previous, current = before['projects'][pid], after['projects'][pid]
            assert (current['canvas'], current['media']) == (previous['canvas'], previous['media'])
            for key, records in current['events'].items():
                old = previous['events'][key]
                if pid == self.project and key[0] == actor:
                    assert records[:-1] == old and records[-1]['event'] == live
                else:
                    assert records == old
        ok, read = self.call('agent.present', {'action': 'get', 'presentation_id': presentation['id']},
                            transport=transport, actor=actor, sid=sid)
        assert ok and read == result, read
        assert self.state() == after
        assert self.human_read(draft, actor=actor) == draft
        return presentation

    def wait_event(self, actor, event_type, *, after_seq=0):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            found = [record for record in self.records(self.project, actor)
                     if record['event']['type'] == event_type and record['event']['seq'] > after_seq]
            if found:
                return found[-1]
            time.sleep(0.01)
        pytest.fail(f'Native pipe event {event_type} was not recorded for {actor}')

    def finish(self, session):
        reader = session._reader_task
        session.process.finish()

        async def drained():
            await reader

        self.client.portal.call(drained)


@pytest.fixture
def binding_http(tmp_path, monkeypatch):
    monkeypatch.setenv('FIREFLY_CODEX_WARMUP', 'off')
    # Resolve a genuine executable; all native commands still terminate at offline Popen.
    monkeypatch.setenv('FIREFLY_PI_CLI', sys.executable)
    processes = []

    class Process:
        def __init__(self, argv, **kwargs):
            self.argv, self.env = argv, kwargs['env']
            self.host = 'codex' if 'exec' in argv else 'pi'
            self.stdout, self.stderr = Output(), StringIO()
            self.stdin, self.returncode = Pipe(self), None
            if self.host == 'pi':
                native_id = argv[argv.index('--session-id') + 1]
                self.native_state = {'sessionId': native_id,
                    'sessionFile': str(Path(kwargs['cwd']) / f'{native_id}.jsonl')}
            processes.append(self)

        def emit(self, event):
            self.stdout.lines.put(json.dumps(event) + '\n')

        def poll(self):
            return self.returncode

        def wait(self):
            return self.returncode

        def finish(self):
            if self.returncode is None:
                self.returncode = 0
                if self.host == 'codex':
                    self.emit({'type': 'turn.completed'})
                self.stdout.lines.put('')

        terminate = kill = finish

    original_which = agent_host.shutil.which
    monkeypatch.setattr(agent_host.shutil, 'which',
                        lambda name: sys.executable if name == 'codex' else original_which(name))
    async def accept_fixture_pi(*args, **kwargs):
        pass

    monkeypatch.setattr(agent_host, 'verify_pi_launch_version', accept_fixture_pi)
    monkeypatch.setattr(agent_host.subprocess, 'Popen', Process)
    store = ProjectStore(Path(os.environ['FIREFLY_PROJECTS_ROOT']) / tmp_path.name, snapshot_delay=0)
    manager = agent_host.AgentHostManager()
    monkeypatch.setattr(main, 'FIREFLY_PROJECT_STORE', store)
    monkeypatch.setattr(agent_host, 'agent_host_manager', manager)
    monkeypatch.setattr(api_agent_nodes, 'agent_host_manager', manager)
    projects = [store.create_project('482 binding regression')['id'] for _ in range(2)]
    tabs = [object(), object()]
    for project, tab in zip(projects, tabs):
        register_project_session(tab, project)
        store.apply_mutations(project, [{'op': 'upsert_node', 'node': {
            'id': actor, 'type': 'smart-agent', 'agentCli': host, 'autonomy': 'autonomous',
        }} for actor, host in ACTORS.items()], base_revision=0)
        store.flush(project)
    try:
        with TestClient(main.app, base_url=ORIGIN) as client:
            try:
                yield PlanBindingHTTP(client, store, manager, projects, processes)
            finally:
                for process in processes:
                    process.finish()

                async def drained():
                    for session in manager.sessions.values():
                        if session._reader_task is not None:
                            await session._reader_task

                client.portal.call(drained)
    finally:
        for tab in tabs:
            unregister_project_session(tab)
        store.close()


@pytest.mark.parametrize('transport', TRANSPORTS)
@pytest.mark.parametrize('target', ['foreign-project', 'foreign-actor'])
def test_plan_reference_rejects_another_project_or_actor_without_events(binding_http, transport, target):
    http = binding_http
    _, draft = http.imported(transport)
    project = http.foreign if target == 'foreign-project' else http.project
    actor = 'other' if target == 'foreign-actor' else 'actor'
    http.prime(project=project, actor=actor)
    http.reject(http.reference(draft), transport=transport, project=project, actor=actor)
    http.show(draft, transport=transport)


@pytest.mark.parametrize(('original', 'intruder'), [
    ('rest', 'external'), ('rest', 'hosted'), ('external', 'rest'),
    ('external', 'hosted'), ('hosted', 'rest'), ('hosted', 'external'),
])
def test_human_edit_does_not_widen_plan_reference_transport_or_owner(binding_http, original, intruder):
    http = binding_http
    _, draft = http.imported(original)
    edited = http.human_merge(draft)
    http.reject(http.reference(edited), transport=intruder)
    http.show(edited, transport=original)



def test_external_reference_and_receipt_require_the_original_mcp_session(binding_http):
    http = binding_http
    _, draft = http.imported('external')
    edited = http.human_merge(draft)
    http.reject(http.reference(edited), transport='external', sid='foreign-plan-present-482')
    presentation = http.show(edited, transport='external')
    read = {'action': 'get', 'presentation_id': presentation['id']}
    http.prime(actor='other')
    http.prime(project=http.foreign)
    for overrides in ({'sid': 'foreign-plan-present-482'}, {'actor': 'other'}, {'project': http.foreign}):
        http.reject(read, code='CONFLICT', transport='external', **overrides)
    for transport in ('rest', 'hosted'):
        http.reject(read, code='CONFLICT', transport=transport)
    before = http.state()
    ok, result = http.call('agent.present', read, transport='external')
    assert ok and result == {'presentation': presentation}, result
    assert http.state() == before


@pytest.mark.parametrize('transport', TRANSPORTS)
def test_edited_plan_reference_rejects_old_and_future_generations_without_events(binding_http, transport):
    http = binding_http
    _, draft = http.imported(transport)
    edited = http.human_merge(draft)
    http.reject(http.reference(draft), transport=transport)
    http.reject({**http.reference(edited), 'generation': 3}, transport=transport)
    http.show(edited, transport=transport)


@pytest.mark.parametrize('transport', TRANSPORTS)
def test_reference_generation_is_required_exact_and_not_an_authority_override(binding_http, transport):
    http = binding_http
    _, draft = http.imported(transport)
    args = http.reference(draft)
    without_generation = {key: value for key, value in args.items() if key != 'generation'}
    http.reject(without_generation, transport=transport, code='INVALID_ARGUMENT')
    for generation in (None, True, False, 0, -1, 1.0, 1.5, '1'):
        http.reject({**args, 'generation': generation}, transport=transport, code='INVALID_ARGUMENT')
    http.reject({**args, 'steps': [{'id': 'forged', 'text': 'Not the imported preview'}]},
                transport=transport, code='INVALID_ARGUMENT')
    http.show(draft, transport=transport)


@pytest.mark.parametrize('transport', TRANSPORTS)
def test_original_transport_cannot_present_a_plan_from_an_aborted_input_epoch(binding_http, transport):
    http = binding_http
    source, draft = http.imported(transport)
    session = http.manager.conversation_session(http.project, 'actor')
    original_epoch = session.input_epoch
    response = http.client.post(f'/api/projects/{http.project}/agent-nodes/actor/abort')
    assert response.status_code == 200, response.text
    http.wait_event('actor', 'agent_end')
    assert session.input_epoch != original_epoch
    http.prime(source=source)
    http.reject(http.reference(draft), transport=transport)
    assert http.human_read(draft) == draft
    ok, replacement = http.call('plan.import_excel', {'attachment_id': source['id']}, transport=transport)
    assert ok and replacement['plan_id'] != draft['plan_id'], replacement
    http.show(replacement, transport=transport)


def test_codex_hosted_reference_uses_real_admission_thread_binding_and_resumed_turn(
    binding_http, tmp_path, monkeypatch,
):
    codex_home = tmp_path / 'codex-home'
    codex_home.mkdir()
    monkeypatch.setenv('CODEX_HOME', str(codex_home))
    http = binding_http
    actor = 'codex-actor'
    source = upload(http.client, http.project, 'codex-binding.xlsx', workbook(''), actor=actor)
    session = http.prime(actor=actor, source=source)
    first_process = session.process
    accepted = next(record for record in http.records(http.project, actor) if record.get('accepted_input'))
    assert accepted['input_branch'] == ''
    thread = str(uuid4())
    first_process.emit({'type': 'thread.started', 'thread_id': thread})
    bound = http.wait_event(actor, 'input_branch_bound')
    assert bound['input_binding'] is True and bound['input_branch'] == thread
    assert (bound['input_epoch'], bound['generation']) == (accepted['input_epoch'], accepted['generation'])
    ok, draft = http.call('plan.import_excel', {'attachment_id': source['id']}, transport='hosted', actor=actor)
    assert ok and draft['generation'] == 1, draft
    edited = http.human_merge(draft, actor=actor)
    http.reject(http.reference(draft), transport='hosted', actor=actor)
    for transport in ('rest', 'external'):
        http.reject(http.reference(edited), transport=transport, actor=actor)
    http.prime(actor='other')
    http.reject(http.reference(edited), transport='hosted', actor='other')
    original = http.show(edited, transport='hosted', actor=actor)
    # Hosted MCP is owned by the original actor, not its reconnectable transport SID.
    http.show(edited, transport='hosted', actor=actor, sid='reconnected-codex-482')
    assert '-s' in first_process.argv and first_process.argv[first_process.argv.index('-s') + 1] == 'read-only'
    assert source['id'] in first_process.stdin.getvalue()
    assert first_process.env['CODEX_HOME'] == str(codex_home)
    first_turn, first_generation = accepted['event']['turn_id'], accepted['generation']
    http.finish(session)
    http.prime(actor=actor)
    second_process = session.process
    assert second_process is not first_process and 'resume' in second_process.argv
    assert second_process.argv[second_process.argv.index('resume') + 1] == thread
    current = next(record for record in reversed(http.records(http.project, actor))
                   if record['event']['type'] == 'user_prompt')
    assert current['generation'] > first_generation and current['event']['turn_id'] != first_turn
    assert session.input_epoch == accepted['input_epoch'] and session.input_branch == thread
    http.reject({'action': 'get', 'presentation_id': original['id']},
                code='CONFLICT', transport='hosted', actor=actor)
    http.show(edited, transport='hosted', actor=actor)
    # A real native thread change must not inherit attachment or preview ownership.
    second_process.emit({'type': 'thread.started', 'thread_id': str(uuid4())})
    changed = http.wait_event(actor, 'input_branch_bound', after_seq=bound['event']['seq'])
    assert changed['input_branch'] != thread
    http.reject(http.reference(edited), transport='hosted', actor=actor)
    assert http.human_read(edited, actor=actor) == edited
    before = http.state()
    ok, error = http.call('plan.import_excel', {'attachment_id': source['id']}, transport='hosted', actor=actor)
    assert not ok and error['code'] == 'FILE_NOT_ALLOWED', error
    assert http.state() == before
