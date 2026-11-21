"""Project-only, read-only historical Agent conversations over REST/MCP."""
import json
from pathlib import Path
import subprocess
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

import main
from app.constants import NODE_TYPE_AGENT
from app.project_sessions import register_project_session, unregister_project_session
from app.services import agent_chat
from app.services.agent_adapters.codex_adapter import map_codex_event
from app.services.agent_host import agent_host_manager
from firefly_projects import ProjectStore


@pytest.fixture
def chat(tmp_path, monkeypatch):
    store = ProjectStore(tmp_path / 'projects', snapshot_delay=0)
    monkeypatch.setattr(main, 'FIREFLY_PROJECT_STORE', store)
    project, other = [store.create_project(name)['id'] for name in ('457 synthetic', '457 other')]
    store.apply_mutations(project, [{'op': 'upsert_node', 'node': {
        'id': 'actor', 'type': NODE_TYPE_AGENT, 'title': 'Current Agent'}}], base_revision=0)
    tab = object()
    register_project_session(tab, project)

    def seed(node, events, *, pid=project, host='pi'):
        path = store.root / pid / 'agent-events' / f'{node}.jsonl'
        path.parent.mkdir(parents=True, exist_ok=True)
        records = [{'project_id': pid, 'node_id': node, 'host': host, 'seq': seq, 'ts': seq,
                    'event': event} for seq, event in enumerate(events, 1)]
        path.write_text(''.join(json.dumps(record) + '\n' for record in records), encoding='utf-8')
        return path

    seed('actor', [
        {'type': 'user_prompt', 'text': 'Find the FIREFLY sketch', 'turn_id': 'one',
         'node_ref_context': 'hidden reference secret', 'attachments': [{'name': 'attachment secret'}]},
        {'type': 'message_update', 'assistantMessageEvent': {'type': 'thinking_delta', 'delta': 'thinking secret'}},
        {'type': 'tool_execution_start', 'args': {'api_key': 'tool secret'}},
        {'type': 'tool_execution_end', 'result': 'tool secret'},
        {'type': 'message_update', 'assistantMessageEvent': {'type': 'text_delta', 'delta': 'A bright '}},
        {'type': 'message_update', 'assistantMessageEvent': {'type': 'text_delta', 'delta': 'firefly answer'}},
        {'type': 'message_end', 'message': {'role': 'assistant', 'content': [
            {'type': 'text', 'text': 'A bright firefly answer'}, {'type': 'toolCall', 'arguments': 'tool secret'}]}},
        {'type': 'agent_end', 'turn_id': 'one', 'messages': [{'content': 'raw secret'}]},
        {'type': 'system_continuation', 'text': 'system secret', 'turn_id': 'one'},
        {'type': 'queued_input', 'input_type': 'follow_up', 'text': 'not delivered firefly'},
        {'type': 'user_prompt', 'text': 'Next drawing', 'turn_id': 'two'},
        {'type': 'message_update', 'assistantMessageEvent': {'type': 'text_delta', 'delta': 'Other answer'}},
        {'type': 'agent_end', 'turn_id': 'two'},
    ])
    seed('retired', [
        {'type': 'user_prompt', 'text': 'Historical firefly', 'turn_id': 'old'},
        {'type': 'message_update', 'assistantMessageEvent': {'type': 'text_delta', 'delta': 'Codex firefly'}},
        {'type': 'agent_end', 'turn_id': 'old'},
    ], host='codex')
    seed('actor', [{'type': 'user_prompt', 'text': 'OTHER PROJECT FIREFLY'}], pid=other)
    try:
        with TestClient(main.app, base_url='http://127.0.0.1:3457') as client:
            yield client, store, project, other, seed
    finally:
        unregister_project_session(tab)
        store.close()


def rest(chat, tool, args=None, **kwargs):
    client, _, project, _, _ = chat
    return client.get(f'/api/projects/{project}/chat/{tool}', params=args, **kwargs)


def mcp(chat, tool, args, *, node='actor', headers=None):
    client, _, project, _, _ = chat
    response = client.post(f'/api/mcp?project={project}&node={node}', headers=headers, json={
        'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
        'params': {'name': f'chat.{tool}', 'arguments': args},
    })
    assert response.status_code == 200, response.text
    result = response.json()['result']
    return result if result['isError'] else json.loads(result['content'][0]['text'])


