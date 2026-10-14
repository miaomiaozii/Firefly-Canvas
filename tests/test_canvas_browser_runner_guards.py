"""Issue #167: a canvas browser runner that asserts nothing must not exit 0.

The three canvas browser runners post a JSON report from the browser client and
then decide their exit code in Python. The report shapes differ per runner
(``ok`` vs ``pass``), so the pass/fail decision lives in one shared helper.
"""

from __future__ import annotations

import ast
import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tests"))

import canvas_browser_runner_support as runner_support  # noqa: E402

BROWSER_RUNNERS = [
    REPO_ROOT / "tests" / "canvas_boundaries_browser.py",
    REPO_ROOT / "tests" / "canvas_interactions_browser.py",
    REPO_ROOT / "tests" / "video_failure_browser.py",
]

# tests/canvas_boundaries_browser_client.js payload shape.
BOUNDARIES_OK = {
    "assertions": [{"name": f"a{i}", "ok": True, "detail": ""} for i in range(13)],
    "total": 13,
    "ok": True,
}

# tests/video_failure_browser_client.js / canvas_interactions_browser_client.js shape.
VIDEO_OK = {
    "pass": True,
    "assertions": [{"name": f"a{i}", "pass": True, "detail": ""} for i in range(8)],
    "errors": [],
}


def _finish(label: str, payloads, **kwargs) -> tuple[int, str]:
    with redirect_stdout(io.StringIO()) as output:
        code = runner_support.finish_run(label, payloads, **kwargs)
    return code, output.getvalue()


class ZeroAssertionGuardTests(unittest.TestCase):
    def test_report_without_assertions_exits_nonzero(self) -> None:
        code, text = _finish("demo browser checks", [{"assertions": [], "total": 0, "ok": True}])
        self.assertEqual(1, code)
        self.assertIn("0 assertions", text)
        self.assertTrue(text.startswith("FAIL"), text)

    def test_report_without_assertions_reports_the_reason(self) -> None:
        code, text = _finish(
            "demo browser checks",
            [{"assertions": [], "total": 0, "ok": True, "reason": "canvas state container is missing"}],
        )
        self.assertEqual(1, code)
        self.assertIn("canvas state container is missing", text)

    def test_default_reason_names_the_browser_client(self) -> None:
        _, text = _finish("demo browser checks", [{"assertions": [], "total": 0, "ok": True}])
        self.assertIn("browser client", text)

    def test_report_path_is_printed_for_locating_the_run(self) -> None:
        report_path = Path("/tmp/demo/report.json")
        _, text = _finish(
            "demo browser checks",
            [{"assertions": [], "total": 0, "ok": True}],
            report_paths=[report_path],
        )
        self.assertIn(str(report_path), text)

    def test_no_results_at_all_exits_nonzero(self) -> None:
        code, text = _finish("demo browser checks", [])
        self.assertEqual(1, code)
        self.assertIn("0 assertions", text)

    def test_boundaries_report_with_assertions_exits_zero(self) -> None:
        code, text = _finish("demo browser checks", [BOUNDARIES_OK])
        self.assertEqual(0, code)
        self.assertIn("13 assertions", text)

    def test_video_report_with_assertions_exits_zero(self) -> None:
        code, text = _finish("demo browser checks", [VIDEO_OK])
        self.assertEqual(0, code)
        self.assertIn("8 assertions", text)


class FailureReportingTests(unittest.TestCase):
    def test_failed_assertion_exits_nonzero(self) -> None:
        payload = {
            "assertions": [
                {"name": "alpha", "ok": True, "detail": ""},
                {"name": "beta", "ok": False, "detail": "len=0"},
            ],
            "total": 2,
            "ok": False,
        }
        code, text = _finish("demo browser checks", [payload])
        self.assertEqual(1, code)
        self.assertIn("beta", text)
        self.assertIn("len=0", text)

    def test_failed_assertion_using_pass_key_exits_nonzero(self) -> None:
        payload = {
            "pass": False,
            "assertions": [{"name": "layout", "pass": False, "actual": "x=10"}],
            "errors": [],
        }
        code, text = _finish("demo browser checks", [payload])
        self.assertEqual(1, code)
        self.assertIn("layout", text)
        self.assertIn("x=10", text)

    def test_client_errors_exit_nonzero(self) -> None:
        payload = {"pass": False, "assertions": [{"name": "alpha", "pass": True}], "errors": ["saveCanvas: boom"]}
        code, text = _finish("demo browser checks", [payload])
        self.assertEqual(1, code)
        self.assertIn("saveCanvas: boom", text)

    def test_report_level_failure_without_details_exits_nonzero(self) -> None:
        payload = {"pass": False, "assertions": [{"name": "alpha", "pass": True}], "errors": []}
        code, _ = _finish("demo browser checks", [payload])
        self.assertEqual(1, code)

    def test_only_the_broken_viewport_fails_the_run(self) -> None:
        code, text = _finish("demo browser checks", [VIDEO_OK, {"assertions": [], "total": 0, "pass": True}])
        self.assertEqual(1, code)
        self.assertIn("0 assertions", text)


class RunnerWiringTests(unittest.TestCase):
    def test_every_browser_runner_uses_the_shared_guard(self) -> None:
        for runner in BROWSER_RUNNERS:
            with self.subTest(runner=runner.name):
                source = runner.read_text(encoding="utf-8")
                self.assertIn("canvas_browser_runner_support", source)
                self.assertIn("finish_run(", source)

    def test_no_browser_runner_hardcodes_a_zero_exit(self) -> None:
        for runner in BROWSER_RUNNERS:
            with self.subTest(runner=runner.name):
                tree = ast.parse(runner.read_text(encoding="utf-8"), filename=str(runner))
                main = next(
                    node
                    for node in tree.body
                    if isinstance(node, ast.FunctionDef) and node.name == "main"
                )
                hardcoded = []
                for node in ast.walk(main):
                    if isinstance(node, ast.Return) and isinstance(node.value, ast.Constant):
                        hardcoded.append(ast.unparse(node))
                    if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
                        args = node.exc.args
                        if len(args) == 1 and isinstance(args[0], ast.Constant):
                            hardcoded.append(ast.unparse(node))
                self.assertEqual([], hardcoded)


if __name__ == "__main__":
    unittest.main()
