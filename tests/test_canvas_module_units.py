"""#194 画布模块真单元测试的 Python 入口（runner）。

本目录 `tests/canvas_module_units/` 给 #154 拆出的画布模块做**行为**单元测试：
`harness.mjs`（node:vm 假核心）按 static/smart-canvas.html 的 <script> 顺序把 classic
脚本加载进同一个全局词法作用域，桩掉浏览器全局（document/window/location/localStorage/
fetch/requestAnimationFrame/可控定时器），然后直接调用被测函数、断言具体期望值。

防的回归：现有「逐字钉源码文本」的契约测试（如 tests/test_project_image_generation_ui.py:393
的 `assertIn("<源码片段>", self.source)`）跟不上正常源码修改——本分支提交 099798f
就是被它逼出来的回归修复。行为测试调用真实代码：保持行为的正常重构应绿，改变行为的
改动应红。

为什么用 Python 包一层 Node：tools/quality_gate.py 目前没有任何 JS 单元测试步骤
（只有 javascript-syntax 语法门，见 tools/quality_gate.py:169 的 STEP_DEFINITIONS）。
按 #194 约束不动 quality_gate.py；这里先让 Python unittest 驱动 node 跑测试文件并
断言退出码 0。**建议的质量门接入点：javascript-syntax 步骤之后**新增
`"javascript-units": lambda python: [python, "-m", "unittest", "-b", "tests.test_canvas_module_units"]`
——它只需要 node + 静态文件，不需要浏览器/服务器，与语法门同级（fast/domain 都应跑）。
"""
from __future__ import annotations

import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UNIT_DIR = ROOT / "tests" / "canvas_module_units"
HARNESS = UNIT_DIR / "harness.mjs"
HTML = ROOT / "static" / "smart-canvas.html"


def _node_executable() -> str:
    """与 tests/test_canvas_frontend_sources.py 同口径：先取质量门受信 node，回退 PATH。"""
    from tools.quality_gate import trusted_node_executable

    trusted = trusted_node_executable()
    if Path(trusted).is_file():
        return trusted
    found = shutil.which("node")
    return found if found else trusted


def _node_test_files() -> list[Path]:
    """动态发现：tests/canvas_module_units/test-*.mjs 每个文件 = 一个 Python 测试。

    新增模块测试只加 .mjs 文件，不用改本 runner（新文件立刻进入
    `python -m unittest tests.test_canvas_module_units` 的覆盖面）。
    """
    return sorted(UNIT_DIR.glob("test-*.mjs"))


# is_canvas_module 只认 canvas-* 前缀 + 两个特例；outpaint-ratios.js 是画布扩图
# 纯逻辑模块（HTML 里的真实 <script src>，在 canvas-node-types.js 之前加载），
# 文件名不带 canvas- 前缀所以不在 is_canvas_module 口径内——但它同样是加载顺序
# 的参与者，harness 覆盖它，顺序对账必须包含它。
ORDERED_EXTRA_SCRIPTS = ("outpaint-ratios.js",)


def _html_canvas_scripts() -> list[str]:
    from tests.canvas_frontend_sources import is_canvas_module

    loaded = [
        src.split("?")[0]
        for src in re.findall(r'<script src="/static/js/([^"]+)"', HTML.read_text(encoding="utf-8"))
    ]
    return [name for name in loaded if is_canvas_module(name) or name in ORDERED_EXTRA_SCRIPTS]


def _harness_order() -> list[str]:
    """从 harness.mjs 解析 HTML_SCRIPT_ORDER 表（本测试套件维护的加载顺序）。"""
    text = HARNESS.read_text(encoding="utf-8")
    match = re.search(r"export const HTML_SCRIPT_ORDER = \[(.*?)\];", text, re.S)
    assert match, "harness.mjs 里找不到 HTML_SCRIPT_ORDER 声明"
    return re.findall(r"'([^']+)'", match.group(1))


class CanvasModuleUnitsTests(unittest.TestCase):
    def test_harness_order_matches_html(self):
        # 加载顺序错了会得到难查的 ReferenceError（#194 关键约束 3）。
        # i18n-core.js 由 i18n.js 运行时注入（HTML 里 i18n.js 先于 outpaint-ratios.js），
        # 故排在最前；其余（含 outpaint-ratios.js）必须与 HTML 脚本顺序逐字一致。
        expected = ["i18n-core.js"] + _html_canvas_scripts()
        self.assertEqual(_harness_order(), expected)

    def test_every_node_test_file_is_discovered(self):
        files = _node_test_files()
        self.assertTrue(
            files,
            f"tests/canvas_module_units/ 下没有任何 test-*.mjs（动态发现为空，覆盖为零）",
        )
        for path in files:
            self.assertGreater(path.stat().st_size, 0, f"{path.name} 是空文件")

    def test_node_is_available_for_the_js_suite(self):
        # 与 test_canvas_frontend_sources 同口径：node 不可用时必须显式红，不能假绿。
        node = _node_executable()
        self.assertTrue(Path(node).is_file(), f"node 不可用（{node}）：JS 单测必须真跑")


def _node_module_argument(path: Path) -> str:
    # 门的 sitecustomize guard 只允许 `node <tests/ 下的 .mjs/.js>`，且要求是
    # **相对路径**（guard 无法从绝对路径判断它落在隔离 workspace 内）。
    # 传 str(绝对路径) 会被拒：PermissionError: quality gate denied node arguments
    # （guard/sitecustomize.py:333）。subprocess 已经 cwd=ROOT，所以相对路径等价。
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def _make_node_case(filename: Path):
    def test(self):
        result = subprocess.run(
            [_node_executable(), _node_module_argument(filename)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        if result.returncode != 0:
            self.fail(
                "node 退出码 %d（%s）\n--- stdout ---\n%s\n--- stderr ---\n%s"
                % (result.returncode, filename.name, result.stdout, result.stderr)
            )

    return test


for _path in _node_test_files():
    _method_name = "test_units_" + _path.stem.removeprefix("test-").replace("-", "_")
    setattr(CanvasModuleUnitsTests, _method_name, _make_node_case(_path))


if __name__ == "__main__":
    unittest.main()
