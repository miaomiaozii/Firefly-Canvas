"""#187 regression: outpaint generation must preserve the user-selected resolution.

Verified causal chain (issue #187, 2026-10-15 field report):

- Outpaint confirmation stores ``node.outpaintSize`` (width/height/aspectRatio) via
  ``applyOutpaintSizeToSmartParams`` (static/js/canvas-image-editor.js) and
  ``withOutpaintDisplaySettings`` keeps the user's resolution preset (1k/2k/4k) visible
  in the parameter panel.
- ``runApiGeneration`` (static/js/canvas-generation-tasks.js) used to gate resolution
  behind ``!outpaintSize``, so outpaint submissions sent ``resolution: ''``; the router's
  ``_normalize_generation_size_params`` (app/routers/api_project_library.py) maps ``''``
  to ``None``, and the provider then fell back to its own default (measured 1K output
  while the panel showed 4K).
- ADR docs/adr/0006-model-routing-preserves-user-parameters.md (accepted): width/height,
  aspect ratio, resolution and reference count are explicit user input; the system must
  not silently modify or downgrade them, and when no model fully supports the
  combination the generation must stop and report the incompatible parameter.

These tests pin the server-side contract (the same call ``_normalize_generation_size_params``
feeds into) against the suite's own checked-in capability fixture, never against the
machine's ``data/api_providers.json`` (#386): "no model supports X" is a statement about
the registry under test, and real registries legitimately declare arbitrary-capable
models (``arbitrary_aspect_ratio`` / ``arbitrary_resolution`` / ``"*"`` ratios), so the
outcome must be identical whether that gitignored file exists or not:

- an outpaint-shaped payload (aspect_ratio from outpaintSize, empty size/width/height)
  carrying the user's resolution must route and preserve that resolution;
- an aspect ratio no model in the fixture supports (outpaint "free" mode) must stop
  with ``aspect_ratio`` reported as incompatible — never a silent 1K fallback;
- a resolution no model in the fixture supports (e.g. ``8k``; the fixture offers
  1k/2k/4k only) must stop with ``resolution`` reported.
"""

from __future__ import annotations

import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import os

os.environ.setdefault("FIREFLY_TEST_MODE", "1")

from firefly_generation import ImageGenerationRequest, ModelRoutingError, route_image_generation

# #386：本套件只认 checked-in 能力 fixture，绝不读 data/api_providers.json——
# 它是 .gitignore:22 排除的本机配置。真实注册表里有声明
# arbitrary_aspect_ratio / arbitrary_resolution / "*" 比例的模型（如 comfyui、bailian），
# 「没有模型支持 1250:833 / 8k」这条假设在本机有配置时不成立，两个停止场景就从绿翻红；
# 而 #175 的隔离 workspace 里该文件必然缺位（gate 用 git ls-files 复制，
# gitignored 文件不在其中）——同一根因的两面：结果必须只由注册表本身决定。
# 漂移守卫（test_fixture_capability_assumptions_hold）保证 fixture 不会悄悄过期。
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "outpaint_provider_capabilities.json"
OUTPAINT_PROMPT = "Remove red area and fill the scene"


class OutpaintResolutionRoutingTests(unittest.TestCase):
    """Outpaint-shaped requests against the suite's own pinned capability registry."""

    @classmethod
    def setUpClass(cls) -> None:
        # #386: always route against the checked-in fixture. Reading the machine's
        # data/api_providers.json made the stop-scenarios depend on whether that
        # gitignored file exists (real registries carry arbitrary-capable models);
        # results must be identical in both situations.
        cls.providers = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    def test_fixture_capability_assumptions_hold(self) -> None:
        # 漂移守卫：停止场景依赖 fixture「没有模型支持 1250:833 自由比例 / 8k 分辨率」，
        # 保留场景依赖「有模型支持 21:9+4k」。fixture 一旦漂移（比如某模型新增
        # arbitrary_aspect_ratio / "*" 声明），场景测试会以「assertRaises 没收到异常」
        # 这种不透明方式失败；这里先按路由器的同一语义检查，失败信息直接可操作。
        # 走真实路由器而不是列表比对：arbitrary_* / "*" 声明只有路由器会识别。
        #
        # 不能用 skipTest：门的策略把任何 skipped 都当失败
        # （tools/quality_gate.py:1563 `if summary and any(summary.values()) and status == "passed"`）。
        route = self.route(aspect_ratio="21:9", resolution="4k")
        self.assertEqual(
            route.request.get("resolution"),
            "4k",
            "fixture 不再支持 21:9 + 4k；同步更新 fixture 和场景",
        )
        for label, incompatible, kwargs in (
            ("自由比例 1250:833", "aspect_ratio", {"aspect_ratio": "1250:833", "resolution": "4k"}),
            ("分辨率 8k", "resolution", {"aspect_ratio": "21:9", "resolution": "8k"}),
        ):
            with self.subTest(scene=label):
                with self.assertRaises(ModelRoutingError) as ctx:
                    self.route(**kwargs)
                self.assertIn(
                    incompatible,
                    list(ctx.exception.incompatible_parameters),
                    f"fixture 出现了支持「{label}」的模型；「不支持的 {incompatible}」场景测的不再是它声称的东西",
                )

    def route(self, **kwargs: object):
        # Mirrors the payload runApiGeneration sends after outpaint confirmation:
        # aspect_ratio from outpaintSize, size/width/height empty (server normalizes
        # '' to None), resolution = the user's panel selection.
        request = ImageGenerationRequest(
            prompt=OUTPAINT_PROMPT,
            size=kwargs.get("size"),
            width=kwargs.get("width"),
            height=kwargs.get("height"),
            aspect_ratio=kwargs.get("aspect_ratio"),
            resolution=kwargs.get("resolution"),
        )
        return route_image_generation(request, self.providers)

    def test_outpaint_payload_preserves_user_resolution(self) -> None:
        # #187 scenario after the client fix: outpaint 21:9 with the user's 4K preset.
        route = self.route(aspect_ratio="21:9", resolution="4k")
        self.assertEqual(route.request.get("resolution"), "4k")
        self.assertEqual(route.request.get("aspect_ratio"), "21:9")

    def test_outpaint_free_ratio_stops_and_reports_aspect_ratio(self) -> None:
        # Outpaint "free" mode can produce non-standard ratios (e.g. 1250:833).
        # No model supports it, so generation must stop and name the parameter —
        # it must never be silently downgraded to a default 1K square.
        with self.assertRaises(ModelRoutingError) as ctx:
            self.route(aspect_ratio="1250:833", resolution="4k")
        error = ctx.exception
        self.assertIn("aspect_ratio", list(error.incompatible_parameters))

    def test_unsupported_resolution_stops_and_reports_resolution(self) -> None:
        # '8k' is supported by no model in the pinned fixture (only 1k/2k/4k).
        with self.assertRaises(ModelRoutingError) as ctx:
            self.route(aspect_ratio="21:9", resolution="8k")
        error = ctx.exception
        self.assertIn("resolution", list(error.incompatible_parameters))

    def test_supported_combo_without_resolution_still_routes(self) -> None:
        # Pre-#187 outpaint behavior (no resolution sent) must keep working:
        # the ratio alone routes, and resolution stays absent rather than invented.
        route = self.route(aspect_ratio="21:9")
        self.assertIsNone(route.request.get("resolution"))


if __name__ == "__main__":
    unittest.main()
