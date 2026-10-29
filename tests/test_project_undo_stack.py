"""画布级 undo/redo 栈持久化测试（issue #251：undo 栈刷新即失忆）。

store 层（firefly_project_undo.ProjectUndoStackMixin）：
- 序列化往返：前端快照（canvas-undo.js snapshotForUndo 形状）整体存 ops.db
  undo_snapshots（additive 迁移 undo_001），load 回来与原始内容 deep-equal
- 租户隔离：每项目独立 ops.db 文件 + project_id 主键过滤；A 项目栈不可见于 B 项目
- 裁剪上限：UNDO_STACK_MAX_ENTRIES=40（与 canvas-undo.js UNDO_LIMIT=40 同口径），
  超限时保留最新 N 条（与前端 push→shift 的 FIFO 语义一致）
- ADR 0021 失败模型：无 ops.db = 空状态；非存在项目 KeyError（路由映射 404）
- 与 #257 versions_snapshots 是不同租户（版本树 vs 会话级 undo 连续性），不同表

API 层（app/routers/api_project_undo.py）：
- GET /api/project-library/{id}/undo-stack → {project_id, undo, redo, updated_at}
- PUT /api/project-library/{id}/undo-stack body {undo, redo} → 200 回执
- 非存在项目 404；非法形状 400
"""
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

import main
from firefly_project_undo import UNDO_STACK_MAX_ENTRIES
from firefly_projects import ProjectStore


def _snapshot(nodes, selected_id="", extra=None):
    """canvas-undo.js snapshotForUndo 的快照形状（外加 pushUndo 的 extra 字段）。"""
    snap = {
        "nodes": nodes,
        "connections": [{"from": nodes[0]["id"] if nodes else "", "to": nodes[1]["id"] if len(nodes) > 1 else ""}],
        "selectedId": selected_id,
        "selectedIds": [selected_id] if selected_id else [],
        "selectedImage": {},
    }
    if extra:
        snap.update(extra)
    return snap


class ProjectUndoStackStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name) / "projects"
        self.store = ProjectStore(self.root, snapshot_delay=60.0)

    def tearDown(self):
        self.store.close()
        self.temp_dir.cleanup()

    def create_project(self, name="undo 栈"):
        return self.store.create_project(name)["id"]

    def test_roundtrip_serialization(self):
        """save → load 往返：内容 deep-equal，迁移 key 落在 ops_migrations。"""
        project_id = self.create_project()
        snap1 = _snapshot([{"id": "n1", "x": 10, "label": "img", "images": [{"asset_id": "A1"}]}], "n1")
        snap2 = _snapshot(
            [{"id": "n1"}, {"id": "n2"}],
            "n2",
            extra={"projectOriginalDelete": {"assetId": "orig-1", "deletionId": "del-1"}},
        )
        redo_snap = _snapshot([{"id": "n1"}], "n1")

        result = self.store.save_undo_stack(project_id, [snap1, snap2], [redo_snap])
        self.assertEqual(result["undo_count"], 2)
        self.assertEqual(result["redo_count"], 1)
        self.assertIsNotNone(result["updated_at"])

        got = self.store.load_undo_stack(project_id)
        self.assertEqual(got["undo"], [snap1, snap2])
        self.assertEqual(got["redo"], [redo_snap])
        self.assertEqual(got["project_id"], project_id)
        self.assertIsNotNone(got["updated_at"])

        # ops.db 里真实存在 undo_001 迁移记录与 undo_snapshots 表（ADR 0021 additive）
        db_path = self.root / project_id / "ops.db"
        self.assertTrue(db_path.is_file())
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            self.assertIn("undo_001", {r["key"] for r in conn.execute("SELECT key FROM ops_migrations")})
            tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertIn("undo_snapshots", tables)
            rows = conn.execute("SELECT project_id, undo_json, redo_json FROM undo_snapshots").fetchall()
        finally:
            conn.close()
        self.assertEqual(len(rows), 1)
        self.assertEqual(json.loads(rows[0]["undo_json"]), [snap1, snap2])

    def test_trim_to_max_entries_keeps_newest(self):
        """超限裁剪：保留最新 UNDO_STACK_MAX_ENTRIES 条（与前端 push→shift 同序）。"""
        project_id = self.create_project()
        undo = [{"i": i} for i in range(UNDO_STACK_MAX_ENTRIES + 10)]
        redo = [{"j": j} for j in range(UNDO_STACK_MAX_ENTRIES + 5)]

        self.store.save_undo_stack(project_id, undo, redo)
        got = self.store.load_undo_stack(project_id)

        self.assertEqual(len(got["undo"]), UNDO_STACK_MAX_ENTRIES)
        self.assertEqual(got["undo"], undo[-UNDO_STACK_MAX_ENTRIES:])
        self.assertEqual(got["undo"][0], {"i": 10})
        self.assertEqual(got["undo"][-1], {"i": 49})
        self.assertEqual(len(got["redo"]), UNDO_STACK_MAX_ENTRIES)
        self.assertEqual(got["redo"], redo[-UNDO_STACK_MAX_ENTRIES:])

    def test_tenant_isolation(self):
        """A 项目的栈不可见于 B 项目（独立 ops.db 文件 + project_id 过滤）。"""
        project_a = self.create_project("租户 A")
        project_b = self.create_project("租户 B")
        snap_a = _snapshot([{"id": "only-in-a"}], "only-in-a")
        snap_b = _snapshot([{"id": "only-in-b"}], "only-in-b")

        self.store.save_undo_stack(project_a, [snap_a], [])
        self.store.save_undo_stack(project_b, [snap_b], [])

        got_a = self.store.load_undo_stack(project_a)
        got_b = self.store.load_undo_stack(project_b)
        self.assertEqual(got_a["undo"], [snap_a])
        self.assertEqual(got_b["undo"], [snap_b])
        self.assertEqual(got_a["project_id"], project_a)
        self.assertEqual(got_b["project_id"], project_b)
        # 每项目独立 ops.db 文件（ADR 0021 租户隔离的物理层）
        self.assertTrue((self.root / project_a / "ops.db").is_file())
        self.assertTrue((self.root / project_b / "ops.db").is_file())

    def test_load_without_save_returns_empty(self):
        """ADR 0021 失败模型：无 ops.db = 空状态（不报错）。"""
        project_id = self.create_project()
        got = self.store.load_undo_stack(project_id)
        self.assertEqual(got, {"project_id": project_id, "undo": [], "redo": [], "updated_at": None})

    def test_save_overwrites_previous_stack(self):
        """重复 save 是整栈 upsert（单行）：后存的新栈覆盖旧栈。"""
        project_id = self.create_project()
        big = [_snapshot([{"id": f"n{i}"}], f"n{i}") for i in range(10)]
        self.store.save_undo_stack(project_id, big, big)
        small = [_snapshot([{"id": "fresh"}], "fresh")]
        self.store.save_undo_stack(project_id, small, [])

        got = self.store.load_undo_stack(project_id)
        self.assertEqual(got["undo"], small)
        self.assertEqual(got["redo"], [])
        conn = sqlite3.connect(str(self.root / project_id / "ops.db"))
        try:
            rows = conn.execute("SELECT COUNT(*) FROM undo_snapshots").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(rows, 1, "单行 upsert：不随 save 次数堆积")

    def test_missing_project_raises_key_error(self):
        with self.assertRaises(KeyError):
            self.store.save_undo_stack("never-created", [], [])
        with self.assertRaises(KeyError):
            self.store.load_undo_stack("never-created")

    def test_invalid_entries_raise_value_error(self):
        project_id = self.create_project()
        with self.assertRaises(ValueError):
            self.store.save_undo_stack(project_id, "not-a-list", [])
        with self.assertRaises(ValueError):
            self.store.save_undo_stack(project_id, [], "not-a-list")
        with self.assertRaises(ValueError):
            self.store.save_undo_stack(project_id, [{"x": object()}], [])


class ProjectUndoStackApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = ProjectStore(Path(self.temp_dir.name) / "projects", snapshot_delay=60.0)
        self.previous_store = main.FIREFLY_PROJECT_STORE
        main.FIREFLY_PROJECT_STORE = self.store
        self.client = TestClient(
            main.app,
            base_url="http://127.0.0.1",
            headers={"origin": "http://127.0.0.1"},
        )

    def tearDown(self):
        self.client.close()
        self.store.close()
        main.FIREFLY_PROJECT_STORE = self.previous_store
        self.temp_dir.cleanup()

    def make_project(self):
        created = self.client.post("/api/project-library", json={"name": "undo 栈"})
        self.assertEqual(created.status_code, 200)
        return created.json()["project"]["id"]

    def test_put_then_get_roundtrip(self):
        project_id = self.make_project()
        undo = [
            {"nodes": [{"id": "n1", "label": "before"}], "connections": [], "selectedId": "n1", "selectedIds": ["n1"], "selectedImage": {}},
            {"nodes": [{"id": "n1"}, {"id": "n2"}], "connections": [{"from": "n1", "to": "n2"}], "selectedId": "n2", "selectedIds": ["n2"], "selectedImage": {}},
        ]
        redo = [{"nodes": [{"id": "n1"}, {"id": "n2"}, {"id": "n3"}], "connections": [], "selectedId": "n3", "selectedIds": ["n3"], "selectedImage": {}}]

        put = self.client.put(f"/api/project-library/{project_id}/undo-stack", json={"undo": undo, "redo": redo})
        self.assertEqual(put.status_code, 200)
        body = put.json()
        self.assertEqual(body["project_id"], project_id)
        self.assertEqual(body["undo_count"], 2)
        self.assertEqual(body["redo_count"], 1)

        got = self.client.get(f"/api/project-library/{project_id}/undo-stack")
        self.assertEqual(got.status_code, 200)
        data = got.json()
        self.assertEqual(data["undo"], undo)
        self.assertEqual(data["redo"], redo)
        self.assertIsNotNone(data["updated_at"])

    def test_get_without_save_is_empty(self):
        project_id = self.make_project()
        got = self.client.get(f"/api/project-library/{project_id}/undo-stack")
        self.assertEqual(got.status_code, 200)
        self.assertEqual(got.json(), {"project_id": project_id, "undo": [], "redo": [], "updated_at": None})

    def test_put_over_limit_is_trimmed(self):
        project_id = self.make_project()
        undo = [{"i": i} for i in range(UNDO_STACK_MAX_ENTRIES + 10)]
        put = self.client.put(f"/api/project-library/{project_id}/undo-stack", json={"undo": undo, "redo": []})
        self.assertEqual(put.status_code, 200)
        self.assertEqual(put.json()["undo_count"], UNDO_STACK_MAX_ENTRIES)
        got = self.client.get(f"/api/project-library/{project_id}/undo-stack").json()
        self.assertEqual(got["undo"], undo[-UNDO_STACK_MAX_ENTRIES:])

    def test_unknown_project_is_404(self):
        self.assertEqual(self.client.get("/api/project-library/never-created/undo-stack").status_code, 404)
        self.assertEqual(
            self.client.put("/api/project-library/never-created/undo-stack", json={"undo": [], "redo": []}).status_code,
            404,
        )

    def test_invalid_shape_is_400(self):
        project_id = self.make_project()
        response = self.client.put(
            f"/api/project-library/{project_id}/undo-stack",
            json={"undo": "not-a-list", "redo": []},
        )
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