def snapshot(store):
    return {path.relative_to(store.root): path.read_bytes() for path in store.root.rglob('*') if path.is_file()}


def test_history_default_node_paging_and_message_projection(chat, monkeypatch):
    _, store, project, _, _ = chat
    before, sessions = snapshot(store), dict(agent_host_manager.sessions)
    monkeypatch.setattr(store, 'open_project', Mock(side_effect=AssertionError('must not repair/migrate')))
    history = rest(chat, 'history', {'agent_node_id': 'actor', 'limit': 2}).json()
    assert set(history) == {'project_id', 'scope', 'node_id', 'host', 'messages', 'has_more', 'next_before_seq', 'truncated'}
    assert history['project_id'] == project and history['node_id'] == 'actor' and history['host'] == 'pi'
    assert [message['text'] for message in history['messages']] == ['Next drawing', 'Other answer']
    assert history['has_more'] and history['next_before_seq'] == 11
    older = rest(chat, 'history', {'node_id': 'actor', 'before_seq': 11}).json()
    assert [(message['role'], message['text'], message['turn_id']) for message in older['messages']] == [
        ('user', 'Find the FIREFLY sketch', 'one'), ('assistant', 'A bright firefly answer', 'one')]
    assert older['messages'][1]['message_id'] == 'actor:pi:5'
    assert not older['has_more'] and older['next_before_seq'] is None
    assert not older['truncated']
    assert rest(chat, 'history', {'node_id': 'actor', 'host': 'codex'}).json()['messages'] == []
    assert snapshot(store) == before and agent_host_manager.sessions == sessions


def test_search_matches_messages_not_tools_system_or_foreign_projects(chat):
    result = rest(chat, 'search', {'query': 'FiReFlY'}).json()
    assert result == mcp(chat, 'search', {'query': 'FiReFlY'})
    assert set(result) == {'project_id', 'scope', 'node_id', 'host', 'query', 'hits', 'has_more', 'truncated'}
    assert [hit['message_id'] for hit in result['hits']] == ['actor:pi:5', 'retired:codex:2', 'retired:codex:1', 'actor:pi:1']
    assert all(len(hit['snippet']) <= 240 for hit in result['hits'])
    assert all(set(hit) == {'message_id', 'node_id', 'host', 'seq', 'timestamp', 'turn_id', 'role', 'text_truncated', 'snippet'}
               for hit in result['hits'])
    for word in ('secret', 'not delivered', 'OTHER PROJECT', 'no such text'):
        assert rest(chat, 'search', {'query': word}).json()['hits'] == []
    node = mcp(chat, 'search', {'scope': 'node', 'query': 'firefly', 'limit': 1})
    assert [hit['message_id'] for hit in node['hits']] == ['actor:pi:5'] and node['has_more']
    assert rest(chat, 'search', {'scope': 'canvas', 'host': 'codex', 'query': 'firefly'}).json()['hits'] == result['hits'][1:3]


def test_canvas_retired_conversations_are_accessible_without_sessions_or_writes(chat):
    _, store, _, _, _ = chat
    before, sessions = snapshot(store), dict(agent_host_manager.sessions)
    result = rest(chat, 'history', {'scope': 'canvas'}).json()
    assert result == mcp(chat, 'history', {'scope': 'canvas'}, node='missing-caller')
    assert [(row['conversation_id'], row['retired'], row['message_count']) for row in result['conversations']] == [
        ('actor:pi', False, 4), ('retired:codex', True, 2)]
    retired = rest(chat, 'history', {'node_id': 'retired', 'host': 'codex'}).json()
    assert [message['text'] for message in retired['messages']] == ['Historical firefly', 'Codex firefly']
    assert retired == mcp(chat, 'history', {'node_id': 'retired', 'host': 'codex'}, node='other-caller')
    assert rest(chat, 'history', {'node_id': 'missing', 'host': 'pi'}).json()['messages'] == []
    assert rest(chat, 'history', {'scope': 'canvas', 'limit': 1}).json()['has_more']
    assert rest(chat, 'history', {'node_id': 'missing'}).status_code == 400
    assert snapshot(store) == before and agent_host_manager.sessions == sessions


