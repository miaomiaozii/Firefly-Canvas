"""画布前端拆分守卫（#154）。

`tests/canvas_frontend_sources.py::CANVAS_SCRIPTS` 必须与 `static/smart-canvas.html` 里
画布相关 `<script src>` 的顺序一致，否则按源码文本断言的测试会漏掉刚拆出去的模块。
"""
from __future__ import annotations

import re
import shutil
import subprocess
import unittest
from pathlib import Path

from tests.canvas_frontend_sources import CANVAS_SCRIPTS, ROOT, is_canvas_module
from tools.quality_gate import trusted_node_executable


def _node_for_syntax_check() -> str:
    """#167：语法检查用的 node 先取质量门的受信路径，而不是 PATH 上的 node。

    门会验证 PATH 里的 node 不是伪造的（test_javascript_syntax_uses_trusted_node_instead_of_path），
    所以这里不能先 `shutil.which("node")`；也不能因为找不到 node 就静默跳过。
    """
    trusted = trusted_node_executable()
    if Path(trusted).is_file():
        return trusted
    found = shutil.which("node")
    return found if found else trusted


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

    def test_navigation_index_module_table_covers_every_canvas_script(self):
        # #173 下沉时实际发生了文档漂移：navigation-index.md 的模块表里没有
        # canvas-core.js，正文还写「跨模块可变状态集中在 canvas-state.js」。
        # AGENTS.md 把这份索引当入口用，漏登记会让人按错的图景改代码。
        doc = (ROOT / "docs" / "agents" / "navigation-index.md").read_text(encoding="utf-8")
        listed = re.findall(r"^\|\s*`([A-Za-z0-9_-]+\.js)`", doc, re.M)
        self.assertEqual(sorted(set(CANVAS_SCRIPTS)), sorted(set(listed)))

    def test_every_canvas_module_passes_node_syntax_check(self):
        # #167：这条测试就是质量门的 javascript-syntax 步骤（fast/domain 都跑），
        # 所以不能因为 PATH 上没 node 就静默跳过，否则门会变成假绿。
        node = _node_for_syntax_check()
        self.assertTrue(
            Path(node).is_file(),
            f"node 不可用（{node}）：javascript-syntax 门必须真跑，不能静默跳过",
        )
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
