# -*- coding: utf-8 -*-
"""layout_engine 单元测试：三分区布局 / 防重叠 / 已有画布偏移 / 边连通 / 确定性。"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from layout_engine import DeterministicLayout


def _plan(tasks):
    return {
        "source_file": "t.xlsx",
        "sheet_count": 1,
        "scanned_rows": len(tasks),
        "tasks": tasks,
    }


def _task(task_id, section):
    return {
        "task_id": task_id,
        "name": f"任务{task_id}",
        "section": section,
        "display_no": task_id,
        "base_prompt": "",
        "requirements": "",
        "section_index": 1,
        "source_row": "",
    }


def _rects(ops):
    return [
        (o["node_id"], o["x"], o["y"], o["width"], o["height"])
        for o in ops
        if o["type"] == "create_node"
    ]


def _overlap_pairs(rects):
    pairs = []
    for i in range(len(rects)):
        for j in range(i + 1, len(rects)):
            a_id, ax, ay, aw, ah = rects[i]
            b_id, bx, by, bw, bh = rects[j]
            if not (ax + aw <= bx or bx + bw <= ax or ay + ah <= by or by + bh <= ay):
                pairs.append((a_id, b_id))
    return pairs


class TestEmptyCanvasLayout(unittest.TestCase):
    def test_node_and_edge_counts(self):
        tasks = [_task(f"excel_{i}", "主图方案") for i in range(1, 4)]
        ops = DeterministicLayout().generate_canvas_operations(_plan(tasks))
        nodes = [o for o in ops if o["type"] == "create_node"]
        edges = [o for o in ops if o["type"] == "create_edge"]
        self.assertEqual(len(nodes), 2 + 2 * 3)  # plan + agent + 每任务 2 节点
        self.assertEqual(len(edges), 1 + 3 + 3)  # plan->agent + agent->task + task->result

    def test_no_overlap_single_column(self):
        tasks = [_task(f"excel_{i}", "主图方案") for i in range(1, 6)]
        rects = _rects(DeterministicLayout().generate_canvas_operations(_plan(tasks)))
        self.assertEqual(_overlap_pairs(rects), [])

    def test_no_overlap_two_columns(self):
        # 回归（F-8）：两列网格下，第一列结果节点不得与第二列任务节点水平重叠
        tasks = [_task(f"excel_{i}", "主图方案") for i in range(1, 9)]  # 8 任务 → 4 行 × 2 列
        rects = _rects(DeterministicLayout().generate_canvas_operations(_plan(tasks)))
        self.assertEqual(_overlap_pairs(rects), [])

    def test_edge_connectivity(self):
        tasks = [_task(f"excel_{i}", "主图方案") for i in range(1, 4)]
        ops = DeterministicLayout().generate_canvas_operations(_plan(tasks))
        pairs = {(o["from"], o["to"]) for o in ops if o["type"] == "create_edge"}
        expected = {("excel_plan", "excel_agent")}
        for t in tasks:
            expected.add(("excel_agent", t["task_id"]))
            expected.add((t["task_id"], f"{t['task_id']}-result"))
        self.assertEqual(pairs, expected)

    def test_deterministic(self):
        tasks = [_task(f"excel_{i}", "高级A+方案") for i in range(1, 4)]
        layout = DeterministicLayout()
        self.assertEqual(
            layout.generate_canvas_operations(_plan(tasks)),
            layout.generate_canvas_operations(_plan(tasks)),
        )

    def test_empty_tasks(self):
        self.assertEqual(DeterministicLayout().generate_canvas_operations(_plan([])), [])

    def test_zone_order_left_to_right(self):
        # 左方案 / 中 Agent / 右任务 三分区
        ops = DeterministicLayout().generate_canvas_operations(
            _plan([_task("excel_1", "主图方案")])
        )
        by_id = {o["node_id"]: o for o in ops if o["type"] == "create_node"}
        self.assertLess(by_id["excel_plan"]["x"], by_id["excel_agent"]["x"])
        self.assertLess(by_id["excel_agent"]["x"], by_id["excel_1"]["x"])


class TestExistingCanvasOffset(unittest.TestCase):
    def test_offset_applies_to_all_zones(self):
        # 回归（F-1）：已有节点时，plan / agent / 任务区都必须偏移，
        # agent 节点 y 不得遗漏 offset_y
        tasks = [_task("excel_1", "主图方案")]
        existing = {"nodes": [{"id": "old", "x": 0, "y": 0, "w": 500, "h": 400}]}
        ops = DeterministicLayout().generate_canvas_operations(_plan(tasks), existing)
        by_id = {o["node_id"]: o for o in ops if o["type"] == "create_node"}
        margin = DeterministicLayout.MARGIN
        # 偏移量 = max(0+400+100, 80) = 500 → 所有分区 y = 80+500
        self.assertEqual(by_id["excel_plan"]["y"], margin + 500)
        self.assertEqual(by_id["excel_agent"]["y"], margin + 500)
        self.assertEqual(by_id["excel_1"]["y"], margin + 500)

    def test_no_overlap_with_existing_nodes(self):
        tasks = [_task(f"excel_{i}", "主图方案") for i in range(1, 5)]
        existing = {"nodes": [
            {"id": "a", "x": 0, "y": 0, "w": 600, "h": 300},
            {"id": "b", "x": 100, "y": 400, "w": 800, "h": 500},
        ]}
        new_rects = _rects(DeterministicLayout().generate_canvas_operations(_plan(tasks), existing))
        existing_rects = [(n["id"], n["x"], n["y"], n["w"], n["h"]) for n in existing["nodes"]]
        all_rects = new_rects + existing_rects
        self.assertEqual(_overlap_pairs(all_rects), [])

    def test_width_height_legacy_keys(self):
        # 兼容 width/height 历史键名
        tasks = [_task("excel_1", "主图方案")]
        existing = {"nodes": [{"id": "old", "x": 0, "y": 0, "width": 900, "height": 700}]}
        ops = DeterministicLayout().generate_canvas_operations(_plan(tasks), existing)
        by_id = {o["node_id"]: o for o in ops if o["type"] == "create_node"}
        # 偏移量 = max(0+700+100, 80) = 800 → y = 80+800
        self.assertEqual(by_id["excel_plan"]["y"], DeterministicLayout.MARGIN + 800)


if __name__ == "__main__":
    unittest.main()