@pytest.mark.parametrize('args', [
    {'query': ''}, {'query': ' '}, {'query': 'x' * 201}, {'query': 'ok', 'limit': 0},
    {'query': 'ok', 'limit': 201}, {'query': 'ok', 'limit': True}, {'query': 123},
    {'query': 'ok', 'project_id': 'foreign'}, {'query': 'ok', 'caller': 'foreign'},
    {'query': 'ok', 'scope': 'node', 'node_id': '../other'}, {'query': 'ok', 'host': 'other'},
    {'query': 'ok', 'scope': 'canvas', 'node_id': 'actor'}, {'query': 'ok', 'scope': 'unknown'},
])
def test_mcp_arguments_are_bounded_and_cannot_override_project_or_caller_scope(chat, args):
    result = mcp(chat, 'search', args)
    assert result['isError']
    detail = result['structuredContent']
    assert detail['code'] == 'INVALID_ARGUMENT' and {'message', 'hint', 'retryable'} <= detail.keys()


@pytest.mark.parametrize('tool', ['history', 'search'])
@pytest.mark.parametrize('args', [[], ['query'], None, 42, 'query', False])
def test_mcp_rejects_non_object_chat_arguments_without_writes(chat, tool, args):
    _, store, _, _, _ = chat
    before = snapshot(store)
    result = mcp(chat, tool, args)
    assert result['isError']
    assert result['structuredContent']['code'] == 'INVALID_ARGUMENT'
    assert snapshot(store) == before


def test_rest_errors_same_origin_and_declared_project_are_isolated(chat):
    client, _, project, other, _ = chat
    assert rest(chat, 'search', {'query': 'ok'}, headers={'Origin': 'http://evil.example'}).status_code == 403
    response = rest(chat, 'search', {'query': 'ok'}, headers={'X-Agent-Project': other})
    assert response.status_code == 403 and response.json()['detail']['code'] == 'PROJECT_MISMATCH'
    assert mcp(chat, 'search', {'query': 'ok'}, headers={'X-Agent-Project': other})['structuredContent']['code'] == 'PROJECT_MISMATCH'
    missing = client.get('/api/projects/missing/chat/search', params={'query': 'ok'})
    assert missing.status_code == 404 and missing.json()['detail']['code'] == 'PROJECT_NOT_FOUND'
    assert rest(chat, 'history', {'node_id': 'actor', 'limit': 'invalid'}).json()['detail']['code'] == 'INVALID_ARGUMENT'
    assert rest(chat, 'history', {'scope': 'canvas', 'before_seq': 2}).status_code == 400


def test_steering_does_not_duplicate_partial_assistant_text_or_open_a_turn(chat):
    _, _, _, _, seed = chat
    seed('steered', [
        {'type': 'user_prompt', 'text': 'request', 'turn_id': 'original'},
        {'type': 'message_update', 'assistantMessageEvent': {'type': 'text_delta', 'delta': 'first '}},
        {'type': 'queued_input', 'input_type': 'steer', 'text': 'redirect'},
        {'type': 'message_update', 'assistantMessageEvent': {'type': 'text_delta', 'delta': 'second'}},
        {'type': 'message_end', 'message': {'role': 'assistant', 'content': 'first second'}},
        {'type': 'agent_end', 'turn_id': 'original'},
    ])
    result = rest(chat, 'history', {'node_id': 'steered', 'host': 'pi'}).json()
    assert [(message['role'], message['text'], message['turn_id']) for message in result['messages']] == [
        ('user', 'request', 'original'), ('assistant', 'first second', 'original'), ('user', 'redirect', 'original')]
    assert len(rest(chat, 'search', {'query': 'first', 'scope': 'node', 'node_id': 'steered', 'host': 'pi'}).json()['hits']) == 1


