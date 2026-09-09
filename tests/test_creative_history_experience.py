from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CANVAS_HTML = (ROOT / "static" / "smart-canvas.html").read_text(encoding="utf-8")
SCRIPT = (ROOT / "static" / "js" / "smart-canvas.js").read_text(encoding="utf-8")
I18N = (ROOT / "static" / "js" / "i18n" / "smart-canvas.js").read_text(encoding="utf-8")
CANVAS_CSS = (ROOT / "static" / "css" / "smart-canvas.css").read_text(encoding="utf-8")
SETTINGS_HTML = (ROOT / "static" / "global-settings.html").read_text(encoding="utf-8")
SETTINGS_CSS = (ROOT / "static" / "css" / "global-settings.css").read_text(encoding="utf-8")


class CreativeHistoryExperienceTests(unittest.TestCase):
    def test_history_drawer_is_fixed_and_image_first(self):
        self.assertNotIn("data-history-resize-handle", CANVAS_HTML)
        self.assertNotIn("beginSmartHistoryDrawerResize", SCRIPT)
        self.assertNotIn("--history-drawer-height", CANVAS_CSS)
        self.assertIn("/* Creative history: fixed, image-first rail. */", CANVAS_CSS)
        self.assertRegex(CANVAS_CSS, r"\.log-modal\s*\{[^}]*height:min\(324px, calc\(100dvh - 32px\)\)")
        self.assertRegex(CANVAS_CSS, r"\.log-thumb-square\s*\{[^}]*height:168px")

    def test_history_card_has_one_image_overlay_action(self):
        self.assertIn('class="log-media-download-original"', SCRIPT)
        self.assertNotIn('class="log-media-delete-original"', SCRIPT)
        final_thumbnail_rules = CANVAS_CSS[CANVAS_CSS.rfind(".log-thumb-square .log-media-card,"):]
        self.assertIn("aspect-ratio:auto", final_thumbnail_rules)
        self.assertIn("object-fit:cover", final_thumbnail_rules)
        self.assertIn('class="log-output-gallery"', SCRIPT)
        self.assertIn("const outputCards = outputs.map(item =>", SCRIPT)
        # Selection is now an IIFE that keys on both logId and outputId so a
        # specific output within a multi-output log can be addressed.
        self.assertIn("const selectedIndex = (() => {", SCRIPT)
        self.assertIn("smartHistorySelectedImage?.logId === logId && smartHistorySelectedImage?.outputId", SCRIPT)
        runner = (ROOT / "tests" / "ui_integrity_native_browser.py").read_text(encoding="utf-8")
        self.assertIn("multiOutputIndependentDeleteRestore", runner)
        self.assertIn("rootScrollStable", runner)
        self.assertRegex(
            CANVAS_CSS,
            r"\.log-output-gallery \.log-media-card\s*\{[^}]*flex:0 0 100%[^}]*aspect-ratio:auto",
        )
        self.assertNotIn("--log-media-width", SCRIPT)

    def test_history_restore_refreshes_the_real_undo_redo_controls(self):
        self.assertNotIn("updateUndoRedoButtons", SCRIPT)
        restore = re.search(r"async function restoreSmartProjectOriginal\([^)]*\)\{(?P<body>.*?)\n\}", SCRIPT, re.S)
        self.assertIsNotNone(restore)
        self.assertIn("syncUndoRedoButtons();", restore.group("body"))

    def test_history_heading_and_prompt_are_two_pixels_larger(self):
        self.assertRegex(CANVAS_CSS, r"\.log-title\s*\{[^}]*font-size:16px[^}]*color:var\(--designer-accent-strong\)")
        self.assertRegex(CANVAS_CSS, r"\.log-prompt-preview\s*\{[^}]*font-size:13px")

    def test_prompt_surfaces_are_opaque_and_decorative_english_is_gone(self):
        self.assertRegex(CANVAS_CSS, r"\.composer-card\s*\{[^}]*background:var\(--panel\)[^}]*backdrop-filter:none")
        self.assertRegex(CANVAS_CSS, r"\.prompt-input\s*\{[^}]*background:var\(--soft\)")
        self.assertRegex(CANVAS_CSS, r"\.composer\.prompt-flow \.prompt-input\s*\{[^}]*background:var\(--soft\)")
        self.assertRegex(CANVAS_CSS, r"\.prompt-node-text\s*\{[^}]*background:var\(--card\)")
        self.assertRegex(
            CANVAS_CSS,
            r"\.image-node\.prompt-smart-node\.prompt-node-flow \.prompt-node-text\s*\{[^}]*background:var\(--soft\)",
        )
        self.assertNotIn('content:"SPACE TO CREATE"', CANVAS_CSS)
        self.assertNotIn('content:"WORKSPACE / "', CANVAS_CSS)
        self.assertNotIn('class="smart-log-detail-kicker"', SCRIPT)
        self.assertIn('"smart.promptFlowEscape": { zh: "退出后不会触发生成"', I18N)
        self.assertIn('"smart.longContentHint": { zh: "只读视图 · 按退出键关闭"', I18N)
        self.assertRegex(CANVAS_CSS, r"\.smart-log-detail-head h2\s*\{[^}]*color:var\(--designer-accent-strong\)")

    def test_canvas_assets_use_the_current_ui_cache_version(self):
        # Both CSS and JS must carry a cache-busting ?v= parameter so browsers
        # fetch fresh assets after updates. The exact value is intentionally
        # version-independent: tools/restart-firefly.ps1 (Bump-AssetVersions)
        # rewrites it on every restart, so pinning a literal value here would
        # break on every dev restart. We only assert the parameter exists and
        # that css/js share the same version (Bump-AssetVersions sets both).
        css_match = re.search(r'smart-canvas\.css\?v=([A-Za-z0-9._-]+)', CANVAS_HTML)
        js_match = re.search(r'smart-canvas\.js\?v=([A-Za-z0-9._-]+)', CANVAS_HTML)
        self.assertIsNotNone(css_match, 'smart-canvas.css must have a ?v= cache-buster')
        self.assertIsNotNone(js_match, 'smart-canvas.js must have a ?v= cache-buster')
        self.assertEqual(
            css_match.group(1), js_match.group(1),
            'css and js cache-buster versions must match (Bump-AssetVersions sets both)',
        )

    def test_multi_output_history_has_discoverable_previews_and_detail_switching(self):
        self.assertIn('class="log-output-preview-list"', SCRIPT)
        self.assertIn('data-log-output-preview="${item.outputIndex}"', SCRIPT)
        self.assertIn("smartLogModal?.querySelectorAll('.log-output-preview.is-selected')", SCRIPT)
        self.assertIn('class="smart-log-detail-output-strip"', SCRIPT)
        self.assertIn('class="smart-log-detail-output"', SCRIPT)
        self.assertIn("function selectSmartLogDetailOutput(outputIndex, {focus=false}={})", SCRIPT)
        self.assertIn("switchSmartLogDetailOutput(1)", SCRIPT)
        self.assertIn("switchSmartLogDetailOutput(-1)", SCRIPT)
        self.assertRegex(CANVAS_CSS, r"\.log-output-preview-list\s*\{[^}]*display:grid")
        self.assertRegex(CANVAS_CSS, r"\.smart-log-detail-output-strip\s*\{[^}]*display:grid")

    def test_eight_output_switchers_are_left_aligned_unobscured_and_scrollbar_free(self):
        self.assertIn("--log-output-columns:${previewColumns}", SCRIPT)
        self.assertIn("--log-output-rows:${previewRows}", SCRIPT)
        self.assertIn("--log-output-track-height:${previewTrackHeight}px", SCRIPT)
        self.assertIn("const previewColumns = previewCount", SCRIPT)
        self.assertIn("const previewRows = 1", SCRIPT)
        self.assertIn("const previewTrackHeight = 25", SCRIPT)
        self.assertNotIn("--log-output-track-width", SCRIPT)
        self.assertNotRegex(
            CANVAS_CSS,
            r"\.log-thumb-square:has\(\.log-output-preview-list\)\s*\{[^}]*padding-bottom:[^;}]+",
        )
        preview_rules = re.search(r"\.log-output-preview-list\s*\{(?P<body>.*?)\}", CANVAS_CSS, re.S)
        self.assertIsNotNone(preview_rules)
        preview_body = preview_rules.group("body")
        for declaration in (
            "position:absolute",
            "left:5px",
            "right:5px",
            "bottom:5px",
            "z-index:4",
            "grid-template-columns:repeat(var(--log-output-columns, 4), 30px)",
            "grid-auto-flow:column",
            "grid-auto-rows:25px",
            "gap:3px",
            "padding:0",
            "overflow-x:auto",
            "overflow-y:hidden",
            "touch-action:pan-x",
            "border:0",
            "border-radius:0",
            "background:transparent",
            "box-shadow:none",
            "scrollbar-width:none",
        ):
            self.assertIn(declaration, preview_body)
        self.assertNotIn("overflow:visible", preview_body)
        self.assertNotIn("left:50%", preview_body)
        self.assertNotIn("translateX(-50%)", preview_body)
        detail_rules = re.search(r"\.smart-log-detail-output-strip\s*\{(?P<body>.*?)\}", CANVAS_CSS, re.S)
        self.assertIsNotNone(detail_rules)
        detail_body = detail_rules.group("body")
        for declaration in (
            "gap:7px",
            "padding:0",
            "overflow:visible",
            "border:0",
            "border-radius:0",
            "background:transparent",
            "box-shadow:none",
            "scrollbar-width:none",
        ):
            self.assertIn(declaration, detail_body)
        self.assertRegex(CANVAS_CSS, r"\.smart-log-detail-output-strip::\-webkit-scrollbar\s*\{[^}]*display:none")

    def test_multi_output_delete_targets_only_the_selected_original_and_reconciles_ui(self):
        self.assertIn("const destructiveRecordDelete = !(log.deletedOutputTombstones || []).length || outputs.length > 0", SCRIPT)
        self.assertIn("destructiveRecordDelete ? `<button", SCRIPT)
        self.assertIn("function reconcileSmartOriginalDeletionUi(", SCRIPT)
        self.assertIn("reconcileSmartOriginalDeletionUi(logId, outputId, deletionUiState);", SCRIPT)
        self.assertIn("data-log-delete-original", SCRIPT)
        self.assertIn('class="smart-log-detail-delete-original"', SCRIPT)
        self.assertIn("deleteOriginal.dataset.assetId = String(item.asset_id || '')", SCRIPT)
        self.assertIn("data-log-delete=\"media\"", SCRIPT)

    def test_output_occurrence_identity_drives_delete_restore_and_ui_reconciliation(self):
        self.assertIn("outputId:String(item.output_id || '')", SCRIPT)
        self.assertIn('data-output-id="${escapeAttr(item.output_id || \'\')}"', SCRIPT)
        self.assertIn("log_id:logId", SCRIPT)
        self.assertIn("output_id:outputId", SCRIPT)
        self.assertIn("deletion_id:snapshot.projectOriginalDelete.deletionId", SCRIPT)
        self.assertIn("const deletionId = String(data.deleted?.deletion_id || '')", SCRIPT)
        self.assertIn("projectOriginalDelete:{assetId, outputId, deletionId, logId, projectId}", SCRIPT)
        self.assertIn("historySelectedOutputId:String(historyOutput?.output_id || '')", SCRIPT)
        self.assertIn("detailSelectedOutputId:String(detailOutput?.output_id || '')", SCRIPT)
        self.assertIn("canvasSelectedOutputId:String(selectedCanvasItem?.output_id || '')", SCRIPT)
        self.assertNotIn("logOutputs.findIndex(output => String(output.asset_id || '')", SCRIPT)

    def test_original_delete_and_restore_wait_for_the_authoritative_revision(self):
        self.assertIn("async function settleProjectCanvasBeforeOriginalMutation()", SCRIPT)
        self.assertGreaterEqual(
            SCRIPT.count("await settleProjectCanvasBeforeOriginalMutation()"),
            2,
        )
        settle = re.search(
            r"async function settleProjectCanvasBeforeOriginalMutation\(\)\{(?P<body>.*?)\n\}",
            SCRIPT,
            re.S,
        )
        self.assertIsNotNone(settle)
        self.assertIn("while(canvasSyncInFlight)", settle.group("body"))
        self.assertIn("saveProjectCanvasUntilSettled", settle.group("body"))

    def test_generation_detail_contains_metadata_and_sharing_actions(self):
        self.assertIn("function openSmartLogDetail({url, kind='image', logId='', outputIndex=0}={})", SCRIPT)
        for selector in (
            "smart-log-detail-frame",
            "smart-log-detail-zoom-tools",
            "smart-log-detail-output-strip",
            "smart-log-detail-model",
            "smart-log-detail-time",
            "smart-log-detail-prompt",
            "smart-log-detail-download",
            "smart-log-detail-copy-2k",
        ):
            self.assertIn(selector, SCRIPT)
        self.assertIn("function copySmartLogImage2k(item)", SCRIPT)
        self.assertIn("new ClipboardItem({'image/png':blob})", SCRIPT)
        self.assertIn("navigator.clipboard.write", SCRIPT)
        self.assertIn("function zoomPanAroundPoint(", SCRIPT)
        self.assertIn("function setupSmartLogDetailInteractions(box)", SCRIPT)
        self.assertIn("2048", SCRIPT)
        self.assertRegex(CANVAS_CSS, r"\.smart-log-detail\s*\{[^}]*width:min\(1600px, calc\(100vw - 40px\)\)[^}]*height:min\(920px, calc\(100dvh - 40px\)\)")

    def test_generation_detail_escape_does_not_close_history(self):
        self.assertIn("document.querySelector('#smartLogLightbox.open')", SCRIPT)
        self.assertIn("Generation history is persistent workspace chrome", SCRIPT)
        self.assertNotIn(
            "closeSmartLogLightbox(); closeAllSmartPopovers(); closeCreateMenu(); closeSmartCanvasLog();",
            SCRIPT,
        )

    def test_focus_mode_centers_context_above_the_original_editor(self):
        self.assertIn('id="promptFlowContext"', CANVAS_HTML)
        self.assertIn("function recentPromptFlowImages(limit=2)", SCRIPT)
        self.assertIn("function renderPromptFlowContext(node)", SCRIPT)
        self.assertIn("function promptFlowReferenceImagesFor(node)", SCRIPT)
        self.assertIn("renderPromptFlowContext(node);", SCRIPT)
        self.assertRegex(CANVAS_CSS, r"\.composer\.prompt-flow\s*\{[^}]*max-width:1320px")
        self.assertRegex(CANVAS_CSS, r"\.prompt-flow-context-image img\s*\{[^}]*object-fit:contain")
        self.assertRegex(
            CANVAS_CSS,
            r"\.prompt-flow-context-image\s*\{[^}]*max-width:none[^}]*min-width:0[^}]*aspect-ratio:var\(--prompt-flow-media-ratio, 1\.5\)",
        )
        self.assertNotRegex(CANVAS_CSS, r"\.prompt-flow-context-image img\s*\{[^}]*object-fit:cover")
        self.assertIn("bindPromptFlowImageAspectRatios(promptFlowContext);", SCRIPT)
        self.assertIn("function fitPromptFlowRecentImages(root)", SCRIPT)
        self.assertRegex(
            CANVAS_CSS,
            r"\.prompt-flow-context-group\.is-recent \.prompt-flow-context-list\s*\{[^}]*overflow:hidden[^}]*scrollbar-width:none",
        )
        context_position = CANVAS_HTML.index('id="promptFlowContext"')
        prompt_position = CANVAS_HTML.index('id="promptInput"')
        self.assertLess(context_position, prompt_position)

    def test_selected_image_toolbar_avoids_the_outside_image_name(self):
        self.assertIn("const nameBadge = nodeEl.querySelector('.image-name-badge-outside')", SCRIPT)
        self.assertIn("const blockers = [nodeRect, nameRect, ...uiBlockers].filter(Boolean)", SCRIPT)
        self.assertIn("nodeRect.right + margin + menuRect.width / 2", SCRIPT)
        self.assertIn("nodeRect.left - margin - menuRect.width / 2", SCRIPT)

    def test_composer_is_world_anchored_below_node_and_wheel_ownership_is_narrow(self):
        self.assertIn("composer.style.left = `${modelRect.x + (modelRect.width - composerWidth) / 2}px`", SCRIPT)
        self.assertIn("composer.style.top = `${modelRect.y + modelRect.height + 14}px`", SCRIPT)
        self.assertIn("function canvasWheelIsReserved(target)", SCRIPT)
        self.assertIn("'.composer .smart-popover'", SCRIPT)
        self.assertIn("if(canvasWheelIsReserved(e.target)) return;", SCRIPT)
        wheel_handler = re.search(r"shell\.addEventListener\('wheel', e => \{(?P<body>.*?)\}, \{passive:false\}\);", SCRIPT, re.S)
        self.assertIsNotNone(wheel_handler)
        self.assertNotIn("closest('.composer,", wheel_handler.group("body"))

    def test_embedded_settings_use_the_available_desktop_width(self):
        self.assertRegex(CANVAS_CSS, r"\.smart-settings-panel\s*\{[^}]*width:min\(1560px, calc\(100vw - 32px\)\)")
        shell = re.search(r"\.settings-shell\s*\{(?P<body>.*?)\}", SETTINGS_CSS, re.S)
        self.assertIsNotNone(shell)
        self.assertIn("width: calc(100% - 40px);", shell.group("body"))
        self.assertIn("max-width: none;", shell.group("body"))
        self.assertIn("window.self !== window.top", SETTINGS_HTML)
        self.assertIn("dataset.studioScale = 'off'", SETTINGS_HTML)
        self.assertRegex(SETTINGS_CSS, r"@media \(max-width: 800px\)")


if __name__ == "__main__":
    unittest.main()
