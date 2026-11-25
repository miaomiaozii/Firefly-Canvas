"""Public prompt -> real host adapter -> offline native pipes, not model compliance (#475)."""
import json
from pathlib import Path
import re
import tomllib
from uuid import uuid4

import pytest

import main
from tests.test_agent_plan_apply_484 import headers
from tests.test_agent_plan_present_binding_482 import binding_http
from app.routers import api_agent_tools as tools
from tests.test_agent_plan_resumable_hosted_475 import (
    apply, hosted_confirm_475 as confirm, end_response, hosted_imported_475 as imported,
    inputs, live_http, operations, read, wait_record,
)


def submit_prompt(http, actor, text, request_id):
    response = http.client.post(f'/api/projects/{http.project}/agent-nodes/{actor}/prompt',
        headers=headers(http.project), json={'message': text, 'request_id': request_id})
    assert response.status_code == 200, response.text
    record = http.wait_event(actor, 'user_prompt')
    assert record['event']['text'] == text and record['event']['request_id'] == request_id
    assert record['event']['turn_id']
    process = http.processes[-1]
    if actor == 'codex-actor':
        assert process.host == 'codex' and process.stdin.getvalue() == text
        instruction, = [arg for arg in process.argv if arg.startswith('developer_instructions=')]
        policy = tomllib.loads(instruction)['developer_instructions']
    else:
        assert process.host == 'pi'
        commands = [json.loads(line) for line in process.stdin.getvalue().splitlines()]
        assert [cmd['message'] for cmd in commands if cmd['type'] == 'prompt'] == [text]
        path = Path(process.argv[process.argv.index('--system-prompt') + 1])
        assert path.is_relative_to(http.store.root / http.project / 'agent_nodes' / actor)
        policy = path.read_text(encoding='utf-8')
    return record, process, policy


@pytest.mark.parametrize('actor', ['actor', 'codex-actor'], ids=['Pi', 'Codex'])
@pytest.mark.parametrize('autonomy', ['cautious', 'standard', 'autonomous'])
@pytest.mark.parametrize('round_kind', ['initial', 'resumed'])
def test_R34_public_prompt_submits_pending_policy_without_a_plan_exception(
    live_http, actor, autonomy, round_kind,
):
    http = live_http
    changed = http.client.post(f'/api/projects/{http.project}/agent-nodes/{actor}/autonomy',
        headers=headers(http.project), json={'autonomy': autonomy})
    assert changed.status_code == 200, changed.text
    if round_kind == 'resumed':
        first, previous, _ = submit_prompt(http, actor, 'Prepare the original session', '475-original')
        if actor == 'codex-actor':
            native_id = str(uuid4())
            previous.emit({'type': 'thread.started', 'thread_id': native_id})
            bound = http.wait_event(actor, 'input_branch_bound')
            assert bound['input_branch'] == native_id
        else:
            native_id = previous.native_state['sessionId']
        session = http.manager.conversation_session(http.project, actor)
        end_response(http, session, actor)
        if actor == 'actor':
            http.finish(session)
    current, process, policy = submit_prompt(http, actor,
        'Import the Excel plan; keep the original apply while awaiting confirmation.', '475-policy')
    status = http.client.get(f'/api/projects/{http.project}/agent-nodes/{actor}/status')
    assert status.status_code == 200 and status.json()['autonomy'] == autonomy
    if round_kind == 'resumed':
        assert process is not previous and current['event']['turn_id'] != first['event']['turn_id']
        if actor == 'codex-actor':
            assert process.argv[process.argv.index('resume') + 1] == native_id
        else:
            assert process.argv[process.argv.index('--session-id') + 1] == native_id
    elif actor == 'codex-actor':
        assert 'resume' not in process.argv
    assert 'PLAN_NOT_CONFIRMED 不会自动恢复 plan.apply' not in policy
    for requirement in (
        'CONFIRM_PENDING / awaiting_confirmation', '结束当前响应并等待原操作',
        '原执行仍由服务端保留（包括 plan.apply）', '不在聊天里再问批准，不重复调用同一操作',
        '不换入口重发', '不要 abort/stop 或取消服务器待决执行',
        '90 秒只是同步回包窗口，不是批准有效期', '系统续接属于原用户逻辑轮，不是新用户消息',
        '真正拒绝或失效', 'PLAN_NOT_CONFIRMED', 'PLAN_STALE / CONFIRM_STALE',
        '暂停', '不承诺无条件自动续跑', '只依据真实回执继续',
    ):
        assert requirement in policy
    assert policy.count('确认由系统处理') == 1