def test_queued_system_continuation_does_not_split_a_streaming_pi_message(chat):
    _, store, _, _, seed = chat
    seed('queued-system', [
        {'type': 'user_prompt', 'text': 'request', 'turn_id': 'original'},
        {'type': 'message_update', 'assistantMessageEvent': {'type': 'text_delta', 'delta': 'first '}},
        {'type': 'system_continuation', 'text': 'hidden result', 'turn_id': 'original', 'delivery': 'queued'},
        {'type': 'message_update', 'assistantMessageEvent': {'type': 'text_delta', 'delta': 'second'}},
        {'type': 'message_end', 'message': {'role': 'assistant', 'content': 'first second'}},
        {'type': 'agent_end', 'turn_id': 'original'},
        {'type': 'system_continuation', 'text': 'hidden result', 'turn_id': 'original', 'delivery': 'sent'},
        {'type': 'message_update', 'assistantMessageEvent': {'type': 'text_delta', 'delta': 'continued answer'}},
        {'type': 'message_end', 'message': {'role': 'assistant', 'content': 'continued answer'}},
        {'type': 'agent_end', 'turn_id': 'original'},
    ])
    before = snapshot(store)
    args = {'node_id': 'queued-system', 'host': 'pi'}
    messages = rest(chat, 'history', args).json()['messages']
    assert [(message['message_id'], message['text'], message['turn_id']) for message in messages] == [
        ('queued-system:pi:1', 'request', 'original'),
        ('queued-system:pi:2', 'first second', 'original'),
        ('queued-system:pi:8', 'continued answer', 'original')]
    hits = rest(chat, 'search', {**args, 'scope': 'node', 'query': 'first'}).json()['hits']
    assert [hit['message_id'] for hit in hits] == ['queued-system:pi:2']
    assert snapshot(store) == before


@pytest.mark.parametrize('host', ['pi', 'codex'])
def test_legacy_user_prompt_resets_turn_after_system_continuation(chat, host):
    _, store, _, _, seed = chat
    seed('legacy-turn', [
        {'type': 'user_prompt', 'text': 'known request', 'turn_id': 'known'},
        {'type': 'message_update', 'assistantMessageEvent': {'type': 'text_delta', 'delta': 'initial answer'}},
        {'type': 'agent_end', 'turn_id': 'known'},
        {'type': 'system_continuation', 'text': 'hidden continuation', 'turn_id': 'known'},
        {'type': 'message_update', 'assistantMessageEvent': {'type': 'text_delta', 'delta': 'continued answer'}},
        {'type': 'agent_end', 'turn_id': 'known'},
        {'type': 'user_prompt', 'text': 'legacy request'},
        {'type': 'message_update', 'assistantMessageEvent': {'type': 'text_delta', 'delta': 'legacy answer'}},
        {'type': 'agent_end'},
        {'type': 'user_prompt', 'text': 'next request', 'turn_id': 'next'},
        {'type': 'message_update', 'assistantMessageEvent': {'type': 'text_delta', 'delta': 'next answer'}},
        {'type': 'agent_end', 'turn_id': 'next'},
    ], host=host)
    before = snapshot(store)
    args = {'node_id': 'legacy-turn', 'host': host}
    result = rest(chat, 'history', args).json()
    assert [(message['text'], message['turn_id']) for message in result['messages']] == [
        ('known request', 'known'), ('initial answer', 'known'), ('continued answer', 'known'),
        ('legacy request', None), ('legacy answer', None), ('next request', 'next'), ('next answer', 'next')]
    hits = rest(chat, 'search', {**args, 'scope': 'node', 'query': 'legacy'}).json()['hits']
    assert len(hits) == 2 and all(hit['turn_id'] is None for hit in hits)
    assert snapshot(store) == before


