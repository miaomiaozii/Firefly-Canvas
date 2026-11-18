"""Opt-in bounded event history; no real projects or running server required."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import main
from app.services import agent_host as ah
from app.services.agent_host import agent_host_manager


@pytest.fixture
def history(tmp_path):
    path = tmp_path / 'agent-events' / 'node.jsonl'
    path.parent.mkdir()
    with patch.object(agent_host_manager, '_get_project_dir', return_value=str(tmp_path)):
        with TestClient(main.app, base_url='http://127.0.0.1:8125') as client:
            yield path, client


def append(path, *records):
    with path.open('a', encoding='utf-8', newline='\n') as f:
        for seq, host, kind in records:
            rec = {'seq': seq, 'event': {'type': kind, 'text': f'消息{seq}'}}
            if host is not None:
                rec['host'] = host
            f.write(json.dumps(rec, ensure_ascii=False) + '\n')


def get(client, **params):
    return client.get('/api/projects/p/agent-nodes/node/events/page', params=params)


def test_legacy_unchanged_and_pages_complete(history):
    path, client = history
    append(path, *[(i, None, 'user_prompt' if i % 3 == 1 else 'message_update') for i in range(1, 19)])
    legacy = client.get('/api/projects/p/agent-nodes/node/events', params={'host': 'pi', 'since': 3})
    assert legacy.status_code == 200
    assert [x['seq'] for x in legacy.json()['events']] == list(range(4, 19))
    pages = []
    cursor = None
    while True:
        response = get(client, host='pi', turns=2, **({'cursor': cursor} if cursor else {}))
        assert response.status_code == 200
        data = response.json()
        assert data['last_seq'] == 18
        pages.append(data['events'])
        assert data['has_more'] == bool(data['cursor'])
        if not data['has_more']:
            break
        cursor = data['cursor']
    assert [[r['seq'] for r in page] for page in pages] == [list(range(13, 19)), list(range(7, 13)), list(range(1, 7))]
    assert pages[0][0] == legacy.json()['events'][9]


def test_mixed_hosts_snapshot_and_append(history):
    path, client = history
    append(path, (1, None, 'user_prompt'), (2, 'codex', 'user_prompt'),
           (3, 'pi', 'message_update'), (4, 'pi', 'user_prompt'),
           (5, 'codex', 'message_update'), (6, 'pi', 'message_update'))
    first = get(client, host='pi', turns=1).json()
    assert [r['seq'] for r in first['events']] == [4, 6]
    assert first['last_seq'] == 6 and first['has_more']
    append(path, (7, 'codex', 'user_prompt'), (8, 'pi', 'user_prompt'))
    second = get(client, host='pi', turns=1, cursor=first['cursor']).json()
    assert [r['seq'] for r in second['events']] == [1, 3]
    assert second['last_seq'] == 6 and not second['has_more']
    assert get(client, host='codex', cursor=first['cursor']).status_code == 400
    absent = get(client, host='codex', turns=2).json()
    assert absent['last_seq'] == 8
    assert get(client, host='pi', cursor='not-a-cursor').status_code == 400


def test_missing_invalid_and_stale(history):
    path, client = history
    assert get(client, host='pi').json() == {'events': [], 'last_seq': 0, 'has_more': False, 'cursor': None}
    assert get(client, host='bad').status_code == 400
    assert get(client, host='pi', turns=0).status_code in (400, 422)
    assert get(client, host='pi', turns=51).status_code in (400, 422)
    assert get(client, host='pi', cursor='!!!').status_code == 400
    append(path, (1, 'pi', 'user_prompt'), (2, 'pi', 'user_prompt'))
    cursor = get(client, host='pi', turns=1).json()['cursor']
    path.unlink()
    assert get(client, host='pi', cursor=cursor).status_code == 409
    append(path, (1, 'pi', 'user_prompt'), (2, 'pi', 'user_prompt'))
    cursor = get(client, host='pi', turns=1).json()['cursor']
    replacement = path.with_suffix('.tmp')
    replacement.write_bytes(path.read_bytes())
    replacement.replace(path)
    assert get(client, host='pi', cursor=cursor).status_code == 409
    cursor = get(client, host='pi', turns=1).json()['cursor']
    path.write_bytes(b'')
    assert get(client, host='pi', cursor=cursor).status_code == 409


def test_corrupt_partial_and_host_absent(history):
    path, client = history
    append(path, (1, 'codex', 'user_prompt'), (2, 'codex', 'message_update'))
    empty = get(client, host='pi').json()
    assert empty == {'events': [], 'last_seq': 2, 'has_more': False, 'cursor': None}
    with path.open('ab') as f:
        f.write(b'{"seq":3')
    assert get(client, host='pi').status_code >= 400
    path.write_bytes(b'{broken}\n')
    assert get(client, host='codex').status_code >= 400


def test_large_turn_and_retained_orphan(history):
    path, client = history
    append(path, (1, 'pi', 'message_update'), (2, 'pi', 'user_prompt'))
    with path.open('a', encoding='utf-8') as f:
        f.write(json.dumps({'seq': 3, 'host': 'pi', 'event': {'type': 'message_update', 'text': '汉' * 30000}}, ensure_ascii=False) + '\n')
    page = get(client, host='pi', turns=1).json()
    assert [r['seq'] for r in page['events']] == [1, 2, 3]
    assert not page['has_more']


def test_short_tail_does_not_parse_whole_log(history):
    path, _ = history
    append(path, *[(i, 'pi', 'user_prompt' if i % 2 else 'message_update')
                   for i in range(1, 5001)])
    original_loads = json.loads
    count = 0

    def counted(*args, **kwargs):
        nonlocal count
        count += 1
        return original_loads(*args, **kwargs)

    with patch.object(ah.json, 'loads', side_effect=counted):
        result = agent_host_manager.load_events_page('p', 'node', 'pi', turns=2)
    assert [r['seq'] for r in result['events']] == [4997, 4998, 4999, 5000]
    assert result['has_more'] and count < 20


@pytest.mark.parametrize('project_id,node_id', [
    ('p', r'..\..\secret'), ('p', r'C:\secret'),
    (r'..\outside', 'node'), ('..', 'node'), ('p', '../secret'),
])
def test_page_rejects_path_components(history, project_id, node_id):
    with pytest.raises(ah.InvalidEventsCursor):
        agent_host_manager.load_events_page(project_id, node_id, 'pi')


def test_page_http_rejects_windows_separators(history):
    _, client = history
    response = client.get('/api/projects/p/agent-nodes/..%5Csecret/events/page', params={'host': 'pi'})
    assert response.status_code == 400


def test_same_file_rewrite_is_stale_and_corruption_in_older_page(history):
    path, client = history
    append(path, (1, 'pi', 'user_prompt'), (2, 'pi', 'user_prompt'))
    cursor = get(client, host='pi', turns=1).json()['cursor']
    data = path.read_bytes().replace(b'\xe6\xb6\x88\xe6\x81\xaf1', b'\xe6\xb6\x88\xe6\x81\xaf9')
    path.write_bytes(data)
    assert get(client, host='pi', cursor=cursor).status_code == 409
    path.write_bytes(b'{broken}\n' + data[data.find(b'\n') + 1:])
    assert get(client, host='pi', turns=1).status_code == 500
