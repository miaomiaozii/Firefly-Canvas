"""Real presentation routes with a representative 15-row plan journal (#506)."""
import asyncio
import json
from uuid import uuid4

import pytest

from app.services.agent_ops_delta import build_delta
from app.services.agent_ops_journal import record_sides
from app.services.agent_ops_store import append_operation
from firefly_opsdb import OpsDb
from tests.test_agent_present_406 import agent_present_env, agent_present_events, agent_present_mcp, present


CAP = 65_536


def json_size(value):
    return len(json.dumps(value, ensure_ascii=False).encode('utf-8'))


def record_plan_diff(env, prompt_chars):
    db = OpsDb(env.store.root)
    nodes, edges = [], []
    for i in range(15):
        task = f'task-{i:032x}'
        prompt_id, image_id = f'{task}-prompt', f'{task}-image'
        text = f'{i}: ' + '构图' * prompt_chars
        common = {'excel_task_id': task, 'excel_section': f'产品 {i % 2}',
                  'excel_task_data': {'task_id': task, 'base_prompt': text, 'aspect_ratio': '1:1'},
                  'planLineage': {'source_row': i + 2, 'source_sha256': 'f' * 64}}
        nodes += [{'id': prompt_id, 'type': 'smart-prompt', 'title': f'商品 {i + 1}', 'text': text, **common},
                  {'id': image_id, 'type': 'smart-image', 'title': f'生成 {i + 1}', **common}]
        edges.append({'id': f'{prompt_id}-{image_id}', 'from': prompt_id, 'to': image_id, 'kind': 'input'})
    frames = [{'id': f'frame-{s}', 'type': 'smart-group',
               'items': [n['id'] for n in nodes if n['excel_section'] == f'产品 {s}']} for s in range(2)]
    nodes.extend(frames)
    original = env.store.canvas(env.project)
    delta = build_delta(original, {**original, 'nodes': original['nodes'] + nodes, 'connections': edges})
    before, after = record_sides(delta)
    operation = {'op_id': uuid4().hex, 'turn_id': env.session._current_turn_id, 'agent_node_id': 'actor',
                 'tool': 'plan.apply', 'args': {}, 'before': before, 'after': after,
                 'touched_node_ids': [n['id'] for n in nodes], 'created_at': '2026-01-01T00:00:00Z',
                 'irreversible_effects': [], 'undone': False}
    append_operation(db, env.project, operation)
    return operation, delta


@pytest.mark.parametrize('transport', ['rest', 'mcp'])
@pytest.mark.parametrize('detail,prompt_chars', [('merged', 138), ('summary', 1200)])
def test_large_plan_diff_is_complete_or_explicitly_summarized(env, transport, detail, prompt_chars):
    async def scenario():
        await env.start()
        try:
            original = env.store.canvas(env.project)
            record, delta = record_plan_diff(env, prompt_chars)
            show = {'action': 'show', 'kind': 'diff', 'title': '15 项原方案差异', 'op_id': record['op_id']}
            meta = {k: record[k] for k in ('op_id', 'turn_id', 'tool', 'undone')}
            assert json_size({'source': 'operation', **meta, 'before': record['before'], 'after': record['after']}) > CAP
            merged = {'source': 'operation', **meta, 'detail': 'merged', 'changes': delta}
            assert (json_size(merged) <= CAP) == (detail == 'merged')

            async def call(args):
                if transport == 'mcp':
                    reply = await agent_present_mcp(env, args, caller='hosted')
                    assert 'presentation' in reply, reply
                    return reply['presentation']
                reply = await present(env, args)
                assert reply.status_code == 200, reply.text
                return reply.json()['presentation']

            card = await call(show)
            data = card['data']
            assert data['detail'] == detail and data['op_id'] == record['op_id'] and data['undone'] is False
            assert json_size(data) <= CAP
            if detail == 'merged':
                assert data == merged
                rows = [c['after']['value']['planLineage']['source_row'] for c in data['changes']
                        if c['entity'] == 'node' and c['after']['value']['type'] == 'smart-image']
                assert sorted(rows) == list(range(2, 17))
            else:
                assert data['counts'] == {'changes': 47, 'nodes': 32, 'connections': 15, 'media': 0}
                assert 'changes' not in data and 'before' not in data and 'after' not in data
            assert await call({'action': 'get', 'presentation_id': card['id']}) == card
            live = [e for e in env.live if e['type'] == 'diff']
            assert len(live) == 1 and live[0]['presentation'] == card
            for endpoint in ('events', 'events/page'):
                assert [r['event'] for r in await agent_present_events(env, endpoint) if r['event']['type'] == 'diff'] == live
            denied = await agent_present_mcp(env, show)
            assert denied['isError']
            assert env.store.canvas(env.project) == original and not env.session.confirm_handlers
            ops = await env.client.get(f'/api/projects/{env.project}/ops')
            assert ops.status_code == 200 and ops.json()['operations'] == [record]
        finally:
            await env.client.aclose()

    asyncio.run(scenario())


def test_large_prompt_preview_summary_is_not_applied_or_consumed(env):
    async def scenario():
        await env.start()
        try:
            canvas = env.store.canvas(env.project)
            old = '原' * 11_000
            env.store.apply_mutations(env.project, [{'op': 'upsert_node', 'node': {**n, 'text': old}}
                                                     for n in canvas['nodes'] if n['id'] == 'text'],
                                      base_revision=canvas['revision'])
            canvas = env.store.canvas(env.project)
            preview = await env.client.post(f'/api/projects/{env.project}/canvas/batch_prompt_edit?agent_node_id=actor',
                                           json={'apply': False, 'edits': [{'node_id': 'text', 'prompt': '新' * 11_000}]})
            assert preview.status_code == 200, preview.text
            diff_id = preview.json()['diff_id']
            show = {'action': 'show', 'kind': 'diff', 'title': '长提示词预览', 'diff_id': diff_id}
            for _ in range(2):
                reply = await present(env, show)
                assert reply.status_code == 200, reply.text
                assert reply.json()['presentation']['data'] == {
                    'source': 'preview', 'detail': 'summary', 'diff_id': diff_id, 'applied': False,
                    'counts': {'edits': 1},
                }
            assert env.store.canvas(env.project) == canvas and not env.session.confirm_handlers
        finally:
            await env.client.aclose()

    asyncio.run(scenario())