def test_codex_completed_messages_keep_separate_history_ids_search_hits_and_pages(chat):
    _, _, _, _, seed = chat
    completed = [event for item_id, text in [('first', 'alpha'), ('second', 'beta')]
                 for event in map_codex_event({'type': 'item.completed', 'item': {
                     'id': item_id, 'type': 'agent_message', 'text': text}})]
    assert len(completed) == 2 and all(event['type'] == 'message_update' for event in completed)
    seed('codex-items', [
        {'type': 'user_prompt', 'text': 'request', 'turn_id': 'codex-turn'},
        *completed,
        {'type': 'agent_end', 'turn_id': 'codex-turn'},
    ], host='codex')
    args = {'node_id': 'codex-items', 'host': 'codex'}
    messages = rest(chat, 'history', args).json()['messages']
    assert [(message['message_id'], message['text'], message['turn_id']) for message in messages] == [
        ('codex-items:codex:1', 'request', 'codex-turn'),
        ('codex-items:codex:2', 'alpha', 'codex-turn'), ('codex-items:codex:3', 'beta', 'codex-turn')]
    page = rest(chat, 'history', {**args, 'limit': 1}).json()
    assert page['messages'] == messages[-1:] and page['next_before_seq'] == 3
    older = rest(chat, 'history', {**args, 'before_seq': page['next_before_seq']}).json()
    assert older['messages'] == messages[:2]
    hits = rest(chat, 'search', {**args, 'scope': 'node', 'query': 'a'}).json()['hits']
    assert [(hit['message_id'], hit['snippet']) for hit in hits] == [
        ('codex-items:codex:3', 'beta'), ('codex-items:codex:2', 'alpha')]
    assert rest(chat, 'search', {**args, 'scope': 'node', 'query': 'alphabeta'}).json()['hits'] == []


def test_pi_deltas_combine_and_final_snapshots_replace_without_duplicate_messages(chat):
    _, _, _, _, seed = chat
    seed('pi-stream', [
        {'type': 'user_prompt', 'text': 'request', 'turn_id': 'pi-turn'},
        {'type': 'message_update', 'assistantMessageEvent': {'type': 'text_delta', 'delta': 'draft '}},
        {'type': 'message_update', 'assistantMessageEvent': {'type': 'text_delta', 'delta': 'answer'}},
        {'type': 'message_end', 'message': {'role': 'assistant', 'content': 'final answer'}},
        {'type': 'message_update', 'assistantMessageEvent': {'type': 'text_delta', 'delta': 'partial '}},
        {'type': 'message_update', 'assistantMessageEvent': {'type': 'text_delta', 'delta': 'answer'}},
        {'type': 'tool_execution_start', 'toolName': 'canvas.read'},
        {'type': 'message_end', 'message': {'role': 'assistant', 'content': 'snapshot only'}},
        {'type': 'agent_end', 'turn_id': 'pi-turn'},
    ])
    args = {'node_id': 'pi-stream', 'host': 'pi'}
    messages = rest(chat, 'history', args).json()['messages']
    assert [(message['message_id'], message['text']) for message in messages] == [
        ('pi-stream:pi:1', 'request'), ('pi-stream:pi:2', 'final answer'),
        ('pi-stream:pi:5', 'partial answer'), ('pi-stream:pi:8', 'snapshot only')]
    assert all(message['turn_id'] == 'pi-turn' for message in messages)
    assert rest(chat, 'search', {**args, 'scope': 'node', 'query': 'draft'}).json()['hits'] == []
    hits = rest(chat, 'search', {**args, 'scope': 'node', 'query': 'answer'}).json()['hits']
    assert [hit['message_id'] for hit in hits] == ['pi-stream:pi:5', 'pi-stream:pi:2']


@pytest.mark.parametrize('prefix, body, query, match_offset', [
    ('x' * 600, 'Needle', 'NEEDLE', 0),
    ('ß' * 600, 'Needle', 'NEEDLE', 0),
    ('ß' * 600, 'Straße', 'STRASSE', 0),
    ('x' * 600, 'STRASSE', 'Straße', 0),
    ('ß' * 600, 'İstanbul', 'i\u0307STANBUL', 0),
    ('ß' * 600, 'aßneedle', 'sneedle', 1),
], ids=['ascii-prefix', 'expanded-prefix', 'expanded-match', 'expanded-query', 'dotted-i', 'inside-expansion'])
def test_search_snippet_maps_casefolded_match_to_original_unicode_text(chat, prefix, body, query, match_offset):
    _, _, _, _, seed = chat
    text = prefix + body + ' tail' * 80
    seed('unicode', [{'type': 'user_prompt', 'text': text}])
    result = rest(chat, 'search', {'scope': 'node', 'node_id': 'unicode', 'host': 'pi', 'query': query}).json()
    assert len(result['hits']) == 1
    hit = result['hits'][0]
    start = len(prefix) + match_offset - 80
    assert hit['snippet'] == text[start:start + 240]
    assert query.casefold() in hit['snippet'].casefold()
    assert hit['message_id'] == 'unicode:pi:1' and hit['turn_id'] is None


