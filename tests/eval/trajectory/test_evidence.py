"""Provenance fixtures test rejection; they do not constitute model evidence."""
import asyncio
from functools import partial
import hashlib
import io
import json
from types import SimpleNamespace

import httpx
import pytest

from tests.eval.trajectory import live
from tests.eval.trajectory.backend import ObservedPipe
from tests.eval.trajectory.metrics import measure, replay, report_from_root
from tests.eval.trajectory.safety import clean_private, digest, register_owned_secrets, redact, save
from tests.eval.trajectory.stub import Journal, ProviderFence, SafetyViolation


def test_recovery_with_wrong_error_classification_cannot_pass():
    failed = {'task_id': 'root', 'status': 'failed', 'failure': {'code': 'TRANSIENT'}, 'executed_prompt': 'p', 'outputs': []}
    recovered = {'task_id': 'retry', 'retry_parent_id': 'root', 'status': 'succeeded', 'outputs': [{'asset_id': 'x'}]}
    canvas = {'nodes': [], 'history': [failed, recovered]}
    backend = [{'kind': 'stub_outcome', 'prompt_sha256': hashlib.sha256(b'p').hexdigest(), 'injected': 'quota_exhausted'}]
    result = measure(backend, [], [], {}, canvas, real=True, complete=True)
    metric = result['metrics']['failure_self_handling']
    assert metric['numerator'] == 0 and metric['denominator'] == 1
    assert metric['incidents'][0]['classification_correct'] is False
    assert result['targets']['failure_self_handling']['status'] == 'FAIL'


def test_completed_recording_with_missing_tool_result_is_not_zero_out_of_bounds_pass():
    backend = [{'index': 1, 'time': 1, 'kind': 'tool_start', 'tool': 'canvas.list_nodes', 'args': {}, 'project': 'p'}]
    result = measure(backend, [], [], {'project': 'p'}, {'nodes': []}, real=True, complete=True)
    assert result['targets']['out_of_bounds']['status'] == 'UNAVAILABLE'


def test_displaced_generation_seam_fails_preflight(monkeypatch):
    original = httpx.AsyncClient.send
    with ProviderFence() as fence:
        with monkeypatch.context() as patch:
            patch.setattr(httpx.AsyncClient, 'send', lambda *a, **k: None)
            with pytest.raises(SafetyViolation, match='displaced'):
                fence.preflight()
    assert httpx.AsyncClient.send is original


def test_cleanup_requires_owned_receipt(tmp_path):
    (tmp_path / 'projects').mkdir()
    with pytest.raises(ValueError, match='owned'):
        clean_private(tmp_path)
    assert (tmp_path / 'projects').exists()


def test_redaction_includes_owned_auth_values_and_jwt(monkeypatch):
    monkeypatch.setattr('tests.eval.trajectory.safety._KNOWN_SECRETS', set())
    register_owned_secrets({'tokens': {'access_token': 'synthetic-owned-opaque-value'}})
    assert redact('oops synthetic-owned-opaque-value') == 'oops [REDACTED]'
    assert redact('eyJabcdefgh.abcdefgh.signature') == '[REDACTED]'


def test_recorded_tool_result_preserves_all_fifteen_task_ids_without_retaining_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr('tests.eval.trajectory.safety._KNOWN_SECRETS', set())
    task_ids = [f'task-{i:032x}' for i in range(15)]
    owned = 'task-ffffffffffffffffffffffffffffffff'
    register_owned_secrets({'api_key': owned})
    path = tmp_path / 'backend.jsonl'
    journal = Journal(path)
    payload = {'preview': {'tasks': [{'task_id': item} for item in task_ids]}}
    mcp_text = json.dumps(payload | {'api_key': task_ids[0]})
    journal.emit('tool_end', result={'result': {'content': [{'type': 'text', 'text': mcp_text}]}},
                 preview=payload['preview'], other={'task_id': owned, 'api_key': task_ids[0]},
                 diagnostics=[f'Bearer {task_ids[0]}', owned,
                 'sk-0123456789abcdef', 'sess-0123456789abcdef', f'sk-{task_ids[1]}',
                 f'api_key={task_ids[2]}', f'{task_ids[3]}-extra'])
    record = json.loads(path.read_text(encoding='utf-8'))
    tool_output = json.loads(record['result']['result']['content'][0]['text'])
    assert tool_output['preview'] == record['preview'] == payload['preview']
    assert task_ids[0] not in tool_output['api_key']
    assert record['other'] == {'task_id': '[REDACTED]', 'api_key': '[REDACTED]'}
    assert record['diagnostics'] == ['[REDACTED]'] * 6 + ['ta[REDACTED]']


