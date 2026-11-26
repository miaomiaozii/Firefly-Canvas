"""Prepared batch identity and strictly scoped human approvals (#487)."""
import hashlib
import json
import re

import pytest

from app.services.agent_ops_store import list_operations
from firefly_opsdb import OpsDb
from tests.test_agent_batch_400 import fx, patch_node, rest, status_until


def test_prepared_items_publish_stable_identity_and_snapshot(fx):
    code, preview = rest(fx, 'preflight', {'node_ids': ['a', 'b']})
    assert code == 200, preview
    identities = [row.get('item_id') for row in preview['items']]
    assert all(isinstance(value, str) and value and value not in ('a', 'b') for value in identities)
    assert len(set(identities)) == 2
    assert preview['preview_revision'] == 1
    assert re.fullmatch('[a-f0-9]{64}', preview['snapshot_sha256'])
    code, receipt = rest(fx, 'request_batch', {'preflight_id': preview['preflight_id']})
    assert code == 200, receipt
    event = fx.events.get(timeout=3)
    assert event['params']['preflight']['items'] == preview['items']
    assert event['params']['preflight']['snapshot_sha256'] == preview['snapshot_sha256']
    code, status = rest(fx, 'batch_status', {'batch_id': receipt['batch_id']})
    assert code == 200, status
    assert [row['item_id'] for row in status['items']] == identities
    assert status['preview_revision'] == 1
    assert status['snapshot_sha256'] == preview['snapshot_sha256']
    assert fx.calls.empty()
    code, _ = rest(fx, 'stop_batch', {'batch_id': receipt['batch_id']})
    assert code == 200


def test_only_selected_items_execute_and_consume_their_counts(fx):
    for node, count in (('a', 1), ('b', 3), ('ref', 2)):
        patch_node(fx, node, prompt='Selected ' + node,
                   runSettings={'model': 'fixture', 'provider_id': 'offline', 'routingMode': 'manual',
                                'ratio': 'landscape169', 'count': count})
    code, preview = rest(fx, 'preflight', {'node_ids': ['a', 'b', 'ref']})
    assert code == 200, preview
    assert not preview['blockers'], preview
    code, receipt = rest(fx, 'request_batch', {'preflight_id': preview['preflight_id']})
    assert code == 200, receipt
    event = fx.events.get(timeout=3)
    chosen = preview['items'][1]['item_id']
    response = fx.client.post(f'/api/projects/{fx.pid}/agent-nodes/actor/confirm',
                              headers={'X-Agent-Project': fx.pid},
                              json={'confirm_id': event['confirm_id'], 'approved': True,
                                    'approved_item_ids': [chosen], 'snapshot_sha256': preview['snapshot_sha256']})
    assert response.status_code == 200, response.text
    status = status_until(fx, receipt['batch_id'], lambda row: row['status'] in ('done', 'rejected'))
    assert status['status'] == 'done', status
    assert status['approved_count'] == status['spent'] == 3, status
    assert [row['status'] for row in status['items']] == ['skipped', 'succeeded', 'skipped']
    assert [row['item_id'] for row in status['items'] if row['task_id']] == [chosen]
    assert fx.calls.qsize() == 3
    assert all(fx.calls.get_nowait()[0] == 'Selected b' for _ in range(3))
    records = list_operations(OpsDb(fx.store.root), fx.pid)
    assert records
    assert all('Selected a' not in str(record) and 'Selected ref' not in str(record) for record in records)
    _, again = rest(fx, 'batch_status', {'batch_id': receipt['batch_id']})
    assert again['spent'] == 3


