# -*- coding: utf-8 -*-
"""#H39 空画布覆盖防护的 API 级回归（diagnosing-bugs 反馈环）。

背景（H39 事故根因）：
- 标签页重载/重连失败时本地节点为空，autosave 定时器仍触发，
  增量 diff 生成整批 remove_node，PATCH 200 OK，服务端画布被清空，用户节点丢失。
- 这是服务端空状态防护缺陷：apply_mutations 对"非空 -> 空"的清空操作不设防。

红->绿 目标（修复前 RED，修复后 GREEN）：
1. 对含 N 节点的项目提交"移除全部节点"的 mutations 且不带 force_empty
   -> 必须被拒绝（409），且服务端画布节点仍然保留（不丢）。
2. 同样操作带 force_empty=true -> 允许清空（返回 200，节点确实被清空）。
3. 单节点逐个删除到只剩 1 个、再删这最后 1 个（不带 force_empty）
   -> 被拒绝（409），最后 1 个节点保留。
4. base_revision 不一致的既有行为不变（409 版本冲突）。

验收：修复后本文件全绿，且既有 apply_mutations 相关测试不受影响。
"""
import sys
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main  # noqa: E402
from firefly_projects import ProjectStore  # noqa: E402


class EmptyCanvasGuardTests(unittest.TestCase):
    """PATCH /api/project-library/{pid}/canvas 的空画布覆盖防护。"""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store_root = Path(self.temp_dir.name) / "projects"
        self.store = ProjectStore(self.store_root, snapshot_delay=0)
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

    def _create_project_with_nodes(self, count):
        project = self.store.create_project("空画布防护项目")
        ops = [
            {
                "op": "upsert_node",
                "node": {
                    "id": f"node-{i}",
                    "type": "smart-image",
                    "x": 100 + i * 80,
                    "y": 100,
                    "images": [],
                },
            }
            for i in range(count)
        ]
        result = self.store.apply_mutations(project["id"], ops, base_revision=0)
        return project, int(result["revision"])

    def _get_server_nodes(self, project_id):
        opened = self.store.open_project(project_id)
        return [n for n in (opened["canvas"].get("nodes") or []) if isinstance(n, dict)]

    def test_wipe_without_force_empty_is_rejected(self):
        """1. 移除全部节点且不带 force_empty -> 409，节点保留。"""
        project, revision = self._create_project_with_nodes(3)
        wipe = [{"op": "remove_node", "node_id": f"node-{i}"} for i in range(3)]
        resp = self.client.patch(
            f"/api/project-library/{project['id']}/canvas",
            json={"mutations": wipe, "base_revision": revision},
        )
        self.assertEqual(resp.status_code, 409, resp.text)
        self.assertEqual(len(self._get_server_nodes(project["id"])), 3)

    def test_wipe_with_force_empty_is_allowed(self):
        """2. 移除全部节点带 force_empty=true -> 200，节点被清空。"""
        project, revision = self._create_project_with_nodes(3)
        wipe = [{"op": "remove_node", "node_id": f"node-{i}"} for i in range(3)]
        resp = self.client.patch(
            f"/api/project-library/{project['id']}/canvas",
            json={
                "mutations": wipe,
                "base_revision": revision,
                "force_empty": True,
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(len(self._get_server_nodes(project["id"])), 0)

    def test_removing_last_node_without_force_empty_is_rejected(self):
        """3. 删到只剩 1 个、再删最后 1 个且不带 force_empty -> 409，保留。"""
        project, revision = self._create_project_with_nodes(3)
        # 先正常删掉 2 个（非空 -> 非空，允许）
        partial = [{"op": "remove_node", "node_id": "node-0"}, {"op": "remove_node", "node_id": "node-1"}]
        resp = self.client.patch(
            f"/api/project-library/{project['id']}/canvas",
            json={"mutations": partial, "base_revision": revision},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        revision = int(resp.json()["revision"])
        # 再删最后 1 个（非空 -> 空），不带 force_empty -> 拒绝
        last = [{"op": "remove_node", "node_id": "node-2"}]
        resp2 = self.client.patch(
            f"/api/project-library/{project['id']}/canvas",
            json={"mutations": last, "base_revision": revision},
        )
        self.assertEqual(resp2.status_code, 409, resp2.text)
        self.assertEqual(len(self._get_server_nodes(project["id"])), 1)

    def test_stale_base_revision_still_409(self):
        """4. base_revision 不一致的既有冲突行为不变。"""
        project, revision = self._create_project_with_nodes(1)
        resp = self.client.patch(
            f"/api/project-library/{project['id']}/canvas",
            json={
                "mutations": [{"op": "remove_node", "node_id": "node-0"}],
                "base_revision": revision + 99,
            },
        )
        self.assertEqual(resp.status_code, 409, resp.text)


if __name__ == "__main__":
    unittest.main()
