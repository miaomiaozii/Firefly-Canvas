"""Public MCP preview reads across the Codex 0.162.0 byte-budget boundary."""
import json
from pathlib import Path

from tests.test_agent_plan_399 import SHEET, env, submit, tool, upload, workbook  # noqa: F401
from app.services.agent_attachments import attachment_root, resolve_attachments


def plan_mcp(client, project, tool, args, *, caller='hosted', sid='plan-405'):
    response = client.post(f'/api/mcp?project={project}&node=actor&caller={caller}', json={
        'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
        'params': {'name': tool, 'arguments': args},
    }, headers={'Mcp-Session-Id': sid})
    assert response.status_code == 200, response.text
    return response.json()['result']


def native_text(result):
    # Codex 0.162.0 serializes non-null structuredContent before using content items.
    structured = result.get('structuredContent')
    return result['content'][0]['text'] if structured is None else json.dumps(
        structured, ensure_ascii=False, separators=(',', ':'))


def test_oversized_import_exposes_all_fifteen_task_ids_below_native_mcp_budget(env):
    client, _, _, (project, _) = env
    rows = {1: ['名称', '设计需求', '文案', '参考图', '提示词']}
    rows.update({row: [f'主图{row - 1}', '精确还原金属材质和构图。' * 45] for row in range(2, 17)})
    source = upload(client, project, 'plan.xlsx', workbook('', rows=rows))
    submit(client, project, source)
    result = plan_mcp(client, project, 'plan.import_excel', {'attachment_id': source['id']})
    assert not result['isError'], result
    full = json.loads(result['content'][0]['text'])
    full_tasks = full['preview']['tasks']
    assert len(full_tasks) == 15
    assert len(result['content'][0]['text'].encode('utf-8')) > 12_000
    text = native_text(result)
    assert len(text.encode('utf-8')) < 10_000, 'MCP model-facing JSON exceeds the fallback budget'
    compact = json.loads(text)
    assert (compact['plan_id'], compact['generation']) == (full['plan_id'], full['generation'])
    assert [task['source_row'] for task in compact['preview']['tasks']] == [f'{SHEET}!{row}' for row in range(2, 17)]
    assert [task['task_id'] for task in compact['preview']['tasks']] == [task['task_id'] for task in full_tasks]
    assert len({task['task_id'] for task in compact['preview']['tasks']}) == 15
    assert compact['preview']['next_offset'] is None
    ops = [op for task in full_tasks for op in (
        {'task_id': task['task_id'], 'rename': '🦋"\\\u0001' * 50},
        {'task_id': task['task_id'], 'set_section': '中🦋\u0001\\' * 50},
        {'task_id': task['task_id'], 'select': True},
    )]
    edited = plan_mcp(client, project, 'plan.edit_preview', {
        'plan_id': full['plan_id'], 'generation': full['generation'], 'ops': ops,
    })
    assert not edited['isError'], edited
    assert len(edited['content'][0]['text'].encode('utf-8')) > 12_000
    assert len(native_text(edited).encode('utf-8')) <= 8192
    edited_preview = json.loads(native_text(edited))
    assert edited_preview['generation'] == full['generation'] + 1
    assert [(task['task_id'], task['selected']) for task in edited_preview['preview']['tasks']] == [
        (task['task_id'], True) for task in full_tasks
    ]


def test_small_plan_preserves_legacy_mcp_text_and_rest_detail_reads(env):
    client, store, _, (project, _) = env
    source = upload(client, project, 'plan.xlsx', workbook(''))
    submit(client, project, source)
    mcp = plan_mcp(client, project, 'plan.import_excel', {'attachment_id': source['id']})
    assert not mcp['isError'] and 'structuredContent' not in mcp
    assert 'base_prompt' in json.loads(native_text(mcp))['preview']['tasks'][0]

    draft = tool(client, project, 'plan.import_excel', {'attachment_id': source['id']})
    task = draft['preview']['tasks'][0]
    before = store.canvas(project)['revision']
    args = {'plan_id': draft['plan_id'], 'generation': draft['generation'], 'task_id': task['task_id']}
    result = tool(client, project, 'plan.read_preview', args)
    assert result['preview'] == {'view': 'task', 'task': task, 'next_offset': None}
    for offset in (True, -1, 1):
        assert tool(client, project, 'plan.read_preview', {**args, 'offset': offset})['code'] == 'INVALID_ARGUMENT'
    assert store.canvas(project)['revision'] == before


def test_read_preview_pages_and_reads_a_whole_task_only_for_the_original_scope(env):
    client, store, _, (project, _) = env
    rows = {1: ['名称', '设计需求', '文案', '参考图', '提示词']}
    detail = '\n'.join(f'背景{index}🦋"\\' for index in range(700))
    rows.update({row: [f'主图{row - 1}', detail] for row in range(2, 19)})
    source = upload(client, project, 'plan.xlsx', workbook('', rows=rows))
    submit(client, project, source)

    def invoke(name, args, sid='original'):
        return plan_mcp(client, project, f'plan.{name}', args, caller='external', sid=sid)

    imported = invoke('import_excel', {'attachment_id': source['id']})
    full = json.loads(imported['content'][0]['text'])
    plan_args = {key: full[key] for key in ('plan_id', 'generation')}
    tasks = full['preview']['tasks']
    first = json.loads(native_text(imported))['preview']
    assert len(first['tasks']) == 15 and first['next_offset'] == 15
    second = invoke('read_preview', {**plan_args, 'offset': first['next_offset']})
    assert not second['isError'], second
    second_page = json.loads(native_text(second))['preview']
    assert second_page['offset'] == 15 and second_page['next_offset'] is None
    assert [item['task_id'] for item in first['tasks'] + second_page['tasks']] == [item['task_id'] for item in tasks]

    detail_args = {**plan_args, 'task_id': tasks[-1]['task_id']}
    denied = invoke('read_preview', detail_args, sid='other')
    assert denied['isError'] and denied['structuredContent']['code'] == 'PLAN_STALE'
    assert tool(client, project, 'plan.read_preview', detail_args)['code'] == 'PLAN_STALE'
    chunks, offset = [], 0
    for _ in range(30):
        result = invoke('read_preview', {**detail_args, 'offset': offset})
        assert not result['isError'], result
        serialized = native_text(result)
        assert len(serialized.encode('utf-8')) <= 8192
        preview = json.loads(serialized)['preview']
        assert preview['view'] == 'task_json' and preview['offset'] == offset
        chunks.append(preview['task_json'])
        if preview['next_offset'] is None:
            break
        assert preview['next_offset'] == offset + len(chunks[-1])
        offset = preview['next_offset']
    else:
        raise AssertionError('task detail did not reach the end')
    assert len(chunks) > 1
    assert json.loads(''.join(chunks)) == tasks[-1]

    edited = invoke('edit_preview', {**plan_args, 'ops': [{'task_id': tasks[-1]['task_id'], 'select': True}]})
    assert not edited['isError'], edited
    assert invoke('read_preview', detail_args)['structuredContent']['code'] == 'PLAN_STALE'
    detail_args['generation'] = json.loads(native_text(edited))['generation']
    assert not invoke('read_preview', detail_args)['isError']
    record, = resolve_attachments(attachment_root(store.root, project, 'actor'), [source['id']])
    path = Path(record['path'])
    before = path.read_bytes()
    path.write_bytes(bytes([before[0] ^ 1]) + before[1:])
    assert invoke('read_preview', detail_args)['structuredContent']['code'] == 'PLAN_STALE'