def test_subset_canonicalization_and_overlimit_full_preview(fx, monkeypatch):
    from app.routers import api_agent_tools as agent
    monkeypatch.setattr(agent, 'GENERATION_SESSION_LIMIT', 3)
    for node, count in (('a', 1), ('b', 3), ('ref', 2)):
        patch_node(fx, node, prompt='Subset ' + node,
                   runSettings={'model': 'fixture', 'provider_id': 'offline', 'routingMode': 'manual',
                                'ratio': 'landscape169', 'count': count})
    code, preview = rest(fx, 'preflight', {'node_ids': ['a', 'b', 'ref']})
    assert code == 200, preview
    assert preview['total_images'] == 6 and len(preview['items']) == 3
    assert not preview['blockers'], preview
    assert any(row['code'] == 'BUDGET_EXCEEDED' for row in preview['warnings'])
    code, receipt = rest(fx, 'request_batch', {'preflight_id': preview['preflight_id']})
    assert code == 200, receipt
    event = fx.events.get(timeout=3)
    first, _, last = [row['item_id'] for row in preview['items']]
    response = fx.client.post(f'/api/projects/{fx.pid}/agent-nodes/actor/confirm',
                              headers={'X-Agent-Project': fx.pid},
                              json={'confirm_id': event['confirm_id'], 'approved': True,
                                    'approved_item_ids': [last, first], 'snapshot_sha256': preview['snapshot_sha256']})
    assert response.status_code == 200, response.text
    status = status_until(fx, receipt['batch_id'], lambda row: row['status'] in ('done', 'rejected'))
    assert status['status'] == 'done', status
    assert status['approved_item_ids'] == [first, last]
    expected = hashlib.sha256((preview['snapshot_sha256'] + json.dumps([first, last],
                                ensure_ascii=False, separators=(',', ':'))).encode()).hexdigest()
    assert status['approval_sha256'] == expected
    assert status['approved_count'] == status['spent'] == 3
    assert fx.calls.qsize() == 3
    code, second = rest(fx, 'preflight', {'node_ids': ['a', 'b', 'ref']})
    assert code == 200, second
    code, another = rest(fx, 'request_batch', {'preflight_id': second['preflight_id']})
    assert code == 200, another
    while True:
        pending = fx.events.get(timeout=3)
        if pending['type'] == 'confirm':
            break
    response = fx.client.post(f'/api/projects/{fx.pid}/agent-nodes/actor/confirm',
                              headers={'X-Agent-Project': fx.pid},
                              json={'confirm_id': pending['confirm_id'], 'approved': True,
                                    'approved_item_ids': [second['items'][1]['item_id']],
                                    'snapshot_sha256': second['snapshot_sha256']})
    assert response.status_code == 200, response.text
    rejected = status_until(fx, another['batch_id'], lambda row: row['status'] in ('done', 'rejected'))
    assert rejected['status'] == 'rejected' and rejected['reason'] == 'BUDGET_EXCEEDED'
    assert rejected['approved_count'] == rejected['spent'] == 0
    assert fx.calls.qsize() == 3


@pytest.mark.parametrize('kind', ['empty', 'duplicate', 'unknown', 'foreign'])
def test_forged_empty_duplicate_foreign_removed_ids_are_stale(fx, kind):
    code, preview = rest(fx, 'preflight', {'node_ids': ['a', 'b']})
    assert code == 200, preview
    code, foreign = rest(fx, 'preflight', {'node_ids': ['a']})
    assert code == 200, foreign
    code, receipt = rest(fx, 'request_batch', {'preflight_id': preview['preflight_id']})
    assert code == 200, receipt
    event = fx.events.get(timeout=3)
    item_id = preview['items'][0]['item_id']
    invalid = {'empty': [], 'duplicate': [item_id, item_id], 'unknown': ['item-not-issued'],
               'foreign': [foreign['items'][0]['item_id']]}[kind]
    before = fx.store.canvas(fx.pid)
    response = fx.client.post(f'/api/projects/{fx.pid}/agent-nodes/actor/confirm',
                              headers={'X-Agent-Project': fx.pid},
                              json={'confirm_id': event['confirm_id'], 'approved': True,
                                    'approved_item_ids': invalid, 'snapshot_sha256': preview['snapshot_sha256']})
    assert response.status_code == 409, response.text
    assert response.json()['detail']['code'] == 'CONFIRM_STALE'
    assert fx.store.canvas(fx.pid) == before and fx.calls.empty()
    code, pending = rest(fx, 'batch_status', {'batch_id': receipt['batch_id']})
    assert code == 200 and pending['status'] == 'awaiting_confirmation'
    assert pending['spent'] == pending['approved_count'] == 0
    response = fx.client.post(f'/api/projects/{fx.pid}/agent-nodes/actor/confirm',
                              headers={'X-Agent-Project': fx.pid},
                              json={'confirm_id': event['confirm_id'], 'approved': True,
                                    'approved_item_ids': [item_id], 'snapshot_sha256': preview['snapshot_sha256']})
    assert response.status_code == 200, response.text
    final = status_until(fx, receipt['batch_id'], lambda row: row['status'] in ('done', 'rejected'))
    assert final['status'] == 'done' and final['spent'] == 1
    assert fx.calls.qsize() == 1


