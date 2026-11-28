"""画布耦合报告的自洽守卫（ADR 0020）。

`tools/canvas_coupling_report.py` 是继续拆分工作的入口工具。它一旦跟棘轮守卫
口径不一致，或者人工分类悄悄退化，就会给出误导性的拆分建议——而它的输出
看起来总是自洽的，没人会怀疑。所以这里锁三件事：

1. 总数必须等于 REVERSE_REFERENCE_CEILINGS 之和（两个工具口径一致）；
2. 所有「被 >=2 个模块引用」的共享名字都必须已分类（不许悄悄漏）；
3. 「可纯搬家」的名字必须真的不被核心调用（防止把会造成反向依赖的搬家当成安全搬家）；
4. #201 轨道 1 陷阱 2 的守卫：`canvasCore.NAME` 接口引用必须继续计入对 NAME 的
   反向引用——改写成接口后总数不能降（降了就是守卫自我失明，棘轮数字「变好」
   而耦合一点没少）。
"""
from __future__ import annotations

import functools
import unittest

from tests.test_canvas_core_layer import REVERSE_REFERENCE_CEILINGS
from tools.canvas_coupling_report import (
    CATEGORY_BY_NAME,
    count_interface_references,
    count_references,
    coupling,
    reverse_coupling,
    summarize,
)

# 棘轮上限（仿 REVERSE_REFERENCE_CEILINGS 的写法）：未分类引用只许降不许升。
# 2026-10-19（#201 前置）补全分类前实测 130（99 名字、全部 solo）；补全后 0。
# 分类是工具判 movable/blocked 的前提，未分类一升，那些引用的判定就是盲的。
# 之后新出现的跨模块引用（核心新增名字）必须先进 CATEGORY_BY_NAME 分类，
# 否则这里红灯。
UNCLASSIFIED_REFERENCE_CEILING = 0


# 155s→~15s：coupling() 每次调用都重解析全部画布 JS（~13s），而 11 个方法吃同一份
# 输入——缓存一次，结果确定性不变。旧行为下单模块在 #204 分片里是 155.2s 巨块，
# 并行争抢下膨胀到 189-195s 超单片守卫（trial-39888/71728 实败）。
@functools.lru_cache(maxsize=1)
def _cached_summary():
    return summarize(coupling())


@functools.lru_cache(maxsize=1)
def _cached_reverse():
    return reverse_coupling()


