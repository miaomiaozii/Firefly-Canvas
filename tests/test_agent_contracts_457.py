"""Target projection and logical-turn contracts for live and retained events."""
from copy import deepcopy
import asyncio
import json

import pytest

from app.services.agent_confirmation import target_node_ids
from app.services.agent_host import AgentHostManager
from tests.test_agent_confirmation_host_402 import host
from tests.test_agent_batch_400 import fx, confirm


@pytest.mark.parametrize('params, expected', [
    ({'node_id': 'image'}, ['image']),
    ({'node_ids': ['b', 'a', 'b']}, ['b', 'a']),
    ({'node_id': 'acting-agent', 'preflight': {'items': [{'node_id': 'b'}, {'node_id': 'a'}, {'node_id': 'b'}]}}, ['b', 'a']),
    ({'node_id': 'acting-agent', 'preflight': {'node_id': 'image', 'refs': [{'node_id': 'upstream'}]}}, ['image']),
    ({'preflight': [{'node_id': 'b'}, {'node_id': 'a'}]}, ['b', 'a']),
    ({'nodes': [{'id': 'manual'}, {'id': 'manual'}], 'group_id': 'unrelated'}, ['manual']),
    ({'target_node_ids': ['b', 'b', 'a'], 'node_id': 'acting-agent'}, ['acting-agent']),
    ({'from_node': 'a', 'to_node': 'b'}, ['a', 'b']),
    ({'asset_id': 'media'}, []),
    ({'node_count': 4}, []),
])
def test_actual_target_projection_is_stable_and_never_uses_carrier(params, expected):
    assert target_node_ids(params) == expected


@pytest.mark.parametrize('tool', ['edit_rotate', 'edit_erase'])
@pytest.mark.parametrize('transport', ['rest', 'mcp'])
def test_client_cannot_spoof_confirm_targets_with_metadata_or_preflight(fx, monkeypatch, tool, transport):
    from app.routers import api_agent_tools, api_mcp
    monkeypatch.setattr(api_agent_tools, 'AGENT_WRITE_CONFIRM_TIMEOUT_SECONDS', 0)
    monkeypatch.setattr(api_mcp, 'CONFIRM_TIMEOUT_SECONDS', 0)
    args = {'node_id': 'a', 'angle': 90, 'rect': [0, 0, 1, 1], 'prompt': 'remove selected mark',
            'target_node_ids': ['b'], 'preflight': {'node_id': 'b'}, 'node_ids': ['b']}
    if transport == 'rest':
        response = fx.client.post(f'/api/projects/{fx.pid}/canvas/{tool}?agent_node_id=actor',
            headers={'X-Agent-Project': fx.pid}, json=args)
        receipt = response.json()
    else:
        response = fx.client.post(f'/api/mcp?project={fx.pid}&node=actor',
            headers={'Mcp-Session-Id': 'spoof-target'}, json={'jsonrpc': '2.0', 'id': 1,
            'method': 'tools/call', 'params': {'name': f'canvas.{tool}', 'arguments': args}})
        result = response.json()['result']
        assert not result['isError'], result
        receipt = json.loads(result['content'][0]['text'])
    assert response.status_code == 200, response.text
    assert receipt['code'] == 'CONFIRM_PENDING'
    event = fx.events.get(timeout=2)
    assert event['type'] == 'confirm' and event['target_node_ids'] == ['a']
    confirm(fx, event, approved=False)
    resolved = fx.events.get(timeout=2)
    assert resolved['type'] == 'confirm_resolved' and resolved['target_node_ids'] == ['a']
    assert fx.calls.empty()


def test_turn_ids_persist_through_system_continuation_then_next_user(host, tmp_path, monkeypatch):
    (tmp_path / 'project.json').write_text('{}', encoding='utf-8')
    monkeypatch.setattr(host.manager, '_get_project_dir', lambda project: str(tmp_path))

    def persist(session, event, **kwargs):
        saved = AgentHostManager._persist_event(host.manager, session, event, **kwargs)
        host.events.append(deepcopy(event))
        return saved

    monkeypatch.setattr(host.manager, '_persist_event', persist)

    async def scenario():
        m, s = host.manager, host.session
        await m.send_prompt(s, 'real user one', request_id='one')
        original = s._current_turn_id
        await host.continue_with('confirmation result')
        await m.send_follow_up(s, 'real user two', request_id='two')
        await host.finish()
        assert s._current_turn_id == original
        await host.finish()
        second = s._current_turn_id
        assert second not in (None, original)
        # The original event object must remain the old turn even after FIFO advances.
        assert [event['turn_id'] for event in host.live if event['type'] == 'agent_end'] == [original, original]
        await host.finish()
        replay = m.load_events('project', 'actor', host=s.agent_cli)['events']
        paged = m.load_events_page('project', 'actor', s.agent_cli, turns=5)['events']
        assert [record['event'] for record in paged] == [record['event'] for record in replay]
        assert [record['event']['turn_id'] for record in replay if record['event']['type'] == 'agent_end'] == [original, original, second]
        prompts = [record['event'] for record in replay if record['event']['type'] == 'user_prompt']
        assert [(event['text'], event['turn_id']) for event in prompts] == [('real user one', original), ('real user two', second)]
        assert [event for event in host.live if event['type'] == 'agent_end'] == [
            record['event'] for record in replay if record['event']['type'] == 'agent_end']
        # An unattributed terminal frame must not borrow the previous completed turn.
        await m._dispatch_event(s, {'type': 'agent_end', 'turn_id': 'native-forged'})
        assert host.live[-1]['turn_id'] is None
    asyncio.run(scenario())