@pytest.mark.parametrize('kind', ['hash', 'replayed_id'])
def test_stale_or_tampered_snapshot_cannot_authorize(fx, kind):
    code, preview = rest(fx, 'preflight', {'node_ids': ['b']})
    assert code == 200, preview
    code, receipt = rest(fx, 'request_batch', {'preflight_id': preview['preflight_id']})
    assert code == 200, receipt
    event = fx.events.get(timeout=3)
    body = {'confirm_id': event['confirm_id'], 'approved': True,
            'approved_item_ids': [preview['items'][0]['item_id']], 'snapshot_sha256': preview['snapshot_sha256']}
    if kind == 'hash':
        assert body['snapshot_sha256'] != '0' * 64
        body['snapshot_sha256'] = '0' * 64
    else:
        response = fx.client.post(f'/api/projects/{fx.pid}/agent-nodes/actor/confirm',
                                  headers={'X-Agent-Project': fx.pid}, json=body)
        assert response.status_code == 200, response.text
        final = status_until(fx, receipt['batch_id'], lambda row: row['status'] in ('done', 'rejected'))
        assert final['status'] == 'done' and final['spent'] == 1
    before = fx.store.canvas(fx.pid)
    response = fx.client.post(f'/api/projects/{fx.pid}/agent-nodes/actor/confirm',
                              headers={'X-Agent-Project': fx.pid}, json=body)
    assert response.status_code == 409, response.text
    assert response.json()['detail']['code'] == 'CONFIRM_STALE'
    assert fx.store.canvas(fx.pid) == before
    assert fx.calls.qsize() == (1 if kind == 'replayed_id' else 0)
    code, status = rest(fx, 'batch_status', {'batch_id': receipt['batch_id']})
    assert code == 200
    assert status['spent'] == status['approved_count'] == (1 if kind == 'replayed_id' else 0)
    if kind == 'hash':
        assert status['status'] == 'awaiting_confirmation'
        code, _ = rest(fx, 'stop_batch', {'batch_id': receipt['batch_id']})
        assert code == 200


