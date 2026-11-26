"""#405 entry point: no product imports or writes until an explicit mode is selected."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys


def parser():
    command = argparse.ArgumentParser(description='B2 trajectories; media stays offline unless an approved Soon mode is selected.')
    modes = command.add_subparsers(dest='mode', required=True)
    offline = modes.add_parser('offline', help='Zero-fee report; real E0 metrics remain explicitly unmeasured')
    offline.add_argument('--report', type=Path, required=True, help='NEW external JSON file')
    stub = modes.add_parser('stub-check', help='Run the offline product pipeline with local generation faults')
    stub.add_argument('--report', type=Path, required=True, help='NEW external JSON file')
    live = modes.add_parser('live', help='Requires separate user cost approval; NOT part of the zero-fee batch')
    live.add_argument('--ack-paid-model-approval', action='store_true', required=True,
                      help='Caller attests separate explicit user approval for real model inference')
    live.add_argument('--host', choices=['codex'], required=True)
    live.add_argument('--model', required=True)
    live.add_argument('--llm-base-url', required=True, help='Explicit model endpoint, passed ONLY to native children')
    live.add_argument('--autonomy', choices=['standard', 'autonomous'], default='standard')
    live.add_argument('--auth-home-source', type=Path, required=True, help='Owned readonly COPY containing auth.json and ownership marker')
    live.add_argument('--ack-owned-auth-copy', action='store_true', required=True)
    native_options(live, ('normal', 'no_channel', 'transient', 'rate_limit', 'quota_exhausted',
                          'content_refusal', 'size_mismatch', 'campaign'))
    strata = modes.add_parser('live-strata', allow_abbrev=False,
                              help='Evaluate only approved credential-free Strata; media stays stubbed')
    strata.set_defaults(mode='live', strata=True, host='codex', autonomy='standard', auth_home_source=None,
                        ack_owned_auth_copy=False, ack_paid_model_approval=False)
    native_options(strata, ('normal', 'campaign'))
    strata.add_argument('--soon-ledger', type=Path, help='Original successful smoke image-budget.sqlite; opt in to Soon')
    strata.add_argument('--ack-soon-image-approval', action='store_true', help='Attest approval for this single E0 run')
    soon = modes.add_parser('soon-smoke', allow_abbrev=False, help='One explicitly approved real Soon image')
    soon.add_argument('--ack-soon-image-approval', action='store_true', required=True)
    soon.add_argument('--output-root', type=Path, required=True, help='NEW external run root; keeps the shared image ledger')
    replay = modes.add_parser('replay', help='Recalculate a REAL recorded trace, not a model run')
    replay.add_argument('--trace-root', type=Path, required=True)
    replay.add_argument('--report', type=Path, required=True, help='NEW external JSON file')
    replay.add_argument('--annotations', type=Path, help='Optional evidence-referenced human adjudication JSON')
    return command


def native_options(command, scenarios):
    command.add_argument('--native-cli', type=Path, required=True, help='Explicit Codex executable')
    command.add_argument('--scenario', choices=scenarios, required=True)
    command.add_argument('--output-root', type=Path, required=True, help='NEW absolute external evidence/private-data root')
    command.add_argument('--fixtures-manifest', type=Path, required=True, help='External #404 manifest.json (E0 only is read)')
    command.add_argument('--product-render', required=True, help='Explicit E0 render relative_path from the manifest')
    command.add_argument('--port', type=int, required=True, help='3194 or a dedicated 3200..3299 port')
    command.add_argument('--timeout', type=int, default=900)
    command.add_argument('--max-provider-calls', type=int, default=60)


def main(argv=None):
    args = parser().parse_args(argv)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    if args.mode == 'offline':
        from tests.eval.trajectory.metrics import offline_report
        offline_report(args.report)
        return 0
    if args.mode == 'stub-check':
        from tests.eval.trajectory.stub_check import stub_report
        stub_report(args.report)
        return 0
    if args.mode == 'live':
        from tests.eval.trajectory.live import run
        return run(args)
    if args.mode == 'soon-smoke':
        from tests.eval.trajectory.soon_smoke import run_smoke
        return run_smoke(args.output_root)
    from tests.eval.trajectory.metrics import replay
    replay(args.trace_root, args.report, args.annotations)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
