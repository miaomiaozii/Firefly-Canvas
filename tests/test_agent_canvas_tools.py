"""Tests for agent canvas tools API endpoints (issue #119)."""
import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main
from firefly_projects import ProjectStore
from app.routers import api_agent_tools
from app.services import agent_host


class AgentCanvasToolsTests(unittest.TestCase):
    """Test the canvas tools API endpoints used by the Pi agent."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="firefly-canvas-tools-tests-")
        self.store = ProjectStore(Path(self.temp_dir.name) / "projects", snapshot_delay=0)
        self.previous_store = main.FIREFLY_PROJECT_STORE
        main.FIREFLY_PROJECT_STORE = self.store
        self.client = TestClient(main.app, base_url="http://127.0.0.1", headers={"origin": "http://127.0.0.1"})

        self.project = self.store.create_project("Canvas Tools Test")
        self.project_id = self.project["id"]
        self.actor_id = "agent-1"
        self.agent_headers = {"X-Agent-Project": self.project_id}
        self.actor_query = {"agent_node_id": self.actor_id}
        self.store.apply_mutations(
            self.project_id,
            [
                {"op": "upsert_node", "node": {"id": "prompt-1", "type": "smart-prompt", "title": "Test Prompt", "text": "A glowing firefly", "x": 100, "y": 100}},
                {"op": "upsert_node", "node": {"id": "image-1", "type": "smart-image", "title": "Test Image", "x": 300, "y": 100}},
                {"op": "upsert_edge", "edge": {"id": "edge-1", "from": "prompt-1", "to": "image-1", "kind": "flow"}},
            ],
            base_revision=0,
        )

    def tearDown(self):
        self.client.close()
        self.store.close()
        main.FIREFLY_PROJECT_STORE = self.previous_store
        self.temp_dir.cleanup()

    def _add_actor(self, autonomy="cautious"):
        # Keep the two-node read fixture unchanged; only tool writes need an actor.
        self.store.apply_mutations(self.project_id, [
            {"op": "upsert_node", "node": {"id": self.actor_id, "type": "smart-agent",
                                          "title": "Agent", "autonomy": autonomy}},
        ], base_revision=1)

    def test_list_nodes(self):
        """canvas.list_nodes returns node summaries."""
        response = self.client.get(f"/api/projects/{self.project_id}/canvas/nodes")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("nodes", data)
        self.assertEqual(data["count"], 2)

        # Check that each node has the required summary fields
        node_ids = {node["id"] for node in data["nodes"]}
        self.assertIn("prompt-1", node_ids)
        self.assertIn("image-1", node_ids)

        for node in data["nodes"]:
            self.assertIn("id", node)
            self.assertIn("type", node)
            self.assertIn("title", node)
            self.assertIn("x", node)
            self.assertIn("y", node)
            self.assertIn("incoming", node)
            self.assertIn("outgoing", node)
            self.assertIn("status", node)

    def test_list_nodes_includes_connections(self):
        """canvas.list_nodes includes connection info."""
        response = self.client.get(f"/api/projects/{self.project_id}/canvas/nodes")
        data = response.json()

        prompt_node = next(n for n in data["nodes"] if n["id"] == "prompt-1")
        image_node = next(n for n in data["nodes"] if n["id"] == "image-1")

        self.assertEqual(prompt_node["outgoing"], ["image-1"])
        self.assertEqual(image_node["incoming"], ["prompt-1"])

    def test_get_node(self):
        """canvas.get_node returns detailed node info."""
        response = self.client.get(f"/api/projects/{self.project_id}/canvas/nodes/prompt-1")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["id"], "prompt-1")
        self.assertEqual(data["type"], "smart-prompt")
        self.assertEqual(data["title"], "Test Prompt")
        self.assertEqual(data["text"], "A glowing firefly")

    def test_get_node_not_found(self):
        """canvas.get_node returns 404 for missing node."""
        response = self.client.get(f"/api/projects/{self.project_id}/canvas/nodes/nonexistent")
        self.assertEqual(response.status_code, 404)

    def test_get_selection(self):
        """canvas.get_selection is removed (#243): the fake tool always
        returned an empty selection (selection is purely client-side state),
        so the audit stripped it from both the MCP surface and this endpoint.
        The route must no longer exist."""
        response = self.client.get(f"/api/projects/{self.project_id}/canvas/selection")
        self.assertEqual(response.status_code, 404)

    def test_create_node(self):
        """canvas.create_node creates a new node.（#343 升位：id 服务端生成，不再接受调用方 id）"""
        self._add_actor()
        new_node = {
            "type": "smart-video",
            "title": "Test Video",
            "x": 500,
            "y": 100,
        }
        response = self.client.post(
            f"/api/projects/{self.project_id}/canvas/nodes",
            json={"node": new_node, "agent_node_id": self.actor_id},
            headers=self.agent_headers,
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertTrue(data["node_id"].startswith("node-"))

        # Verify the node was created
        response = self.client.get(
            f"/api/projects/{self.project_id}/canvas/nodes/{data['node_id']}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["type"], "smart-video")

    def test_create_node_generates_id(self):
        """canvas.create_node generates an ID if not provided."""
        self._add_actor()
        new_node = {
            "type": "smart-prompt",
            "title": "Auto ID",
        }
        response = self.client.post(
            f"/api/projects/{self.project_id}/canvas/nodes",
            json={"node": new_node, "agent_node_id": self.actor_id},
            headers=self.agent_headers,
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertTrue(data["node_id"].startswith("node-"))

    def test_update_node(self):
        """canvas.update_node updates node properties."""
        self._add_actor()
        response = self.client.patch(
            f"/api/projects/{self.project_id}/canvas/nodes/prompt-1",
            json={"title": "Updated Title", "text": "Updated text"},
            headers=self.agent_headers,
            params=self.actor_query,
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])

        # Verify the update
        response = self.client.get(f"/api/projects/{self.project_id}/canvas/nodes/prompt-1")
        self.assertEqual(response.json()["title"], "Updated Title")
        self.assertEqual(response.json()["text"], "Updated text")

    def test_delete_node(self):
        """canvas.delete_node deletes a node."""
        self._add_actor()
        with patch.object(api_agent_tools, "request_confirmation_core", return_value={"approved": True}):
            response = self.client.delete(
                f"/api/projects/{self.project_id}/canvas/nodes/prompt-1",
                headers=self.agent_headers,
                params=self.actor_query,
            )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])

        # Verify the node was deleted
        response = self.client.get(f"/api/projects/{self.project_id}/canvas/nodes/prompt-1")
        self.assertEqual(response.status_code, 404)

    def test_create_edge(self):
        """canvas.connect creates a connection."""
        # Seed the fixed edge target directly; create_node only accepts server-generated IDs.
        self._add_actor()
        edge = {
            "id": "edge-2",
            "from": "prompt-1",
            "to": "agent-1",
            "kind": "input",
        }
        response = self.client.post(
            f"/api/projects/{self.project_id}/canvas/edges",
            json={"edge": edge},
            headers=self.agent_headers,
            params=self.actor_query,
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["edge_id"], "edge-2")

        # Verify the edge was created by checking list_nodes
        response = self.client.get(f"/api/projects/{self.project_id}/canvas/nodes")
        nodes = {n["id"]: n for n in response.json()["nodes"]}
        self.assertIn("agent-1", nodes["prompt-1"]["outgoing"])

    def test_delete_edge(self):
        """canvas.disconnect removes a connection."""
        self._add_actor()
        with patch.object(api_agent_tools, "request_confirmation_core", return_value={"approved": True}):
            response = self.client.delete(
                f"/api/projects/{self.project_id}/canvas/edges/prompt-1/image-1",
                headers=self.agent_headers,
                params=self.actor_query,
            )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])

        # Verify the edge was removed
        response = self.client.get(f"/api/projects/{self.project_id}/canvas/nodes")
        nodes = {n["id"]: n for n in response.json()["nodes"]}
        self.assertNotIn("image-1", nodes["prompt-1"]["outgoing"])

    def test_delete_edge_not_found(self):
        """canvas.disconnect returns 404 for missing edge."""
        self._add_actor(autonomy="autonomous")
        # Real auto-approval lets the request reach the missing-edge check without mocking the gate.
        host = agent_host.AgentHostManager()
        asyncio.run(host.get_or_create_session(self.project_id, self.actor_id, autonomy="autonomous"))
        with patch.object(agent_host, "agent_host_manager", host):
            response = self.client.delete(
                f"/api/projects/{self.project_id}/canvas/edges/nonexistent/nonexistent",
                headers=self.agent_headers,
                params=self.actor_query,
            )
        self.assertEqual(response.status_code, 404)

    def test_project_not_found(self):
        """Canvas tools return 404 for missing project."""
        response = self.client.get("/api/projects/nonexistent/canvas/nodes")
        self.assertEqual(response.status_code, 404)