@pytest.mark.parametrize('mode', ['autonomous', 'cautious', 'standard'])
def test_legacy_whole_approval_uses_current_real_gate_in_every_mode(fx, mode):
    fx.session.autonomy = mode
    code, preview = rest(fx, 'preflight', {'node_ids': ['a', 'b']})
    assert code == 200 and not preview['blockers'], preview
    before = fx.store.canvas(fx.pid)
    code, receipt = rest(fx, 'request_batch', {'preflight_id': preview['preflight_id']})
    assert code == 200, receipt
    assert receipt['status'] == 'awaiting_confirmation', receipt
    assert receipt['confirm_id']
    event = fx.events.get(timeout=3)
    assert event['type'] == 'confirm' and event['confirm_id'] == receipt['confirm_id']
    assert event['params']['preflight']['items'] == preview['items']
    assert event['params']['preflight']['snapshot_sha256'] == preview['snapshot_sha256']
    assert fx.calls.empty() and fx.store.canvas(fx.pid) == before
    code, pending = rest(fx, 'batch_status', {'batch_id': receipt['batch_id']})
    assert code == 200 and pending['status'] == 'awaiting_confirmation', pending
    assert pending['spent'] == pending['approved_count'] == 0
    response = fx.client.post(f'/api/projects/{fx.pid}/agent-nodes/actor/confirm',
                              headers={'X-Agent-Project': fx.pid},
                              json={'confirm_id': event['confirm_id'], 'approved': True})
    assert response.status_code == 200, response.text
    if mode == 'cautious':
        for index, item in enumerate(preview['items']):
            while True:
                gate = fx.events.get(timeout=3)
                if gate['type'] == 'confirm':
                    break
            assert gate['confirm_id'] != event['confirm_id']
            assert gate['params']['preflight']['items'] == [item]
            code, waiting = rest(fx, 'batch_status', {'batch_id': receipt['batch_id']})
            assert code == 200 and waiting['spent'] == waiting['approved_count'] == index
            assert fx.calls.qsize() == index
            response = fx.client.post(f'/api/projects/{fx.pid}/agent-nodes/actor/confirm',
                                      headers={'X-Agent-Project': fx.pid},
                                      json={'confirm_id': gate['confirm_id'], 'approved': True})
            assert response.status_code == 200, response.text
    final = status_until(fx, receipt['batch_id'], lambda row: row['status'] in ('done', 'rejected'))
    assert final['status'] == 'done', final
    assert final['approved_count'] == final['spent'] == 2
    identities = [item['item_id'] for item in preview['items']]
    assert final['approved_item_ids'] == identities
    expected = hashlib.sha256((preview['snapshot_sha256'] + json.dumps(identities,
                                ensure_ascii=False, separators=(',', ':'))).encode()).hexdigest()
    assert final['approval_sha256'] == expected
    assert [item['status'] for item in final['items']] == ['succeeded', 'succeeded']
    assert fx.calls.qsize() == 2
    response = fx.client.post(f'/api/projects/{fx.pid}/agent-nodes/actor/confirm',
                              headers={'X-Agent-Project': fx.pid},
                              json={'confirm_id': event['confirm_id'], 'approved': True})
    assert response.status_code == 409 and response.json()['detail']['code'] == 'CONFIRM_STALE'
    assert fx.calls.qsize() == 2


def test_survivor_identity_and_original_preflight_lookup_after_revisions(fx):
    from tests.test_agent_batch_confirmation_transports_487 import _transports_start_batch_487, read_gate, revise_gate
    preview, receipt, _ = _transports_start_batch_487(fx)
    identities = [row['item_id'] for row in preview['items']]
    _, edited = revise_gate(fx, receipt, 'edit')
    assert [row['item_id'] for row in edited['preflight']['items']] == identities
    _, removed = revise_gate(fx, receipt, 'remove')
    assert [row['item_id'] for row in removed['preflight']['items']] == identities[1:]
    _, edited_again = revise_gate(fx, receipt, 'edit')
    assert [row['item_id'] for row in edited_again['preflight']['items']] == identities[1:]
    assert edited_again['preflight']['preview_revision'] == 4
    code, same = rest(fx, 'request_batch', {'preflight_id': preview['preflight_id']})
    assert code == 200 and same['batch_id'] == receipt['batch_id']
    assert same['confirm_id'] == edited_again['confirm_id']
    code, fresh = rest(fx, 'preflight', {'node_ids': ['a', 'b']})
    assert code == 200 and not set(identities) & {row['item_id'] for row in fresh['items']}
    assert read_gate(fx, receipt) == edited_again and fx.calls.empty()
    assert rest(fx, 'stop_batch', {'batch_id': receipt['batch_id']})[0] == 200


@pytest.mark.parametrize('transport', ['http', 'callback', 'ws'])
def test_removed_item_id_cannot_authorize_current_gate(fx, transport):
    from tests.test_agent_batch_confirmation_transports_487 import _transports_start_batch_487, read_gate, revise_gate, dispatch, gate_handler
    preview, receipt, _ = _transports_start_batch_487(fx)
    _, current = revise_gate(fx, receipt, 'remove')
    body = {'confirm_id': current['confirm_id'], 'approved': True,
            'approved_item_ids': [preview['items'][0]['item_id']],
            'snapshot_sha256': current['preflight']['snapshot_sha256']}
    handler = gate_handler(fx, current)
    code, result = dispatch(fx, transport, body, handler=handler, token=current['decision_token'])
    assert code == 409 and result['code'] == 'CONFIRM_STALE', result
    assert read_gate(fx, receipt) == current and fx.calls.empty()
    body['approved_item_ids'] = [preview['items'][1]['item_id']]
    assert dispatch(fx, transport, body, handler=handler, token=current['decision_token'])[0] == 200
    final = status_until(fx, receipt['batch_id'], lambda row: row['status'] == 'done')
    assert final['spent'] == final['approved_count'] == 1 and fx.calls.qsize() == 1


