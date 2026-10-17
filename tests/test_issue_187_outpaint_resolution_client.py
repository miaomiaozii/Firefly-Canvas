"""#187 regression (client-side): outpaint generation must carry the user's resolution.

Static-asserts the post-fix contract of
``static/js/canvas-generation-tasks.js`` (``runApiGeneration``) and
``static/js/canvas-image-editor.js`` (``withOutpaintDisplaySettings`` /
``applyOutpaintSizeToSmartParams``):

- the old gate ``const usesExplicitResolution = !outpaintSize &&
  apiImageUsesExplicitResolution(runSettings)`` sent ``resolution: ''`` for every
  outpaint submission (the #187 bug: panel shows 4K, provider falls back to 1K).
- the fix must drop the ``!outpaintSize`` gate so the user-selected resolution is
  forwarded on outpaint runs too.
"""

from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
GENERATION_TASKS = ROOT / "static" / "js" / "canvas-generation-tasks.js"
IMAGE_EDITOR = ROOT / "static" / "js" / "canvas-image-editor.js"


class OutpaintResolutionClientContractTests(unittest.TestCase):
    def read(self, path: pathlib.Path) -> str:
        return path.read_text(encoding="utf-8")

    def test_outpaint_no_longer_gates_out_resolution(self) -> None:
        source = self.read(GENERATION_TASKS)
        # The buggy expression must be gone in full (gate on !outpaintSize).
        self.assertNotIn(
            "const usesExplicitResolution = !outpaintSize && apiImageUsesExplicitResolution(runSettings)",
            source,
            "runApiGeneration must not gate resolution behind !outpaintSize (#187)",
        )
        # Resolution must be forwarded whenever the settings declare an explicit
        # one — including outpaint runs.
        self.assertIn(
            "const usesExplicitResolution = apiImageUsesExplicitResolution(runSettings)",
            source,
        )

    def test_outpaint_display_settings_keep_user_resolution(self) -> None:
        source = self.read(IMAGE_EDITOR)
        # The parameter panel keeps the user's preset (1k/2k/4k) during outpaint —
        # that promise must match the outgoing request after the fix.
        self.assertIn("function withOutpaintDisplaySettings", source)
        self.assertIn("keepCurrentResolution", source)


if __name__ == "__main__":
    unittest.main()
