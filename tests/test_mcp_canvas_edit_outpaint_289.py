"""Tests for issue #289: canvas.edit_outpaint（服务端 PIL 复刻站内红底扩图链）。

票面语义：把站内手工扩图链（static/js/canvas-image-editor.js:1944-1987
applyImageOutpaint 红底合成 + node.outpaintSize）复刻到服务端：PIL 用高饱和红
#ff0044 填充新画布 + 贴原图（canvas drawImage source-over 语义），结果作为节点
图内容覆写（原资源保留），写 node.outpaintSize（含约分比例），提示下一步
generate.trigger 填外区（红底 keying，绝不在服务端/模型侧发 mask——ADR 0007）。
与 edit_crop 同模式：本地 PIL 纯处理不耗生成、写操作走 #242 确认门（批准 / 拒绝分支）。
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
from local_edit import OUTPAINT_FILL, outpaint_rgba, reduced_aspect_ratio
from app.routers import api_mcp
from app.services.agent_host import agent_host_manager
from tests.fake_agent_host import FakeAgentProcess
from tests.test_mcp_endpoint import McpTestBase, run_async

#: 与前端 applyImageOutpaint 一致的高饱和红（#ff0044）
RED = (255, 0, 68, 255)


def _png_bytes(size=(16, 16), color=(200, 30, 30)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _open_asset(store, project_id, asset_id):
    record = store.media_record(project_id, asset_id)
    with open(record["path"], "rb") as f:
        return Image.open(io.BytesIO(f.read()))


class OutpaintPillTests(unittest.TestCase):
    """local_edit.outpaint_rgba：复刻前端 applyImageOutpaint 的 pad+paste 合成语义（纯 PIL，无应用依赖）。"""

    def test_fill_and_paste_replaces_frontend_canvas_semantics(self):
        source = Image.new("RGBA", (8, 4), (0, 120, 200, 255))
        out = outpaint_rgba(source, 12, 8, (0, 0))
        self.assertEqual(out.size, (12, 8))
        self.assertEqual(out.mode, "RGBA")
        # 原图 8x4 原样在左上角
        self.assertEqual(out.getpixel((0, 0)), (0, 120, 200, 255))
        self.assertEqual(out.getpixel((7, 3)), (0, 120, 200, 255))
        # 外区全部为高饱和红（#ff0044，与前端 fillStyle 一致）
        self.assertEqual(out.getpixel((8, 0)), RED)
        self.assertEqual(out.getpixel((11, 7)), RED)
        self.assertEqual(OUTPAINT_FILL, RED)

    def test_offset_places_image_inside_canvas(self):
        source = Image.new("RGBA", (8, 4), (0, 120, 200, 255))
        out = outpaint_rgba(source, 12, 8, (4, 2))
        # 原图占据 x∈[4,11]、y∈[2,5]（宽 8 高 4，贴到画布右缘）
        self.assertEqual(out.getpixel((3, 1)), RED)    # 原图左上之外
        self.assertEqual(out.getpixel((4, 2)), (0, 120, 200, 255))  # 原图左上角
        self.assertEqual(out.getpixel((11, 2)), (0, 120, 200, 255))  # 原图最后一列（贴右缘）
        self.assertEqual(out.getpixel((11, 6)), RED)   # 原图下方（y=6 > 2+4-1=5）
        self.assertEqual(out.getpixel((11, 7)), RED)   # 画布右下角

    def test_semitransparent_source_blends_over_red_like_draw_image(self):
        # canvas drawImage 是 source-over 混合：半透明像素 = 源色×α + 红×(1-α)（α≈128/255≈50%）
        source = Image.new("RGBA", (2, 2), (0, 0, 0, 128))
        out = outpaint_rgba(source, 2, 2, (0, 0))
        r, g, b, a = out.getpixel((0, 0))
        # 红通道 ≈ 255*(1-α) ≈ 127；蓝通道 ≈ 68*(1-α) ≈ 34；底不透明 → α=255
        self.assertIn(r, range(120, 136))
        self.assertIn(b, range(28, 42))
        self.assertEqual(g, 0)
        self.assertEqual(a, 255)

    def test_rejects_canvas_smaller_than_source(self):
        source = Image.new("RGBA", (8, 4), (0, 0, 0, 255))
        with self.assertRaises(ValueError):
            outpaint_rgba(source, 10, 3, (0, 0))   # 高小于原图
        with self.assertRaises(ValueError):
            outpaint_rgba(source, 3, 8, (0, 0))    # 宽小于原图

    def test_same_size_canvas_is_valid_noop_recomposition(self):
        # 前端 outW/outH = max(原图, 目标)（canvas-image-editor.js:1953-1954）：等尺寸合法
        # ——红底被原图全覆盖，即纯重合成（不报错）
        source = Image.new("RGBA", (8, 4), (0, 120, 200, 255))
        out = outpaint_rgba(source, 8, 4, (0, 0))
        self.assertEqual(out.size, (8, 4))
        self.assertEqual(out.getpixel((0, 0)), (0, 120, 200, 255))
        self.assertEqual(out.getpixel((7, 3)), (0, 120, 200, 255))

    def test_rejects_offset_out_of_bounds(self):
        source = Image.new("RGBA", (8, 4), (0, 0, 0, 255))
        for offset in ((-1, 0), (0, -1), (5, 0), (0, 5), (12, 8)):
            with self.assertRaises(ValueError):
                outpaint_rgba(source, 12, 8, offset)

    def test_reduced_aspect_ratio_matches_frontend_gcd_semantics(self):
        # 前端 canvasCore.reducedAspectRatio（smart-canvas.js:898）：GCD 约分到最小整数比
        self.assertEqual(reduced_aspect_ratio(12, 8), "3:2")
        self.assertEqual(reduced_aspect_ratio(16, 9), "16:9")
        self.assertEqual(reduced_aspect_ratio(24, 20), "6:5")
        self.assertEqual(reduced_aspect_ratio(7, 3), "7:3")
        self.assertEqual(reduced_aspect_ratio(100, 100), "1:1")


class EditOutpaintSurfaceTests(McpTestBase):
    """声明表机制 + #242 语义：写操作必须声明确认门（同 edit_crop 先例）。"""

    def test_edit_outpaint_declared_with_strict_schema_and_gate(self):
        resp = self.mcp({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        tools = {t["name"]: t for t in resp.json()["result"]["tools"]}
        self.assertIn("canvas.edit_outpaint", tools)
        schema = tools["canvas.edit_outpaint"]["inputSchema"]
        self.assertEqual(schema["type"], "object")
        self.assertFalse(schema.get("additionalProperties"))
        self.assertEqual(schema["required"], ["node_id", "outpaint_size"])
        size = schema["properties"]["outpaint_size"]
        self.assertEqual(size["required"], ["width", "height"])
        self.assertFalse(size.get("additionalProperties"))
        offset = schema["properties"]["offset"]
        self.assertFalse(offset.get("additionalProperties"))
        # #242 语义：写操作必须走确认门（描述必须声明）
        self.assertIn("确认门", tools["canvas.edit_outpaint"]["description"])
        # 确认门集合与 impl 接线
        self.assertIn("canvas.edit_outpaint", api_mcp.CONFIRMATION_GATED_TOOLS)
        self.assertIn("canvas.edit_outpaint", api_mcp.TOOL_IMPLS)


class EditOutpaintGateTests(McpTestBase):
    """canvas.edit_outpaint 经 #242 确认门：批准（合成正确 + outpaintSize 落盘）/ 拒绝 / 各类越界。"""

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
        # materialize the agent node + its session（同 _setup 先例）
        self.tool_result(self.tool_call("canvas.list_nodes", {}, request_id=83))
        agent_node_id = self.materialized_agent_node()["id"]
        session = run_async(agent_host_manager.get_or_create_session(
            self.project_id, agent_node_id))
        session.process = FakeAgentProcess(script=[])
        return session, agent_node_id, asset_id

    def _run_gated_outpaint(self, approve, args):
        session, agent_node_id, asset_id = self._setup()
        from app.routers.api_mcp import _impl_edit_outpaint

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
                outcome["result"] = await _impl_edit_outpaint(
                    self.project_id, agent_node_id, args)
            except Exception as exc:  # noqa: BLE001 — gate raises McpToolError
                outcome["error"] = exc
            finally:
                session.event_handlers.remove(decide_handler)

        run_async(scenario())
        confirms = [e for e in captured if e.get("type") == "confirm"]
        self.assertEqual(len(confirms), 1, f"gate must dispatch exactly one confirm event, got {captured}")
        self.assertEqual(confirms[0]["tool"], "canvas.edit_outpaint")
        return (outcome.get("error") or outcome.get("result")), confirms, asset_id

    def _node(self, node_id="img-1"):
        canvas = self.store.canvas(self.project_id)
        node = next((n for n in canvas.get("nodes", []) if n.get("id") == node_id), None)
        self.assertIsNotNone(node, node_id)
        return node

    def test_approval_outpaints_replaces_node_image_and_writes_outpaint_size(self):
        result, _confirms, original = self._run_gated_outpaint(
            True, {"node_id": "img-1",
                   "outpaint_size": {"width": 24, "height": 20}, "offset": {"x": 0, "y": 0}})
        self.assertIsInstance(result, dict, result)
        self.assertTrue(result.get("success"), result)
        self.assertEqual(result["asset_id"] != original, True)
        self.assertEqual(result["original_asset_id"], original)
        self.assertEqual((result["width"], result["height"]), (24, 20))
        # 下一步提示：generate.trigger 填外区（票面验收点）
        self.assertIn("generate.trigger", result["next_step"])
        # 节点图被替换为新资源；原资源保留在资源库可恢复
        self.assertEqual(self._node()["images"][0]["asset_id"], result["asset_id"])
        self.assertEqual(self.store.media_record(self.project_id, original) is not None, True)
        # node.outpaintSize 落盘（含前端同语义的约分比例 24:20 → 6:5）
        outpaint_size = self._node().get("outpaintSize")
        self.assertEqual(outpaint_size, {"width": 24, "height": 20, "aspectRatio": "6:5"})
        # 合成像素断言（复刻 applyImageOutpaint 红底语义）
        img = _open_asset(self.store, self.project_id, result["asset_id"])
        self.assertEqual(img.size, (24, 20))
        self.assertEqual(img.getpixel((0, 0)), (200, 30, 30, 255))  # 原图像素原样
        self.assertEqual(img.getpixel((15, 15)), (200, 30, 30, 255))
        self.assertEqual(img.getpixel((16, 0)), RED)                # 右外区 = 高饱和红
        self.assertEqual(img.getpixel((0, 16)), RED)                # 下外区 = 高饱和红
        self.assertEqual(img.getpixel((23, 19)), RED)

    def test_offset_centers_image_inside_canvas(self):
        result, _confirms, _original = self._run_gated_outpaint(
            True, {"node_id": "img-1",
                   "outpaint_size": {"width": 24, "height": 24}, "offset": {"x": 4, "y": 4}})
        self.assertIsInstance(result, dict, result)
        img = _open_asset(self.store, self.project_id, result["asset_id"])
        self.assertEqual(img.getpixel((3, 3)), RED)                  # 原图左上之外 = 红
        self.assertEqual(img.getpixel((4, 4)), (200, 30, 30, 255))   # 原图左上角
        self.assertEqual(img.getpixel((19, 19)), (200, 30, 30, 255)) # 原图右下角
        self.assertEqual(img.getpixel((20, 20)), RED)
        self.assertEqual(self._node().get("outpaintSize", {}).get("aspectRatio"), "1:1")

    def test_missing_offset_defaults_to_top_left(self):
        result, _confirms, _original = self._run_gated_outpaint(
            True, {"node_id": "img-1", "outpaint_size": {"width": 20, "height": 16}})
        self.assertIsInstance(result, dict, result)
        img = _open_asset(self.store, self.project_id, result["asset_id"])
        self.assertEqual(img.getpixel((0, 0)), (200, 30, 30, 255))
        self.assertEqual(img.getpixel((16, 0)), RED)

    def test_rejection_leaves_node_untouched(self):
        outcome, _confirms, original = self._run_gated_outpaint(
            False, {"node_id": "img-1", "outpaint_size": {"width": 24, "height": 16}})
        self.assertIsInstance(outcome, api_mcp.McpToolError)
        self.assertIn("拒绝", str(outcome))
        self.assertEqual(self._node()["images"][0]["asset_id"], original)
        self.assertNotIn("outpaintSize", self._node())

    def test_size_smaller_than_source_is_error_with_next_step(self):
        outcome, _confirms, original = self._run_gated_outpaint(
            True, {"node_id": "img-1", "outpaint_size": {"width": 12, "height": 16}})
        self.assertIsInstance(outcome, (api_mcp.McpToolError, ValueError))
        self.assertIn("canvas.get_node", str(outcome))  # 下一步建议
        self.assertEqual(self._node()["images"][0]["asset_id"], original)

    def test_offset_out_of_bounds_is_error_with_next_step(self):
        outcome, _confirms, original = self._run_gated_outpaint(
            True, {"node_id": "img-1", "outpaint_size": {"width": 24, "height": 16},
                   "offset": {"x": 50, "y": 0}})
        self.assertIsInstance(outcome, (api_mcp.McpToolError, ValueError))
        self.assertIn("越界", str(outcome))
        self.assertIn("canvas.get_node", str(outcome))  # 下一步建议
        self.assertEqual(self._node()["images"][0]["asset_id"], original)

    def test_non_numeric_outpaint_size_is_error_with_next_step(self):
        outcome, _confirms, original = self._run_gated_outpaint(
            True, {"node_id": "img-1", "outpaint_size": {"width": "abc", "height": 16}})
        self.assertIsInstance(outcome, (api_mcp.McpToolError, ValueError))
        self.assertIn("canvas.get_node", str(outcome))
        self.assertEqual(self._node()["images"][0]["asset_id"], original)

    def test_node_without_image_is_error_with_next_step(self):
        canvas = self.store.canvas(self.project_id)
        node = {"id": "empty-1", "type": "smart-image", "title": "empty", "x": 0, "y": 0}
        self.store.apply_mutations(
            self.project_id, [{"op": "upsert_node", "node": node}],
            base_revision=int(canvas.get("revision") or 0))
        outcome, _confirms, _original = self._run_gated_outpaint(
            True, {"node_id": "empty-1", "outpaint_size": {"width": 24, "height": 16}})
        self.assertIsInstance(outcome, (api_mcp.McpToolError, ValueError))
        self.assertIn("media.upload", str(outcome))

    def test_missing_node_is_error_with_next_step(self):
        outcome, _confirms, _original = self._run_gated_outpaint(
            True, {"node_id": "ghost", "outpaint_size": {"width": 24, "height": 16}})
        self.assertIsInstance(outcome, (api_mcp.McpToolError, ValueError))
        self.assertIn("ghost", str(outcome))
        self.assertIn("canvas.list_nodes", str(outcome))


if __name__ == "__main__":
    unittest.main()
