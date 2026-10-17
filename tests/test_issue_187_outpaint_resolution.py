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
OUTPAINT_PROMPT = "Remove red area and fill the scene"


class OutpaintResolutionRoutingTests(unittest.TestCase):
    """Outpaint-shaped requests against the real provider capability registry."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.providers = json.loads(PROVIDERS_PATH.read_text(encoding="utf-8"))

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
