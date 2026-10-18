r"""#194：把画布模块的 Node 单元测试接进 Python 测试套件。

为什么要有这个文件：`tests/canvas_module_units/*.mjs` 是真正的行为单测，
但它们只有被 `unittest discover` 发现才会跑，否则等于没写。#175 的教训是
「看起来绿但测的不是这份工作区的代码」，所以这里用 `Path(__file__)` 定位
仓库根，绝不硬编码 `D:\Firefly-Canvas`。

同时锁一条守卫：模块清单必须与 `static/smart-canvas.html` 里的 `<script>`
顺序一致 —— classic 脚本共享一个全局词法作用域，加载顺序错了会得到难查的
ReferenceError，而跨文件重复的顶层 let/const 会让整个后加载的文件静默解析失败。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UNIT_DIR = ROOT / "tests" / "canvas_module_units"
HTML = ROOT / "static" / "smart-canvas.html"


def _node_executable() -> str | None:
    return shutil.which("node")


class CanvasModuleUnitTests(unittest.TestCase):
    def test_node_unit_tests_pass(self):
        node = _node_executable()
        if node is None:
            self.skipTest("本机没有 node，跳过画布模块 Node 单测")
        targets = sorted(p.name for p in UNIT_DIR.glob("*_units.mjs"))
        self.assertTrue(targets, f"{UNIT_DIR} 下没有 *_units.mjs 测试文件")
        completed = subprocess.run(
            [node, "--test", "--test-reporter=tap", *targets],
            cwd=str(UNIT_DIR),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
        )
        if completed.returncode != 0:
            self.fail(
                "画布模块 Node 单测失败：\n"
                f"--- stdout ---\n{completed.stdout[-6000:]}\n"
                f"--- stderr ---\n{completed.stderr[-3000:]}"
            )
        # TAP 汇总行必须存在，否则说明测试根本没被发现（例如文件名不匹配）。
        self.assertIn("# pass ", completed.stdout)
        self.assertNotIn("not ok", completed.stdout)

    def test_harness_load_order_matches_html_script_order(self):
        # harness.mjs 里 CANVAS_SCRIPT_ORDER 必须与 HTML 的实际顺序一致。
        # 只比对其声明涉及的那些模块，避免与 HTML 的完整清单耦合。
        harness = (UNIT_DIR / "harness.mjs").read_text(encoding="utf-8")
        html = HTML.read_text(encoding="utf-8")
        declared = [
            name
            for name in _declared_module_names(harness)
        ]
        self.assertTrue(declared, "harness.mjs 没有声明模块顺序")
        html_order = [
            Path(line.split('src=', 1)[1].split('"')[1].split('?')[0]).name
            for line in html.splitlines()
            if '/static/js/' in line and '<script' in line
        ]
        html_order = [Path(p).name for p in html_order]
        indices = []
        for name in declared:
            self.assertIn(name, html_order, f"{name} 不在 smart-canvas.html 的 <script> 清单里")
            indices.append(html_order.index(name))
        self.assertEqual(
            indices,
            sorted(indices),
            "harness.mjs 声明的模块加载顺序与 HTML 不一致："
            f"{declared} vs {[html_order[i] for i in sorted(indices)]}",
        )


def _declared_module_names(harness_source: str) -> list[str]:
    """从 harness.mjs 里读出它自己声明的模块名（用于顺序守卫）。"""
    marker = "CANVAS_SCRIPT_ORDER"
    start = harness_source.find(marker)
    if start == -1:
        return []
    bracket = harness_source.find("[", start)
    end = harness_source.find("]", bracket)
    chunk = harness_source[bracket + 1 : end]
    return [part.strip().strip("'\"") for part in chunk.split(",") if part.strip()]


if __name__ == "__main__":
    unittest.main()
