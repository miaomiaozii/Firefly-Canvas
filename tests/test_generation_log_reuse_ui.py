import json
import subprocess
import sys
import unittest
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class _CanvasMarkupParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.elements = []

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


class GenerationHistoryDrawerUiTests(unittest.TestCase):
    def test_history_drawer_exposes_a_real_region_and_public_controls(self):
        parser = _CanvasMarkupParser()
        parser.feed((ROOT / "static/smart-canvas.html").read_text(encoding="utf-8"))
        by_id = {attrs.get("id"): (tag, attrs) for tag, attrs in parser.elements if attrs.get("id")}
        self.assertEqual(by_id["smartLogModal"][0], "div")
        self.assertIn("history-drawer", by_id["smartLogModal"][1].get("class", ""))
        self.assertEqual(by_id["smartLogModal"][1].get("role"), "region")
        self.assertIn("smartLogList", by_id)
        self.assertIn("smartHistoryCollapse", by_id)

    def test_history_drawer_behavior_is_exercised_by_the_real_browser_runner(self):
        runner = ROOT / "tests/history_drawer_browser.py"
        self.assertTrue(runner.is_file())
        completed = subprocess.run(
            [sys.executable, str(runner)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=90,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout or completed.stderr)
        self.assertTrue(json.loads(completed.stdout)["pass"])


if __name__ == "__main__":
    unittest.main()
