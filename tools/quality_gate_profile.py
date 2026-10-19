"""#204：逐目标实测 unit/contract 分片的真实耗时。

tools/quality_gate.py 把 unit/contract 工作按「实测耗时」LPT 贪心装箱切分
（见 tools/quality_gate_shard_durations.json）。这份 profile 就是那些数字的
来源：每个 unittest 目标（模块级目标，以及 tests/test_quality_gate.py 的
方法级目标）单独跑一遍，取墙钟秒数，写回 JSON。

用法（在仓库根目录）：
    python tools/quality_gate_profile.py            # 全量测量（约 20 分钟）
    python tools/quality_gate_profile.py --only canvas_server
    python tools/quality_gate_profile.py --refresh  # 忽略已有值重新测

规则：
- 已有 JSON 中未命中 --only/--refresh 的目标保留原值（增量补测）。
- 某个目标运行失败或超时只记 0.0（装箱时按未知目标默认值处理），
  不会让整个 profile 失败——失败的模块会在门里原样暴露。
- 目标是「模块名」还是「tests.test_quality_gate.<Class>.<method>」由
  quality_gate.unit_contract_shards 的切分决定，这里不关心分片本身。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.quality_gate import (  # noqa: E402
    UNIT_CONTRACT_BROWSER_MODULE,
    UNIT_CONTRACT_PROFILE_FILENAME,
    UNIT_CONTRACT_QUALITY_GATE_MODULE,
    discover_unit_contract_modules,
    quality_gate_unit_test_targets,
)

DEFAULT_TIMEOUT_SECONDS = 600
OUTPUT = ROOT / "tools" / UNIT_CONTRACT_PROFILE_FILENAME


def profile_targets(root: Path) -> tuple[str, ...]:
    """需要实测的全部目标：除 gate/browser 外的模块级目标 + gate 方法级目标 + browser 模块。"""
    modules = discover_unit_contract_modules(root)
    targets = [
        module
        for module in modules
        if module not in {UNIT_CONTRACT_QUALITY_GATE_MODULE, UNIT_CONTRACT_BROWSER_MODULE}
    ]
    targets.extend(quality_gate_unit_test_targets(root))
    if UNIT_CONTRACT_BROWSER_MODULE in modules:
        targets.append(UNIT_CONTRACT_BROWSER_MODULE)
    return tuple(sorted(targets))


def load_existing(path: Path) -> dict[str, float]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    targets = payload.get("targets") if isinstance(payload, dict) else None
    return {
        str(name): float(seconds)
        for name, seconds in (targets or {}).items()
        if isinstance(seconds, (int, float)) and seconds > 0
    }


def measure(target: str, root: Path, timeout: float) -> float:
    environment = dict(os.environ)
    environment["FIREFLY_TEST_MODE"] = "1"
    started = time.monotonic()
    try:
        subprocess.run(
            [sys.executable, "-m", "unittest", "-b", target],
            cwd=root,
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return 0.0
    return round(time.monotonic() - started, 3)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", help="只测名字包含该子串的目标")
    parser.add_argument("--refresh", action="store_true", help="忽略已有值重新测")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--limit", type=int, help="最多测多少个目标（调试用）")
    args = parser.parse_args()

    durations = load_existing(OUTPUT) if not args.refresh else {}
    targets = [t for t in profile_targets(ROOT) if not args.only or args.only in t]
    if args.limit:
        targets = targets[: args.limit]
    due = [t for t in targets if args.refresh or t not in durations]
    print(f"profile targets: {len(targets)} due: {len(due)}", file=sys.stderr)

    for index, target in enumerate(due, start=1):
        seconds = measure(target, ROOT, args.timeout)
        if seconds > 0:
            durations[target] = seconds
        print(f"[{index}/{len(due)}] {target}: {seconds}s", file=sys.stderr)

    OUTPUT.write_text(
        json.dumps(
            {
                "schema": 1,
                "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "note": "wall-clock seconds per unittest target; 0/missing = unknown",
                "targets": {name: durations[name] for name in sorted(durations)},
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"wrote {OUTPUT} ({len(durations)} targets)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