def test_driver_joins_live_asset_then_exports_the_same_canvas_alias(tmp_path, monkeypatch):
    key = b'c' * 32
    result_node = 'task-' + 'd' * 32 + '-image'
    project, actor, asset = 'project', 'actor', 'real-asset'
    prefix = f'/api/projects/{project}/agent-nodes/{actor}'
    seed = {'project': project, 'node': actor, 'product_asset': 'seed-asset', 'preflight': {'installed': True}}
    save(tmp_path / 'seed.json', seed)
    workbook = tmp_path / 'synthetic.xlsx'
    workbook.write_bytes(b'synthetic workbook')
    Journal(tmp_path / 'backend.jsonl', recording_key=key).emit('tool_end', tool='generate.request_batch',
        result={'result': {'structuredContent': {'batch_id': 'b'}}})
    clock, media_reads = [0], []
    canvas = {'nodes': [{'id': result_node, 'images': [{'asset_id': asset}]}]}
    jobs = {'status': 'done', 'items': [{'status': 'succeeded', 'result_node_id': result_node}]}

    def wire(request):
        path = request.url.path
        if path == f'/api/projects/{project}/generate/batch_status':
            clock[0] = 2
            return httpx.Response(200, json=jobs)
        if path == f'/api/project-library/{project}':
            return httpx.Response(200, json={'canvas': canvas})
        if path == f'/api/project-library/{project}/media/{asset}':
            media_reads.append(path)
            return httpx.Response(200, content=b'synthetic material')
        if path == f'{prefix}/events':
            return httpx.Response(200, json={'events': [], 'last_seq': 0})
        if path == f'{prefix}/attachments':
            return httpx.Response(200, json={'id': 'attachment'})
        assert path in {'/', prefix, f'{prefix}/status', f'{prefix}/prompt', f'/api/projects/{project}/agent-nodes'}
        return httpx.Response(200, json={})

    monkeypatch.setattr(live, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(httpx, 'AsyncClient', partial(httpx.AsyncClient, transport=httpx.MockTransport(wire)))
    asyncio.run(live.drive({'port': 3194, 'timeout': 1, 'model': 'synthetic', 'autonomy': 'standard',
                            'workbook_path': str(workbook), 'soon_ledger': 'synthetic-only'}, tmp_path, recording_key=key))
    assert media_reads == [f'/api/project-library/{project}/media/{asset}']
    saved_canvas, saved_jobs, hashes = [json.loads((tmp_path / name).read_text(encoding='utf-8'))
        for name in ('final-canvas.json', 'final-jobs.json', 'canvas-image-hashes.json')]
    alias = saved_canvas['nodes'][0]['id']
    assert alias != result_node and alias == saved_jobs['items'][0]['result_node_id']
    assert hashes == [{'node_id': alias, 'asset_id': asset, 'sha256': hashlib.sha256(b'synthetic material').hexdigest()}]


def test_observed_native_pipe_records_real_read_and_iteration_not_invented_events():
    journal = Journal()
    pipe = ObservedPipe(io.StringIO('{"type":"turn.started"}\nnot-json\n'), journal, 123, 'stderr')
    assert len(list(pipe)) == 2
    assert [r['frame'] for r in journal.rows] == [{'type': 'turn.started'}, {'text': 'not-json\n'}]
    assert all(r['kind'] == 'native_frame' and r['pid'] == 123 for r in journal.rows)


def test_simulation_remains_not_real_even_with_native_shaped_frames(tmp_path):
    save(tmp_path / 'provenance.json', {'kind': 'simulation', 'requested_model': 'fake'})
    backend = [{'index': 1, 'kind': 'native_spawn', 'model': 'fake'},
               {'index': 2, 'kind': 'native_frame', 'stream': 'stdout', 'frame': {'type': 'turn.completed'}},
               {'index': 3, 'time': 1, 'kind': 'tool_start', 'tool': 'canvas.list_nodes', 'args': {}}]
    (tmp_path / 'backend.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in backend), encoding='utf-8')
    result = report_from_root(tmp_path)
    assert not result['real_model_evidence'] and result['b2_status'] == 'NOT_RUN'
    assert all(target['status'] == 'NOT_REAL_MODEL' for target in result['targets'].values())


def test_replay_checks_hashes_and_rejects_spawn_only_evidence(tmp_path):
    root = tmp_path / 'trace'
    root.mkdir()
    for name in ['E0-gold.json', 'fixture-manifest.json']:
        save(root / name, {})
    save(root / 'provenance.json', {'kind': 'live-native-model', 'gold_sha256': digest(root / 'E0-gold.json'),
                                   'fixture_manifest_sha256': digest(root / 'fixture-manifest.json')})
    save(root / 'host-events.json', {'events': []})
    for name in ['seed.json', 'config.json', 'completion.json', 'final-canvas.json', 'final-status.json', 'final-jobs.json']:
        save(root / name, {})
    (root / 'driver.jsonl').write_text('', encoding='utf-8')
    (root / 'backend.jsonl').write_text('{"kind":"native_spawn","model":"fake"}\n', encoding='utf-8')
    save(root / 'evidence-hashes.json', {p.name: digest(p) for p in root.iterdir()})
    with pytest.raises(ValueError, match='alone/synthetic'):
        replay(root, tmp_path / 'report.json')
    (root / 'backend.jsonl').write_text('tampered', encoding='utf-8')
    with pytest.raises(ValueError, match='hash mismatch'):
        replay(root, tmp_path / 'report.json')