class AgentCanvasToolsMediaUrlTests(unittest.TestCase):
    """#284：canvas-tools.ts 工具返回值带「可服务绝对 URL」（源码契约；行为测试在
    tests/canvas_module_units/test-agent-canvas-tools.mjs，由本目录 runner 纳管）。

    背景：Agent 回复里的 MEDIA: 行此前是原始文本 `MEDIA:/media/generated_image_*.png`，
    该 URL 404——真实挂载 StaticFiles /output 与 /assets（main.py:1180-1182），生成目录
    OUTPUT_OUTPUT_DIR=ASSETS_DIR/output（main.py:1090）→ 可服务前缀 /assets/output/<file>。
    """

    @classmethod
    def _source(cls) -> str:
        return (ROOT / "app" / "agent_extensions" / "canvas-tools.ts").read_text(encoding="utf-8")

    def test_normalize_helpers_present(self):
        src = self._source()
        self.assertIn("const MEDIA_NAME_RE = ", src)
        self.assertIn("function normalizeMediaUrl(raw) {", src)
        self.assertIn("function normalizeMediaPayload(value) {", src)
        # 归一目标前缀：API_BASE + /assets/output/<file>（不是 /media/）。
        self.assertIn("${API_BASE}/assets/output/${name}", src)

    def test_media_generate_tools_apply_normalization(self):
        # #356 结构：工具注册段已改为 EXECUTORS 映射（schema 来自 generated 文件），
        # 块 = EXECUTORS 条目从键到下一个键。
        src = self._source()
        import re
        keys = [(m.start(), m.group(1)) for m in re.finditer(r'\n    "([a-z_.]+)": async', src)]

        def block(tool):
            for i, (pos, name) in enumerate(keys):
                if name == tool:
                    end = keys[i + 1][0] if i + 1 < len(keys) else len(src)
                    return src[pos:end]
            self.fail(f"EXECUTORS 缺少工具 {tool}")

        # textResult(payload, true) 必须过 normalizeMediaPayload（#284 可服务绝对 URL）。
        tr_start = src.index("function textResult(")
        tr_block = src[tr_start:src.index("\n}", tr_start)]
        self.assertIn("normalizeMediaPayload(payload)", tr_block,
                      "textResult(normalize=true) 必须过 normalizeMediaPayload（#284）")
        # 钉到真实工具：四个工具块都带 `, true)`（归一化开启），防只加一处漏其它。
        for tool in ("generate.status", "generate.wait", "media.upload", "media.list"):
            self.assertIn(
                ", true)",
                block(tool),
                f"{tool} 返回值未开 textResult 归一化（#284 可服务绝对 URL 缺失）",
            )
        # 反面：不该归一化的工具块不带 `, true)`。
        self.assertNotIn(", true)", block("canvas.list_nodes"), "canvas.list_nodes 不应开归一化（#284 口径）")
        # #352：canvas.view_media 改走 buildViewToolResult（image 内容块 + 元数据文本）；
        # 归一化必须仍生效——工具块调用该 helper，且 helper 内部对 meta 过 normalizeMediaPayload。
        vm_block = block("canvas.view_media")
        self.assertIn("buildViewToolResult(data)", vm_block,
                      "canvas.view_media 必须走 buildViewToolResult（#352 image 内容块）")
        helper_start = src.index("function buildViewToolResult(")
        helper_block = src[helper_start:src.index("\n}", helper_start)]
        self.assertIn("normalizeMediaPayload(meta)", helper_block,
                      "buildViewToolResult 元数据文本必须过 normalizeMediaPayload（#284 口径不变）")

    def test_executors_cover_manifest(self):
        """#356：EXECUTORS 键集合 = 清单工具名集合（加载器另有 throw 双保险）。"""
        import re
        from app.services.agent_tools_manifest import TOOL_NAMES as manifest_names
        src = self._source()
        exec_names = {m.group(1) for m in re.finditer(r'\n    "([a-z_.]+)": async', src)}
        self.assertEqual(exec_names, set(manifest_names))

    def test_generated_defs_match_manifest(self):
        """#356：generated.ts 的 name 集合与清单一致（深度同源由 codegen freshness 测试保证）。"""
        import re
        from app.services.agent_tools_manifest import TOOL_NAMES as manifest_names
        gen = (ROOT / "app" / "agent_extensions" / "canvas-tools.generated.ts").read_text(encoding="utf-8")
        gen_names = {m.group(1) for m in re.finditer(r'name: "([a-z_.]+)"', gen)}
        self.assertEqual(gen_names, set(manifest_names))


if __name__ == "__main__":
    unittest.main()
