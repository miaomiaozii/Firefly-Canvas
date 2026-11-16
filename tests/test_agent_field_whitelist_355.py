"""#355 guards: update_node field whitelist, create_node id conflict, media.upload path restriction.

覆盖票面四点（REVIEW.md 第 1 批）：
1. update_node 字段白名单（pi/MCP 共用 manifest 同一份；至少挡 images/model/agentCli/extSessionId/autonomy）；
2. create_node 遇到已存在的 id 报错（不再 upsert 覆盖）；
3. media.upload 只允许读**当前项目目录**（store.root/<pid>，review 01:08：不是 store 根——其他项目也在禁区）和用户选定目录（存储设置三目录）下的图片；
4. 路径不可用时不泄漏「文件是否存在」（越界与不存在同一句错误）；另验 `items` 不在白名单（frame 成员由 frame/连边端点专管）。
"""
import copy
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

from fastapi.testclient import TestClient
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main
from firefly_projects import ProjectStore
from app.routers.api_agent_tools import create_node_core, update_node_core
from app.services.agent_tools_manifest import UPDATABLE_NODE_FIELDS


def run_async(coro):
    import asyncio
    return asyncio.run(coro)


class _FakeTab:
    def __init__(self):
        self.client_id = f"client-{uuid.uuid4().hex[:8]}"
        self.sent = []

    async def send_text(self, text):
        self.sent.append(text)


