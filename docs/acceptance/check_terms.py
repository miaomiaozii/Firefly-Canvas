#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""代号回归（TERM_CHECK，棘轮 v12 / P-60 同源教训）。

目的：班规里的 `代号`（反引号术语）是三份契约互相引用的锚点。条文被删或被改写后，
引用它的文件不会报错，只会静默失效——这跟 P-58（version 字段假阴性）是同一类事故。
本脚本对 v12 新增/复用的代号做一次穷举：每个代号必须在指定文件集合里至少命中一次，
缺一个就 exit 1。

复跑：python docs/acceptance/check_terms.py
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 仓库根（docs/acceptance/ 上三级）

SK = ".pi/skills"
GATE = {
    "supervisor": SK + "/acceptance-supervisor/SKILL.md",
    "worker": SK + "/impl-session-contract/SKILL.md",
    "ratchet": SK + "/process-optimizer/SKILL.md",
    "common": SK + "/three-role-loop/SKILL.md",
    "reasoning": SK + "/reasoning-modes/SKILL.md",
}
ROSTER = "docs/acceptance/roster.md"

# 代号 -> 必须命中的文件（缺任一即失败）
REQUIRED = {
    "同层三树": ["common", "supervisor", "worker", "ratchet", ROSTER],
    "自举顺序": ["common", "supervisor", "worker", "ratchet", ROSTER],
    "种子自举": ["common", "supervisor", "worker", ROSTER],
    "发送判据": ["common", "supervisor", "worker", "reasoning", ROSTER],
    "旧称不是角色": ["common", "supervisor", ROSTER],
    "上岗种子": ["common", "supervisor", "worker", ROSTER],
    "种子带档位": ["reasoning", "supervisor"],
    "身份册存活列": ["ratchet", ROSTER],
    "队列状态计数": ["ratchet", ROSTER],
    "信箱可达性自检": ["ratchet", ROSTER],
    "上架回读对账": ["common", "ratchet", "pain"],
    "AGENTS门集一致": ["ratchet", "pain"],
}

# AGENTS.md 入检查面（P-68）
AGENTS = "AGENTS.md"

# 门集五份必须同版本（P-58 复跑回归：按字段名读，不按行号）
def versions():
    out = {}
    for name, rel in GATE.items():
        path = os.path.join(ROOT, rel.replace("/", os.sep))
        vals = []
        for line in io.open(path, encoding="utf-8", newline="\n"):
            if line.startswith("version:"):
                vals.append(line.split(":", 1)[1].strip())
        if len(vals) != 1:
            print("VERSION_CHECK_BROKEN %s 有 %d 行 version: %r" % (rel, len(vals), vals))
            return None
        out[name] = vals[0]
    if len(set(out.values())) != 1:
        print("VERSION_CHECK_MISMATCH", out)
        return None
    return next(iter(out.values()))


def main():
    ver = versions()
    if ver is None:
        return 1
    print("gate_version =", ver)

    texts = {}
    for key, rel in list(GATE.items()) + [(ROSTER, ROSTER), ("pain", "docs/acceptance/pain.md")]:
        path = os.path.join(ROOT, rel.replace("/", os.sep))
        texts[key] = io.open(path, encoding="utf-8", newline="\n").read()

    bad = 0
    for term, files in sorted(REQUIRED.items()):
        miss = [f for f in files if term not in texts[f]]
        if miss:
            bad += 1
            print("TERM_MISSING %s -> %s" % (term, ", ".join(miss)))
    if bad:
        print("TERM_CHECK_FAILED terms_checked=%d failures=%d" % (len(REQUIRED), bad))
        return 1

    # AGENTS门集一致（P-68）：AGENTS.md 必须带「门集五份 `version: N`」且 N 与门集同值
    agents = io.open(os.path.join(ROOT, AGENTS), encoding="utf-8", newline="\n").read()
    import re
    m = re.search(r"门集五份 `version: (\d+)`", agents)
    if not m:
        print("AGENTS_CHECK_MISSING AGENTS.md 里没有「门集五份 `version: N`」行")
        return 1
    if m.group(1) != ver:
        print("AGENTS_CHECK_MISMATCH AGENTS.md=%s 门集=%s" % (m.group(1), ver))
        return 1
    print("AGENTS_CHECK_OK agents_version=%s" % ver)
    print("TERM_CHECK_OK terms_checked=%d gate_version=%s" % (len(REQUIRED), ver))
    return 0


if __name__ == "__main__":
    sys.exit(main())
