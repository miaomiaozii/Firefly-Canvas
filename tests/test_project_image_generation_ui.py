import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ProjectImageGenerationUiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "static" / "js" / "smart-canvas.js").read_text(encoding="utf-8")

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

    def test_composer_stays_inside_the_viewport_for_large_image_nodes(self):
        self.assertIn("const nodeViewport = renderedNode?.getBoundingClientRect();", self.source)
        self.assertIn("composer.style.removeProperty('width');", self.source)
        self.assertIn("let composerRect = composer.getBoundingClientRect();", self.source)
        self.assertIn("Number(composer.offsetWidth || 0) * scaleX", self.source)
        self.assertNotIn("const cardW =", self.source)
        self.assertNotIn("composer.style.width =", self.source)
        self.assertIn("belowTop + composerH <= viewportH - edge", self.source)
        self.assertIn("requestAnimationFrame(() => positionComposerForNode(activeComposerNode()))", self.source)

    def test_async_generation_re_resolves_nodes_after_server_canvas_broadcast(self):
        self.assertIn("function liveNode(node)", self.source)
        self.assertIn("const completedImageNode = liveNode(pendingNode);", self.source)
        self.assertIn("const completedVideoNode = liveNode(pendingNode);", self.source)

    def test_angle_reference_is_sent_as_prompt_preset_with_exact_parameters(self):
        self.assertIn("anglePreset:'none'", self.source)
        self.assertIn("renderAnglePresetControl", self.source)
        for field in ("width:parsed.width", "height:parsed.height", "aspect_ratio:aspectRatioForRun", "resolution:runSettings.resolution", "media_type:'image'"):
            self.assertIn(field, self.source)

    def test_outpaint_keeps_the_selected_or_exact_result_ratio(self):
        self.assertIn("aspectRatio:outpaintAspectRatio", self.source)
        self.assertIn("ratio:'custom'", self.source)
        self.assertIn("customRatio:size.aspectRatio", self.source)
        self.assertIn("return reducedAspectRatio(parsed.width, parsed.height)", self.source)

    def test_server_recorded_failures_do_not_create_legacy_duplicate_history(self):
        self.assertIn("if(failureResult?.history_recorded) error.smartGenerationLogged = true", self.source)
        self.assertIn("if(!e?.smartGenerationLogged){", self.source)
        self.assertIn("result.history_record?.status !== 'failed'", self.source)

    def test_project_completion_applies_server_node_and_only_advances_contiguous_revision(self):
        self.assertIn("result.project_node_applied = true", self.source)
        self.assertIn("revision === registeredRevision + 1", self.source)
        self.assertIn("Number(canvas.revision || 0) === registeredRevision", self.source)
        self.assertIn("result?.project_node_applied", self.source)

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


if __name__ == "__main__":
    unittest.main()
