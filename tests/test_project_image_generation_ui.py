import json
import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ProjectImageGenerationUiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "static" / "js" / "smart-canvas.js").read_text(encoding="utf-8")
        cls.styles = (ROOT / "static" / "css" / "smart-canvas.css").read_text(encoding="utf-8")

    def test_core_canvas_uses_project_generation_endpoint_and_result_node(self):
        self.assertIn("/image-generation-tasks`,", self.source)
        self.assertIn("result_node_id:resultNode.id", self.source)
        self.assertIn("reference_asset_ids", self.source)
        self.assertIn("project_revision", self.source)
        self.assertIn("history_recorded", self.source)
        self.assertIn("await saveCanvas()", self.source)
        self.assertIn("projectRevision:task.project_revision", self.source)
        self.assertIn("registeredNode:task.registered_node", self.source)
        self.assertNotIn("registeredCanvas:task.registered_canvas", self.source)

    def test_auto_routing_is_invisible_and_default(self):
        self.assertIn("routingMode:'auto'", self.source)
        self.assertIn("function renderImageRoutingControl(){", self.source)
        self.assertIn("return '';", self.source)
        self.assertIn("if(key === 'model' && settings.routingMode !== 'manual') settings.routingMode = 'manual';", self.source)
        self.assertIn("routing_mode:runSettings.routingMode === 'manual' ? 'manual' : 'auto'", self.source)
        self.assertIn("renderProviderControl", self.source)
        self.assertIn("renderModelControl", self.source)

    def test_model_picker_is_available_and_searchable_for_large_provider_lists(self):
        self.assertIn("${renderModelControl(models)}", self.source)
        self.assertIn("data-model-search", self.source)
        self.assertIn("data-model-options", self.source)
        self.assertIn("modelSearchInput", self.source)
        self.assertIn("if(key === 'model' && settings.routingMode !== 'manual') settings.routingMode = 'manual';", self.source)
        self.assertIn("smart.modelSelectionNote", (ROOT / "static" / "js" / "i18n" / "smart-canvas.js").read_text(encoding="utf-8"))

    def test_run_button_requires_a_prompt_for_normal_image_generation(self):
        self.assertIn("let missingPrompt = false;", self.source)
        self.assertIn("buildPromptRequestForNode(node, null)", self.source)
        self.assertIn("smartRunNeedsPrompt(settings)", self.source)
        self.assertIn("tr('smart.toastNeedPrompt')", self.source)
        self.assertIn("syncRunButtonState(selectedNode());", self.source)

    def test_prompt_validation_does_not_replace_live_editor_during_ime_input(self):
        self.assertIn("function collectPromptParts(root=promptInput)", self.source)
        self.assertIn("function promptDraftHtmlForNode(node)", self.source)
        self.assertIn("const promptRoot = document.createElement('div');", self.source)
        self.assertIn("buildPromptRequest(node, defaultImages, false, ctx, promptRoot)", self.source)
        self.assertNotIn("const oldHtml = promptInput.innerHTML;", self.source)
        self.assertIn("closeAllSmartPopovers();", self.source)

    def test_composer_stays_world_anchored_directly_below_the_node(self):
        match = re.search(r"function positionComposerForNode\(node\)\{(?P<body>.*?)\n\}", self.source, re.S)
        self.assertIsNotNone(match)
        body = match.group("body")
        self.assertIn("const modelRect = nodeRect(node);", body)
        self.assertIn("composer.style.removeProperty('width');", body)
        self.assertIn("composer.style.removeProperty('max-width');", body)
        self.assertIn("const composerWidth = Math.max(1, Number(composer.offsetWidth || modelRect.width || 1));", body)
        self.assertIn("composer.style.left = `${modelRect.x + (modelRect.width - composerWidth) / 2}px`;", body)
        self.assertIn("composer.style.top = `${modelRect.y + modelRect.height + 14}px`;", body)
        self.assertNotIn("getBoundingClientRect", body)
        self.assertNotIn("candidates", body)
        self.assertNotIn("clamp(", body)
        self.assertIn("@media (min-width:760px) and (max-width:900px)", self.styles)
        self.assertIn('composer:has(#apiKindToggle [data-kind="image"].active) { width:580px; min-width:580px; max-width:580px; }', self.styles)
        self.assertIn("frameSmartCanvasContentIfNeeded();", self.source)
        self.assertIn("if(subject && composer && getComputedStyle(composer).display !== 'none')", self.source)
        self.assertIn("requestAnimationFrame(() => positionComposerForNode(activeComposerNode()))", self.source)
        self.assertIn("const repositionComposer = () => {", self.source)
        self.assertIn("requestAnimationFrame(repositionComposer);", self.source)

    def test_async_generation_re_resolves_nodes_after_server_canvas_broadcast(self):
        self.assertIn("function liveNode(node)", self.source)
        self.assertIn("const completedImageNode = liveNode(pendingNode);", self.source)
        self.assertIn("const completedVideoNode = liveNode(pendingNode);", self.source)

    def test_angle_reference_is_sent_as_prompt_preset_with_exact_parameters(self):
        self.assertIn("anglePreset:'none'", self.source)
        self.assertIn("renderAnglePresetControl", self.source)
        for field in ("width:parsed?.width || null", "height:parsed?.height || null", "aspect_ratio:aspectRatioForRun", "resolution:usesExplicitResolution", "media_type:'image'"):
            self.assertIn(field, self.source)

    def test_outpaint_keeps_the_selected_or_exact_result_ratio(self):
        self.assertIn("aspectRatio:outpaintAspectRatio", self.source)
        self.assertIn("ratio:'custom'", self.source)
        self.assertIn("customRatio:size.aspectRatio", self.source)
        self.assertIn("return reducedAspectRatio(parsed.width, parsed.height)", self.source)

    def test_project_task_cards_expose_scoped_cancel_retry_and_stable_idempotency(self):
        self.assertIn("idempotency_key:", self.source)
        self.assertIn("function projectImageTaskUrl(taskId)", self.source)
        self.assertIn("data-log-cancel-task", self.source)
        self.assertIn("data-log-retry-task", self.source)
        self.assertIn("data-log-download-original", self.source)
        self.assertIn("async function controlProjectImageTask", self.source)
        self.assertIn("task.status === 'cancelled'", self.source)
        self.assertIn("retry_of_task_id", self.source)
        self.assertIn("const remainingTasks = smartPendingTasks(node).filter(item => item.taskId !== task.taskId);", self.source)
        self.assertIn("remainingTasks.reduce((total, item) => total + Math.max(1, Number(item.count || 1)), 0)", self.source)
        self.assertNotIn("node.pending = Math.max(0, Number(node.pending || 0) - 1);", self.source)
        self.assertNotIn("fetch(`/api/canvas-image-tasks/${encodeURIComponent(taskId)}`)", self.source)

    def test_server_recorded_failures_do_not_create_legacy_duplicate_history(self):
        self.assertIn("if(failureResult?.history_recorded) error.smartGenerationLogged = true", self.source)
        self.assertIn("if(!e?.smartGenerationLogged){", self.source)
        self.assertIn("result.history_record?.status === 'succeeded'", self.source)
        self.assertIn("if(failures.length){", self.source)
        self.assertNotIn("if(failures.length && !(node.images || []).length){", self.source)
        self.assertIn("void resumeSmartPendingNode(node).catch(() => {});", self.source)

    def test_project_completion_applies_server_node_and_only_advances_contiguous_revision(self):
        self.assertIn("result.project_node_applied = true", self.source)
        self.assertIn("revision === registeredRevision + 1", self.source)
        self.assertIn("Number(canvas.revision || 0) === registeredRevision", self.source)
        self.assertIn("const smartGenerationNodeRevisions = new Map();", self.source)
        self.assertIn("smartGenerationNodeRevisions.get(resultNodeId)", self.source)
        self.assertIn("smartGenerationNodeRevisions.set(nodeId, Math.max(serverRevision", self.source)
        self.assertIn("const nodeSnapshotCurrent = revision <= 0 || revision >= appliedNodeRevision;", self.source)
        self.assertIn("if(resultNode && result.final_node && nodeSnapshotCurrent)", self.source)
        self.assertIn("&& nodeSnapshotCurrent", self.source)
        self.assertIn("result?.project_node_applied", self.source)
        self.assertIn("if(smartLogModal?.classList.contains('open')) scheduleSmartHistoryWindowRender(true);", self.source)

    def test_project_submission_outcome_is_durable_and_replayed_with_the_same_key(self):
        self.assertIn("function upsertSmartGenerationSubmission(node, submission)", self.source)
        self.assertIn("requestPayload:{...payload}", self.source)
        self.assertIn("if(!await flushProjectCanvasBeforeGeneration())", self.source)
        self.assertIn("async function postProjectImageTaskWithReplay(projectId, payload)", self.source)
        self.assertIn("unknown.smartSubmissionUnknown = true", self.source)
        self.assertIn("async function resumeSmartGenerationSubmission(node, submission)", self.source)
        self.assertIn("idempotency_key:idempotencyKey", self.source)
        self.assertIn("smartGenerationSubmissions(node).forEach", self.source)

    def test_project_merge_preserves_distinct_output_occurrences_that_share_one_asset(self):
        match = re.search(
            r"function mergeSmartImageLists\(localImgs, remoteImgs\)\{.*?\n\}",
            self.source,
            re.S,
        )
        self.assertIsNotNone(match)
        images = [
            {"asset_id": "asset-a", "output_id": "occ-1", "output_order": 0, "url": "/media/asset-a"},
            {"asset_id": "asset-b", "output_id": "occ-2", "output_order": 1, "url": "/media/asset-b"},
            {"asset_id": "asset-a", "output_id": "occ-3", "output_order": 2, "url": "/media/asset-a"},
            {"asset_id": "asset-d", "output_id": "occ-4", "output_order": 3, "url": "/media/asset-d"},
            {"asset_id": "asset-e", "output_id": "occ-5", "output_order": 4, "url": "/media/asset-e"},
            {"asset_id": "asset-f", "output_id": "occ-6", "output_order": 5, "url": "/media/asset-f"},
            {"asset_id": "asset-g", "output_id": "occ-7", "output_order": 6, "url": "/media/asset-g"},
            {"asset_id": "asset-h", "output_id": "occ-8", "output_order": 7, "url": "/media/asset-h"},
        ]
        local = images[:2] + images[3:]
        script = (
            match.group(0)
            + f"\nconst result=mergeSmartImageLists({json.dumps(local)}, {json.dumps(images)});"
            + "\nconsole.log(JSON.stringify(result.map(item=>[item.output_id,item.output_order])));"
        )
        completed = subprocess.run(
            ["node", "-e", script],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
        )
        self.assertEqual(
            json.loads(completed.stdout),
            [[f"occ-{index}", index - 1] for index in range(1, 9)],
        )

    def test_restore_selection_prefers_stable_output_identity_over_a_stale_index(self):
        match = re.search(
            r"function captureSmartOriginalDeletionUiState\(logId, outputId, outputIndex=-1\)\{.*?\n\}",
            self.source,
            re.S,
        )
        self.assertIsNotNone(match)
        outputs = [
            {"output_id": f"occ-{index}", "outputIndex": index - 1}
            for index in range(1, 9)
        ]
        script = (
            "const outputs=" + json.dumps(outputs) + ";"
            + "const smartHistorySelectedImage={logId:'log-1',outputId:'occ-5',outputIndex:3};"
            + "const smartLogDetailSession={logId:'log-1',outputId:'occ-5',outputIndex:3,outputs};"
            + "const selectedImage={nodeId:'node-1',outputId:'occ-5',index:3};"
            + "const nodes=[{id:'node-1',images:outputs}];"
            + "const smartLogImageOutputs=()=>outputs;"
            + "const smartLogEntryById=()=>({id:'log-1',outputs});"
            + "const document={querySelector:()=>({})};"
            + match.group(0)
            + "\nconsole.log(JSON.stringify(captureSmartOriginalDeletionUiState('log-1','')));"
        )
        completed = subprocess.run(
            ["node", "-e", script],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
        )
        state = json.loads(completed.stdout)
        self.assertEqual(state["historySelectedOutputId"], "occ-5")
        self.assertEqual(state["detailSelectedOutputId"], "occ-5")
        self.assertEqual(state["detailPosition"], 4)
        self.assertEqual(state["canvasSelectedOutputId"], "occ-5")

    def test_project_merge_deduplicates_hydrated_and_persisted_legacy_media_by_asset(self):
        match = re.search(
            r"function mergeSmartImageLists\(localImgs, remoteImgs\)\{.*?\n\}",
            self.source,
            re.S,
        )
        self.assertIsNotNone(match)
        local = [{
            "asset_id": "asset-a",
            "url": "/api/project-library/project-a/media/asset-a",
            "kind": "image",
        }]
        remote = [{"asset_id": "asset-a", "kind": "image"}]
        script = (
            match.group(0)
            + f"\nconst result=mergeSmartImageLists({json.dumps(local)}, {json.dumps(remote)});"
            + "\nconsole.log(JSON.stringify(result));"
        )
        completed = subprocess.run(
            ["node", "-e", script],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
        )
        self.assertEqual(
            json.loads(completed.stdout),
            [{
                "asset_id": "asset-a",
                "url": "/api/project-library/project-a/media/asset-a",
                "kind": "image",
            }],
        )

    def test_project_merge_preserves_non_generation_node_shape(self):
        self.assertIn("function smartNodeUsesGenerationMerge(node){", self.source)
        self.assertIn(
            "if(!smartNodeUsesGenerationMerge(local) && !smartNodeUsesGenerationMerge(remote))",
            self.source,
        )
        self.assertIn("return {...remote};", self.source)

    def test_project_merge_preserves_independent_pending_task_identities(self):
        self.assertIn("function mergeSmartPendingTaskLists(local, remote, terminalTaskIds=new Set())", self.source)
        self.assertIn("terminalTaskIds.has(taskId)", self.source)
        self.assertIn("const pendingTasks = mergeSmartPendingTaskLists(local, remote, terminalTaskIds);", self.source)
        self.assertIn("terminal.add(`key:${idempotencyKey}`)", self.source)
        self.assertIn("!terminalTaskIds.has(`key:${key}`)", self.source)
        self.assertIn("const submissions = mergeSmartSubmissionLists(local, remote, terminalTaskIds);", self.source)
        self.assertIn("merged.pendingTasks = pendingTasks", self.source)
        self.assertIn("node.pendingTasks = mergeSmartPendingTaskLists(smartPendingTasks(node), registeredTasks);", self.source)
        self.assertIn("currentNode.pendingTasks = smartPendingTasks(currentNode).filter", self.source)
        self.assertIn("function mergeSmartHistoryRecordLists(localLogs, remoteLogs)", self.source)
        self.assertIn("canvas.logs = mergeSmartHistoryRecordLists(canvas.logs || [], serverCanvas.logs || []);", self.source)
        self.assertIn("createdRemoteTaskCards = ensureSmartPendingHistoryLogs", self.source)
        self.assertIn("smartHistoryTaskId(log) === taskId", self.source)
        self.assertIn("canvas.logs = mergeSmartHistoryRecordLists(canvas.logs, []);", self.source)
        self.assertIn("const taskLog = (canvas?.logs || []).find(log => smartHistoryTaskId(log) === normalizedTaskId);", self.source)

    def test_loop_archives_old_output_before_task_registration(self):
        pattern = re.compile(
            r"if\(isApiLikeEngine\(runSettings\.engine\).*?const existing = cleanHistoryImages"
            r".*?outputSlot\.images = \[\];.*?await runApiGeneration",
            re.DOTALL,
        )
        self.assertRegex(self.source, pattern)
        self.assertIn("const restoreArchive = (conditional=false) =>", self.source)
        self.assertIn("if(restored === false)", self.source)
        self.assertIn("restoreArchive(true)", self.source)
        self.assertIn("exactArchivedHistory", self.source)
        self.assertIn("exactArchivedConnections", self.source)
        self.assertIn("archivedHistoryConnections", self.source)
        self.assertIn("await saveCanvas()", self.source)

    def test_default_reference_images_never_include_own_history_output(self):
        self.assertNotIn("const self = selfReferenceImagesForNode(node, consume, ctx).filter(img => img?.url);", self.source)
        self.assertNotIn("if(self.length) return uniqueReferenceImages([...self, ...upstream, ...manual]);", self.source)
        self.assertIn("const upstream = (smartImageUsesWorkflowInput(node, ctx) ? workflowInputImagesFor(node, consume, ctx) : inputImagesFor(node, consume, ctx))", self.source)
        self.assertIn("const manual = manualReferenceImagesFor(node);", self.source)
        self.assertIn("return uniqueReferenceImages([...upstream, ...manual]);", self.source)

    def test_history_uses_provider_friendly_model_name_and_preserves_raw_id(self):
        self.assertIn("function historyLogModelMeta(log){", self.source)
        self.assertIn("modelDisplayName(provider, raw)", self.source)
        self.assertIn("class=\"log-chip model-chip\"", self.source)
        self.assertIn("title=\"${escapeAttr(modelMeta.raw)}\"", self.source)

    def test_generation_has_persistent_pending_feedback_and_result_entry_effect(self):
        self.assertIn("const isPending = Boolean(node.pending || isQueued || isJimengPending);", self.source)
        # generationPlaceholderHtml was extended with completedImgs (progressive
        # completion: show finished thumbs in placeholder slots) and
        # candidatePhases (per-cell stage from backend progress.candidate_phases).
        self.assertIn("function generationPlaceholderHtml(layout, count=1, queued=false, progress={}, completedImgs=[], candidatePhases=[], taskInfo=null){", self.source)
        self.assertIn("if(node.pending){", self.source)
        self.assertNotIn("if(node.pending && imgs.length === 0){", self.source)
        self.assertIn("generation-placeholder", self.source)
        self.assertIn(".generation-placeholder-copy", self.styles)
        self.assertIn("@keyframes generation-placeholder-spin", self.styles)
        self.assertIn("function animateSmartGenerationResult(node){", self.source)
        self.assertIn("generation-result-enter", self.source)
        self.assertIn(".image-node.node-pending .node-body::after", self.styles)
        self.assertIn("@keyframes generation-result-enter", self.styles)

    def test_history_is_sorted_newest_first_at_every_server_boundary(self):
        normalized = "canvas.logs = sortSmartHistoryLogs(hydrateProjectMediaReferences(canvas.logs || []));"
        merged = "canvas.logs = mergeSmartActiveHistoryTaskLogs("
        self.assertGreaterEqual(self.source.count(normalized), 4)
        # #76（P2-2）：项目画布变更的 409/成功模板统一走 adoptServerCanvas（内部经
        # mergeSmartActiveHistoryTaskLogs 合并，该函数内部 sortSmartHistoryLogs），
        # 因此直连调用点从 3+ 收敛为 2（deleteCanvasLogEntry 遗留路径 + 原图恢复）。
        self.assertGreaterEqual(self.source.count(merged), 2)
        self.assertIn("async function adoptServerCanvas(serverCanvasData, {activeTaskLogs = null, settleMs = 0} = {}){", self.source)
        self.assertIn("async function projectCanvasRequest(url, {method = 'POST', payload, retries = 0, settleMs = 0} = {}){", self.source)
        self.assertIn("return sortSmartHistoryLogs([", self.source)
        self.assertIn("canvas.logs = sortSmartHistoryLogs(canvas.logs);", self.source)

    def test_history_pending_card_is_immediate_animated_and_never_persisted(self):
        self.assertIn("function beginSmartPendingHistoryLog(node, defaults={}){", self.source)
        self.assertIn("const id = `pending-local-${uid('generation')}`;", self.source)
        self.assertIn("function ensureSmartPendingHistoryLogs(node, tasks=smartPendingTasks(node), defaults={}){", self.source)
        self.assertIn("function absorbProjectGenerationResult(result, taskId=''){", self.source)
        self.assertIn(".filter(log => log?.status !== 'pending')", self.source)
        self.assertIn("delete node._pendingHistoryLogId;", self.source)
        self.assertIn("log-history-placeholder", self.source)
        self.assertIn(".log-item.pending", self.styles)
        self.assertIn("@keyframes log-history-placeholder-pulse", self.styles)

    def test_node_and_history_pending_cards_share_honest_six_stage_progress(self):
        self.assertIn("function generationStageProgressHtml(progress={}){", self.source)
        self.assertIn("function applySmartTaskProgress(taskId, progress){", self.source)
        self.assertIn("generation-stage-progress", self.source)
        self.assertIn("generation-stage-segment", self.source)
        self.assertIn("data-phase=", self.source)
        self.assertIn("applySmartTaskProgress(taskId, task.progress)", self.source)
        self.assertIn(".generation-stage-progress", self.styles)
        self.assertIn(".generation-stage-segment.done", self.styles)
        self.assertIn(".generation-stage-segment.active", self.styles)

    def test_canvas_node_timer_and_delete_stay_at_top_right(self):
        self.assertIn(".floating-node-actions { position:absolute; right:7px; top:7px;", self.styles)
        self.assertIn(".run-time-pill { position:absolute; right:41px; top:8px;", self.styles)
        self.assertNotIn(".floating-node-actions { position:absolute; left:7px; bottom:7px;", self.styles)
        self.assertNotIn(".run-time-pill { position:absolute; left:41px; bottom:8px;", self.styles)

    def test_composer_and_history_cards_do_not_force_critical_labels_into_tiny_widths(self):
        self.assertNotIn(".smart-image-composer .size-picker-control { width:88px; }", self.styles)
        self.assertNotIn(".smart-image-composer .angle-control { width:86px; }", self.styles)
        self.assertIn("width:min(720px, calc(100vw - 36px));", self.styles)
        self.assertNotIn("max-width:min(980px, calc(100vw - 36px));", self.styles)
        self.assertIn(".composer:has(#apiKindToggle [data-kind=\"image\"].active) .dynamic-params { flex-wrap:wrap;", self.styles)
        self.assertIn("flex:0 0 208px;", self.styles)
        self.assertIn("width:208px;", self.styles)

    def test_resolution_picker_does_not_show_misleading_pixel_dimensions(self):
        self.assertIn("<span>${value.toUpperCase()}</span>", self.source)
        self.assertNotIn(
            "<small>${escapeHtml(apiImageSize(currentRatio, value, currentCustomRatio, '') || '')}</small>",
            self.source,
        )

    def test_image_models_with_declared_resolution_tiers_show_and_send_resolution(self):
        self.assertIn("function apiImageUsesExplicitResolution(sourceSettings=settings){", self.source)
        self.assertIn("provider?.image_model_capabilities?.[String(sourceSettings?.model || '').trim()]", self.source)
        self.assertIn("capabilities.arbitrary_resolution === true || (Array.isArray(capabilities.resolutions) && capabilities.resolutions.length > 0)", self.source)
        self.assertNotIn("identity.includes('comfly')", self.source)
        self.assertNotIn("identity.includes('ai.comfly.org')", self.source)
        self.assertIn("settingsSnapshot.resolution = '';", self.source)
        self.assertIn("const usesExplicitResolution = !outpaintSize && apiImageUsesExplicitResolution(runSettings);", self.source)
        self.assertIn("resolution:usesExplicitResolution ? (runSettings.resolution || '') : ''", self.source)
        self.assertIn(".size-picker-popover.resolution-free", self.styles)

    def test_llm_prompt_output_remains_directly_editable(self):
        self.assertNotIn("const readonly = node.llmEnabled ? 'readonly' : '';", self.source)
        self.assertNotIn('class="prompt-node-text prompt-node-control" ${readonly}', self.source)
        self.assertIn('class="prompt-node-text prompt-node-control"', self.source)

    def test_prompt_llm_posts_project_scope_for_project_media(self):
        self.assertIn("project_id:projectId,", self.source)


if __name__ == "__main__":
    unittest.main()
