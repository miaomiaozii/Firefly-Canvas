from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "static" / "smart-canvas.html").read_text(encoding="utf-8")
SCRIPT = (ROOT / "static" / "js" / "smart-canvas.js").read_text(encoding="utf-8")
STYLES = (ROOT / "static" / "css" / "smart-canvas.css").read_text(encoding="utf-8")
RUNNER = (ROOT / "tests" / "ui_integrity_native_browser.py").read_text(encoding="utf-8")


class UiIntegrityContractTests(unittest.TestCase):
    def test_shortcut_panel_does_not_translate_the_canvas_world(self):
        shortcut_rule = re.search(
            r"@media \(min-width:901px\)\s*\{\s*\.shell\.shortcut-panel-open \.world\s*\{(?P<body>.*?)\}\s*\}",
            STYLES,
            re.S,
        )
        self.assertIsNotNone(shortcut_rule)
        self.assertNotIn("translate:", shortcut_rule.group("body"))
        self.assertIn("pointer-events:none", shortcut_rule.group("body").replace(" ", ""))

    def test_static_icon_only_close_controls_have_accessible_names_and_tooltips(self):
        for control_id in (
            "composerExpandBtn",
            "composerFlowBtn",
            "promptPresetClose",
            "promptTemplateClose",
            "assetCloseBtn",
        ):
            tag = re.search(rf'<button\b[^>]*\bid="{control_id}"[^>]*>', HTML)
            self.assertIsNotNone(tag, control_id)
            self.assertRegex(tag.group(0), r'\btitle="[^"]+"')
            self.assertRegex(tag.group(0), r'\baria-label="[^"]+"')

        for onclick in ("closeSmartCanvasLog()", "closeSmartCanvasShortcuts()"):
            tag = re.search(rf'<button\b[^>]*\bonclick="{re.escape(onclick)}"[^>]*>', HTML)
            self.assertIsNotNone(tag, onclick)
            self.assertRegex(tag.group(0), r'\btitle="[^"]+"')
            self.assertRegex(tag.group(0), r'\baria-label="[^"]+"')

    def test_create_menu_clamps_with_its_rendered_size(self):
        function = re.search(
            r"function openCreateMenu\(event, options=\{\}\)\{(?P<body>.*?)\n\}",
            SCRIPT,
            re.S,
        )
        self.assertIsNotNone(function)
        body = function.group("body")
        self.assertIn("createMenu.getBoundingClientRect()", body)
        self.assertNotIn("const h = 218", body)
        self.assertLess(body.index("createMenu.classList.add('open')"), body.index("createMenu.getBoundingClientRect()"))

    def test_smallest_supported_desktop_keeps_selection_actions_reachable(self):
        self.assertIn("@media (max-width: 1100px){\n    .smart-node-floating-menu", STYLES)
        self.assertGreaterEqual(
            SCRIPT.count('aria-label="${escapeAttr(action.label)}"'),
            2,
        )

    def test_selection_menu_is_hidden_from_keyboard_and_clamped_to_the_viewport(self):
        # 字符串快照断言：钉的是浮动工具排的可见性/定位契约（选择器与函数名）而非实现细节，
        # 重构改名时需同步更新本测试（#93）。
        self.assertRegex(
            STYLES,
            r"\.smart-node-floating-menu\s*\{[^}]*visibility:hidden",
        )
        self.assertRegex(
            STYLES,
            r"\.image-node\.selected:not\(\.multi\) \.smart-node-floating-menu\s*\{[^}]*visibility:visible",
        )
        self.assertIn("function positionSmartNodeFloatingMenus()", SCRIPT)
        self.assertIn("function scheduleSmartNodeFloatingMenuPosition()", SCRIPT)
        self.assertIn("scheduleSmartNodeFloatingMenuPosition();", SCRIPT)

    def test_native_evidence_fails_closed_on_every_selection_action_and_embedded_scale(self):
        for marker in (
            "const expectedActionIds=['preview','detail','crop','outpaint','mask','local-edit','brush','grid','send-ps','download']",
            "button[data-smart-node-action]",
            "selectionActions:{{expectedActionIds,actualActionIds,actions:selectionActions,allReachable:selectionActionsReachable}}",  
            "result.selectionMenuAvoidsName",
            "result.promptSurfacesOpaque",
            "result.decorativeEnglishAbsent",
            "titleThemeColor:getComputedStyle(title).color===accentColor",
            "englishKickerAbsent:!document.querySelector('.smart-log-detail-kicker')",
            "result.unselectedMenuTabStops===0",
            "embeddedScaleOptOut:documentElement.dataset.studioScale==='off'",
            'settings_width.get("scaledClassPresent") is False',
        ):
            self.assertIn(marker, RUNNER)

    def test_native_evidence_covers_new_media_preview_and_wheel_ownership_contracts(self):
        for marker in (
            'document.querySelectorAll(\'#promptFlowContext .prompt-flow-context-image.is-recent\').length === 2',
            "promptOpaque:promptBackgroundAlpha>=.999",
            "recentNoScroll:Boolean(recentList",
            'mediaAdaptive:media.length===24',
            'browser.native_wheel(".composer-head"',
            'browser.native_wheel("#promptInput"',
            'browser.native_wheel(".smart-log-detail-stage"',
            'browser.native_drag(".smart-log-detail-stage"',
            'Escape closed generation history together with generation detail',
        ):
            self.assertIn(marker, RUNNER)

    def test_key_overlay_close_controls_use_compact_geometry(self):
        self.assertIn(
            ".smart-settings-head .preview-icon-btn,\n.shortcut-head .preview-icon-btn { border-radius:8px; }",
            STYLES,
        )

    def test_runtime_icon_only_controls_mirror_tooltip_and_accessible_name(self):
        self.assertIn("function syncIconButtonAccessibility(root=document)", SCRIPT)
        self.assertIn("if(button.textContent.trim()) return;", SCRIPT)
        self.assertIn("button.setAttribute('aria-label', title);", SCRIPT)
        self.assertIn("button.title = accessibleName;", SCRIPT)
        self.assertIn("composerExpandBtn.setAttribute('aria-label', composerExpandBtn.title);", SCRIPT)
        self.assertIn("composerFlowBtn.setAttribute('aria-label', composerFlowBtn.title);", SCRIPT)
        self.assertRegex(
            SCRIPT,
            r"function refreshIcons\(root=document\)\{[^}]*syncIconButtonAccessibility\(root\)",
        )


if __name__ == "__main__":
    unittest.main()
