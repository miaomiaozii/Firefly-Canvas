"""External corpus is opt-in; ordinary pytest only collects deterministic tests."""
import json
import os
from pathlib import Path

import pytest


def pytest_addoption(parser):
    parser.addoption('--agent-plan-fixtures', help='Owned external #404 fixture root (or AGENT_PLAN_FIXTURES)')
    parser.addoption('--agent-plan-report', help='NEW external report directory (or AGENT_PLAN_REPORT)')


@pytest.fixture(scope='session')
def dataset(request):
    from tests.eval.agent_plan.evaluator import evaluate, load_gold, snapshot_gold, write_report
    from tests.eval.agent_plan.manifest import verify_manifest
    from tools.copy_eval_fixtures import REPO, REVISIONS, SOURCE_ROOT, overlaps

    fixture_root = request.config.getoption('--agent-plan-fixtures') or os.environ.get('AGENT_PLAN_FIXTURES')
    report_root = request.config.getoption('--agent-plan-report') or os.environ.get('AGENT_PLAN_REPORT')
    if not report_root:
        pytest.fail('Explicit dataset run requires --agent-plan-report or AGENT_PLAN_REPORT; no skip')
    report = Path(report_root).resolve()
    if (any(overlaps(report, forbidden) for forbidden in (REPO, REVISIONS, SOURCE_ROOT))
            or (fixture_root and overlaps(report, Path(fixture_root)))):
        pytest.fail('Report must be external and disjoint from repository, approved sources and fixture root')
    report.mkdir(parents=True, exist_ok=False)
    gold = load_gold()
    hashes = snapshot_gold(report / 'gold-before-measurement')
    records = []
    try:
        if not fixture_root:
            raise ValueError('Explicit dataset run requires --agent-plan-fixtures or AGENT_PLAN_FIXTURES; no skip')
        root = Path(fixture_root).resolve()
        manifest = verify_manifest(root)
        (report / 'fixture-manifest.json').write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        for case in gold:
            for variant in case['variants']:
                records.append(evaluate(case, variant, None, error=str(exc)) | {'transport': 'REST'})
        write_report(report, records, hashes)
        pytest.fail(f'External dataset unavailable: {exc}')
    yield {'root': root, 'report': report, 'gold': gold, 'hashes': hashes,
           'manifest': manifest, 'records': records}
    write_report(report, records, hashes)
