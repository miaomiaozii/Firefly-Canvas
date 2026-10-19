"""#216: guard the stale-409 arm sizes of the #189 gate runners against product constant drift.

The #189 gate runners arm N stale-base 409 rewrites to force a canvas-save
conflict. The meaning of N is DERIVED from two product constants in
static/js/smart-canvas.js:

  * saveProjectCanvasUntilSettled maxAttempts=4  (smart-canvas.js:6619)
  * flushProjectCanvasMutations maxConflictRetries=2  (smart-canvas.js:6662)

One settle absorbs at most maxAttempts * (1 + maxConflictRetries) = 4 * 3 = 12
armed 409s (each saveCanvas attempt's mutation flush rebases up to
maxConflictRetries times: 1 initial PATCH + 2 rebases = 3 save PATCHes).

  * tests/issue189gates_pending_resume_native_browser.py ARM_STALE=15 must stay
    > 12, or the armed 409s are fully absorbed, the gate never fails, the
    pending path is never exercised, and the repro/fixed gates time out waiting
    for the generation result (the #216 false red).
  * tests/issue189_gate_flush_native_browser.py INJECT_STALE=3 must stay >=
    1 + maxConflictRetries (one flush's rebase budget exhausted, so the gate's
    first save attempt reports conflictLimitReached) and < 12 (the whole settle
    still recovers, so the #189 pending path — not the pre-#189 stop toast —
    is exercised).

If either product constant changes, this test fails and the runner arm sizes
must be re-derived (the derivation is documented in both runners' comments).
"""

from __future__ import annotations

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PRODUCT_JS = ROOT / "static" / "js" / "smart-canvas.js"
GATES_RUNNER = ROOT / "tests" / "issue189gates_pending_resume_native_browser.py"
SINGULAR_RUNNER = ROOT / "tests" / "issue189_gate_flush_native_browser.py"

# Anchored to the unique product locations (see the module docstring).
MAX_ATTEMPTS_RE = r"saveProjectCanvasUntilSettled\(\{maxAttempts=(\d+)\}=\{\}\)\{"
MAX_CONFLICT_RETRIES_RE = r"maxConflictRetries:(\d+)"


def _extract(pattern: str, text: str, label: str) -> int:
    match = re.search(pattern, text)
    if match is None:
        raise AssertionError(f"{label}: pattern {pattern!r} not found — the product code drifted, re-derive the arm sizes")
    return int(match.group(1))


class ArmStaleGuardTests(unittest.TestCase):
    def setUp(self):
        self.product = PRODUCT_JS.read_text(encoding="utf-8")
        self.gates = GATES_RUNNER.read_text(encoding="utf-8")
        self.singular = SINGULAR_RUNNER.read_text(encoding="utf-8")
        self.max_attempts = _extract(MAX_ATTEMPTS_RE, self.product, "saveProjectCanvasUntilSettled maxAttempts")
        self.max_conflict_retries = _extract(MAX_CONFLICT_RETRIES_RE, self.product, "flush maxConflictRetries")

    def settle_budget(self) -> int:
        return self.max_attempts * (1 + self.max_conflict_retries)

    def test_product_constants_current(self):
        # Pinned to the values the runner comments derive from; if these change,
        # the ARM_STALE / INJECT_STALE derivations below fail and must be updated.
        self.assertEqual(self.max_attempts, 4)
        self.assertEqual(self.max_conflict_retries, 2)

    def test_gates_arm_stale_outlives_settle_budget(self):
        arm_stale = _extract(r"(?m)^ARM_STALE = (\d+)", self.gates, "gates runner ARM_STALE")
        self.assertEqual(arm_stale, 15)
        self.assertGreater(
            arm_stale, self.settle_budget(),
            f"ARM_STALE={arm_stale} no longer outlives the settle budget "
            f"{self.max_attempts}*(1+{self.max_conflict_retries})={self.settle_budget()}: "
            "the armed 409s would be fully absorbed and the gate would never fail",
        )

    def test_singular_inject_stale_exhausts_one_flush_but_not_the_settle(self):
        inject_stale = _extract(r"(?m)^INJECT_STALE = (\d+)", self.singular, "singular runner INJECT_STALE")
        self.assertEqual(inject_stale, 3)
        self.assertGreaterEqual(
            inject_stale, 1 + self.max_conflict_retries,
            f"INJECT_STALE={inject_stale} no longer exhausts one flush's rebase budget "
            f"(1+{self.max_conflict_retries}): the gate flush would not report conflictLimitReached",
        )
        self.assertLess(
            inject_stale, self.settle_budget(),
            f"INJECT_STALE={inject_stale} no longer stays below the settle budget "
            f"{self.max_attempts}*(1+{self.max_conflict_retries})={self.settle_budget()}: "
            "the settle would not recover and the #189 pending path would not be exercised",
        )


if __name__ == "__main__":
    unittest.main()