@pytest.mark.parametrize('actor', ['actor', 'codex-actor'], ids=['Pi', 'Codex'])
@pytest.mark.parametrize('autonomy', ['cautious', 'standard', 'autonomous'])
def test_R35_published_plan_apply_contract_matches_real_pending_and_late_receipt(
    live_http, monkeypatch, actor, autonomy,
):
    http = live_http
    response = http.client.post(f'/api/projects/{http.project}/agent-nodes/{actor}/autonomy',
        headers=headers(http.project), json={'autonomy': autonomy})
    assert response.status_code == 200, response.text
    listing = http.client.post('/api/mcp', params={'project': http.project, 'node': actor, 'caller': 'hosted'},
        json={'jsonrpc': '2.0', 'id': 475, 'method': 'tools/list'})
    assert listing.status_code == 200, listing.text
    published = next(tool for tool in listing.json()['result']['tools'] if tool['name'] == 'plan.apply')
    schema = published['inputSchema']
    assert set(schema['properties']) == {'plan_id', 'generation', 'layout', 'task_ids'}
    assert schema['required'] == ['plan_id'] and schema['additionalProperties'] is False
    _, draft, session = imported(http, 'hosted', actor)
    original = next(record['event']['turn_id'] for record in reversed(inputs(http, actor))
                    if record['event']['type'] == 'user_prompt')
    before = http.client.get(f'/api/project-library/{http.project}').json()['canvas']
    monkeypatch.setattr(tools, 'AGENT_WRITE_CONFIRM_TIMEOUT_SECONDS', .01)
    ok, pending = apply(http, draft, 'hosted', actor)
    event = http.wait_event(actor, 'confirm')['event']
    try:
        assert ok and pending['code'] == 'CONFIRM_PENDING', pending
        assert pending['status'] == 'awaiting_confirmation' and pending['confirm_id'] == event['confirm_id']
        assert read(http, draft, actor)['state'] == 'awaiting_confirmation'
        assert operations(http) == []
        assert http.client.get(f'/api/project-library/{http.project}').json()['canvas'] == before
        confirm(http, actor, event)
        queued = wait_record(http, actor, lambda e: e['type'] == 'system_continuation' and e['delivery'] == 'queued')
        application = read(http, draft, actor)
        assert application['state'] == 'applied' and application['receipt']['applied_by'] == 'agent'
        receipt = application['receipt']['result']
        assert len(operations(http)) == 1 and operations(http)[0]['op_id'] == receipt['op_id']
        assert operations(http)[0]['turn_id'] == original == queued['turn_id']
        assert json.loads(queued['text'].split('：', 1)[1])['op_id'] == receipt['op_id']
        end_response(http, session, actor)
        sent = wait_record(http, actor, lambda e: e['type'] == 'system_continuation' and e['delivery'] == 'sent')
        assert sent['request_id'] == queued['request_id'] and sent['turn_id'] == original
        if actor == 'codex-actor':
            assert http.processes[-1].stdin.getvalue() == sent['text']
        else:
            commands = [json.loads(line) for line in session.process.stdin.getvalue().splitlines()]
            assert any(cmd['type'] == 'follow_up' and cmd['message'] == sent['text'] for cmd in commands)
    finally:
        confirm(http, actor, event, False)
        wait_record(http, actor, lambda e: e['type'] == 'system_continuation' and e['delivery'] in ('queued', 'sent'))
    description = published['description']
    for requirement in ('三档均须确认', 'CONFIRM_PENDING', 'awaiting_confirmation', 'confirm_id',
                        '等待窗结束不等于拒绝', '批准后原操作续跑', '勿重复提交',
                        'PLAN_NOT_CONFIRMED', 'PLAN_STALE', '原用户逻辑轮', '外部 MCP 不注入宿主',
                        '不承诺重启续跑'):
        assert requirement in description


def test_R35_generated_pi_definitions_match_the_public_tool_manifest(live_http):
    from tools.gen_canvas_tools import render

    response = live_http.client.post('/api/mcp', json={
        'jsonrpc': '2.0', 'id': 475, 'method': 'tools/list'})
    assert response.status_code == 200, response.text
    published = {tool['name']: tool['description'] for tool in response.json()['result']['tools']}
    path = Path(__file__).resolve().parents[1] / 'app/agent_extensions/canvas-tools.generated.ts'
    generated = path.read_text(encoding='utf-8')
    entries = re.findall(r'        name: ("[^"\n]+"),\n        description: ("(?:[^"\\]|\\.)*"),', generated)
    registered = {json.loads(name): json.loads(description) for name, description in entries}
    assert registered == published
    assert generated == render()


def test_R35_docs_distinguish_agent_pending_from_human_non_continuation():
    root = Path(__file__).resolve().parents[1]
    context = (root / 'CONTEXT.md').read_text(encoding='utf-8')
    adr = (root / 'docs/adr/0022-mcp-streamable-http-canvas-endpoint.md').read_text(encoding='utf-8')
    assert '原 Agent `plan.apply` 超窗缺口仍归 #475' not in context
    assert '原 Agent `plan.apply` 超出同步窗口仍返回 PLAN_NOT_CONFIRMED' not in adr
    assert '仅人工应用接入 resumable' not in adr
    for document in (context, adr):
        assert '#475' in document and 'CONFIRM_PENDING' in document
        assert '原执行保留' in document and '原用户逻辑轮' in document
        assert '#484 人工应用仍不向 Agent 注入输入或系统续接' in document
    assert '## 修订（#475）：原 Agent 方案应用的待决执行与回执' in adr
    assert '已回 pending 的请求没有第二个 HTTP 响应' in adr
    assert '不承诺重启恢复执行' in adr
