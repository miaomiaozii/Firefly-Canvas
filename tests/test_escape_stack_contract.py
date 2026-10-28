"""#249 Escape 栈契约：全局面板/弹窗统一走 FireflyEscapeStack，不留散装全局 Escape 监听。

回归形状：有人给新弹窗挂回 `document/window.addEventListener('keydown', ...)`
式的全局 Escape 监听，一次 Escape 又会同时关多层并穿透到画布。本测试钉住：

1. escape-stack.js 存在，且在 smart-canvas.html 里先于 canvas-image-editor.js
   和 smart-canvas.js 加载（classic script 顺序依赖）。
2. 栈核心用捕获阶段（capture=true）注册唯一全局 keydown。
3. 四个全局模态层（图片编辑器/设置弹窗/批量任务详情/删除范围对话框）都用
   FireflyEscapeStack.push 登记、关闭时 release。
4. 旧的散装全局 Escape 监听（imageEditModal/smartSettingsModal 的
   window keydown 分支）已被移除。
"""
import unittest

from tests.canvas_frontend_sources import ROOT

ESCAPE_STACK_JS = ROOT / "static" / "js" / "escape-stack.js"
SMART_CANVAS_HTML = ROOT / "static" / "smart-canvas.html"
IMAGE_EDITOR_JS = ROOT / "static" / "js" / "canvas-image-editor.js"
SMART_CANVAS_JS = ROOT / "static" / "js" / "smart-canvas.js"


class EscapeStackContract(unittest.TestCase):
    def test_module_loads_before_consumers(self):
        html = SMART_CANVAS_HTML.read_text(encoding="utf-8")
        stack_pos = html.find("escape-stack.js")
        editor_pos = html.find("canvas-image-editor.js")
        smart_pos = html.find("smart-canvas.js?v=")
        self.assertGreaterEqual(stack_pos, 0, "smart-canvas.html 没有加载 escape-stack.js")
        self.assertGreater(editor_pos, stack_pos, "escape-stack.js 必须先于 canvas-image-editor.js 加载")
        self.assertGreater(smart_pos, stack_pos, "escape-stack.js 必须先于 smart-canvas.js 加载")

    def test_core_listener_uses_capture_phase(self):
        source = ESCAPE_STACK_JS.read_text(encoding="utf-8")
        self.assertIn("window.FireflyEscapeStack", source)
        self.assertIn(", true);", source, "栈的 keydown 监听必须是捕获阶段（先于业务监听结算）")
        self.assertIn("stopPropagation", source)
        self.assertIn("preventDefault", source)

    def test_modal_layers_registered_on_stack(self):
        editor = IMAGE_EDITOR_JS.read_text(encoding="utf-8")
        smart = SMART_CANVAS_JS.read_text(encoding="utf-8")
        self.assertIn("'image-editor'", editor, "图片编辑器必须登记到 Escape 栈")
        self.assertIn("releaseImageEditorEscape", editor)
        self.assertIn("'settings'", smart, "设置弹窗必须登记到 Escape 栈")
        self.assertIn("releaseSmartSettingsEscape", smart)
        self.assertIn("'batch-task-detail'", smart, "批量任务详情必须登记到 Escape 栈")
        self.assertIn("'delete-scope'", smart, "删除范围对话框必须登记到 Escape 栈")

    def test_legacy_global_escape_listeners_removed(self):
        editor = IMAGE_EDITOR_JS.read_text(encoding="utf-8")
        smart = SMART_CANVAS_JS.read_text(encoding="utf-8")
        self.assertNotIn(
            "if(e.key === 'Escape' && imageEditModal.classList.contains('open'))",
            smart,
            "图片编辑器的旧 window keydown Escape 分支必须移除（已迁入 Escape 栈）",
        )
        self.assertNotIn(
            "if(event.key === 'Escape' && smartSettingsModal?.classList.contains('open'))",
            smart,
            "设置弹窗的旧 window keydown Escape 监听必须移除（已迁入 Escape 栈）",
        )
        self.assertNotIn("document.addEventListener('keydown', onKey, true)", smart)


if __name__ == "__main__":
    unittest.main()
