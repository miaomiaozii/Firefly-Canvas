"""Synthetic workbooks, submitted attachments, and actual plan transports/confirmation/undo."""
import base64
from io import BytesIO
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock
from xml.sax.saxutils import escape
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import main
from app.project_sessions import register_project_session, unregister_project_session
from app.routers import api_agent_nodes, api_agent_tools
from app.services.agent_host import AgentHostManager
from excel_importer import NS_MAIN, NS_PKG_REL, NS_REL, NS_XDR, NS_A
from firefly_projects import ProjectStore, RevisionConflict


SHEET = '图片方案'


def png(color='red'):
    buffer = BytesIO()
    Image.new('RGB', (8, 8), color).save(buffer, 'PNG')
    return buffer.getvalue()


def workbook(path):
    rows = {
        1: ['名称', '设计需求', '文案', '参考图', '提示词'],
        3: ['主图1', '白底拍摄', 'Overlay only', path, '=FORMULA'],
        5: ['主图2', '场景共4张图', '', ''],
        6: ['主图3', '老图不用重做', '', ''],
        7: ['主图视频', '测试视频', '', ''],
        8: ['产品蒙版', '不要创建节点', '', ''],
        9: ['高级A+方案'],
        10: ['A+ 首图', '同主图1，背景更宽', '', ''],
        11: ['', '', '', ''],
    }
    data = []
    for row, cells in rows.items():
        xml = ''.join(f'<c r="{chr(65+i)}{row}" t="inlineStr"><is><t>{escape(cell)}</t></is></c>'
                      for i, cell in enumerate(cells) if cell != '=FORMULA')
        if row == 3:
            xml += '<c r="E3" t="str"><f>[external.xlsx]Sheet!A1</f><v>CACHED_FORMULA</v></c>'
        data.append(f'<row r="{row}">{xml}</row>')
    buffer = BytesIO()
    with ZipFile(buffer, 'w') as zipfile:
        zipfile.writestr('xl/workbook.xml', f'<workbook xmlns="{NS_MAIN}" xmlns:r="{NS_REL}"><sheets>'
                         f'<sheet name="{SHEET}" r:id="rId1"/></sheets></workbook>')
        zipfile.writestr('xl/_rels/workbook.xml.rels', f'<Relationships xmlns="{NS_PKG_REL}">'
                         '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
        zipfile.writestr('xl/worksheets/sheet1.xml', f'<worksheet xmlns="{NS_MAIN}"><sheetData>'
                         + ''.join(data) + '</sheetData></worksheet>')
        zipfile.writestr('xl/worksheets/_rels/sheet1.xml.rels', f'<Relationships xmlns="{NS_PKG_REL}">'
                         '<Relationship Id="drawing1" Target="../drawings/drawing1.xml"/></Relationships>')
        anchors = ''.join(f'<xdr:oneCellAnchor><xdr:from><xdr:row>{r}</xdr:row><xdr:col>3</xdr:col></xdr:from>'
                          '<a:blip r:embed="img"/></xdr:oneCellAnchor>' for r in [2, 10, 99])
        zipfile.writestr('xl/drawings/drawing1.xml', f'<xdr:wsDr xmlns:xdr="{NS_XDR}" xmlns:a="{NS_A}" '
                         f'xmlns:r="{NS_REL}">{anchors}</xdr:wsDr>')
        zipfile.writestr('xl/drawings/_rels/drawing1.xml.rels', f'<Relationships xmlns="{NS_PKG_REL}">'
                         '<Relationship Id="img" Target="../media/img.png"/></Relationships>')
        zipfile.writestr('xl/media/img.png', png('blue'))
    return buffer.getvalue()


@pytest.fixture
def env(tmp_path, monkeypatch):
    store = ProjectStore(Path(os.environ['FIREFLY_PROJECTS_ROOT']) / tmp_path.name, snapshot_delay=0)
    host = AgentHostManager()

    async def spawn(session, **kwargs):
        session.status = 'running'
        return True

    commands = AsyncMock()
    monkeypatch.setattr(host, 'spawn_session', spawn)
    monkeypatch.setattr(host, 'send_command', commands)
    monkeypatch.setattr(main, 'FIREFLY_PROJECT_STORE', store)
    monkeypatch.setattr('app.services.agent_host.agent_host_manager', host)
    monkeypatch.setattr(api_agent_nodes, 'agent_host_manager', host)
    projects = [store.create_project('synthetic plan')['id'] for _ in range(2)]
    tabs = [object(), object()]
    for project, tab in zip(projects, tabs):
        register_project_session(tab, project)
        store.apply_mutations(project, [{'op': 'upsert_node', 'node': {
            'id': node, 'type': 'smart-agent', 'autonomy': 'autonomous'}} for node in ('actor', 'other')], base_revision=0)
        store.flush(project)
    try:
        with TestClient(main.app, base_url='http://127.0.0.1:3198') as client:
            yield client, store, host, projects
    finally:
        for tab in tabs:
            unregister_project_session(tab)
        store.close()


def upload(client, project, name, content, *, actor='actor', mime='application/octet-stream'):
    response = client.post(f'/api/projects/{project}/agent-nodes/{actor}/attachments', content=content,
                           headers={'X-Attachment-Name': name, 'Content-Type': mime})
    assert response.status_code == 200, response.text
    return response.json()


def submit(client, project, *attachments, actor='actor'):
    response = client.post(f'/api/projects/{project}/agent-nodes/{actor}/prompt', json={
        'message': 'synthetic input', 'attachments': [record['id'] for record in attachments],
    })
    assert response.status_code == 200, response.text


def tool(client, project, operation, args, *, mcp=False, actor='actor', sid='plan-test', header=None):
    namespace, _, name = operation.partition('.')
    if mcp:
        response = client.post(f'/api/mcp?project={project}&node={actor}', json={
            'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
            'params': {'name': operation, 'arguments': args},
        }, headers={'Mcp-Session-Id': sid})
        result = response.json()['result']
        return result['structuredContent'] if result['isError'] else json.loads(result['content'][0]['text'])
    response = client.post(f'/api/projects/{project}/{namespace}/{name}?agent_node_id={actor}',
                           headers={'X-Agent-Project': header or project}, json=args)
    return response.json()['detail'] if response.is_error else response.json()


def confirmation(client, host, project, approved=True, inject=None):
    seen = []

    async def subscribe():
        session = await host.get_or_create_session(project, 'actor')
        session.autonomy = 'autonomous'

        async def observe(_, event):
            if event['type'] != 'confirm':
                return
            seen.append(event)
            if inject and event['tool'] == 'plan.apply':
                await inject()
            for handler in list(session.confirm_handlers):
                await handler(event['confirm_id'], approved)

        session.event_handlers.append(observe)

    client.portal.call(subscribe)
    return seen


@pytest.mark.parametrize('mcp', [False, True])
def test_preview_and_apply_journal_undo(env, tmp_path, monkeypatch, mcp):
    client, store, host, (project, _) = env
    sentinel = str(tmp_path / 'outside-reference.png')
    Path(sentinel).write_bytes(png())
    original_exists = os.path.exists

    def guarded_exists(path):
        assert os.fspath(path) != sentinel, 'workbook path was probed'
        return original_exists(path)

    monkeypatch.setattr(os.path, 'exists', guarded_exists)
    source = upload(client, project, 'plan.xlsx', workbook(sentinel))
    image = upload(client, project, 'reference.png', png(), mime='image/png')
    unarchived = upload(client, project, 'unarchived.png', png('green'), mime='image/png')
    submit(client, project, source, image, unarchived)
    before = store.canvas(project)
    seen = confirmation(client, host, project)
    invoke = lambda name, args: tool(client, project, name, args, mcp=mcp)
    draft = invoke('plan.import_excel', {'attachment_id': source['id']})
    assert draft == invoke('plan.import_excel', {'attachment_id': source['id']})
    preview = draft['preview']
    assert preview['sections'] == {'主图方案': 2, '高级A+方案': 1}
    assert {item['skip_reason'] for item in preview['excluded']} == {'no_remake', 'video', 'mask'}
    assert {'ANCHOR_OUT_OF_RANGE', 'CONTINUATION_OR_NON_TASK_ROW', 'NO_WORKBOOK_IMAGE'} <= {
        item['code'] for item in preview['warnings']}
    assert preview['suggested_refs'] == []
    tasks = preview['tasks']
    assert all(not task['selected'] and task['refs'] == [] for task in tasks)
    assert not any('split_from_row' in task for task in tasks)
    assert tasks[-1]['linked_task_id'] == tasks[0]['task_id']
    first, second = [t['task_id'] for t in tasks[:2]]
    assert tasks[0]['base_prompt'] == '白底拍摄' and tasks[0]['overlay_plan'] == 'Overlay only'
    assert tasks[0]['source_row'] == f'{SHEET}!3' and tasks[0]['suggested_refs'][0]['kind'] == 'workbook_image'
    assert 'CACHED_FORMULA' not in str(preview) and sentinel not in str(preview)
    args = {'plan_id': draft['plan_id'], 'generation': draft['generation']}
    assert invoke('plan.apply', args)['op_id'] is None
    assert not seen and store.canvas(project) == before and store.list_media(project) == []

    def edit(*ops, **override):
        return invoke('plan.edit_preview', {**args, **override, 'ops': ops})

    assert edit({'task_id': second, 'split': 4})['code'] == 'INVALID_ARGUMENT'
    assert edit({'task_id': first, 'set_refs': [image['id']]})['code'] == 'INVALID_ARGUMENT'
    assert edit({'task_id': first, 'select': True}, {'task_id': 'nope', 'rename': 'bad'})['code'] == 'TASK_NOT_FOUND'
    assert invoke('plan.apply', args)['tasks'] == []
    draft = edit({'task_id': first, 'select': True}, {'task_id': first, 'rename': 'Approved name'},
                 {'task_id': second, 'set_section': '新增'})
    assert invoke('plan.apply', args)['code'] == 'PLAN_STALE'
    args['generation'] = draft['generation']
    with monkeypatch.context() as patch:
        normal = store.apply_mutations
        calls = []

        def count(*a, **kw):
            calls.append(kw['base_revision'])
            return normal(*a, **kw)

        patch.setattr(store, 'apply_mutations', count)
        result = invoke('plan.apply', args)
        assert calls == [before['revision']]
    assert seen[-1]['params']['tasks'] == [{'task_id': first, 'display_no': '主图 1', 'section': '主图方案',
                                            'name': 'Approved name', 'refs': []}]
    assert 'Approved name' in seen[-1]['message']
    nodes = {n['id']: n for n in store.canvas(project)['nodes']}
    first_task, = result['tasks']
    assert nodes[first_task['prompt_node']]['overlay_plan'] == 'Overlay only'
    assert nodes[first_task['gen_node']]['runSettings']['prompt'] == ''
    assert nodes[first_task['gen_node']]['runSettings']['count'] == 1
    assert next(edge for edge in store.canvas(project)['connections']
                if edge['from'] == first_task['prompt_node'] and edge['to'] == first_task['gen_node'])['kind'] == 'input'
    assert nodes[first_task['prompt_node']]['planLineage']['source_attachment_id'] == source['id']
    assert first_task['ref_nodes'] == []
    assert nodes[result['groups'][0]['group_id']]['layoutFrame'] is True
    assert invoke('plan.apply', args)['already_applied'] == [first]
    later = invoke('plan.apply', {**args, 'task_ids': [first, second]})
    assert [item['task_id'] for item in later['tasks']] == [second]
    assert [item['section'] for item in later['groups']] == ['新增']
    assert store.list_media(project) == []
    for transaction in (later, result):
        response = client.post(f'/api/projects/{project}/ops/undo', json={'op_id': transaction['op_id']},
                               headers={'X-Agent-Project': project})
        assert response.status_code == 200, response.text
        assert response.json()['reverted'] == [transaction['op_id']]
    assert store.canvas(project)['nodes'] == before['nodes']
    assert store.canvas(project)['connections'] == before['connections']
    assert store.list_media(project) == []


@pytest.mark.parametrize('mcp', [False, True])
def test_only_submitted_attachment_and_scoped_preview(env, mcp):
    client, store, host, (project, foreign) = env
    source = upload(client, project, 'local.xlsx', workbook(''))
    other = upload(client, project, 'other.xlsx', workbook(''), actor='other')
    abroad = upload(client, foreign, 'foreign.xlsx', workbook(''))
    submit(client, project, other, actor='other')
    submit(client, foreign, abroad)
    submit(client, project)
    before = store.canvas(project)
    session = host.conversation_session(project, 'actor')
    client.portal.call(host._persist_event, session, {'type': 'user_prompt', 'accepted_input': True, 'attachments': [source]})
    for args in [{}, {'file_path': source['path']}, {'attachment_id': source['path']},
                 {'attachment_id': source['id']}, {'attachment_id': abroad['id']}, {'attachment_id': other['id']}]:
        assert tool(client, project, 'plan.import_excel', args, mcp=mcp)['code'] == 'FILE_NOT_ALLOWED'
    client.portal.call(host._dispatch_event, session, {'type': 'agent_end'})
    submit(client, project, source)
    assert tool(client, project, 'plan.import_excel', {'attachment_id': source['id'], 'sheet': 'bad'},
                mcp=mcp)['details']['sheets'] == [SHEET]
    draft = tool(client, project, 'plan.import_excel', {'attachment_id': source['id']}, mcp=mcp)
    args = {'plan_id': draft['plan_id']}
    assert tool(client, foreign, 'plan.apply', args, mcp=mcp)['code'] == 'PLAN_STALE'
    assert tool(client, project, 'plan.apply', args, mcp=mcp, actor='other')['code'] == 'PLAN_STALE'
    if mcp:
        assert tool(client, project, 'plan.apply', args, mcp=True, sid='unrelated')['code'] == 'PLAN_STALE'
    else:
        assert tool(client, project, 'plan.apply', args, header=foreign)['code'] == 'PLAN_STALE'
    session.session_file = 'new-branch'
    assert tool(client, project, 'plan.apply', args, mcp=mcp)['code'] == 'PLAN_STALE'
    assert tool(client, project, 'plan.import_excel', {'attachment_id': source['id']}, mcp=mcp)['code'] == 'FILE_NOT_ALLOWED'
    assert store.canvas(project) == before and not store.list_media(project)


@pytest.mark.parametrize('failure', ['reject', 'source', 'revision', 'preview', 'announcement', 'cas'])
def test_confirmation_recheck_writes_no_plan(env, monkeypatch, failure):
    client, store, host, (project, _) = env
    source = upload(client, project, 'plan.xlsx', workbook(''))
    image = upload(client, project, 'reference.png', png(), mime='image/png')
    submit(client, project, source, image)
    draft = tool(client, project, 'plan.import_excel', {'attachment_id': source['id']})
    first = draft['preview']['tasks'][0]['task_id']
    args = {'plan_id': draft['plan_id'], 'task_ids': [first]}
    before = store.canvas(project)
    media_before = store.list_media(project)
    disk_canvas = store.root / project / 'canvas.json'
    original = disk_canvas.read_bytes()
    normal_broadcast = api_agent_tools._broadcast_canvas_updated

    async def mutate_during_announcement(pid, *a, **kw):
        if kw.get('phase') == 'start':
            Path(source['path']).write_bytes(b'x' * source['size'])
        return await normal_broadcast(pid, *a, **kw)

    if failure == 'announcement':
        monkeypatch.setattr(api_agent_tools, '_broadcast_canvas_updated', mutate_during_announcement)
    attempts = []

    if failure == 'cas':
        def conflict(*a, **kw):
            attempts.append(kw['base_revision'])
            raise RevisionConflict('synthetic conflict')
        monkeypatch.setattr(store, 'apply_mutations', conflict)

    async def inject():
        if failure == 'source':
            Path(source['path']).write_bytes(b'x' * source['size'])
        elif failure == 'revision':
            store.apply_mutations(project, [{'op': 'upsert_node', 'node': {'id': 'human', 'type': 'smart-prompt'}}],
                                  base_revision=before['revision'])
            store.flush(project)
        elif failure == 'preview':
            await _edit()

    async def _edit():
        from app.routers.api_agent_plan import plan_core
        return await plan_core(project, 'actor', {'plan_id': draft['plan_id'], 'ops': [
            {'task_id': first, 'rename': 'new preview'},
        ]}, tool='plan.edit_preview')

    seen = confirmation(client, host, project, failure != 'reject', inject)
    result = tool(client, project, 'plan.apply', args)
    assert len(seen) == 1
    assert result['code'] == ('PLAN_NOT_CONFIRMED' if failure == 'reject' else 'PLAN_STALE'), result
    assert not any(n.get('planLineage') for n in store.canvas(project)['nodes'])
    if failure != 'revision':
        assert store.canvas(project) == before and disk_canvas.read_bytes() == original
    assert store.list_media(project) == media_before
    if failure == 'cas':
        assert attempts == [before['revision']]


def test_codex_first_input_belongs_to_its_resumed_thread(env):
    from app.services.agent_plan import submitted

    client, _, host, (project, _) = env
    source = upload(client, project, 'plan.xlsx', workbook(''))

    async def record_turn():
        session = await host.get_or_create_session(project, 'actor')
        session.agent_cli = 'codex'
        session._generation = 1
        host._persist_event(session, {'type': 'user_prompt', 'attachments': [source]}, accepted_input=True)
        session.ext_session_id = 'thread-1'
        host._persist_event(session, {'type': 'input_branch_bound'}, input_binding=True)
        session._generation = 2
        assert source['id'] in submitted(host, project, 'actor', 'codex', 'thread-1')
        session.ext_session_id = 'thread-2'
        assert source['id'] not in submitted(host, project, 'actor', 'codex', 'thread-2')

    client.portal.call(record_turn)


def test_apply_multiple_sections_is_one_revision_and_one_undo(env):
    client, store, host, (project, _) = env
    source = upload(client, project, 'plan.xlsx', workbook(''))
    submit(client, project, source)
    before = store.canvas(project)
    draft = tool(client, project, 'plan.import_excel', {'attachment_id': source['id']})
    wanted = [draft['preview']['tasks'][index]['task_id'] for index in (0, -1)]
    seen = confirmation(client, host, project)
    result = tool(client, project, 'plan.apply', {'plan_id': draft['plan_id'], 'task_ids': wanted})
    assert [t['task_id'] for t in result['tasks']] == wanted
    assert [g['section'] for g in result['groups']] == ['主图方案', '高级A+方案']
    assert store.canvas(project)['revision'] == before['revision'] + 1
    assert len(seen) == 1 and [t['task_id'] for t in seen[0]['params']['tasks']] == wanted
    response = client.post(f'/api/projects/{project}/ops/undo', json={'op_id': result['op_id']},
                           headers={'X-Agent-Project': project})
    assert response.status_code == 200, response.text
    assert store.canvas(project)['nodes'] == before['nodes']
    assert store.canvas(project)['connections'] == before['connections']


def test_explicit_reference_archive_is_separate_and_rejectable(env):
    client, store, host, (project, _) = env
    source = upload(client, project, 'plan.xlsx', workbook(''))
    first = upload(client, project, 'first.png', png(), mime='image/png')
    same_bytes = upload(client, project, 'second.png', png(), mime='image/png')
    submit(client, project, source, first, same_bytes)
    seen = confirmation(client, host, project, False)
    result = tool(client, project, 'media.upload', {'attachment_id': first['id']})
    assert result['code'] == 'CONFIRM_REJECTED' and not store.list_media(project)
    assert len(seen) == 1 and seen[0]['params']['attachment_id'] == first['id']
    host.conversation_session(project, 'actor').event_handlers.clear()
    confirmation(client, host, project)
    assert tool(client, project, 'media.upload', {'attachment_id': first['id']})['success']
    draft = tool(client, project, 'plan.import_excel', {'attachment_id': source['id']})
    result = tool(client, project, 'plan.edit_preview', {'plan_id': draft['plan_id'], 'ops': [{
        'task_id': draft['preview']['tasks'][0]['task_id'], 'set_refs': [same_bytes['id']],
    }]})
    assert result['code'] == 'INVALID_ARGUMENT'


def test_copy_only_task_has_reviewable_nonempty_visual_prompt():
    from app.services.agent_plan import parse_workbook
    from plan_ir.agent_preview import project_workbook
    from excel_importer import XlsxParser

    parsed = XlsxParser(workbook(''), literal_only=True).parse()
    sheet = parsed['sheets'][SHEET]
    # Copy-only workbook rows still need a reviewable visual instruction (#399).
    sheet.raw_rows[sheet.row_numbers.index(3)][1] = ''
    preview = project_workbook(parsed, None)
    first = preview['tasks'][0]
    assert first['base_prompt'].strip()
    assert '主图1' in first['base_prompt']
    assert '文字' in first['base_prompt'] and '后期叠加' in first['base_prompt']
    assert first['overlay_plan'] == 'Overlay only'
    assert any(w['code'] == 'PROMPT_NEEDS_REVIEW' and w['source_row'] == first['source_row']
               for w in preview['warnings'])
    assert len(parse_workbook(workbook(''), None)['tasks']) == 3


def test_headerless_image_count_remains_one_agent_task_but_legacy_still_splits():
    from excel_importer import SheetData
    from plan_ir.agent_preview import project_workbook
    from plan_ir.task_builder import add_tasks_from_rows

    rows = [['A+ 模块1 共4张图'], ['产品四场景展示']]
    sheet = SheetData(name='A+方案', raw_rows=rows, row_numbers=[1, 2])
    preview = project_workbook({'sheets': {sheet.name: sheet}, 'cell_images': {}}, None)
    assert len(preview['tasks']) == 1
    assert preview['tasks'][0]['name'] == rows[0][0]
    legacy = []
    add_tasks_from_rows(legacy, rows, sheet.name, {}, 0, {})
    assert len(legacy) == 4


@pytest.mark.parametrize('mcp', [False, True])
def test_existing_product_is_not_implicitly_attached(env, monkeypatch, mcp):
    from layout_engine import DEFAULT_IMAGE_MODEL

    client, store, host, (project, _) = env
    media = store.import_media(project, png(), 'user-product.png', 'image/png')
    original = {'id': 'user-product', 'type': 'smart-image', 'images': [{'asset_id': media['asset_id']}]}
    store.apply_mutations(project, [{'op': 'upsert_node', 'node': original}],
                          base_revision=store.canvas(project)['revision'])
    monkeypatch.setattr(main, 'load_api_providers', lambda **kw: [{
        'id': 'offline', 'enabled': True, 'primary': True, 'protocol': 'openai',
        'base_url': 'http://127.0.0.1:1/offline', 'api_key': 'stub', 'image_models': [DEFAULT_IMAGE_MODEL],
        'image_model_capabilities': {DEFAULT_IMAGE_MODEL: {'max_references': 20, 'arbitrary_size': True}},
    }])
    provider = AsyncMock(side_effect=AssertionError('preflight must not call a provider'))
    monkeypatch.setattr(main, 'generate_ai_image', provider)
    source = upload(client, project, 'plan.xlsx', workbook(''))
    submit(client, project, source)
    draft = tool(client, project, 'plan.import_excel', {'attachment_id': source['id']}, mcp=mcp)
    confirmation(client, host, project)
    result = tool(client, project, 'plan.apply', {'plan_id': draft['plan_id'], 'task_ids': [
        task['task_id'] for task in draft['preview']['tasks']]}, mcp=mcp)
    canvas = store.canvas(project)
    assert store.find_canvas_node(project, original['id']) == original
    assert len(store.list_media(project)) == 1
    assert not any(edge['from'] == original['id'] for edge in canvas['connections'])
    assert all(not task['ref_nodes'] for task in result['tasks'])
    selections = []
    for task in result['tasks']:
        node = store.find_canvas_node(project, task['gen_node'])
        node['runSettings']['provider_id'] = 'offline'
        selections.append({'op': 'upsert_node', 'node': node})
    store.apply_mutations(project, selections, base_revision=canvas['revision'])
    preview = tool(client, project, 'generate.preflight', {'node_ids': [
        task['gen_node'] for task in result['tasks']]}, mcp=mcp)
    assert preview['total_images'] == 3
    assert all(item['final_prompt'].strip() and item['count'] == 1 and not item['refs'] for item in preview['items'])
    assert [item['code'] for item in preview['blockers']] == ['MISSING_PRODUCT_REF'] * 3
    provider.assert_not_called()


def test_legacy_expand_still_archives_embedded_reference(env):
    client, store, _, (project, _) = env
    response = client.post('/api/excel/expand-plan', json={'project_id': project, 'plan': {'tasks': [{
        'task_id': 'legacy', 'section': '主图', 'base_prompt': 'legacy prompt', 'generation_references': [{
            'asset_id': 'maps/legacy.png', 'source': 'legacy reference', 'data': base64.b64encode(png()).decode(),
        }],
    }]}})
    assert response.status_code == 200, response.text
    result = response.json()
    assert (result['nodes_created'], result['edges_created']) == (3, 2) and result['bounds']
    assert len(store.list_media(project)) == 1
    assert not any(node.get('layoutFrame') for node in store.canvas(project)['nodes'])
