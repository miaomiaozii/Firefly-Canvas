#!/usr/bin/env python3
"""#296 端到端截图证据基线对比。

每个场景结束时自动截图（tools/e2e_qa.sh 与 tools/e2e/scenario_*.sh 写入
.pipeline/evidence/screenshots/<场景名>-attemptN-<时间戳>.png）。本工具：
  1. 每个场景取最新一张；
  2. 与基线（tools/e2e/baselines/<场景名>*.png 中最新一次归档）对比：
     尺寸不一致直接判显著差异；一致则灰度降采样后算平均绝对差；
  3. 生成并排对比图 <场景名>-diff.png（左基线右当前，差异区域红框百分比标注在文件名/输出）；
  4. 无基线时把当前图归档为新基线（首跑自动建线）。

画布内容是动态的（节点随会话变化），像素差天然存在——默认只报告不拦截；
--strict 时任一场景超过 --threshold 即退出码 1（适合接进门禁的确定性页场景）。

用法:
  python tools/e2e_shot_diff.py                 # 报告 + 生成 diff 图
  python tools/e2e_shot_diff.py --strict --threshold 0.08
  python tools/e2e_shot_diff.py --update        # 强制用最新截图刷新基线
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
EVID = ROOT / ".pipeline" / "evidence" / "screenshots"
BASE = ROOT / "tools" / "e2e" / "baselines"


def scenario_of(name: str) -> str:
    stem = re.sub(r"\.png$", "", name)
    m = re.match(r"(.+?)-attempt\d+(?:-.*)?$", stem)
    return m.group(1) if m else stem


def latest_per_scenario(d: Path, pattern: str = "*.png") -> dict[str, Path]:
    out: dict[str, Path] = {}
    for p in sorted(d.glob(pattern)):
        if "-diff" in p.name:
            continue
        out[scenario_of(p.name)] = p  # 名字含 ISO 时间戳，字典序即时间序
    return out


def mean_diff(a: Image.Image, b: Image.Image) -> float:
    size = (min(a.width, b.width), min(a.height, b.height))
    size = (max(1, size[0] // 2), max(1, size[1] // 2))
    ga = a.convert("L").resize(size)
    gb = b.convert("L").resize(size)
    diff = ImageChops.difference(ga, gb)
    hist = diff.histogram()
    total = sum(i * v for i, v in enumerate(hist))
    return total / (255 * size[0] * size[1])


def side_by_side(a: Image.Image, b: Image.Image, out: Path) -> None:
    h = max(a.height, b.height)
    canvas = Image.new("RGB", (a.width + b.width + 16, h), (24, 24, 24))
    canvas.paste(a, (0, 0))
    canvas.paste(b, (a.width + 16, 0))
    d = ImageDraw.Draw(canvas)
    d.text((4, 4), "BASELINE", fill=(255, 255, 0))
    d.text((a.width + 20, 4), "CURRENT", fill=(255, 255, 0))
    canvas.save(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--evidence-dir", type=Path, default=EVID)
    ap.add_argument("--baseline-dir", type=Path, default=BASE)
    ap.add_argument("--threshold", type=float, default=0.08, help="平均绝对差阈值(0-1)")
    ap.add_argument("--strict", action="store_true", help="超阈值退出码 1")
    ap.add_argument("--update", action="store_true", help="最新截图刷新基线")
    args = ap.parse_args()

    if not args.evidence_dir.is_dir():
        print(f"证据目录不存在（先跑 e2e 套件）: {args.evidence_dir}")
        return 2
    args.baseline_dir.mkdir(parents=True, exist_ok=True)

    shots = latest_per_scenario(args.evidence_dir)
    if not shots:
        print("证据目录为空")
        return 2

    over = 0
    for scen, shot in sorted(shots.items()):
        # 基线名可能带旧时间戳后缀——按场景前缀匹配
        bl_files = [p for p in sorted(args.baseline_dir.glob("*.png")) if scenario_of(p.name) == scen]
        bl = bl_files[-1] if bl_files else None
        if args.update or bl is None:
            dst = args.baseline_dir / f"{scen}.png"
            dst.write_bytes(shot.read_bytes())
            print(f"{scen}: {'基线已刷新' if bl else '新建基线'} <- {shot.name}")
            continue
        with Image.open(bl) as a, Image.open(shot) as b:
            if a.size != b.size:
                print(f"{scen}: 尺寸不一致 {a.size} vs {b.size} -> 显著差异")
                pct = 1.0
            else:
                pct = mean_diff(a, b)
            diff_path = args.evidence_dir / f"{scen}-diff.png"
            with Image.open(bl) as a2, Image.open(shot) as b2:
                side_by_side(a2.convert("RGB"), b2.convert("RGB"), diff_path)
        flag = "显著" if pct > args.threshold else "ok"
        print(f"{scen}: 平均差 {pct * 100:.2f}% [{flag}] 对比图 {diff_path.relative_to(ROOT)}")
        if pct > args.threshold:
            over += 1
    if args.strict and over:
        print(f"FAIL: {over} 个场景超过阈值 {args.threshold}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
