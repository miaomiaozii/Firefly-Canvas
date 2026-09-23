import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ProjectEntryUiTests(unittest.TestCase):
    def test_project_entry_uses_project_library_and_core_canvas(self):
        script = (ROOT / "static" / "js" / "project-list.js").read_text(encoding="utf-8")
        page = (ROOT / "static" / "project-list.html").read_text(encoding="utf-8")
        self.assertIn("/api/project-library", script)
        self.assertIn("/static/smart-canvas.html", script)
        self.assertIn("project_mode=1", script)
        self.assertIn("/api/project-library/import", script)
        self.assertNotIn("'default'", script)
        self.assertIn("一个项目，一张画布", page)
        self.assertIn("id=\"emptyState\"", page)

    def test_project_canvas_websocket_registers_the_open_project_session(self):
        script = (ROOT / "static" / "js" / "smart-canvas.js").read_text(encoding="utf-8")
        self.assertIn("project_id=${encodeURIComponent(canvasId)}", script)
        self.assertIn("client_id=${clientId}${projectQuery}", script)
        self.assertIn("socket?.close()", script)

    def test_empty_canvas_has_a_visible_first_step(self):
        page = (ROOT / "static" / "smart-canvas.html").read_text(encoding="utf-8")
        script = (ROOT / "static" / "js" / "smart-canvas.js").read_text(encoding="utf-8")
        self.assertIn('id="canvasEmptyState"', page)
        self.assertIn('id="canvasEmptyCreate"', page)
        self.assertIn('id="canvasEmptyImport"', page)
        self.assertIn("syncCanvasEmptyState", script)
        self.assertIn("canvasEmptyCreate?.addEventListener", script)

    def test_project_entry_exposes_fly_export_and_whole_project_trash(self):
        script = (ROOT / "static" / "js" / "project-list.js").read_text(encoding="utf-8")
        page = (ROOT / "static" / "project-list.html").read_text(encoding="utf-8")
        self.assertIn("/export`;", script)
        self.assertIn("method:'DELETE'", script)
        self.assertIn("/api/project-library/trash", script)
        self.assertIn("/restore`, {method:'POST'}", script)
        self.assertIn('id="showTrash"', page)
        self.assertIn('aria-controls="trashPanel" aria-expanded="false"', page)
        self.assertIn('id="trashPanel"', page)
        self.assertIn("showTrash.setAttribute('aria-expanded', 'true')", script)
        self.assertIn("showTrash.setAttribute('aria-expanded', 'false')", script)
        self.assertIn("closeTrash.focus()", script)
        self.assertIn("showTrash.focus()", script)

    def test_closed_project_cleanup_entry_previews_read_only_then_binds_confirmed_execution(self):
        script = (ROOT / "static" / "js" / "project-list.js").read_text(encoding="utf-8")
        page = (ROOT / "static" / "project-list.html").read_text(encoding="utf-8")
        self.assertIn('class="project-action project-cleanup-preview"', script)
        self.assertIn("if(project.cleanup_preview_available)", script)
        self.assertIn("/${encodeURIComponent(project.id)}/cleanup-preview`", script)
        self.assertIn("id=\"cleanupPreviewDialog\"", page)
        self.assertIn("id=\"cleanupCandidateCount\"", page)
        self.assertIn("id=\"cleanupCandidateList\"", page)
        self.assertIn("id=\"cleanupPermanentScope\"", page)
        self.assertIn("candidate.thumbnail_url", script)
        self.assertIn("candidate.kind === 'video'", script)
        self.assertIn("thumbnail.preload = 'metadata'", script)
        self.assertIn("candidate.source_category", script)
        preview_flow = script[
            script.index("async function showCleanupPreview"):
            script.index("function render(projects)")
        ]
        self.assertIn("method:'GET'", preview_flow)
        self.assertNotIn("method:'DELETE'", preview_flow)
        self.assertNotIn("confirm(", preview_flow)

    def test_cleanup_confirm_posts_only_preview_bound_state_and_never_builds_paths(self):
        script = (ROOT / "static" / "js" / "project-list.js").read_text(encoding="utf-8")
        page = (ROOT / "static" / "project-list.html").read_text(encoding="utf-8")
        self.assertIn('id="confirmCleanup"', page)
        self.assertIn('id="cleanupConfirmArea"', page)
        self.assertIn('id="cleanupConfirmNote"', page)
        self.assertIn('id="cleanupConfirmError"', page)
        self.assertIn("/cleanup-execute`", script)
        self.assertIn("method:'POST'", script)
        # 确认负载只由预览返回的数据构造：版本、所有权证明和候选身份。
        self.assertIn("revision: Number(preview.revision || 0)", script)
        self.assertIn("ownership_proof: String(preview.ownership_proof || '')", script)
        self.assertIn("candidate_asset_ids: candidates.map(item => String(item.asset_id || ''))", script)
        self.assertIn("body: JSON.stringify(state)", script)
        self.assertNotIn("../", script)
        # 未取得有效预览时确认按钮不可用。
        self.assertIn("cleanupConfirmButton.disabled = false", script)
        self.assertIn("cleanupConfirmButton.hidden = true", script)
        self.assertIn("cleanupConfirmState = null", script)
        # 服务器要求重新预览时给出明确提示而不是重试删除。
        self.assertIn("cleanupRepreview", script)
        self.assertIn("重新预览", script)

    def test_project_entry_exposes_project_duplication(self):
        script = (ROOT / "static" / "js" / "project-list.js").read_text(encoding="utf-8")
        self.assertIn('class="project-action project-duplicate"', script)
        self.assertIn("/${encodeURIComponent(project.id)}/duplicate`, {method:'POST'}", script)
        self.assertIn("await load()", script)
        self.assertRegex(
            script,
            r"project-duplicate[\s\S]+?catch\s*\([^)]*\)[\s\S]+?duplicateFailed[\s\S]+?finally[\s\S]+?disabled\s*=\s*false",
        )

    def test_core_canvas_sends_node_and_edge_diffs_to_incremental_endpoint(self):
        script = (ROOT / "static" / "js" / "smart-canvas.js").read_text(encoding="utf-8")
        mutation_script = (ROOT / "static" / "js" / "project-canvas-mutations.js").read_text(encoding="utf-8")
        translations = (ROOT / "static" / "js" / "i18n" / "smart-canvas.js").read_text(encoding="utf-8")
        self.assertIn("buildCoreMutations", script)
        self.assertIn("op:'upsert_node'", mutation_script)
        self.assertIn("op:'remove_node'", mutation_script)
        self.assertIn("op:'upsert_edge'", mutation_script)
        self.assertIn("op:'remove_edge'", mutation_script)
        self.assertIn("method:'PATCH'", script)
        self.assertIn("/api/project-library/${encodeURIComponent", script)
        self.assertIn("materializeProjectMediaReferences", script)
        self.assertIn("hydrateProjectMediaReferences", script)
        self.assertIn("/media/import", script)
        self.assertIn("tr('smart.projectDirty')", script)
        self.assertIn('"smart.projectDirty": { zh: "有未保存修改", en: "Unsaved changes" }', translations)
        self.assertIn("if(await saveCanvas() === false) return", script)
        self.assertIn("return data.media", script)
        self.assertIn("const clean = {...item, asset_id:media.asset_id", script)
        self.assertIn("tr('smart.projectImporting')", script)
        self.assertIn('"smart.projectImporting": { zh: "正在归档项目媒体…", en: "Importing project media…" }', translations)
        self.assertIn("/media/import/${encodeURIComponent(taskId)}", script)
        self.assertIn("activeProjectMediaImports >= 3", script)
        self.assertIn("Math.min(1500", script)

    def test_canvas_history_entry_and_view_only_navigation_do_not_persist(self):
        page = (ROOT / "static" / "smart-canvas.html").read_text(encoding="utf-8")
        script = (ROOT / "static" / "js" / "smart-canvas.js").read_text(encoding="utf-8")
        toggle = re.search(r'<button id="smartHistoryToggle".*?</button>', page, re.S)
        self.assertIsNotNone(toggle)
        self.assertIn('data-i18n="canvas.generationHistory"', toggle.group(0))
        self.assertNotIn('data-i18n="canvas.history"', toggle.group(0))
        mouseup_pan = script[script.index("if(panState) {"):script.index("if(smartMinimapDrag)", script.index("if(panState) {"))]
        self.assertNotIn("scheduleSave()", mouseup_pan)
        wheel = script[script.index("shell.addEventListener('wheel'"):script.index("shell.ondragover", script.index("shell.addEventListener('wheel'"))]
        self.assertNotIn("scheduleSave()", wheel)

    def test_autosave_is_content_coalesced_and_drag_only_updates_layout(self):
        page = (ROOT / "static" / "smart-canvas.html").read_text(encoding="utf-8")
        script = (ROOT / "static" / "js" / "smart-canvas.js").read_text(encoding="utf-8")
        self.assertNotIn('id="smartAutosaveCountdown"', page)
        self.assertIn('id="smartSaveButton"', page)
        self.assertIn('aria-keyshortcuts="Control+S"', page)
        self.assertIn('/static/js/global-preferences.js?v=', page)
        self.assertNotIn("autoSaveDueAt", script)
        self.assertNotIn("autosave_interval_minutes", script)
        self.assertNotIn("setInterval(updateAutosaveCountdown", script)
        self.assertIn("markContentDirty", script)
        self.assertIn("hasDurableCanvasChanges", script)
        self.assertIn("function manualSaveCanvas", script)
        self.assertIn("key === 's'", script)
        self.assertIn("scheduleDebouncedSave", script)
        drag = script[script.index("if(dragState){"):script.index("shell.addEventListener('wheel'")]
        self.assertIn("contentChanged", drag)
        self.assertNotRegex(drag, r"\n\s*scheduleSave\(\);\n\s*scheduleConnectionLayerRefresh\(\)")
        arrange = script[script.index("function arrangeSelectedSmartNodes"):script.index("function applyViewport")]
        self.assertNotIn("scheduleSave()", arrange)
        mouseup_resize = script[script.index("if(resizeState){"):script.index("if(thumbDragState)")]
        self.assertNotIn("scheduleSave()", mouseup_resize)

    def test_global_settings_are_isolated_from_canvas_navigation(self):
        page = (ROOT / "static" / "smart-canvas.html").read_text(encoding="utf-8")
        canvas_script = (ROOT / "static" / "js" / "smart-canvas.js").read_text(encoding="utf-8")
        settings_script = (ROOT / "static" / "js" / "global-settings.js").read_text(encoding="utf-8")
        self.assertNotIn('target="_blank"', page)
        self.assertIn('id="smartSettingsModal"', page)
        self.assertIn('id="smartSettingsFrame"', page)
        self.assertIn("function openSmartSettings", canvas_script)
        self.assertIn("function closeSmartSettings", canvas_script)
        self.assertIn("embedded", settings_script)
        self.assertIn("BroadcastChannel('studio-api')", settings_script)
        self.assertIn("preferences-changed", settings_script)
        self.assertIn("providers-changed", settings_script)
        self.assertIn("refreshAutosavePreferences", canvas_script)
        self.assertNotIn("window.location.assign(smartSettingsLink.href)", canvas_script)

    def test_fetched_models_need_explicit_selection_before_entering_provider_lists(self):
        settings_script = (ROOT / "static" / "js" / "global-settings.js").read_text(encoding="utf-8")
        self.assertIn("renderModelPicker", settings_script)
        self.assertIn("availableModels", settings_script)
        self.assertIn("preferredModelForKind", settings_script)
        self.assertNotIn("textarea.value = joinModels(value)", settings_script)

    def test_asset_drawer_separates_project_media_from_explicit_global_library(self):
        script = (ROOT / "static" / "js" / "smart-canvas.js").read_text(encoding="utf-8")
        translations = (ROOT / "static" / "js" / "i18n" / "smart-canvas.js").read_text(encoding="utf-8")
        self.assertIn("PROJECT_ASSET_LIBRARY_ID = '__project_media__'", script)
        self.assertIn("projectMediaSourceLibrary()", script)
        self.assertIn("/media`).then", script)
        self.assertIn("hydrated.preview_url = projectMediaPreviewUrl", script)
        self.assertIn("/api\\/project-library\\/[^/]+\\/media\\/[^/]+$", script)
        self.assertIn("copyProjectMediaToGlobal", script)
        self.assertIn("/copy-to-global`, {method:'POST'}", script)
        self.assertIn("items: isRoot ? (localAssetLibrary.items || []) : (node.items || [])", script)
        self.assertIn("data-save-project-global", script)
        self.assertIn('"smart.projectMedia": { zh: "项目素材", en: "Project media" }', translations)
        self.assertIn('"smart.globalResources": { zh: "全局资源库", en: "Global library" }', translations)


if __name__ == "__main__":
    unittest.main()
