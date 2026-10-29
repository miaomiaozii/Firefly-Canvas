"""画布批注对象持久化测试（issue #272：批注=画布可持久画布对象，ops.db ADR 0021 租户之三 anno_*）。

产品决策（#259 评论区维护者已批）：批注=画布可持久对象，存 ops.db 新租户 anno_*
（additive 迁移）；可撤销、刷新恢复、随画板走。本票不含 runAll 批注→任务转化（下一票）。

store 层（firefly_project_annotations.ProjectAnnotationsMixin）：
- CRUD 往返：create → list → update → delete 全走 ops.db anno_annotations
  （additive 迁移 anno_001），list 返回内容与写入 deep-equal
- 锚定：批注锚定节点（node_id）+ 节点内相对坐标（x/y ∈ [0,1]，0..1 归一化，
  随画板缩放不失真）；状态机 status ∈ {pending, converted}（converted 由下一票 runAll 写）
- 租户隔离：每项目独立 ops.db 文件 + project_id 过滤；A 项目批注不可见于 B 项目
- ADR 0021 失败模型：无 ops.db = 空状态（list 返回 []，不报错）；非存在项目 KeyError
  （路由映射 404）；字段非法 ValueError（路由映射 400）

API 层（app/routers/api_project_annotations.py）：
- GET  /api/project-library/{id}/annotations → {project_id, items}
- POST /api/project-library/{id}/annotations body {node_id, text, x?, y?, status?} → 201 回执
- PATCH /api/project-library/{id}/annotations/{anno_id} body {text?, x?, y?, status?} → 200
- DELETE /api/project-library/{id}/annotations/{anno_id} → 200
- 非存在项目 404；非存在批注 404；非法字段 400
"""
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
from firefly_projects import ProjectStore

MAX_TEXT_LEN = 500


class ProjectAnnotationsStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name) / "projects"
        self.store = ProjectStore(self.root, snapshot_delay=60.0)

    def tearDown(self):
        self.store.close()
        self.temp_dir.cleanup()

    def create_project(self, name="批注项目"):
        return self.store.create_project(name)["id"]

    def test_create_and_list_roundtrip(self):
        """create → list 往返：内容 deep-equal，迁移 key 落在 ops_migrations。"""
        project_id = self.create_project()
        created = self.store.create_project_annotation(
            project_id, node_id="n1", text="把背景换成黄昏", x=0.3, y=0.25
        )
        self.assertEqual(created["project_id"], project_id)
        self.assertEqual(created["node_id"], "n1")
        self.assertEqual(created["text"], "把背景换成黄昏")
        self.assertAlmostEqual(created["x"], 0.3)
        self.assertAlmostEqual(created["y"], 0.25)
        self.assertEqual(created["status"], "pending")  # 默认 pending（产品决策：pending→converted）
        self.assertIsNotNone(created["id"])
        self.assertIsNotNone(created["created_at"])
        self.assertIsNotNone(created["updated_at"])

        items = self.store.list_project_annotations(project_id)
        self.assertEqual(items, [created])

        # ops.db 里真实存在 anno_001 迁移记录与 anno_annotations 表（ADR 0021 additive）
        db_path = self.root / project_id / "ops.db"
        self.assertTrue(db_path.is_file())
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            self.assertIn("anno_001", {r["key"] for r in conn.execute("SELECT key FROM ops_migrations")})
            tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertIn("anno_annotations", tables)
            rows = conn.execute("SELECT project_id, node_id, text, status FROM anno_annotations").fetchall()
        finally:
            conn.close()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["text"], "把背景换成黄昏")
        self.assertEqual(rows[0]["status"], "pending")

    def test_anchor_position_clamped_to_unit_interval(self):
        """锚定坐标归一化口径：x/y 必须在 [0,1] 内（超出 = 非法，ValueError）。"""
        project_id = self.create_project()
        for bad_x, bad_y in [(-0.1, 0.5), (1.0001, 0.5), (0.5, -1), (0.5, 2)]:
            with self.assertRaises(ValueError, msg=f"x={bad_x}, y={bad_y} 应越界"):
                self.store.create_project_annotation(
                    project_id, node_id="n1", text="t", x=bad_x, y=bad_y
                )
        # 边界值 0 与 1 合法
        self.store.create_project_annotation(project_id, node_id="n1", text="角点", x=0.0, y=1.0)
        items = self.store.list_project_annotations(project_id)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["x"], 0.0)
        self.assertEqual(items[0]["y"], 1.0)

    def test_update_text_and_status(self):
        """update：改文本 / 坐标 / 状态（pending→converted 由 runAll 票驱动，这里只验证通路）。"""
        project_id = self.create_project()
        created = self.store.create_project_annotation(project_id, node_id="n1", text="原文")
        updated = self.store.update_project_annotation(
            project_id, created["id"], text="改后", x=0.9, y=0.1, status="converted"
        )
        self.assertEqual(updated["id"], created["id"])
        self.assertEqual(updated["text"], "改后")
        self.assertAlmostEqual(updated["x"], 0.9)
        self.assertAlmostEqual(updated["y"], 0.1)
        self.assertEqual(updated["status"], "converted")
        self.assertEqual(updated["created_at"], created["created_at"])  # 创建时间不变

        items = self.store.list_project_annotations(project_id)
        self.assertEqual(items, [updated])

    def test_update_partial_fields(self):
        """update 只传要改的字段：其余保持原值。"""
        project_id = self.create_project()
        created = self.store.create_project_annotation(project_id, node_id="n1", text="原文", x=0.2, y=0.4)
        updated = self.store.update_project_annotation(project_id, created["id"], text="只改文本")
        self.assertEqual(updated["text"], "只改文本")
        self.assertAlmostEqual(updated["x"], 0.2)
        self.assertAlmostEqual(updated["y"], 0.4)
        self.assertEqual(updated["status"], "pending")

    def test_delete(self):
        project_id = self.create_project()
        created = self.store.create_project_annotation(project_id, node_id="n1", text="待删")
        result = self.store.delete_project_annotation(project_id, created["id"])
        self.assertEqual(result["id"], created["id"])
        self.assertEqual(self.store.list_project_annotations(project_id), [])

    def test_delete_unknown_annotation_raises_key_error(self):
        project_id = self.create_project()
        with self.assertRaises(KeyError):
            self.store.delete_project_annotation(project_id, 99999)

    def test_update_unknown_annotation_raises_key_error(self):
        project_id = self.create_project()
        with self.assertRaises(KeyError):
            self.store.update_project_annotation(project_id, 99999, text="x")

    def test_invalid_fields_raise_value_error(self):
        project_id = self.create_project()
        with self.assertRaises(ValueError):
            self.store.create_project_annotation(project_id, node_id="", text="t")
        with self.assertRaises(ValueError):
            self.store.create_project_annotation(project_id, node_id="  ", text="t")
        with self.assertRaises(ValueError):
            self.store.create_project_annotation(project_id, node_id="n1", text="")
        with self.assertRaises(ValueError):
            self.store.create_project_annotation(project_id, node_id="n1", text="   ")
        with self.assertRaises(ValueError):
            self.store.create_project_annotation(project_id, node_id="n1", text="x" * (MAX_TEXT_LEN + 1))
        with self.assertRaises(ValueError):
            self.store.create_project_annotation(project_id, node_id="n1", text="t", status="bogus")
        with self.assertRaises(ValueError):
            self.store.create_project_annotation(project_id, node_id="n1", text="t", x="0.5")
        # update 路径同样校验
        created = self.store.create_project_annotation(project_id, node_id="n1", text="t")
        with self.assertRaises(ValueError):
            self.store.update_project_annotation(project_id, created["id"], text="")
        with self.assertRaises(ValueError):
            self.store.update_project_annotation(project_id, created["id"], status="bogus")
        with self.assertRaises(ValueError):
            self.store.update_project_annotation(project_id, created["id"], x=1.5)
        # 文本上限边界：正好 MAX_TEXT_LEN 合法
        self.store.update_project_annotation(project_id, created["id"], text="y" * MAX_TEXT_LEN)

    def test_missing_project_raises_key_error(self):
        with self.assertRaises(KeyError):
            self.store.create_project_annotation("never-created", node_id="n1", text="t")
        with self.assertRaises(KeyError):
            self.store.list_project_annotations("never-created")
        with self.assertRaises(KeyError):
            self.store.update_project_annotation("never-created", 1, text="x")
        with self.assertRaises(KeyError):
            self.store.delete_project_annotation("never-created", 1)

    def test_tenant_isolation(self):
        """A 项目的批注不可见于 B 项目（独立 ops.db 文件 + project_id 过滤）。"""
        project_a = self.create_project("租户 A")
        project_b = self.create_project("租户 B")
        a_anno = self.store.create_project_annotation(project_a, node_id="a1", text="A 的批注")
        self.store.create_project_annotation(project_b, node_id="b1", text="B 的批注")

        got_a = self.store.list_project_annotations(project_a)
        got_b = self.store.list_project_annotations(project_b)
        self.assertEqual(got_a, [a_anno])
        self.assertEqual(got_b[0]["text"], "B 的批注")
        # 每项目独立 ops.db 文件（ADR 0021 租户隔离的物理层）
        self.assertTrue((self.root / project_a / "ops.db").is_file())
        self.assertTrue((self.root / project_b / "ops.db").is_file())

    def test_list_without_create_returns_empty(self):
        """ADR 0021 失败模型：无 ops.db = 空状态（不报错）。"""
        project_id = self.create_project()
        self.assertEqual(self.store.list_project_annotations(project_id), [])


