"""Tests for issue #266 尾票: canvas.edit_rotate / canvas.edit_flip（edit_* 再扩两个高价值本地项）。

票面语义：包装 canvas-image-editor.js 已有能力成工具（该模块首行：「裁剪、旋转、
预览导航与对比」——rotate 是既有能力；flip 同族本地变换）。
与 edit_crop 同模式：本地 PIL 处理覆写节点图、纯本地不耗生成、写操作走 #242 确认门
（批准 / 拒绝分支），原资源保留在资源库可恢复。
Mock 边界：无网络、无 provider；图像媒体走 store.import_media 本地字节；
确认门决策走 gate 自身的 confirm handler（同 test_mcp_canvas_edit_266.py 先例）。
"""
import io
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


def _png_bytes(size=(16, 8), color=(200, 30, 30)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _two_tone_png(size=(16, 8)):
    """左半红、右半蓝的不对称图（flip 前后像素可断言）。"""
    img = Image.new("RGB", size, (200, 30, 30))
    w, h = size
    for x in range(w // 2, w):
        for y in range(h):
            img.putpixel((x, y), (30, 30, 200))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class EditTransformSurfaceTests(McpTestBase):
    """声明表机制：新工具必须进 api_mcp.TOOLS 声明表（ADR 0022）+ 严格 schema + 确认门声明。"""

    def test_rotate_flip_declared_with_strict_schemas_and_gate(self):
        resp = self.mcp({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        tools = {t["name"]: t for t in resp.json()["result"]["tools"]}
        for name in ("canvas.edit_rotate", "canvas.edit_flip"):
            self.assertIn(name, tools)
            schema = tools[name]["inputSchema"]
            self.assertEqual(schema["type"], "object")
            self.assertFalse(schema.get("additionalProperties"),
                             f"{name} 必须声明 additionalProperties:false")
            # #242 语义：写操作必须走确认门（描述必须声明）
            self.assertIn("确认门", tools[name]["description"])
            # 确认门集合与 impl 接线
            self.assertIn(name, api_mcp.CONFIRMATION_GATED_TOOLS)
            self.assertIn(name, api_mcp.TOOL_IMPLS)
        rotate = tools["canvas.edit_rotate"]["inputSchema"]
        self.assertEqual(rotate["required"], ["node_id", "angle"])
        self.assertEqual(rotate["properties"]["angle"]["enum"], [90, 180, 270])
        flip = tools["canvas.edit_flip"]["inputSchema"]
        self.assertEqual(flip["required"], ["node_id", "direction"])
        self.assertEqual(flip["properties"]["direction"]["enum"], ["horizontal", "vertical"])


class EditTransformGateTests(McpTestBase):
    """canvas.edit_rotate / canvas.edit_flip 经 #242 确认门：批准 / 拒绝 / 非法参数 / 无图。"""

    def _setup(self, png=None):
        self.open_tab()
        record = self.store.import_media(self.project_id, png or _png_bytes(), "media.png", "image/png")
        asset_id = record["asset_id"]
        canvas = self.store.canvas(self.project_id)
        node = {
            "id": "img-1", "type": "smart-image", "title": "img-1", "x": 0, "y": 0,
            "images": [{"asset_id": asset_id, "kind": "image", "name": "media.png"}],
        }
        self.store.apply_mutations(
            self.project_id, [{"op": "upsert_node", "node": node}],
            base_revision=int(canvas.get("revision") or 0))
        # materialize the agent node + its session（同 test_mcp_canvas_edit_266.py 先例）
        self.tool_result(self.tool_call("canvas.list_nodes", {}, request_id=82))
        agent_node_id = self.materialized_agent_node()["id"]
        session = run_async(agent_host_manager.get_or_create_session(
            self.project_id, agent_node_id))
        session.process = FakeAgentProcess(script=[])
        return session, agent_node_id, asset_id

    def _run_gated(self, impl_name, tool_name, approve, args, png=None):
        session, agent_node_id, asset_id = self._setup(png)
        impl = getattr(api_mcp, impl_name)

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
                outcome["result"] = await impl(self.project_id, agent_node_id, args)
            except Exception as exc:  # noqa: BLE001 — gate raises McpToolError
                outcome["error"] = exc
            finally:
                session.event_handlers.remove(decide_handler)

        run_async(scenario())
        confirms = [e for e in captured if e.get("type") == "confirm"]
        self.assertEqual(len(confirms), 1, f"gate must dispatch exactly one confirm event, got {captured}")
        self.assertEqual(confirms[0]["tool"], tool_name)
        return (outcome.get("error") or outcome.get("result")), confirms, asset_id

    def _node_image_asset(self):
        canvas = self.store.canvas(self.project_id)
        node = next(n for n in canvas.get("nodes", []) if n.get("id") == "img-1")
        return str(node["images"][0]["asset_id"])

    def _load_node_image(self):
        record = self.store.media_record(self.project_id, self._node_image_asset())
        return Image.open(io.BytesIO(open(record["path"], "rb").read())).convert("RGB")

    def test_approval_rotates_and_replaces_node_image(self):
        result, _confirms, original = self._run_gated(
            "_impl_edit_rotate", "canvas.edit_rotate", True, {"node_id": "img-1", "angle": 90})
        self.assertIsInstance(result, dict, result)
        self.assertTrue(result.get("success"), result)
        self.assertEqual(result["asset_id"] != original, True)
        self.assertEqual(result["original_asset_id"], original)
        self.assertEqual(result["angle"], 90)
        # 16x8 → 8x16（宽高互换）
        self.assertEqual((result["width"], result["height"]), (8, 16))
        # 节点图被替换为新资源
        self.assertEqual(self._node_image_asset(), result["asset_id"])
        self.assertEqual(self._load_node_image().size, (8, 16))
        # 原资源保留在资源库（可恢复）
        self.assertIsNotNone(self.store.media_record(self.project_id, original))

    def test_approval_flips_horizontally(self):
        result, _confirms, original = self._run_gated(
            "_impl_edit_flip", "canvas.edit_flip", True, {"node_id": "img-1", "direction": "horizontal"},
            png=_two_tone_png())
        self.assertIsInstance(result, dict, result)
        self.assertTrue(result.get("success"), result)
        self.assertEqual((result["width"], result["height"]), (16, 8))
        self.assertEqual(result["direction"], "horizontal")
        self.assertEqual(self._node_image_asset(), result["asset_id"])
        # 像素级断言：左半原本红色 → 水平翻转后左侧变蓝、右侧变红
        img = self._load_node_image()
        self.assertEqual(img.getpixel((1, 4)), (30, 30, 200))
        self.assertEqual(img.getpixel((14, 4)), (200, 30, 30))

    def test_rejection_leaves_node_untouched(self):
        outcome, _confirms, original = self._run_gated(
            "_impl_edit_rotate", "canvas.edit_rotate", False, {"node_id": "img-1", "angle": 180})
        self.assertIsInstance(outcome, api_mcp.McpToolError)
        self.assertIn("拒绝", str(outcome))
        self.assertEqual(self._node_image_asset(), original)

    def test_invalid_angle_is_error_with_next_step(self):
        outcome, _confirms, original = self._run_gated(
            "_impl_edit_rotate", "canvas.edit_rotate", True, {"node_id": "img-1", "angle": 45})
        self.assertIsInstance(outcome, (api_mcp.McpToolError, ValueError))
        self.assertIn("90/180/270", str(outcome))
        self.assertEqual(self._node_image_asset(), original)

    def test_invalid_direction_is_error_with_next_step(self):
        outcome, _confirms, original = self._run_gated(
            "_impl_edit_flip", "canvas.edit_flip", True, {"node_id": "img-1", "direction": "diagonal"})
        self.assertIsInstance(outcome, (api_mcp.McpToolError, ValueError))
        self.assertIn("horizontal", str(outcome))
        self.assertIn("vertical", str(outcome))
        self.assertEqual(self._node_image_asset(), original)

    def test_node_without_image_is_error_with_next_step(self):
        canvas = self.store.canvas(self.project_id)
        node = {"id": "empty-1", "type": "smart-image", "title": "empty", "x": 0, "y": 0}
        self.store.apply_mutations(
            self.project_id, [{"op": "upsert_node", "node": node}],
            base_revision=int(canvas.get("revision") or 0))
        outcome, _confirms, _original = self._run_gated(
            "_impl_edit_flip", "canvas.edit_flip", True, {"node_id": "empty-1", "direction": "horizontal"})
        self.assertIsInstance(outcome, (api_mcp.McpToolError, ValueError))
        self.assertIn("media.upload", str(outcome))

    def test_missing_node_is_error_with_next_step(self):
        outcome, _confirms, _original = self._run_gated(
            "_impl_edit_rotate", "canvas.edit_rotate", True, {"node_id": "ghost", "angle": 90})
        self.assertIsInstance(outcome, (api_mcp.McpToolError, ValueError))
        self.assertIn("ghost", str(outcome))
        self.assertIn("canvas.list_nodes", str(outcome))


if __name__ == "__main__":
    unittest.main()
