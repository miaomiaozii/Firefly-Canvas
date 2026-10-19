"""画布耦合报告的自洽守卫（ADR 0020）。

`tools/canvas_coupling_report.py` 是继续拆分工作的入口工具。它一旦跟棘轮守卫
口径不一致，或者人工分类悄悄退化，就会给出误导性的拆分建议——而它的输出
看起来总是自洽的，没人会怀疑。所以这里锁三件事：

1. 总数必须等于 REVERSE_REFERENCE_CEILINGS 之和（两个工具口径一致）；
2. 所有「被 >=2 个模块引用」的共享名字都必须已分类（不许悄悄漏）；
3. 「可纯搬家」的名字必须真的不被核心调用（防止把会造成反向依赖的搬家当成安全搬家）。
"""
from __future__ import annotations

import unittest

from tests.test_canvas_core_layer import REVERSE_REFERENCE_CEILINGS
from tools.canvas_coupling_report import coupling, summarize

# 棘轮上限（仿 REVERSE_REFERENCE_CEILINGS 的写法）：未分类引用只许降不许升。
# 2026-10-19（#201 前置）补全分类前实测 130（99 名字、全部 solo）；补全后 0。
# 分类是工具判 movable/blocked 的前提，未分类一升，那些引用的判定就是盲的。
# 之后新出现的跨模块引用（核心新增名字）必须先进 CATEGORY_BY_NAME 分类，
# 否则这里红灯。
UNCLASSIFIED_REFERENCE_CEILING = 0


class CanvasCouplingReportTests(unittest.TestCase):
    def setUp(self):
        self.summary = summarize(coupling())

    def test_total_matches_the_ratchet_ceilings(self):
        # 两个工具必须同一口径，否则「降了多少」有两个互相矛盾的答案。
        self.assertEqual(
            self.summary["grand_total"],
            sum(REVERSE_REFERENCE_CEILINGS.values()),
            "canvas_coupling_report 的总数与棘轮上限之和不一致：口径漂移了",
        )

    def test_every_shared_core_name_is_classified(self):
        # 被 >=2 模块引用的名字才是真共享服务，分类决定拆方向，漏一个就可能拆错。
        self.assertEqual(
            self.summary["unclassified_shared_names"],
            [],
            "这些被多个模块引用的核心名字还没有人工分类，见 ADR 0020",
        )

    def test_movable_names_are_not_called_by_the_core(self):
        # 「可纯搬家」的定义就是核心自己不调用；若这里出现 core_internal_calls>0，
        # 搬走会造出 核心->模块 反向依赖，工具在给出危险建议。
        offenders = [
            row["name"]
            for row in self.summary["movable"]
            if row["core_internal_calls"] > 0
        ]
        self.assertEqual(offenders, [])

    def test_unclassified_reference_count_does_not_grow(self):
        # 棘轮：未分类引用只许降不许升（见 ADR 0020：分类表是 movable/blocked
        # 判定的前提，未分类一升，工具对那批引用就是盲的）。
        unclassified = self.summary["per_category"].get("unclassified", 0)
        self.assertLessEqual(
            unclassified,
            UNCLASSIFIED_REFERENCE_CEILING,
            f"未分类引用 {unclassified} 超过棘轮上限 {UNCLASSIFIED_REFERENCE_CEILING}："
            "新增的跨模块引用必须先分类进 CATEGORY_BY_NAME，见 ADR 0020",
        )

    def test_solo_and_shared_partition_is_complete(self):
        scope = self.summary["scope"]
        self.assertEqual(
            scope["solo_refs"] + scope["shared_refs"], self.summary["grand_total"]
        )
        self.assertEqual(
            scope["movable_solo_names"] + scope["blocked_by_core_call_names"],
            scope["solo_names"],
        )


if __name__ == "__main__":
    unittest.main()
