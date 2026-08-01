import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ProjectVideoGenerationUiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "static" / "js" / "smart-canvas.js").read_text(encoding="utf-8")
        cls.i18n_source = (ROOT / "static" / "js" / "i18n" / "smart-canvas.js").read_text(encoding="utf-8")

    def test_core_canvas_posts_project_owned_video_request_and_polls_video_task(self):
        self.assertIn("/video-generation-tasks`,", self.source)
        self.assertIn("runApiVideoGeneration(prompt, refs, activeSettings, node)", self.source)
        self.assertIn("routing_mode:(runSettings.engine === 'volcengine' || runSettings.videoRoutingMode === 'manual') ? 'manual' : 'auto'", self.source)
        self.assertIn("function renderVideoRoutingControl(){", self.source)
        self.assertIn("return '';", self.source)
        self.assertIn("result_node_id:resultNode.id", self.source)
        self.assertIn("image_reference_asset_ids:ownedRefs", self.source)
        self.assertIn("video_reference_asset_ids:ownedRefs", self.source)
        self.assertIn("audio_reference_asset_ids:ownedRefs", self.source)
        self.assertIn("media_type:'video'", self.source)
        self.assertIn("resolution:runSettings.videoResolution || ''", self.source)
        self.assertIn("quality:runSettings.videoQuality || runSettings.quality || 'auto'", self.source)
        self.assertIn("size:videoSize.size", self.source)
        self.assertIn("width:videoSize.width", self.source)
        self.assertIn("height:videoSize.height", self.source)
        self.assertIn("quality:runSettings.videoQuality || runSettings.quality || 'auto'", self.source)
        self.assertIn("return_last_frame:Boolean(runSettings.videoReturnLastFrame)", self.source)
        self.assertIn("videoReturnLastFrame:false", self.source)
        self.assertIn(
            "renderVideoToggleControl('videoReturnLastFrame', tr('smart.videoReturnLastFrame'))",
            self.source,
        )
        self.assertIn('"smart.videoReturnLastFrame"', self.i18n_source)
        self.assertIn("kind:'video'", self.source)
        self.assertIn("result?.video_items?.length", self.source)
        self.assertIn("video_items", self.source)

    def test_video_frame_export_reimports_the_frame_as_project_image_media(self):
        self.assertIn("expected_kind:expectedKind", self.source)
        self.assertIn("const cacheKey = `${projectId}\\n${expectedKind}\\n${sourceUrl}`;", self.source)
        self.assertIn("importProjectMediaReference({...frame, kind:'image', mime:'image/png'})", self.source)
        self.assertIn("projectMediaUrl(owned.asset_id)", self.source)
        self.assertIn("async function exportVideoFrame", self.source)

    def test_project_canvas_keeps_video_preview_and_audio_reference_controls(self):
        self.assertIn("mediaKindForItem(editing.image || {}) === 'video'", self.source)
        self.assertIn("currentVideo.src = previewSrc", self.source)
        self.assertIn("audioRefsOnly(uploadedRefs)", self.source)
        self.assertIn("videoRefsOnly(uploadedRefs)", self.source)


if __name__ == "__main__":
    unittest.main()
