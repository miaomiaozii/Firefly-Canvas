"""#336: 滚轮 zoom 灵敏度常数锁定（源契约测试，防无声回弹）。"""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "static" / "js" / "smart-canvas.js"


class WheelZoomContract(unittest.TestCase):
    def setUp(self):
        self.src = SRC.read_text(encoding="utf-8")

    def test_sensitivity_pinned_at_00005(self):
        # #336：0.0008→0.0005（每格 1.083→1.051）
        self.assertIn("const sensitivity = 0.0005;", self.src)

    def test_semantics_shape_unchanged(self):
        # #328 裁决面：deltaMode 单位换算与 mac 乘数语义不得被本票改动
        m = re.search(
            r"function canvasWheelZoomFactor\(event, pageSize\)\{(.*?)\n\}", self.src, re.S)
        self.assertIsNotNone(m, "canvasWheelZoomFactor 必须存在")
        body = m.group(1)
        self.assertIn("event.deltaMode === 1 ? 40", body)
        self.assertIn("macMultiplier", body)
        self.assertIn("Math.exp(-event.deltaY * unit * sensitivity * macMultiplier)", body)


if __name__ == "__main__":
    unittest.main()
