"""画布前端拆分守卫（#154）。

`tests/canvas_frontend_sources.py::CANVAS_SCRIPTS` 必须与 `static/smart-canvas.html` 里
画布相关 `<script src>` 的顺序一致，否则按源码文本断言的测试会漏掉刚拆出去的模块。
"""
from __future__ import annotations

import re
import unittest

from tests.canvas_frontend_sources import CANVAS_SCRIPTS, ROOT

CANVAS_OWNED = ("canvas-", "smart-canvas.js", "project-canvas-mutations.js")


def _html_canvas_scripts() -> list[str]:
    html = (ROOT / "static" / "smart-canvas.html").read_text(encoding="utf-8")
    loaded = [src.split("?")[0] for src in re.findall(r'<script src="/static/js/([^"]+)"', html)]
    return [name for name in loaded if name.startswith("canvas-") or name in ("smart-canvas.js", "project-canvas-mutations.js")]


class CanvasFrontendSourceListTests(unittest.TestCase):
    def test_canvas_scripts_match_html_load_order(self):
        self.assertEqual(_html_canvas_scripts(), list(CANVAS_SCRIPTS))

    def test_every_listed_canvas_script_exists(self):
        for name in CANVAS_SCRIPTS:
            self.assertTrue((ROOT / "static" / "js" / name).is_file(), f"missing static/js/{name}")

    def test_smart_canvas_is_the_last_canvas_script_loaded(self):
        # smart-canvas.js 依赖前面模块的全局绑定，必须最后加载。
        self.assertEqual(CANVAS_SCRIPTS[-1], "smart-canvas.js")


if __name__ == "__main__":
    unittest.main()
