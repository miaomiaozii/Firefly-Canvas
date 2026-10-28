"""#238 Perf(W1) 源码契约：缩放自适应缩略图 + 视频 hover 播放（宿主侧）。

档位选择/URL 重写/poster 的可执行行为在 tests/canvas_module_units/test-canvas-media-lod.mjs
（node 单测）。本文件钉住 smart-canvas.js 宿主侧接线，防止后续重构悄悄脱钩：

1. singleMediaHtml 用 smartNodePreviewTier（而不是写死 768）选图片/视频预览档；
2. applyViewport 挂 schedulePreviewLodRefresh（zoom 稳定后重刷已渲染预览的 w 档）；
3. shell 上有 pointerover/pointerout 委托的 hover 播放（muted+loop+暂停归零），
   且只命中 data-video-preview="1"（不误触内联播放器/音频）。
"""
from __future__ import annotations

import re
import unittest

from tests.canvas_frontend_sources import ROOT


def _source(name: str) -> str:
    return (ROOT / "static" / "js" / name).read_text(encoding="utf-8")


class MediaLodContractTests(unittest.TestCase):
    def test_single_media_html_uses_zoom_tier(self):
        src = _source("smart-canvas.js")
        body = re.search(r"function singleMediaHtml\(.*?\n\}", src, re.S)
        self.assertIsNotNone(body, "singleMediaHtml 不存在")
        self.assertIn("smartNodePreviewTier(w, h)", body.group(0))
        self.assertNotIn("smartPreviewImgHtml(img, 768", body.group(0))
        self.assertNotIn("smartVideoPreviewHtml(img, 768", body.group(0))

    def test_apply_viewport_schedules_lod_refresh(self):
        src = _source("smart-canvas.js")
        body = re.search(r"function applyViewport\(\)\{.*?\n\}", src, re.S)
        self.assertIsNotNone(body, "applyViewport 不存在")
        self.assertIn("schedulePreviewLodRefresh()", body.group(0))
        self.assertIn("function refreshCanvasPreviewLodWidths()", src)
        # 只重写带 w 参数的预览 URL，不碰原始 URL。
        self.assertIn("setPreviewUrlWidth(current, tier)", src)
        self.assertIn('setPreviewUrlWidth(poster, tier)', src)

    def test_hover_play_listers_target_preview_videos_only(self):
        src = _source("smart-canvas.js")
        self.assertIn("""shell.addEventListener('pointerover'""", src)
        self.assertIn("""shell.addEventListener('pointerout'""", src)
        self.assertIn('video[data-video-preview="1"]', src)
        self.assertIn("video.loop = true", src)
        self.assertIn("video.currentTime = 0", src)

    def test_module_video_preview_builders_preload_none(self):
        src = _source("canvas-media-history.js")
        body = re.search(r"function smartVideoFallbackHtml\(.*?\n\}", src, re.S)
        self.assertIsNotNone(body, "smartVideoFallbackHtml 应在 canvas-media-history.js")
        self.assertIn('preload="none"', body.group(0))
        self.assertNotIn('preload="metadata"', body.group(0))
        self.assertIn('poster="${escapeHtml(posterUrl)}"', body.group(0))
        # 宿主不得重复声明（同一全局词法作用域会 SyntaxError）。
        host = _source("smart-canvas.js")
        self.assertIsNone(re.search(r"function smartVideoFallbackHtml\(", host))
        self.assertIsNone(re.search(r"function smartVideoPreviewHtml\(", host))


if __name__ == "__main__":
    unittest.main()
