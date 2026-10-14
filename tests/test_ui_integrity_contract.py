from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "static" / "smart-canvas.html").read_text(encoding="utf-8")
SCRIPT = (ROOT / "static" / "js" / "smart-canvas.js").read_text(encoding="utf-8")
STYLES = (ROOT / "static" / "css" / "smart-canvas.css").read_text(encoding="utf-8")
RUNNER = (ROOT / "tests" / "ui_integrity_native_browser.py").read_text(encoding="utf-8")
MEDIA_HISTORY = (ROOT / "static" / "js" / "canvas-media-history.js").read_text(encoding="utf-8")
FRONTEND = "\n".join(
    path.read_text(encoding="utf-8")
    for path in [
        *sorted((ROOT / "static").glob("*.html")),
        *sorted((ROOT / "static" / "js").rglob("*.js")),
        *sorted((ROOT / "static" / "css").glob("*.css")),
    ]
)


def kebab(key: str) -> str:
    """DOM 契约名：`dataset.fooBar` 读取对应的属性是 `data-foo-bar`。"""
    return "data-" + re.sub(r"([a-z0-9])([A-Z])", lambda match: f"{match.group(1)}-{match.group(2).lower()}", key)



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
            "const expectedActionIds={json.dumps(list(EXPECTED_NODE_ACTION_IDS))};",
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


    def test_runner_dataset_reads_match_frontend_attributes(self):
        # #168：runner 里每个 `dataset.<camelCase>` 读取都必须在前端真实存在
        # （写成 `data-<kebab>` 属性，或用 `.dataset.<camelCase> =` 赋值）。
        # 0b1e249（#137 术语 log→history）把 `data-log-output-preview` 改名成
        # `data-history-output-preview`，runner 里的 `dataset.logOutputPreview` 没跟上，
        # `previewsDiscoverable` 从此恒假，ui-integrity-evidence 长期红。
        # 这条静态契约把“前端改名、浏览器 runner 落后”这类脱钩钉死。
        frontend = HTML + "\n" + "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted((ROOT / "static" / "js").rglob("*.js"))
        )
        reads = set(re.findall(r"\bdataset\.([A-Za-z0-9_]+)", RUNNER))
        self.assertTrue(reads)
        missing = []
        for key in sorted(reads):
            attribute = kebab(key)
            if attribute in frontend or f".dataset.{key}" in frontend:
                continue
            missing.append(f"dataset.{key} -> {attribute}")
        self.assertEqual([], missing)

    def test_generation_runner_dataset_reads_match_frontend_attributes(self):
        # #182：同一类脱钩在生成旅程 runner 里又红了一次。0b1e249（#137 log→history）把
        # `data-log-cancel-task` 改名成 `data-history-cancel-task`，但
        # tests/generation_task_native_browser.py 里的 `item.dataset.logCancelTask` 没跟上，
        # 读回 [None, None, None]，`overlapping task cards are not independent` 从此恒假，
        # complete-generation-lifecycle-evidence 长期红。
        # #168 的同类守卫只扫 ui_integrity_native_browser.py，把覆盖面扩到生成旅程 runner。
        frontend = HTML + "\n" + "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted((ROOT / "static" / "js").rglob("*.js"))
        )
        runners = [
            ROOT / "tests" / "generation_task_native_browser.py",
            ROOT / "tests" / "generation_lifecycle_browser.py",
        ]
        missing: list[str] = []
        reads = 0
        for runner in runners:
            for key in sorted(set(re.findall(r"\bdataset\.([A-Za-z0-9_]+)", runner.read_text(encoding="utf-8")))):
                reads += 1
                attribute = kebab(key)
                if attribute in frontend or f".dataset.{key}" in frontend:
                    continue
                missing.append(f"{runner.name}: dataset.{key} -> {attribute}")
        self.assertTrue(reads)
        self.assertEqual([], missing)

    def test_detail_lightbox_dataset_reads_match_emitted_attributes(self):
        # #168：同一个改名脱钩也打在产品代码里。0b1e249（#137 log→history）把详情灯箱
        # 三个按钮组的 dataset 读取改成 `dataset.smartHistoryDetail*`，但模板发出的属性
        # 仍是 `data-smart-log-detail-*`（`git show 0b1e249` 可见只改了读取一侧）。
        # 结果 `action` 恒为 undefined：灯箱里的缩放（含 reset）、上下一个输出、
        # 模式按钮全部失效，ui-integrity-evidence 的“点 reset 后缩放复位”永远超时。
        reads = set(re.findall(r"\bdataset\.(smart(?:History|Log)Detail[A-Za-z0-9]*)", MEDIA_HISTORY))
        self.assertTrue(reads)
        missing = []
        for key in sorted(reads):
            attribute = kebab(key)
            if attribute in FRONTEND:
                continue
            missing.append(f"dataset.{key} -> {attribute}")
        self.assertEqual([], missing)


    def test_validator_post_delete_keys_are_produced_by_the_runner(self):
        # #176：校验器 `tools/ui_integrity_evidence.py` 用 `post_delete.get("<key>")` 读
        # runner 产出的证据；runner 在 0b1e2498 把 `destructiveGroupDeleteHiddenWhileRestorable`
        # 改名成 `destructiveGroupDeleteVisibleWhileOutputsAlive`（旧断言与产品合同及同文件
        # dangerousGroupDeletePresent 自相矛盾），但校验器没跟上。校验器读到 None →
        # `post_delete_synchronized` 恒假 → 整个巨型条件失败 → 报出一条指向剪贴板的
        # 误导性错误，`ui-integrity-evidence` 自 2026-09-23 起长期红。
        # fixture 还伪造着旧键名，所以校验器自己的单元测试一直是绿的。
        validator = (ROOT / "tools" / "ui_integrity_evidence.py").read_text(encoding="utf-8")
        required = set(re.findall(r"\bpost_delete\.get\(\"([A-Za-z0-9_]+)\"\)", validator))
        self.assertTrue(required)

        start = RUNNER.index("post_delete_facts = browser.evaluate(")
        block = RUNNER[start:RUNNER.index('"""', RUNNER.index('"""', start) + 1) + 3]
        produced = set()
        for line in block.splitlines():
            stripped = line.strip()
            if stripped.startswith("//") or stripped.startswith("#"):
                continue
            stripped = stripped.split("//")[0]
            produced.update(re.findall(r"([A-Za-z_][A-Za-z0-9_]*):", stripped))
            shorthand = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*),?", stripped)
            if shorthand:
                produced.add(shorthand.group(1))
        produced |= set(re.findall(r'post_delete_facts\["([A-Za-z0-9_]+)"\]', RUNNER))
        self.assertTrue(produced)

        self.assertEqual([], sorted(required - produced))


if __name__ == "__main__":
    unittest.main()