def test_pi_chat_executor_preserves_project_target_filters_and_cursor():
    source = (Path(__file__).resolve().parents[1] / 'app/agent_extensions/canvas-tools.ts').read_text(encoding='utf-8')
    code = source.split('async function readChat', 1)[1].split('const EXECUTORS:', 1)[0]
    code = 'async function readChat' + code.replace('tool: string', 'tool').replace('params: any', 'params')
    script = '''
        const PROJECT_ID = 'project-only', NODE_ID = 'actor';
        const textResult = value => value;
        async function callApi(path, method) { return {path, method}; }
    ''' + code + '''
        const result = await readChat('history', {node_id: 'retired', host: 'codex', before_seq: 42, limit: 7});
        const url = new URL(result.path, 'http://127.0.0.1:1');
        if (url.pathname !== '/api/projects/project-only/chat/history' || result.method !== 'GET'
            || url.searchParams.get('node_id') !== 'retired' || url.searchParams.get('host') !== 'codex'
            || url.searchParams.get('agent_node_id') !== 'actor' || url.searchParams.get('before_seq') !== '42'
            || url.searchParams.get('limit') !== '7') throw new Error('Pi chat contract drift');
    '''
    result = subprocess.run(['node', '--input-type=module', '-e', script], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_unreadable_snapshot_is_a_structured_error_without_repair(chat, monkeypatch):
    from firefly_projects import ProjectStoreError
    _, store, _, _, _ = chat
    before = snapshot(store)
    monkeypatch.setattr(store, 'agent_inverse_snapshot', Mock(side_effect=ProjectStoreError('private path')))
    response = rest(chat, 'history', {'scope': 'canvas'})
    assert response.status_code == 409
    assert response.json()['detail']['code'] == 'CHAT_HISTORY_UNAVAILABLE'
    assert 'private path' not in response.text
    assert snapshot(store) == before


def test_scan_and_text_budgets_are_explicit_and_legacy_host_is_pi(chat, monkeypatch):
    _, _, project, other, seed = chat
    path = seed('legacy', [
        {'type': 'user_prompt', 'text': 'start ' + 'x' * 1000, 'turn_id': 'legacy'},
        {'type': 'message_update', 'assistantMessageEvent': {'type': 'text_delta', 'delta': 'first second'}},
        {'type': 'agent_end'},
    ])
    lines = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
    for line in lines:
        line.pop('host')
    foreign = {**lines[0], 'seq': 99, 'project_id': other, 'event': {'type': 'user_prompt', 'text': 'FOREIGN INJECTED'}}
    path.write_text(''.join(json.dumps(line) + '\n' for line in [*lines, foreign]) + '{"incomplete":', encoding='utf-8')
    monkeypatch.setattr(agent_chat, 'MAX_TEXT', 20)
    result = rest(chat, 'history', {'node_id': 'legacy', 'host': 'pi'}).json()
    assert result['messages'][0]['text_truncated'] and len(result['messages'][0]['text']) == 20
    assert result['messages'][1]['text'] == 'first second'
    assert rest(chat, 'search', {'query': 'FOREIGN INJECTED'}).json()['hits'] == []
    monkeypatch.setattr(agent_chat, 'MAX_FILE_BYTES', 200)
    assert rest(chat, 'history', {'node_id': 'legacy', 'host': 'pi'}).json()['truncated']
    monkeypatch.setattr(agent_chat, 'MAX_SCAN_BYTES', 100)
    assert rest(chat, 'search', {'query': 'firefly'}).json()['truncated']
    monkeypatch.setattr(agent_chat, 'MAX_FILES', 1)
    assert rest(chat, 'history', {'scope': 'canvas'}).json()['truncated']
