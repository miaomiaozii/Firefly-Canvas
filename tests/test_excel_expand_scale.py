# -*- coding: utf-8 -*-
"""#109 T5：60 节点规模验证（原子+无重叠+边连通+409 冲突）。"""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

import main
import app.routers.api_excel_expand as api_excel_expand
from firefly_projects import ProjectStore
from layout_engine import DeterministicLayout


def _make_task(task_id, section, has_refs=False):
    """创建一个任务字典。"""
    task = {
        "task_id": task_id,
        "name": f"任务{task_id}",
        "section": section,
        "display_no": task_id,
        "base_prompt": f"prompt for {task_id}",
        "requirements": "",
        "target_aspect_ratio": "1:1",
        "section_index": 1,
        "source_row": "",
        "generation_references": [],
    }
    if has_refs:
        task["generation_references"] = [
            {"asset_id": f"asset-{task_id}", "source": f"ref-{task_id}.png", "url": None}
        ]
    return task


def _make_plan(num_tasks):
    """创建一个 num_tasks 个任务的 Plan IR。"""
    tasks = []
    for i in range(1, num_tasks + 1):
        # 每 3 个任务有 1 个带参考图
        has_refs = (i % 3 == 0)
        section = "主图方案" if i <= 30 else "高级A+方案"
        tasks.append(_make_task(f"excel_{i}", section, has_refs))
    return {
        "source_file": "test_60.xlsx",
        "sheet_count": 1,
        "scanned_rows": num_tasks,
        "tasks": tasks,
    }


def _rects_from_ops(ops):
    """从操作序列提取节点矩形。"""
    return [
        (o["node_id"], o["x"], o["y"], o["width"], o["height"])
        for o in ops
        if o["type"] == "create_node"
    ]


def _overlap_pairs(rects):
    """检测重叠对。"""
    pairs = []
    for i in range(len(rects)):
        for j in range(i + 1, len(rects)):
            a_id, ax, ay, aw, ah = rects[i]
            b_id, bx, by, bw, bh = rects[j]
            if not (ax + aw <= bx or bx + bw <= ax or ay + ah <= by or by + bh <= ay):
                pairs.append((a_id, b_id))
    return pairs


