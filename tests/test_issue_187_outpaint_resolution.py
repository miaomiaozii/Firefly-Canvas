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

These tests pin the server-side contract with the real ``data/api_providers.json``
(the same call ``_normalize_generation_size_params`` feeds into):

- an outpaint-shaped payload (aspect_ratio from outpaintSize, empty size/width/height)
  carrying the user's resolution must route and preserve that resolution;
- an aspect ratio no model supports (outpaint "free" mode) must stop with
  ``aspect_ratio`` reported as incompatible — never a silent 1K fallback;
- a resolution no model supports (e.g. ``8k``; real data only offers
  1k/2k/4k/auto) must stop with ``resolution`` reported.
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

PROVIDERS_PATH = ROOT / "data" / "api_providers.json"
# #187：不能硬读 data/api_providers.json——它被 .gitignore:22 排除，
# tools/quality_gate.py 的隔离 workspace 用 `git ls-files --cached --others --exclude-standard`
# 复制文件，gitignored 文件不在里面，于是 setUpClass 直接 FileNotFoundError
# （跟 #175 同一个根因模式）。缺位时退回 checked-in fixture，
# 并用下面的漂移守卫保证 fixture 不会悄悄过期。
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "outpaint_provider_capabilities.json"
OUTPAINT_PROMPT = "Remove red area and fill the scene"


def _capability_models(providers):
    for provider in providers:
        for model_id, caps in (provider.get("image_model_capabilities") or {}).items():
            yield provider, model_id, caps


def _supports(providers, *, aspect_ratio=None, resolution=None):
    for _, _, caps in _capability_models(providers):
        ratios = caps.get("aspect_ratios") or []
        resolutions = caps.get("resolutions") or []
        if (aspect_ratio is None or aspect_ratio in ratios) and (
            resolution is None or resolution in resolutions
        ):
            return True
    return False


class OutpaintResolutionRoutingTests(unittest.TestCase):
    """Outpaint-shaped requests against the real provider capability registry."""

    @classmethod
    def setUpClass(cls) -> None:
        if PROVIDERS_PATH.exists():
            cls.providers = json.loads(PROVIDERS_PATH.read_text(encoding="utf-8"))
            cls.providers_source = "real"
        else:
            cls.providers = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
            cls.providers_source = "fixture"

    def test_provider_source_assumptions_hold(self) -> None:
        # 漂移守卫：本文件所有场景都依赖「有模型支持 21:9+4k」、「无模型支持 8k」。
        # 真实注册表变了就得同步改 fixture，否则门里跑的是过期的能力假设。
        #
        # 不能用 skipTest：门的策略把任何 skipped 都当失败
        # （tools/quality_gate.py:1563 `if summary and any(summary.values()) and status == "passed"`），
        # 而隔离 workspace 里 gitignored 的真实注册表必然缺位。
        # fixture 是无条件检查的；真实注册表存在时额外检查一份。
        fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        sources = [("fixture", fixture)]
        if PROVIDERS_PATH.exists():
            sources.append(("real", json.loads(PROVIDERS_PATH.read_text(encoding="utf-8"))))
        for label, providers in sources:
            with self.subTest(source=label):
                self.assertTrue(
                    _supports(providers, aspect_ratio="21:9", resolution="4k"),
                    f"{label} 不再支持 21:9 + 4k；同步更新 fixture 和场景",
                )
                self.assertFalse(
                    _supports(providers, resolution="8k"),
                    f"{label} 出现了 8k；「不支持的 resolution」场景测的不再是它声称的东西",
                )

    def test_routes_against_the_fixture_when_real_registry_absent(self) -> None:
        # 显式钉住兜底路径本身可用（门里跑的就是这条）。
        fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        route = route_image_generation(
            ImageGenerationRequest(prompt=OUTPAINT_PROMPT, size=None, width=None, height=None, aspect_ratio="21:9", resolution="2k"),
            fixture,
        )
        self.assertEqual(route.request.get("resolution"), "2k")
        self.assertEqual(route.request.get("aspect_ratio"), "21:9")

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
        # '8k' is supported by no model in the real registry (only 1k/2k/4k/auto).
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
