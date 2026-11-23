import re
import unittest
from pathlib import Path


class MarkedOutputVisibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.css = (Path(__file__).resolve().parents[1] / "static/css/smart-canvas.css").read_text(encoding="utf-8")

    def _star_rule_492(self, selector):
        for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", self.css):
            selectors = re.sub(r"/\*.*?\*/", "", match[1], flags=re.S).strip().split(",")
            if selector not in [value.strip() for value in selectors]:
                continue
            return {key.strip(): value.strip() for item in match[2].split(";") if ":" in item
                    for key, value in [item.split(":", 1)]}
        self.fail(f"Missing CSS state selector: {selector}")

    def test_marked_outputs_remain_visible_and_actionable_without_hover(self):
        for selector in (".image-wrap .image-mark.image-marked", ".thumb-item .image-mark.image-marked"):
            with self.subTest(selector=selector):
                rule = self._star_rule_492(selector)
                self.assertEqual(rule.get("opacity"), "1")
                self.assertEqual(rule.get("pointer-events"), "auto")
                self.assertEqual(rule.get("transform"), "translateY(0)")

    def test_unmarked_controls_keep_the_existing_hover_policy(self):
        rule = self._star_rule_492(".image-wrap .mini-x")
        self.assertEqual(rule.get("opacity"), "0")
        self.assertEqual(rule.get("pointer-events"), "none")
        rule = self._star_rule_492(".image-wrap:hover .mini-x")
        self.assertEqual(rule.get("opacity"), "1")
        self.assertEqual(rule.get("pointer-events"), "auto")

    def test_dragging_still_hides_image_controls(self):
        rule = self._star_rule_492(".image-node.dragging .mini-x")
        self.assertEqual(rule.get("opacity"), "0 !important")
        self.assertEqual(rule.get("pointer-events"), "none")


if __name__ == "__main__":
    unittest.main()