class CanvasCouplingReportTests(unittest.TestCase):
    def setUp(self):
        self.summary = _cached_summary()

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

    def test_free_plus_interface_equals_total_per_module(self):
        # #201 轨道 1 的拆分自洽：总数 = 自由全局 + 接口，对每个模块都成立。
        for module, stats in self.summary["per_module"].items():
            self.assertEqual(
                stats["free_refs"] + stats["interface_refs"],
                stats["total"],
                f"{module} 的自由/接口拆分与总数不一致（口径漂移）",
            )
        self.assertEqual(
            self.summary["free_total"] + self.summary["interface_total"],
            self.summary["grand_total"],
        )

    def test_interface_call_still_counts_as_reverse_reference(self):
        # #201 陷阱 2：守卫不许在新旧两种写法下计数不一致。裸 `render()` 和接口
        # `canvasCore.render()` 必须计入相同的反向引用数，否则棘轮「变好」而耦合没少。
        old_text = "render(); shell; scheduleSave()"
        new_text = "canvasCore.render(); canvasCore.shell; canvasCore.scheduleSave()"
        names = {"render", "shell", "scheduleSave"}
        self.assertEqual(
            dict(count_references(old_text, names, set())),
            {"render": 1, "shell": 1, "scheduleSave": 1},
        )
        self.assertEqual(
            dict(count_references(new_text, names, set())),
            {"render": 1, "shell": 1, "scheduleSave": 1},
            "canvasCore.NAME 没有被计入对 NAME 的反向引用：守卫自我失明",
        )

    def test_interface_references_are_split_off_from_free_globals(self):
        text = "render(); canvasCore.render(); canvasCore.shell; shell"
        names = {"render", "shell"}
        total = count_references(text, names, set())
        iface = count_interface_references(text, names, set())
        self.assertEqual(dict(iface), {"render": 1, "shell": 1})
        free = {n: total[n] - iface.get(n, 0) for n in total}
        self.assertEqual(dict(free), {"render": 1, "shell": 1})

    def test_member_access_is_not_an_interface_call(self):
        # foo.render 是成员访问，不是接口引用；不计入 interface（总数口径不变）。
        self.assertEqual(
            dict(count_interface_references("foo.render(); canvasCore.render()", {"render"}, set())),
            {"render": 1},
        )

    def test_local_name_exclusion_applies_to_interface_count(self):
        # 口径与总数一致：模块自己声明过的名字，接口引用也不计入（避免 free 变负）。
        self.assertEqual(
            dict(count_interface_references("canvasCore.render()", {"render"}, {"render"})),
            {},
        )


    def test_agent_reference_synchronizer_is_classified_at_its_tui_owner(self):
        self.assertEqual(CATEGORY_BY_NAME.get("syncAgentInputNodeRefs"), "hub")
        rows = {row["name"]: row for row in _cached_reverse()["reverse_rows"]}
        self.assertNotIn("syncAgentInputNodeRefs", rows)

    def test_storage_projection_service_is_classified_without_filtering_references(self):
        self.assertEqual(CATEGORY_BY_NAME.get("canvasForStorage"), "hub")
        references = count_references(
            "typeof canvasCore.canvasForStorage; canvasCore.canvasForStorage()",
            {"canvasForStorage"},
            set(),
        )
        self.assertEqual(references["canvasForStorage"], 2)
        report = summarize({"canvas-agent-tui.js": dict(references)})
        self.assertEqual(report["grand_total"], 2)
        self.assertEqual(report["per_category"]["hub"], 2)

    def test_reverse_rows_expose_ownership_classification_without_filtering_counts(self):
        rev = _cached_reverse()
        rows = {row["name"]: row for row in rev["reverse_rows"]}
        expected = {
            "finalizePendingCanvasUndo": ("canvas-undo.js", "hub"),
            "reconcileCanvasDeltaMediaReferences": ("canvas-media-history.js", "util"),
            "refreshProjectMediaLibraryItems": ("canvas-undo.js", "hub"),
        }
        for name, (owner, category) in expected.items():
            with self.subTest(name=name):
                self.assertEqual(rows[name]["owner"], owner)
                self.assertEqual(rows[name]["category"], category)
        self.assertEqual(rev["reverse_names"], len(rev["reverse_rows"]))
        self.assertEqual(rev["reverse_core_refs"], sum(row["core_refs"] for row in rev["reverse_rows"]))
        self.assertTrue(all("category" in row for row in rev["reverse_rows"]))

    def test_reviewed_resume_reference_is_included(self):
        rows = {row["name"]: row for row in _cached_reverse()["reverse_rows"]}
        self.assertEqual(rows["resumeSmartPendingActionResumes"]["core_refs"], 2)
        self.assertEqual(rows["queueSmartPendingActionResume"]["core_refs"], 2)

    def test_activity_bridge_remains_measured_and_classified(self):
        rows = {row["name"]: row for row in _cached_reverse()["reverse_rows"]}
        self.assertEqual(rows["canvasAgentActivity"], {
            "name": "canvasAgentActivity",
            "owner": "canvas-agent-activity.js",
            "core_refs": 1,
            "owner_refs": 0,
            "category": "hub",
        })
        self.assertEqual(rows["isSmartAgentTaskNode"]["core_refs"], 5)

    def test_completion_adapter_remains_measured_and_classified(self):
        rows = {row["name"]: row for row in _cached_reverse()["reverse_rows"]}
        self.assertEqual(rows["canvasAgentCompletion"], {
            "name": "canvasAgentCompletion",
            "owner": "canvas-agent-completion.js",
            "core_refs": 1,
            "owner_refs": 0,
            "category": "hub",
        })
        self.assertEqual(rows["isSmartAgentNode"]["core_refs"], 5)
        self.assertEqual(rows["isSmartAgentNode"]["owner_refs"], 11)

    def test_chat_reader_adapter_remains_measured_and_classified(self):
        rows = {row["name"]: row for row in _cached_reverse()["reverse_rows"]}
        self.assertEqual(rows["canvasAgentChat"], {
            "name": "canvasAgentChat", "owner": "canvas-agent-chat.js",
            "core_refs": 1, "owner_refs": 0, "category": "hub",
        })

    def test_reverse_coupling_is_measurable_and_pinned(self):
        # 两个引用方向必须独立实测；合法调用也须同步 ADR 0020 和精确计数，不能豁免。
        rev = _cached_reverse()
        self.assertEqual(rev["reverse_names"], 275)
        self.assertEqual(rev["reverse_core_refs"], 1026)
        self.assertEqual(rev["core_dominant_names"], 182)
        self.assertEqual(rev["core_dominant_core_refs"], 832)
        rows = {(row["owner"], row["name"]): row for row in rev["reverse_rows"]}
        self.assertEqual(rows[("canvas-generation-cascade.js", "composer")]["core_refs"], 65)
        self.assertEqual(rows[("canvas-node-types.js", "NODE_TYPE_IMAGE")], {
            "name": "NODE_TYPE_IMAGE", "owner": "canvas-node-types.js",
            "core_refs": 19, "owner_refs": 1, "category": "unclassified",
        })
        self.assertEqual(rows[("canvas-media-history.js", "mediaReferencesForStorage")], {
            "name": "mediaReferencesForStorage", "owner": "canvas-media-history.js",
            "core_refs": 6, "owner_refs": 3, "category": "unclassified",
        })
        self.assertEqual(rows[("canvas-result-retrieval.js", "canvasResultRetrieval")], {
            "name": "canvasResultRetrieval", "owner": "canvas-result-retrieval.js",
            "core_refs": 8, "owner_refs": 0, "category": "hub",
        })
        self.assertEqual(rows[("canvas-media-history.js", "scheduleSmartHistoryWindowRender")]["core_refs"], 3)
        # canvas-core.js 是合法下层，不能当「寄居在模块里」的 owner。
        self.assertNotIn("canvas-core.js", rev["by_owner"])
        self.assertNotIn("smart-canvas.js", rev["by_owner"])
        # 「核心用得比 owner 多」必须是全集子集，否则两个口径自相矛盾。
        self.assertLessEqual(rev["core_dominant_names"], rev["reverse_names"])

    def test_reviewed_batch_geometry_references_remain_measured(self):
        # #495 的兼容参数减少了部分调用，但迁移和 owner 自引用也改变了主导集合。
        rows = {(row["owner"], row["name"]): row for row in _cached_reverse()["reverse_rows"]}
        expected = {
            ("canvas-layout-utils.js", "BATCH_NODE_PAD_H"): (3, 0),
            ("canvas-layout-utils.js", "BATCH_STATUSBAR_H"): (5, 0),
            ("canvas-layout-utils.js", "BATCH_THUMB"): (24, 3),
            ("canvas-layout-utils.js", "SINGLE_IMAGE_LAYOUT"): (1, 4),
            ("canvas-layout-utils.js", "SMART_GROUP_LAYOUT"): (8, 7),
            ("canvas-layout-utils.js", "getBatchThumbGridSize"): (8, 0),
            ("canvas-node-ops.js", "SMART_GROUP_MAX_VISIBLE_ROWS"): (6, 1),
        }
        for key, counts in expected.items():
            with self.subTest(owner=key[0], name=key[1]):
                self.assertEqual((rows[key]["core_refs"], rows[key]["owner_refs"]), counts)


if __name__ == "__main__":
    unittest.main()