class ProjectAnnotationsApiTests(unittest.TestCase):
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
        created = self.client.post("/api/project-library", json={"name": "批注项目"})
        self.assertEqual(created.status_code, 200)
        return created.json()["project"]["id"]

    def test_full_crud_roundtrip(self):
        project_id = self.make_project()

        # POST 创建（201）
        post = self.client.post(
            f"/api/project-library/{project_id}/annotations",
            json={"node_id": "n1", "text": "把背景换成黄昏", "x": 0.3, "y": 0.25},
        )
        self.assertEqual(post.status_code, 201)
        created = post.json()
        self.assertEqual(created["node_id"], "n1")
        self.assertEqual(created["text"], "把背景换成黄昏")
        self.assertEqual(created["status"], "pending")

        # GET 列表
        got = self.client.get(f"/api/project-library/{project_id}/annotations")
        self.assertEqual(got.status_code, 200)
        body = got.json()
        self.assertEqual(body["project_id"], project_id)
        self.assertEqual(body["items"], [created])

        # PATCH 编辑（文本 + 状态）
        patch = self.client.patch(
            f"/api/project-library/{project_id}/annotations/{created['id']}",
            json={"text": "改成夕阳", "status": "converted"},
        )
        self.assertEqual(patch.status_code, 200)
        updated = patch.json()
        self.assertEqual(updated["id"], created["id"])
        self.assertEqual(updated["text"], "改成夕阳")
        self.assertEqual(updated["status"], "converted")

        # DELETE
        delete = self.client.delete(f"/api/project-library/{project_id}/annotations/{created['id']}")
        self.assertEqual(delete.status_code, 200)
        self.assertEqual(delete.json()["id"], created["id"])
        got2 = self.client.get(f"/api/project-library/{project_id}/annotations").json()
        self.assertEqual(got2["items"], [])

    def test_get_without_create_is_empty(self):
        project_id = self.make_project()
        got = self.client.get(f"/api/project-library/{project_id}/annotations")
        self.assertEqual(got.status_code, 200)
        self.assertEqual(got.json(), {"project_id": project_id, "items": []})

    def test_unknown_project_is_404(self):
        self.assertEqual(self.client.get("/api/project-library/never-created/annotations").status_code, 404)
        self.assertEqual(
            self.client.post(
                "/api/project-library/never-created/annotations", json={"node_id": "n1", "text": "t"}
            ).status_code,
            404,
        )
        self.assertEqual(self.client.patch("/api/project-library/never-created/annotations/1", json={}).status_code, 404)
        self.assertEqual(self.client.delete("/api/project-library/never-created/annotations/1").status_code, 404)

    def test_unknown_annotation_is_404(self):
        project_id = self.make_project()
        self.assertEqual(
            self.client.patch(f"/api/project-library/{project_id}/annotations/99999", json={"text": "x"}).status_code,
            404,
        )
        self.assertEqual(
            self.client.delete(f"/api/project-library/{project_id}/annotations/99999").status_code,
            404,
        )

    def test_invalid_payload_is_400(self):
        project_id = self.make_project()
        cases = [
            {"node_id": "", "text": "t"},
            {"node_id": "n1", "text": ""},
            {"node_id": "n1", "text": "x" * (MAX_TEXT_LEN + 1)},
            {"node_id": "n1", "text": "t", "x": 1.5},
            {"node_id": "n1", "text": "t", "y": -0.2},
            {"node_id": "n1", "text": "t", "status": "bogus"},
        ]
        for payload in cases:
            response = self.client.post(f"/api/project-library/{project_id}/annotations", json=payload)
            self.assertEqual(response.status_code, 400, msg=f"payload={payload}")

    def test_patch_invalid_fields_is_400(self):
        project_id = self.make_project()
        created = self.client.post(
            f"/api/project-library/{project_id}/annotations", json={"node_id": "n1", "text": "t"}
        ).json()
        for payload in [{"text": ""}, {"status": "bogus"}, {"x": 1.5}, {"y": -1}]:
            response = self.client.patch(
                f"/api/project-library/{project_id}/annotations/{created['id']}", json=payload
            )
            self.assertEqual(response.status_code, 400, msg=f"payload={payload}")


if __name__ == "__main__":
    unittest.main()