class TestScale60Expansion(unittest.TestCase):
    """60 节点规模验证。"""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="firefly-excel-expand-scale-")
        self.store = ProjectStore(Path(self.temp_dir.name) / "projects", snapshot_delay=0)
        self.previous_store = main.FIREFLY_PROJECT_STORE
        main.FIREFLY_PROJECT_STORE = self.store
        # 固定 _app_main 指向 main：它是模块级常量，import 时按顺序捕获，
        # 与其他测试合跑时可能落到 __main__（缺 project_store_call）。
        self.previous_app_main = api_excel_expand._app_main
        api_excel_expand._app_main = main
        self.client = TestClient(
            main.app, base_url="http://127.0.0.1", headers={"origin": "http://127.0.0.1"}
        )

    def tearDown(self):
        self.client.close()
        main.FIREFLY_PROJECT_STORE = self.previous_store
        api_excel_expand._app_main = self.previous_app_main
        self.store.close()
        self.temp_dir.cleanup()

    def test_layout_60_tasks_no_overlap(self):
        """布局器：60 任务无重叠。"""
        plan = _make_plan(60)
        ops = DeterministicLayout().generate_canvas_operations(plan)
        rects = _rects_from_ops(ops)
        overlaps = _overlap_pairs(rects)
        self.assertEqual(overlaps, [], f"发现 {len(overlaps)} 个重叠对: {overlaps[:5]}")

    def test_layout_60_tasks_node_edge_counts(self):
        """布局器：60 任务节点/边计数正确。"""
        plan = _make_plan(60)
        ops = DeterministicLayout().generate_canvas_operations(plan)
        nodes = [o for o in ops if o["type"] == "create_node"]
        edges = [o for o in ops if o["type"] == "create_edge"]

        # 60 任务：每任务 1 提示词 + 1 生图 = 120 基础节点
        # 20 个任务有参考图（每 3 个 1 个）= 20 参考图节点
        # 总计：140 节点
        self.assertEqual(len(nodes), 140, f"期望 140 节点，实际 {len(nodes)}")

        # 边：60 提示词→生图 + 20 参考图→生图 = 80 边
        self.assertEqual(len(edges), 80, f"期望 80 边，实际 {len(edges)}")

    def test_layout_60_tasks_edge_connectivity(self):
        """布局器：60 任务边全连通。"""
        plan = _make_plan(60)
        ops = DeterministicLayout().generate_canvas_operations(plan)
        edge_pairs = {(o["from"], o["to"]) for o in ops if o["type"] == "create_edge"}

        # 每任务必须有 提示词→生图 边
        for i in range(1, 61):
            task_id = f"excel_{i}"
            self.assertIn(
                (f"{task_id}-prompt", f"{task_id}-image"),
                edge_pairs,
                f"缺少 {task_id} 的 提示词→生图 边"
            )

        # 有参考图的任务必须有 参考图→生图 边
        for i in range(1, 61):
            if i % 3 == 0:
                task_id = f"excel_{i}"
                self.assertIn(
                    (f"{task_id}-ref-0", f"{task_id}-image"),
                    edge_pairs,
                    f"缺少 {task_id} 的 参考图→生图 边"
                )

    def test_layout_60_tasks_deterministic(self):
        """布局器：60 任务确定性（同输入同输出）。"""
        plan = _make_plan(60)
        layout = DeterministicLayout()
        ops1 = layout.generate_canvas_operations(plan)
        ops2 = layout.generate_canvas_operations(plan)
        self.assertEqual(ops1, ops2)

    def test_expand_api_60_tasks_atomic(self):
        """API：60 任务原子展开成功。"""
        project = self.store.create_project("Scale 60 test")

        # 先上传参考图资产（20 个），获取真实 asset_id
        asset_ids = {}
        for i in range(1, 61):
            if i % 3 == 0:
                record = self.store.import_media(
                    project["id"],
                    b"\x89PNG-fake-ref-bytes",
                    f"ref-excel_{i}.png",
                    "image/png"
                )
                asset_ids[i] = record["asset_id"]

        # 创建计划，使用真实 asset_id
        plan = _make_plan(60)
        for task in plan["tasks"]:
            task_id = task["task_id"]
            idx = int(task_id.split("_")[1])
            if idx in asset_ids:
                task["generation_references"][0]["asset_id"] = asset_ids[idx]

        response = self.client.post(
            "/api/excel/expand-plan",
            json={"project_id": project["id"], "plan": plan}
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["success"])
        # 140 节点，80 边
        self.assertEqual(body["nodes_created"], 140)
        self.assertEqual(body["edges_created"], 80)

        # 验证画布上确实有这些节点
        canvas = self.store.canvas(project["id"])
        node_ids = {n["id"] for n in canvas["nodes"]}
        for i in range(1, 61):
            task_id = f"excel_{i}"
            self.assertIn(f"{task_id}-prompt", node_ids)
            self.assertIn(f"{task_id}-image", node_ids)
            if i % 3 == 0:
                self.assertIn(f"{task_id}-ref-0", node_ids)

    def test_expand_api_409_conflict(self):
        """API：并发展开冲突报 409（通过直接调用 apply_mutations 模拟）。"""
        from app.core import RevisionConflict
        project = self.store.create_project("409 conflict test")

        # 获取当前画布 revision
        canvas = self.store.canvas(project["id"])
        base_revision = int(canvas.get("revision") or 0)

        # 先修改画布（提升 revision）
        self.store.apply_mutations(
            project["id"],
            [{"op": "upsert_node", "node": {"id": "test-node", "type": "smart-prompt", "text": "test"}}],
            base_revision=base_revision
        )

        # 现在用旧的 base_revision 应用 mutations → 应该报 RevisionConflict
        with self.assertRaises(RevisionConflict):
            self.store.apply_mutations(
                project["id"],
                [{"op": "upsert_node", "node": {"id": "test-node-2", "type": "smart-prompt", "text": "test2"}}],
                base_revision=base_revision  # 旧的 revision
            )

    def test_expand_api_no_overlap_on_canvas(self):
        """API：展开后画布上无重叠。"""
        project = self.store.create_project("No overlap test")

        # 先上传参考图资产
        asset_ids = {}
        for i in range(1, 61):
            if i % 3 == 0:
                record = self.store.import_media(
                    project["id"],
                    b"\x89PNG-fake-ref-bytes",
                    f"ref-excel_{i}.png",
                    "image/png"
                )
                asset_ids[i] = record["asset_id"]

        # 创建计划，使用真实 asset_id
        plan = _make_plan(60)
        for task in plan["tasks"]:
            task_id = task["task_id"]
            idx = int(task_id.split("_")[1])
            if idx in asset_ids:
                task["generation_references"][0]["asset_id"] = asset_ids[idx]

        response = self.client.post(
            "/api/excel/expand-plan",
            json={"project_id": project["id"], "plan": plan}
        )
        self.assertEqual(response.status_code, 200, response.text)

        # 验证画布上无重叠
        canvas = self.store.canvas(project["id"])
        rects = [
            (n["id"], n["x"], n["y"], n.get("w", n.get("width", 0)), n.get("h", n.get("height", 0)))
            for n in canvas["nodes"]
        ]
        overlaps = _overlap_pairs(rects)
        self.assertEqual(overlaps, [], f"画布上发现 {len(overlaps)} 个重叠对: {overlaps[:5]}")


if __name__ == "__main__":
    unittest.main()
