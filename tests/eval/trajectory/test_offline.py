"""Zero-fee reports must not manufacture native-model measurements."""
from argparse import Namespace
import json
import sys

import pytest

from tools.run_agent_eval import main
from tests.eval.trajectory.live import run
from tests.eval.trajectory.metrics import report_from_root


def test_offline_report_never_imports_or_starts_product(tmp_path, monkeypatch):
    report_path = tmp_path / 'evidence/report.json'
    forbidden = tmp_path / 'never-runtime'
    monkeypatch.setenv('FIREFLY_RUNTIME_ROOT', str(forbidden))
    monkeypatch.setenv('FIREFLY_PROJECTS_ROOT', str(tmp_path / 'never-projects'))
    before = set(sys.modules)

    def no_process(*args, **kwargs):
        raise AssertionError('Offline reporting must never start a process')

    monkeypatch.setattr('subprocess.Popen', no_process)
    assert main(['offline', '--report', str(report_path)]) == 0
    report = json.loads(report_path.read_text(encoding='utf-8'))
    assert report['mode'] == 'offline-only'
    assert report['real_model_evidence'] is False
    assert report['b2_status'] == 'NOT_RUN'
    assert report['reason'] == '未测（按用户要求零付费）'
    assert set(report['targets']) == {'pre_execution_confirmations', 'execution_interruptions',
                                      'failure_self_handling', 'out_of_bounds'}
    assert all(item['status'] == 'NOT_RUN' for item in report['targets'].values())
    assert all(item['value'] is None and item['available'] is False for item in report['metrics'].values())
    assert report['targets']['execution_interruptions']['standard'] == 1
    assert report['targets']['execution_interruptions']['autonomous'] == 0
    assert report['targets']['failure_self_handling']['target'] == .9
    assert not {'main', 'app.core'} & (set(sys.modules) - before)
    assert not forbidden.exists() and not (tmp_path / 'never-projects').exists()
    with pytest.raises(ValueError, match='NEW'):
        main(['offline', '--report', str(report_path)])


@pytest.mark.parametrize('options', [Namespace(), Namespace(ack_paid_model_approval=False)])
def test_live_needs_separate_cost_approval_before_preflight(options, monkeypatch):
    def no_preflight(*args, **kwargs):
        raise AssertionError('Paid-mode guard must precede preflight and all writes')

    monkeypatch.setattr('tests.eval.trajectory.live.validate', no_preflight)
    with pytest.raises(ValueError, match='paid-model approval'):
        run(options)


def test_missing_fence_cannot_claim_measured_zero_provider_billing(tmp_path):
    report = report_from_root(tmp_path)
    assert report['real_model_evidence'] is False
    assert report['generation_billing']['fence_observed'] is False
    assert report['generation_billing']['real_media_provider_calls'] is None
