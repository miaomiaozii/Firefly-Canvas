"""画布版本树测试（issue #257：回滚前 diff + 先存当前 + KEEP_VERSIONS 裁剪）。

store 层（firefly_project_versions.ProjectVersionsMixin）：
- 每次 flush 自动快照（sha256 去重）；保留上限 KEEP_VERSIONS=30 裁剪最旧
- diff：节点 新增/移除/修改（运行时字段不参与对比）
- 回滚：先存当前（pre-restore 快照，严格失败可见）→ 恢复目标 → flush（ADR 0018 .bak 轮转）
API 层（app/routers/api_project_library.py）：
- GET /api/project-library/{id}/versions
- GET /api/project-library/{id}/versions/{vid}/diff?base=current|<vid>
- POST /api/project-library/{id}/versions/{vid}/restore
"""
import json
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


def _node(node_id, label=""):
    return {"id": node_id, "type": "prompt", "x": 0, "y": 0, "label": label}


def _set_canvas_nodes(store, project_id, nodes):
    canvas = store._canvases[project_id]
    canvas["nodes"] = nodes
    store.flush(project_id)


class ProjectVersionsStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name) / "projects"
        self.store = ProjectStore(self.root, snapshot_delay=60.0)

    def tearDown(self):
        ops = getattr(self.store, "_opsdb_instance", None)
        if ops is not None:
            ops.close_all()
        self.store.close()
        self.temp_dir.cleanup()

    def create_project(self):
        project = self.store.create_project("版本树")
        return project["id"]

    def test_new_project_has_initial_empty_snapshot(self):
        project_id = self.create_project()
        versions = self.store.list_project_versions(project_id)
        self.assertEqual(len(versions), 1)
        self.assertEqual(versions[0]["node_count"], 0)
        self.assertEqual(versions[0]["label"], "auto")

    def test_dedup_skips_identical_canvas(self):
        project_id = self.create_project()
        _set_canvas_nodes(self.store, project_id, [_node("n1", "A")])
        self.store.flush(project_id)  # 无内容变化：不应新增版本
        versions = self.store.list_project_versions(project_id)
        self.assertEqual(len(versions), 2)
        self.assertEqual([v["node_count"] for v in versions], [1, 0])

    def test_keep_versions_prunes_to_30(self):
        project_id = self.create_project()
        for index in range(40):
            _set_canvas_nodes(self.store, project_id, [_node(f"n{index}", f"V{index}")])
        versions = self.store.list_project_versions(project_id)
        self.assertEqual(len(versions), self.store.KEEP_VERSIONS)
        # 最新 30 个保留（新→旧）：V39…V10；最旧 11 个（空 + V0…V9）被裁剪
        self.assertEqual([v["node_count"] for v in versions][:1], [1])
        oldest = versions[-1]
        self.assertTrue(oldest["sha256"])

    def test_list_returns_empty_without_ops_db(self):
        versions = self.store.list_project_versions("never-created")
        self.assertEqual(versions, [])

    def test_diff_added_removed_changed(self):
        project_id = self.create_project()
        _set_canvas_nodes(self.store, project_id, [_node("a", "A")])
        _set_canvas_nodes(self.store, project_id, [_node("b", "B")])
        _set_canvas_nodes(self.store, project_id, [_node("b", "B2")])
        versions = self.store.list_project_versions(project_id)
        v3, v2, v1 = versions[0]["id"], versions[1]["id"], versions[2]["id"]

        diff = self.store.diff_project_versions(project_id, base=v1, target=v2)
        self.assertEqual([n["id"] for n in diff["added"]], ["b"])
        self.assertEqual([n["id"] for n in diff["removed"]], ["a"])
        self.assertEqual(diff["changed"], [])
        self.assertEqual(diff["stats"]["nodes_from"], 1)
        self.assertEqual(diff["stats"]["nodes_to"], 1)

        diff2 = self.store.diff_project_versions(project_id, base=v2, target=v3)
        self.assertEqual(diff2["added"], [])
        self.assertEqual(diff2["removed"], [])
        self.assertEqual([n["id"] for n in diff2["changed"]], ["b"])

    def test_diff_ignores_runtime_keys(self):
        project_id = self.create_project()
        _set_canvas_nodes(self.store, project_id, [_node("a", "A")])
        v1_id = self.store.list_project_versions(project_id)[0]["id"]
        # 仅改运行时字段：prompt（不在版本快照内）→ diff 应为空
        canvas = self.store._canvases[project_id]
        canvas["nodes"][0]["prompt"] = "仅运行时状态"
        _set_canvas_nodes(self.store, project_id, canvas["nodes"])
        v2_id = self.store.list_project_versions(project_id)[0]["id"]
        diff = self.store.diff_project_versions(project_id, base=v1_id, target=v2_id)
        self.assertEqual(diff["added"], [])
        self.assertEqual(diff["removed"], [])
        self.assertEqual(diff["changed"], [])

    def test_diff_against_current_in_memory(self):
        project_id = self.create_project()
        _set_canvas_nodes(self.store, project_id, [_node("a", "A")])
        v1_id = self.store.list_project_versions(project_id)[0]["id"]
        canvas = self.store._canvases[project_id]
        canvas["nodes"].append(_node("b", "B"))  # 未 flush 的内存态
        diff = self.store.diff_project_versions(project_id, base="current", target=v1_id)
        self.assertEqual([n["id"] for n in diff["removed"]], ["b"])
        self.assertEqual(diff["added"], [])

    def test_restore_first_saves_current_then_restores(self):
        project_id = self.create_project()
        _set_canvas_nodes(self.store, project_id, [_node("a", "A")])          # v2
        _set_canvas_nodes(self.store, project_id, [_node("a", "A"), _node("b", "B")])  # v3
        v2_id = self.store.list_project_versions(project_id)[1]["id"]

        result = self.store.restore_project_version(project_id, v2_id)

        # ① 先存当前：回滚前 [a,b] 态与既有 v3 内容相同 → 去重，pre_restore 指向既有 v3
        pre = result["pre_restore_version_id"]
        versions = self.store.list_project_versions(project_id)
        self.assertEqual(len(versions), 4)
        self.assertEqual(pre, versions[1]["id"])
        self.assertEqual(versions[1]["node_count"], 2)
        # 回滚后的新状态也进了版本树
        self.assertEqual(versions[0]["node_count"], 1)
        # ② 回滚生效：内存与磁盘都是 1 节点
        self.assertEqual([n["id"] for n in self.store._canvases[project_id]["nodes"]], ["a"])
        on_disk = json.loads((self.root / project_id / "canvas.json").read_text(encoding="utf-8"))
        self.assertEqual([n["id"] for n in on_disk["nodes"]], ["a"])
        # ADR 0018：回滚前的 canvas.json 已轮转为 .bak
        bak = self.root / project_id / "canvas.json.bak"
        self.assertTrue(bak.is_file())
        # ③ 再回滚到 pre_restore → 回滚前数据不丢（.bak 随 flush 轮转为上次磁盘态 [a]）
        self.store.restore_project_version(project_id, pre)
        self.assertEqual([n["id"] for n in self.store._canvases[project_id]["nodes"]], ["a", "b"])
        on_disk2 = json.loads((self.root / project_id / "canvas.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(n["id"] for n in on_disk2["nodes"]), ["a", "b"])
        bak_nodes = json.loads(bak.read_text(encoding="utf-8"))["nodes"]
        self.assertEqual(sorted(n["id"] for n in bak_nodes), ["a"])

    def test_restore_round_trip(self):
        project_id = self.create_project()
        _set_canvas_nodes(self.store, project_id, [_node("a", "A")])
        _set_canvas_nodes(self.store, project_id, [_node("a", "A"), _node("b", "B")])
        v2_id = self.store.list_project_versions(project_id)[1]["id"]
        first = self.store.restore_project_version(project_id, v2_id)
        self.assertEqual([n["id"] for n in self.store._canvases[project_id]["nodes"]], ["a"])
        second = self.store.restore_project_version(project_id, first["pre_restore_version_id"])
        self.assertEqual(sorted(n["id"] for n in self.store._canvases[project_id]["nodes"]), ["a", "b"])
        self.assertIsNotNone(second["pre_restore_version_id"])

    def test_restore_unknown_version_raises_key_error(self):
        project_id = self.create_project()
        with self.assertRaises(KeyError):
            self.store.restore_project_version(project_id, 999)

    def test_restore_unknown_project_raises_key_error(self):
        with self.assertRaises(KeyError):
            self.store.restore_project_version("never-created", 1)

    def test_restore_bumps_revision(self):
        project_id = self.create_project()
        _set_canvas_nodes(self.store, project_id, [_node("a", "A")])
        before = int(self.store._canvases[project_id].get("revision") or 0)
        v2_id = self.store.list_project_versions(project_id)[0]["id"]
        self.store.restore_project_version(project_id, v2_id)
        after = int(self.store._canvases[project_id].get("revision") or 0)
        self.assertEqual(after, before + 1)


class ProjectVersionsApiTests(unittest.TestCase):
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
        ops = getattr(self.store, "_opsdb_instance", None)
        if ops is not None:
            ops.close_all()
        self.store.close()
        main.FIREFLY_PROJECT_STORE = self.previous_store
        self.temp_dir.cleanup()

    def make_project(self):
        created = self.client.post("/api/project-library", json={"name": "版本树"})
        self.assertEqual(created.status_code, 200)
        return created.json()["project"]["id"]

    def current_revision(self, project_id):
        return int(self.client.get(f"/api/project-library/{project_id}").json()["canvas"]["revision"])

    def save_nodes(self, project_id, node_ids):
        """PATCH 只改内存；显式 flush 才会落盘并进版本树（与真实保存语义一致）。"""
        mutations = [{"op": "upsert_node", "node": _node(nid, nid.upper())} for nid in node_ids]
        response = self.client.patch(
            f"/api/project-library/{project_id}/canvas",
            json={"mutations": mutations, "base_revision": self.current_revision(project_id)},
        )
        self.assertEqual(response.status_code, 200)
        flushed = self.client.post(f"/api/project-library/{project_id}/flush")
        self.assertEqual(flushed.status_code, 200)

    def test_list_versions_endpoint(self):
        project_id = self.make_project()
        response = self.client.get(f"/api/project-library/{project_id}/versions")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data["versions"]), 1)
        self.assertIn("sha256", data["versions"][0])

    def test_diff_endpoint_current_vs_version(self):
        project_id = self.make_project()
        self.save_nodes(project_id, ["a"])
        versions = self.client.get(f"/api/project-library/{project_id}/versions").json()["versions"]
        v1_id = versions[1]["id"]  # 初始空画布版本
        diff = self.client.get(
            f"/api/project-library/{project_id}/versions/{v1_id}/diff",
            params={"base": "current"},
        )
        self.assertEqual(diff.status_code, 200)
        body = diff.json()
        self.assertEqual([n["id"] for n in body["removed"]], ["a"])
        self.assertEqual(body["added"], [])

    def test_restore_endpoint_flow(self):
        project_id = self.make_project()
        self.save_nodes(project_id, ["a"])                                   # v2: [a]
        self.save_nodes(project_id, ["a", "b"])                              # v3: [a, b]
        versions = self.client.get(f"/api/project-library/{project_id}/versions").json()["versions"]
        v_single_id = versions[1]["id"]

        restored = self.client.post(f"/api/project-library/{project_id}/versions/{v_single_id}/restore")
        self.assertEqual(restored.status_code, 200)
        body = restored.json()
        self.assertEqual(body["version_id"], v_single_id)
        self.assertIsNotNone(body["pre_restore_version_id"])

        canvas = self.client.get(f"/api/project-library/{project_id}").json()["canvas"]
        self.assertEqual([n["id"] for n in canvas["nodes"]], ["a"])

    def test_unknown_project_is_404(self):
        response = self.client.get("/api/project-library/never-created/versions/1/diff")
        self.assertEqual(response.status_code, 404)
        response = self.client.post("/api/project-library/never-created/versions/1/restore")
        self.assertEqual(response.status_code, 404)

    def test_unknown_version_is_404(self):
        project_id = self.make_project()
        self.assertEqual(self.client.get(f"/api/project-library/{project_id}/versions/999/diff").status_code, 404)
        self.assertEqual(self.client.post(f"/api/project-library/{project_id}/versions/999/restore").status_code, 404)


if __name__ == "__main__":
    unittest.main()
