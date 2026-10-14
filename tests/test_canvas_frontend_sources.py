"""画布前端拆分守卫（#154）。

`tests/canvas_frontend_sources.py::CANVAS_SCRIPTS` 必须与 `static/smart-canvas.html` 里
画布相关 `<script src>` 的顺序一致，否则按源码文本断言的测试会漏掉刚拆出去的模块。
"""
from __future__ import annotations

import re
import shutil
import subprocess
import unittest

from tests.canvas_frontend_sources import CANVAS_SCRIPTS, ROOT, is_canvas_module


def _html_canvas_scripts() -> list[str]:
    html = (ROOT / "static" / "smart-canvas.html").read_text(encoding="utf-8")
    loaded = [src.split("?")[0] for src in re.findall(r'<script src="/static/js/([^"]+)"', html)]
    return [name for name in loaded if is_canvas_module(name)]


class CanvasFrontendSourceListTests(unittest.TestCase):
    def test_canvas_scripts_match_html_load_order(self):
        self.assertEqual(_html_canvas_scripts(), list(CANVAS_SCRIPTS))

    def test_every_listed_canvas_script_exists(self):
        for name in CANVAS_SCRIPTS:
            self.assertTrue((ROOT / "static" / "js" / name).is_file(), f"missing static/js/{name}")

    def test_smart_canvas_is_the_last_canvas_script_loaded(self):
        # smart-canvas.js 依赖前面模块的全局绑定，必须最后加载。
        self.assertEqual(CANVAS_SCRIPTS[-1], "smart-canvas.js")

    def test_every_canvas_module_passes_node_syntax_check(self):
        # 质量门的 javascript-syntax 步骤只查 smart-canvas.js；拆出的模块在此补上。
        node = shutil.which("node")
        if node is None:
            self.skipTest("node 不可用")
        for name in CANVAS_SCRIPTS:
            result = subprocess.run(
                [node, "--check", str(ROOT / "static" / "js" / name)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, f"static/js/{name}: {result.stderr.strip()}")

    def test_agent_node_path_literals_match_backend_prefix(self):
        # #170：#162 把裸全局 nodes 批量改成 canvasState.nodes 时，顺手污染了模板字符串里的
        # 路径段 agent-nodes/，前端全部 Agent 节点端点因此 404。路径段必须跟后端前缀一致。
        backend = (ROOT / "app" / "routers" / "api_agent_nodes.py").read_text(encoding="utf-8")
        match = re.search(r'prefix="(/api/projects/\{project_id\}/[^"]+)"', backend)
        self.assertIsNotNone(match, "未能从 api_agent_nodes.py 解析出路由前缀")
        expected = match.group(1).split("/")[-1]
        source = (ROOT / "static" / "js" / "canvas-agent-tui.js").read_text(encoding="utf-8")
        segments = {
            m.group(1)
            for m in re.finditer(r'/api/projects/\$\{[^}]*\}/([^/"\'`$\{]+)', source)
        }
        self.assertTrue(segments, "canvas-agent-tui.js 里找不到 Agent 节点端点字面量")
        self.assertEqual(segments, {expected}, f"前端路径段 {segments} 与后端前缀 {expected} 不一致")

    def test_no_state_container_rename_inside_path_literals(self):
        # 同一批盲替换的通用守卫：状态容器名不得出现在 URL 字面量或路径注释里。
        offenders: list[str] = []
        for name in CANVAS_SCRIPTS:
            source = (ROOT / "static" / "js" / name).read_text(encoding="utf-8")
            for lineno, line in enumerate(source.splitlines(), start=1):
                for path in re.findall(r"/api/[A-Za-z0-9/_${}.-]*", line):
                    if "canvasState." in path:
                        offenders.append(f"static/js/{name}:{lineno} {path}")
        self.assertEqual(offenders, [], "路径字面量被状态容器改名污染：\n" + "\n".join(offenders))


if __name__ == "__main__":
    unittest.main()
