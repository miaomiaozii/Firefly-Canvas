"""Tests for issue #290: canvas.edit_erase + production local_edit_adapter（bailian qwen-image-edit-max-2026-01-16）。

票面语义：按 prompt 擦除节点图选区——REST 链裁好选区后调 local-edit adapter（ADR 0007：
绝不向模型发 mask），模型结果合成回节点图（原资源保留可恢复）。生产 adapter = bailian
provider（qwen-image-edit-max-2026-01-16，model_specs.json img2img+size），无凭据时保留
离线 fake。与 edit_outpaint 同模式：写操作走 #242 确认门；错误文案带下一步建议（票面规范）。
Mock 边界：无真实网络（adapter 单测 stub http 响应）；图像媒体走 store.import_media 本地字节；
确认门决策走 gate 自身的 confirm handler（同 test_mcp_canvas_edit_outpaint_289.py 先例）。
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main  # noqa: F401
from app.routers import api_mcp
from app.core import BAILIAN_LOCAL_EDIT_MODEL, BAILIAN_PROVIDER_ID


class EditEraseSurfaceTests(unittest.TestCase):
    """声明表机制：edit_erase 已注册（24 项）、严格 schema、确认门 + impl 接线。"""

    def test_tool_registered_gated_and_wired(self):
        tools = {t["name"]: t for t in api_mcp.TOOLS}
        self.assertEqual(len(api_mcp.TOOLS), 24)
        self.assertIn("canvas.edit_erase", tools)
        schema = tools["canvas.edit_erase"]["inputSchema"]
        self.assertEqual(schema["type"], "object")
        self.assertFalse(schema.get("additionalProperties"))
        self.assertEqual(schema["required"], ["node_id", "rect", "prompt"])
        # 描述必须声明确认门（票面规范）
        self.assertIn("确认门", tools["canvas.edit_erase"]["description"])
        self.assertIn("canvas.edit_erase", api_mcp.CONFIRMATION_GATED_TOOLS)
        self.assertIn("canvas.edit_erase", api_mcp.TOOL_IMPLS)

    def test_bailian_model_constants(self):
        # model_specs.json 登记的编辑模型（img2img + size）
        from app.core import model_spec_for
        spec = model_spec_for(BAILIAN_LOCAL_EDIT_MODEL)
        self.assertIsNotNone(spec, "qwen-image-edit-max-2026-01-16 must be in model_specs.json")
        self.assertEqual(BAILIAN_PROVIDER_ID, "bailian")


if __name__ == "__main__":
    unittest.main()
