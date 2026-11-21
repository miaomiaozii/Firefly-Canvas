"""Provenance fixtures test rejection; they do not constitute model evidence."""
import hashlib
import io
import json

import httpx
import pytest

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
    (root / 'driver.jsonl').write_text('', encoding='utf-8')
    (root / 'backend.jsonl').write_text('{"kind":"native_spawn","model":"fake"}\n', encoding='utf-8')
    save(root / 'evidence-hashes.json', {p.name: digest(p) for p in root.iterdir()})
    with pytest.raises(ValueError, match='alone/synthetic'):
        replay(root, tmp_path / 'report.json')
    (root / 'backend.jsonl').write_text('tampered', encoding='utf-8')
    with pytest.raises(ValueError, match='hash mismatch'):
        replay(root, tmp_path / 'report.json')
