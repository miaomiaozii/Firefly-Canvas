"""Tests for issue #266: canvas.edit_crop（edit_* 包装成工具，首批单个）。

票面语义：包装 canvas-image-editor.js 已有能力成工具；crop = 本地裁选区
覆写节点图（纯 PIL，不消耗外部生成/不联网）。
#242 确认门语义对齐：写操作必须过画布确认门（批准 / 拒绝 / 超时三分支）。
Mock 边界：无网络、无 provider；图像媒体走 store.import_media 本地字节；
确认门决策走 gate 自身的 confirm handler（同 test_mcp_endpoint.py 先例）。
"""
import base64
import io
import json
import sys
import unittest
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main  # noqa: F401
from app.routers import api_mcp
from app.services.agent_host import agent_host_manager
from tests.fake_agent_host import FakeAgentProcess
from tests.test_mcp_endpoint import McpTestBase, run_async


def _png_bytes(size=(8, 8), color=(200, 30, 30)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


class EditCropSurfaceTests(McpTestBase):
    """声明表机制 + #242 语义：写操作必须声明确认门。"""

    def test_edit_crop_declared_with_strict_schema_and_gate(self):
        resp = self.mcp({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        tools = {t["name"]: t for t in resp.json()["result"]["tools"]}
        self.assertIn("canvas.edit_crop", tools)
        schema = tools["canvas.edit_crop"]["inputSchema"]
        self.assertEqual(schema["type"], "object")
        self.assertFalse(schema.get("additionalProperties"))
        self.assertEqual(schema["required"], ["node_id", "rect"])
        rect = schema["properties"]["rect"]
        self.assertEqual(rect["required"], ["x", "y", "width", "height"])
        self.assertFalse(rect.get("additionalProperties"))
        # #242 语义：写操作必须走确认门（描述必须声明）
        self.assertIn("确认门", tools["canvas.edit_crop"]["description"])
        # 确认门集合与 impl 接线
        self.assertIn("canvas.edit_crop", api_mcp.CONFIRMATION_GATED_TOOLS)
        self.assertIn("canvas.edit_crop", api_mcp.TOOL_IMPLS)


class EditCropGateTests(McpTestBase):
    """canvas.edit_crop 经 #242 确认门：批准 / 拒绝 / 越界 / 无图。"""

    def _setup(self):
        self.open_tab()
        record = self.store.import_media(self.project_id, _png_bytes(size=(16, 16)), "media.png", "image/png")
        asset_id = record["asset_id"]
        canvas = self.store.canvas(self.project_id)
        node = {
            "id": "img-1", "type": "smart-image", "title": "img-1", "x": 0, "y": 0,
            "images": [{"asset_id": asset_id, "kind": "image", "name": "media.png"}],
        }
        self.store.apply_mutations(
            self.project_id, [{"op": "upsert_node", "node": node}],
            base_revision=int(canvas.get("revision") or 0))
        # materialize the agent node + its session（同 _setup_delete_scenario 先例）
        self.tool_result(self.tool_call("canvas.list_nodes", {}, request_id=82))
        agent_node_id = self.materialized_agent_node()["id"]
        session = run_async(agent_host_manager.get_or_create_session(
            self.project_id, agent_node_id))
        session.process = FakeAgentProcess(script=[])
        return session, agent_node_id, asset_id

    def _run_gated_crop(self, approve, args):
        session, agent_node_id, asset_id = self._setup()
        from app.routers.api_mcp import _impl_edit_crop

        captured = []
        outcome = {}

        async def scenario():
            async def decide_handler(sess, event):
                captured.append(event)
                if event.get("type") == "confirm" and approve is not None:
                    for handler in list(sess.confirm_handlers):
                        await handler(event["confirm_id"], approve)
            agent_host_manager.on_event(session, decide_handler)
            try:
                outcome["result"] = await _impl_edit_crop(
                    self.project_id, agent_node_id, args)
            except Exception as exc:  # noqa: BLE001 — gate raises McpToolError
                outcome["error"] = exc
            finally:
                session.event_handlers.remove(decide_handler)

        run_async(scenario())
        confirms = [e for e in captured if e.get("type") == "confirm"]
        self.assertEqual(len(confirms), 1, f"gate must dispatch exactly one confirm event, got {captured}")
        self.assertEqual(confirms[0]["tool"], "canvas.edit_crop")
        return (outcome.get("error") or outcome.get("result")), confirms, asset_id

    def _node_image_asset(self):
        canvas = self.store.canvas(self.project_id)
        node = next(n for n in canvas.get("nodes", []) if n.get("id") == "img-1")
        return str(node["images"][0]["asset_id"])

    def test_approval_crops_and_replaces_node_image(self):
        result, _confirms, original = self._run_gated_crop(
            True, {"node_id": "img-1", "rect": {"x": 2, "y": 3, "width": 6, "height": 4}})
        self.assertIsInstance(result, dict, result)
        self.assertTrue(result.get("success"), result)
        self.assertEqual(result["asset_id"] != original, True)
        self.assertEqual(result["original_asset_id"], original)
        self.assertEqual(result["rect"], [2, 3, 8, 7])
        self.assertEqual((result["width"], result["height"]), (6, 4))
        # 节点图被替换为新资源
        self.assertEqual(self._node_image_asset(), result["asset_id"])
        record = self.store.media_record(self.project_id, result["asset_id"])
        img = Image.open(io.BytesIO(open(record["path"], "rb").read()))
        self.assertEqual(img.size, (6, 4))

    def test_rejection_leaves_node_untouched(self):
        outcome, _confirms, original = self._run_gated_crop(
            False, {"node_id": "img-1", "rect": {"x": 0, "y": 0, "width": 4, "height": 4}})
        self.assertIsInstance(outcome, api_mcp.McpToolError)
        self.assertIn("拒绝", str(outcome))
        self.assertEqual(self._node_image_asset(), original)

    def test_out_of_bounds_rect_is_error_with_next_step(self):
        outcome, _confirms, original = self._run_gated_crop(
            True, {"node_id": "img-1", "rect": {"x": 0, "y": 0, "width": 999, "height": 999}})
        self.assertIsInstance(outcome, (api_mcp.McpToolError, ValueError))
        self.assertIn("越界", str(outcome))
        self.assertIn("canvas.get_node", str(outcome))  # 下一步建议
        self.assertEqual(self._node_image_asset(), original)

    def test_node_without_image_is_error_with_next_step(self):
        canvas = self.store.canvas(self.project_id)
        node = {"id": "empty-1", "type": "smart-image", "title": "empty", "x": 0, "y": 0}
        self.store.apply_mutations(
            self.project_id, [{"op": "upsert_node", "node": node}],
            base_revision=int(canvas.get("revision") or 0))
        outcome, _confirms, _original = self._run_gated_crop(True, {"node_id": "empty-1", "rect": {"x": 0, "y": 0, "width": 4, "height": 4}})
        self.assertIsInstance(outcome, (api_mcp.McpToolError, ValueError))
        self.assertIn("media.upload", str(outcome))

    def test_missing_node_is_error_with_next_step(self):
        outcome, _confirms, _original = self._run_gated_crop(
            True, {"node_id": "ghost", "rect": {"x": 0, "y": 0, "width": 4, "height": 4}})
        self.assertIsInstance(outcome, (api_mcp.McpToolError, ValueError))
        self.assertIn("ghost", str(outcome))
        self.assertIn("canvas.list_nodes", str(outcome))


if __name__ == "__main__":
    unittest.main()