@pytest.mark.parametrize('transport', ['http', 'callback'])
@pytest.mark.parametrize('stale', ['old-confirm', 'old-content'])
def test_edited_snapshot_rejects_previous_gate_or_content(fx, transport, stale):
    from tests.test_agent_batch_confirmation_transports_487 import _transports_start_batch_487, read_gate, revise_gate, dispatch, gate_handler
    preview, receipt, event = _transports_start_batch_487(fx, ['b'])
    old_handler = gate_handler(fx, event)
    old, current = revise_gate(fx, receipt, 'edit')
    body = {'confirm_id': old['confirm_id'] if stale == 'old-confirm' else current['confirm_id'], 'approved': True,
            'approved_item_ids': [preview['items'][0]['item_id']], 'snapshot_sha256': preview['snapshot_sha256']}
    handler = old_handler if stale == 'old-confirm' else gate_handler(fx, current)
    code, result = dispatch(fx, transport, body, handler=handler)
    assert code == 409 and result['code'] == 'CONFIRM_STALE', result
    assert read_gate(fx, receipt) == current and fx.calls.empty()
    assert dispatch(fx, 'http', {'confirm_id': current['confirm_id'], 'approved': True})[0] == 200
    final = status_until(fx, receipt['batch_id'], lambda row: row['status'] == 'done')
    assert final['spent'] == final['approved_count'] == 1
    assert fx.calls.get(timeout=3)[0] == 'Human revised body'


