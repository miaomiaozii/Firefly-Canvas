"""#182：runner 注入页面的 JS 表达式不得引用裸全局 `nodes`。

#162（commit 2d3381b）把 `static/js/smart-canvas.js` 顶层的 `let nodes = []` 移进
`window.canvasState`（`static/js/canvas-state.js:3`）。页面里从此没有名为 `nodes` 的
全局绑定，但 `tests/long_content_native_browser.py:277` 注入的表达式仍在读裸 `nodes`，
于是 `long-content-evidence` 在页面里抛 `ReferenceError: nodes is not defined`。

这条守卫把「runner 注入的表达式」当作契约面来扫：它只依赖 AST，不起浏览器。
"""
from __future__ import annotations

import ast
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# NativeBrowser 只有这两个入口把 Python 字符串当作 JS 送进页面（tests/native_browser.py:416/447）。
INJECTED_JS_METHODS = {"evaluate", "wait_for"}

# 裸标识符 `nodes`：前面不是 `.`（放过 canvasState.nodes / canvas.nodes），
# 后面不是 `:`（放过对象字面量键 nodes: [...]）。
BARE_NODES = re.compile(r"(?<![.\w$])nodes\b(?!:)")

# 表达式里的字符串字面量不参与标识符判断。
STRING_LITERALS = re.compile(r"'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"|`(?:\\.|[^`\\])*`")


def injected_js_expressions(path: Path) -> list[tuple[int, str]]:
    """返回 (行号, 文本) 列表：该文件里所有送进页面的 JS 字符串。

    f-string 的非占位符片段按原样保留，占位符位置替换成 NUL，避免把 Python 侧
    变量名误判成页面标识符。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[int, str]] = []

    class Visitor(ast.NodeVisitor):
        def visit_Call(self, node):  # noqa: N802
            function = node.func
            name = getattr(function, "attr", None) or getattr(function, "id", None)
            if name in INJECTED_JS_METHODS:
                for argument in node.args:
                    if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                        found.append((argument.lineno, argument.value))
                    elif isinstance(argument, ast.JoinedStr):
                        text = ""
                        for part in argument.values:
                            if isinstance(part, ast.Constant) and isinstance(part.value, str):
                                text += part.value
                            else:
                                text += "\x00"
                        found.append((argument.lineno, text))
            self.generic_visit(node)

    Visitor().visit(tree)
    return found


def bare_nodes_offenders(text: str) -> list[str]:
    return BARE_NODES.findall(STRING_LITERALS.sub(" ", text))


def all_offenders() -> list[str]:
    offenders: list[str] = []
    for path in sorted((ROOT / "tests").glob("*.py")):
        for lineno, expression in injected_js_expressions(path):
            if bare_nodes_offenders(expression):
                offenders.append(f"tests/{path.name}:{lineno} {expression[:120]}")
    return offenders


class InjectedJsScopeGuardTests(unittest.TestCase):
    def test_scanner_detects_a_bare_nodes_reference(self) -> None:
        # 守卫自检：扫描器本身必须能抓到裸 nodes，否则下面的断言是假绿。
        self.assertTrue(bare_nodes_offenders("inputPromptTextFor(nodes.find(n => n.id === 'x'))"))
        self.assertEqual([], bare_nodes_offenders("canvasState.nodes.find(n => n.id === 'x')"))
        self.assertEqual([], bare_nodes_offenders("({nodes: []}).nodes.length"))

    def test_scanner_actually_reads_injected_expressions(self) -> None:
        # 守卫自检：扫描器必须真的解析到注入点，不能因为 API 改名而空跑。
        self.assertTrue(
            injected_js_expressions(ROOT / "tests" / "long_content_native_browser.py"),
            "未能从 long_content_native_browser.py 解析出任何注入表达式",
        )

    def test_no_injected_expression_references_bare_nodes(self) -> None:
        offenders = all_offenders()
        self.assertEqual(
            [],
            offenders,
            "runner 注入的表达式引用了页面里不存在的裸全局 nodes（#162 起应改用 canvasState.nodes）：\n"
            + "\n".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()
