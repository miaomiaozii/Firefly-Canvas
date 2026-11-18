"""#432: legacy replay must reject path components, on Windows and elsewhere."""
import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import main
from app.services.agent_host import InvalidEventsCursor, agent_host_manager


@pytest.fixture
def isolated_events(tmp_path):
    project = tmp_path / 'e2e-tabbit-432-project'
    events = project / 'agent-events'
    events.mkdir(parents=True)
    safe = {'seq': 3, 'host': 'pi', 'event': {'type': 'user_prompt', 'text': 'safe'}}
    outside = {'seq': 4, 'host': 'pi', 'event': {'type': 'user_prompt', 'text': 'synthetic-outside'}}
    (events / 'node.jsonl').write_text(json.dumps(safe) + '\n', encoding='utf-8')
    (project / 'outside.jsonl').write_text(json.dumps(outside) + '\n', encoding='utf-8')
    with patch.object(agent_host_manager, '_get_project_dir', return_value=str(project)):
        yield TestClient(main.app, base_url='http://127.0.0.1:3177')


@pytest.mark.parametrize('project,node', [
    ('p', r'..\outside'), ('p', r'C:\outside'), ('p', '../outside'),
    ('p', '..'), ('p', ''), (r'..\outside', 'node'), ('../outside', 'node'),
    ('..', 'node'), ('p', 'a:b'), ('p', 'bad\0name'),
])
def test_legacy_service_rejects_path_components(isolated_events, project, node):
    with pytest.raises(InvalidEventsCursor, match='invalid project or node identifier'):
        agent_host_manager.load_events(project, node, host='pi')


@pytest.mark.parametrize('url', [
    '/api/projects/p/agent-nodes/..%5Coutside/events',
    '/api/projects/p/agent-nodes/C:%5Coutside/events',
    '/api/projects/..%5Coutside/agent-nodes/node/events',
])
@pytest.mark.parametrize('host', ['pi', None])
def test_legacy_http_rejects_before_node_lookup(isolated_events, url, host):
    params = {'host': host} if host else {}
    with patch('app.routers.api_agent_nodes._find_node', side_effect=AssertionError('node lookup must not occur')):
        response = isolated_events.get(url, params=params)
    assert response.status_code == 400
    assert response.json()['detail'] == 'invalid project or node identifier'


def test_legacy_valid_history_preserves_host_since_and_cursor(isolated_events):
    response = isolated_events.get('/api/projects/p/agent-nodes/node/events', params={'host': 'pi', 'since': 2})
    assert response.status_code == 200
    result = response.json()
    assert result['last_seq'] == 3
    assert [event['seq'] for event in result['events']] == [3]
    assert result['events'][0]['event']['text'] == 'safe'
    assert isolated_events.get('/api/projects/p/agent-nodes/node/events', params={'host': 'pi', 'since': 3}).json() == {'events': [], 'last_seq': 3}