def test_simultaneous_batches_share_actual_session_quota(fx, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from app.routers import api_agent_tools as agent
    from tests.test_agent_batch_confirmation_transports_487 import _transports_start_batch_487, dispatch
    from tests.test_agent_batch_preview_edit_487 import provider_io_barrier
    monkeypatch.setattr(agent, 'GENERATION_SESSION_LIMIT', 3)
    for node in ('a', 'b'):
        patch_node(fx, node, prompt='Quota ' + node,
                   runSettings={'model': 'fixture', 'provider_id': 'offline', 'routingMode': 'manual', 'count': 2})
    batches = [_transports_start_batch_487(fx, [node]) for node in ('a', 'b')]
    entered, release = provider_io_barrier(fx, monkeypatch, call_number=2, parties=2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(dispatch, fx, 'http', {'confirm_id': event['confirm_id'], 'approved': True,
            'approved_item_ids': [preview['items'][0]['item_id']], 'snapshot_sha256': preview['snapshot_sha256']})
            for preview, _, event in batches]
        try:
            assert entered.wait(3), 'both real decisions must reach external provider IO simultaneously'
        finally:
            release.set()
        replies = [job.result(5) for job in jobs]
    assert all(code == 200 for code, _ in replies), replies
    finals = [status_until(fx, receipt['batch_id'], lambda s: s['status'] in ('done', 'rejected'))
              for _, receipt, _ in batches]
    assert sorted(row['spent'] for row in finals) == [0, 2], finals
    denied, = [row for row in finals if row['status'] == 'rejected']
    assert denied['reason'] == 'BUDGET_EXCEEDED' and denied['approved_count'] == 0
    assert sum(row['approved_count'] for row in finals) == 2 and fx.calls.qsize() == 2
    assert sum(bool(row['task_id']) for final in finals for row in final['items']) == 1
    preview, receipt, event = _transports_start_batch_487(fx, ['b'])
    assert dispatch(fx, 'http', {'confirm_id': event['confirm_id'], 'approved': True})[0] == 200
    denied = status_until(fx, receipt['batch_id'], lambda s: s['status'] == 'rejected')
    assert denied['reason'] == 'BUDGET_EXCEEDED' and denied['spent'] == denied['approved_count'] == 0
    assert fx.calls.qsize() == 2


def test_cautious_subset_selection_and_item_gates_charge_once(fx):
    from tests.test_agent_batch_confirmation_transports_487 import _transports_start_batch_487, dispatch
    from tests.test_agent_batch_preview_edit_487 import next_gate
    fx.session.autonomy = 'cautious'
    for node, count in (('a', 1), ('b', 3), ('ref', 2)):
        patch_node(fx, node, prompt='Cautious ' + node,
                   runSettings={'model': 'fixture', 'provider_id': 'offline', 'routingMode': 'manual',
                                'aspect_ratio': '1:1', 'count': count})
    preview, receipt, selection = _transports_start_batch_487(fx, ['a', 'b', 'ref'])
    assert selection['params']['approval_stage'] == 'select'
    ids = [row['item_id'] for row in preview['items']]
    assert dispatch(fx, 'http', {'confirm_id': selection['confirm_id'], 'approved': True,
        'approved_item_ids': [ids[2], ids[1]], 'snapshot_sha256': preview['snapshot_sha256']})[0] == 200
    fee = next_gate(fx)
    _, waiting = rest(fx, 'batch_status', {'batch_id': receipt['batch_id']})
    assert waiting['approved_item_ids'] == ids[1:]
    assert waiting['spent'] == waiting['approved_count'] == 0 and fx.calls.empty()
    assert fee['params']['approval_stage'] == 'pay_item'
    assert fee['params']['preflight']['items'] == [preview['items'][1]]
    assert dispatch(fx, 'http', {'confirm_id': fee['confirm_id'], 'approved': True})[0] == 200
    rejected_fee = next_gate(fx)
    _, waiting = rest(fx, 'batch_status', {'batch_id': receipt['batch_id']})
    assert waiting['spent'] == waiting['approved_count'] == 3 and fx.calls.qsize() == 3
    assert rejected_fee['params']['preflight']['items'] == [preview['items'][2]]
    assert dispatch(fx, 'http', {'confirm_id': rejected_fee['confirm_id'], 'approved': False})[0] == 200
    final = status_until(fx, receipt['batch_id'], lambda row: row['status'] == 'rejected')
    assert final['spent'] == final['approved_count'] == 3
    assert [row['status'] for row in final['items']] == ['skipped', 'succeeded', 'rejected']
    assert [row['node_id'] for row in final['items'] if row['task_id']] == ['b']
    assert [fx.calls.get_nowait()[0] for _ in range(3)] == ['Cautious b'] * 3 and fx.calls.empty()
    assert dispatch(fx, 'http', {'confirm_id': fee['confirm_id'], 'approved': True})[0] == 409
    records = list_operations(OpsDb(fx.store.root), fx.pid)
    assert records and all('Cautious a' not in str(row) and 'Cautious ref' not in str(row) for row in records)


def test_snapshot_seals_request_options_without_replacing_original_lookup(fx):
    from tests.test_agent_batch_confirmation_transports_487 import read_gate, dispatch, revise_gate
    code, preview = rest(fx, 'preflight', {'node_ids': ['b']})
    assert code == 200
    code, receipt = rest(fx, 'request_batch', {'preflight_id': preview['preflight_id'],
        'fallback_models': ['alternate'], 'max_retries': 0})
    assert code == 200
    event = fx.events.get(timeout=3)
    current = read_gate(fx, receipt)
    assert current['preflight']['snapshot_sha256'] != preview['snapshot_sha256']
    assert event['params']['preflight'] == current['preflight']
    assert event['params']['fallback_models'] == ['alternate'] and event['params']['max_retries'] == 0
    assert dispatch(fx, 'http', {'confirm_id': current['confirm_id'], 'approved': True,
        'snapshot_sha256': preview['snapshot_sha256']})[0] == 409
    _, revised = revise_gate(fx, receipt, 'edit')
    code, duplicate = rest(fx, 'request_batch', {'preflight_id': preview['preflight_id'],
        'fallback_models': ['not-authorized'], 'max_retries': 2})
    assert code == 200 and duplicate['batch_id'] == receipt['batch_id']
    assert read_gate(fx, receipt) == revised
    assert dispatch(fx, 'http', {'confirm_id': revised['confirm_id'], 'approved': True})[0] == 200
    final = status_until(fx, receipt['batch_id'], lambda row: row['status'] == 'done')
    assert final['spent'] == 1