class Guards355Tests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="firefly-355-guards-")
        self.store = ProjectStore(Path(self.temp_dir.name) / "projects", snapshot_delay=0)
        self._prev_store = main.FIREFLY_PROJECT_STORE
        self._prev_dirs = (main.OUTPUT_INPUT_DIR, main.OUTPUT_OUTPUT_DIR, main.LOCAL_UPLOAD_DIR)
        main.FIREFLY_PROJECT_STORE = self.store
        self.client = TestClient(main.app, base_url="http://127.0.0.1:8125",
                                 headers={"origin": "http://127.0.0.1:8125"})
        project = self.store.create_project("Guards 355")
        self.pid = project["id"]
        self.store.apply_mutations(self.pid, [
            {"op": "upsert_node", "node": {"id": "prompt-1", "type": "smart-prompt",
                                           "title": "P", "x": 0, "y": 0}},
        ], base_revision=0)
        self._tabs = []

    def tearDown(self):
        self.client.close()
        for tab in self._tabs:
            from app.project_sessions import unregister_project_session
            unregister_project_session(tab)
            if tab in main.manager.active_connections:
                main.manager.active_connections.remove(tab)
            main.manager.connection_clients.pop(tab, None)
            main.manager.user_connections.pop(tab.client_id, None)
        main.FIREFLY_PROJECT_STORE = self._prev_store
        (main.OUTPUT_INPUT_DIR, main.OUTPUT_OUTPUT_DIR, main.LOCAL_UPLOAD_DIR) = self._prev_dirs
        self.temp_dir.cleanup()

    def _open_tab(self, pid):
        from app.project_sessions import register_project_session
        tab = _FakeTab()
        register_project_session(tab, pid)
        main.manager.active_connections.append(tab)
        main.manager.connection_clients[tab] = tab.client_id
        main.manager.user_connections[tab.client_id] = tab
        self._tabs.append(tab)
        return tab

    def _png(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 24)
        return path

    # ---------------------------------------------------------------- W1 whitelist
    def test_whitelist_covers_blocked_fields_and_is_manifest_shared(self):
        for field in ("images", "model", "agentCli", "extSessionId", "autonomy", "items"):
            self.assertNotIn(field, UPDATABLE_NODE_FIELDS)

    def test_core_rejects_non_whitelisted_fields(self):
        for field in ("images", "model", "agentCli", "extSessionId", "autonomy",
                      "chatFrameId", "mcp_session", "type", "items"):
            with self.assertRaises(HTTPException) as cm:
                run_async(update_node_core(self.pid, "prompt-1", {field: "x"}))
            self.assertEqual(cm.exception.status_code, 400, field)
            self.assertIn("白名单", cm.exception.detail)

    def test_core_allows_whitelisted_and_typechecks_geometry(self):
        r = run_async(update_node_core(self.pid, "prompt-1", {"title": "T", "text": "X", "x": 12}))
        self.assertTrue(r["success"])
        with self.assertRaises(HTTPException) as cm:
            run_async(update_node_core(self.pid, "prompt-1", {"x": "left"}))
        self.assertEqual(cm.exception.status_code, 400)
        self.assertIn("number", cm.exception.detail)

    def test_rest_twin_hits_whitelist(self):
        resp = self.client.patch(f"/api/projects/{self.pid}/canvas/nodes/prompt-1",
                                 json={"model": "some-model"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("白名单", resp.text)

    def test_mcp_path_hits_whitelist(self):
        self._open_tab(self.pid)
        resp = self.client.post(
            f"/api/mcp?project={self.pid}",
            json={"jsonrpc": "2.0", "id": "1", "method": "tools/call",
                  "params": {"name": "canvas.update_node",
                             "args": {"node_id": "prompt-1", "images": ["evil.png"]}}},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        blob = str(body)
        self.assertTrue(body.get("result", {}).get("isError") or "error" in body, blob)
        self.assertIn("白名单", blob)

    # ---------------------------------------------------------------- W2 id conflict
    def test_create_node_existing_id_rejected(self):
        # #343 升位后：任何调用方 id（含已存在 id）一律 400「服务端生成」；
        # #355 W2 的「已存在 id 报错」语义被其包络（更严：连不存在的新 id 也不接受）。
        with self.assertRaises(HTTPException) as cm:
            run_async(create_node_core(self.pid, {"id": "prompt-1", "type": "smart-prompt"}))
        self.assertEqual(cm.exception.status_code, 400)
        self.assertIn("#343", cm.exception.detail)
        # REST 孪生同样报错（#343：调用方 id 一律 400，不分存在/新建）
        dup = self.client.post(f"/api/projects/{self.pid}/canvas/nodes",
                               json={"node": {"id": "prompt-1", "type": "smart-prompt"}})
        self.assertEqual(dup.status_code, 400)
        self.assertIn("#343", dup.text)
        fresh = self.client.post(f"/api/projects/{self.pid}/canvas/nodes",
                                 json={"node": {"id": "brand-new-9", "type": "smart-prompt"}})
        self.assertEqual(fresh.status_code, 400, fresh.text)  # #343：新 id 同样拒

    # ---------------------------------------------------------------- W3 media paths
    def test_media_upload_under_own_project_dir_ok(self):
        img = self._png(self.store.root / self.pid / "ok-image.png")
        resp = self.client.post(f"/api/projects/{self.pid}/media/upload",
                                json={"file_path": str(img)})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertTrue(resp.json()["success"])

    def test_media_upload_cross_project_rejected(self):
        """review 01:08：store.root 是全部项目的根，其他项目目录必须在禁区。"""
        other = self.store.create_project("Other 355")
        img = self._png(self.store.root / other["id"] / "other-secret.png")
        resp = self.client.post(f"/api/projects/{self.pid}/media/upload",
                                json={"file_path": str(img)})
        self.assertEqual(resp.status_code, 404, resp.text)
        self.assertIn("not available", resp.text)
        # store 根本身也不再是允许根（它是全部项目的父目录）
        stray = self._png(self.store.root / "stray.png")
        resp2 = self.client.post(f"/api/projects/{self.pid}/media/upload",
                                 json={"file_path": str(stray)})
        self.assertEqual(resp2.status_code, 404, resp2.text)

    def test_media_upload_under_user_selected_dir_ok(self):
        sel = Path(self.temp_dir.name) / "user-selected"
        sel.mkdir()
        main.OUTPUT_OUTPUT_DIR = str(sel)
        img = self._png(sel / "sel.png")
        resp = self.client.post(f"/api/projects/{self.pid}/media/upload",
                                json={"file_path": str(img)})
        self.assertEqual(resp.status_code, 200, resp.text)

    def test_media_upload_out_of_bounds_rejected(self):
        outside = tempfile.TemporaryDirectory(prefix="firefly-355-outside-")
        try:
            img = self._png(Path(outside.name) / "sneaky.png")
            resp = self.client.post(f"/api/projects/{self.pid}/media/upload",
                                    json={"file_path": str(img)})
            self.assertEqual(resp.status_code, 404, resp.text)
            self.assertIn("not available", resp.text)
        finally:
            outside.cleanup()

    def test_media_upload_does_not_leak_existence(self):
        outside = tempfile.TemporaryDirectory(prefix="firefly-355-leak-")
        try:
            existing = self._png(Path(outside.name) / "probe.png")
            missing = Path(outside.name) / "missing" / "probe.png"  # 同 basename，不存在
            r_existing = self.client.post(f"/api/projects/{self.pid}/media/upload",
                                          json={"file_path": str(existing)})
            r_missing = self.client.post(f"/api/projects/{self.pid}/media/upload",
                                         json={"file_path": str(missing)})
            self.assertEqual(r_existing.status_code, 404)
            self.assertEqual(r_missing.status_code, 404)
            self.assertEqual(r_existing.json()["detail"], r_missing.json()["detail"],
                             "存在与不存在的越界路径必须同一句错误，不得泄漏存在性")
        finally:
            outside.cleanup()

    def test_media_upload_non_image_rejected(self):
        txt = self.store.root / self.pid / "notes.txt"
        txt.parent.mkdir(parents=True, exist_ok=True)
        txt.write_text("not an image", encoding="utf-8")
        resp = self.client.post(f"/api/projects/{self.pid}/media/upload",
                                json={"file_path": str(txt)})
        self.assertEqual(resp.status_code, 400, resp.text)
        self.assertIn("image", resp.text)


if __name__ == "__main__":
    unittest.main()
