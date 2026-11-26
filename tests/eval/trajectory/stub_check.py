"""Run the existing offline pipeline and report observations without scoring model behavior."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from tests.eval.trajectory.safety import external_new
from tests.eval.trajectory.test_pipeline import PIPELINE_CASES, run_pipeline_case


def stub_report(destination):
    destination = external_new(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    cases = []
    with TemporaryDirectory(prefix='stub-check-', dir=destination.parent) as scratch:
        scratch = Path(scratch)
        for index, case in enumerate((*PIPELINE_CASES, (*PIPELINE_CASES[0], 'standard'))):
            cases.append(run_pipeline_case(scratch / str(index), *case))
    report = {
        'schema': 1,
        'mode': 'stub-verification',
        'real_model_evidence': False,
        'b2_status': 'NOT_RUN',
        'scope': 'One synthetic task per case, scripted confirmations, local provider HTTP seam. Not E0 model evidence.',
        'cases': cases,
    }
    with destination.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    return report
