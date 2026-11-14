"""#346：`hidden` 语义全局规则的纯文本守卫（轻、不带浏览器）。

不挂门步骤，靠 unit-contract 分片（trial/release）与全量 pytest 跑到。为什么不进 fast：
fast 步骤在 fixture 工作区里跑，而 `tests/test_quality_gate.py` 约 30 个用例各自往 fixture 写
`tests/test_product_surface_behavior.py` 桩——往那个步骤里加第二个模块就得逐处补桩，代价比收益大。
页面级计算样式断言在 `tests/canvas_hidden_visibility_browser.py`（浏览器证据，接 domain/trial/release）。
这条守的是另外两件会悄悄烂掉的事：
1. `static/css/studio-foundation.css` 里那条全局规则被删了——所有 `hidden` 又恢复成「隐藏不掉」。
2. 有人又开始逐组件打 `.x[hidden]{display:none}` 补丁——#346 之前就是这么活的，补丁追不上新组件。

规则为什么必须待在共享样式表：三个页面（画布 / 项目列表 / 全局设置）都只 link
`studio-foundation.css` + 各自那一页的样式表，写在任何单页样式表里都会漏掉另外两页。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from tests.canvas_frontend_sources import ROOT

CSS_DIR = ROOT / "static" / "css"
SHARED_STYLESHEET = CSS_DIR / "studio-foundation.css"
PAGES = ("static/smart-canvas.html", "static/project-list.html", "static/global-settings.html")

GLOBAL_HIDDEN_RULE = re.compile(r"\[hidden\]\s*\{\s*display:\s*none\s*!important\s*;?\s*\}")
COMPONENT_HIDDEN_PATCH = re.compile(r"[^\r\n{}]*\[hidden\][^\r\n{}]*\{[^}]*\}", re.S)
CSS_COMMENT = re.compile(r"/\*.*?\*/", re.S)


def css_without_comments(name: Path) -> str:
    return CSS_COMMENT.sub("", name.read_text(encoding="utf-8"))


class HiddenAttributeSemanticsTests(unittest.TestCase):
    def test_shared_stylesheet_carries_the_global_hidden_rule(self):
        source = SHARED_STYLESHEET.read_text(encoding="utf-8")
        self.assertRegex(
            source,
            GLOBAL_HIDDEN_RULE,
            "studio-foundation.css 里缺 `[hidden]{display:none !important}`（#346）。"
            "删掉它 = 画布页 13 个带 hidden 的元素重新显示，#337 的搜索面板又关不掉。",
        )

    def test_global_hidden_rule_is_declared_exactly_once(self):
        # 多条 `[hidden]` 全局规则不会错，但会让「谁拥有这条语义」重新变成谜——#346 前
        # 它就是散在 14 条补丁里的。
        for stylesheet in sorted(CSS_DIR.glob("*.css")):
            global_rules = len(GLOBAL_HIDDEN_RULE.findall(css_without_comments(stylesheet)))
            expected = 1 if stylesheet == SHARED_STYLESHEET else 0
            self.assertEqual(
                global_rules,
                expected,
                f"static/css/{stylesheet.name} 里全局 [hidden] 规则应有 {expected} 条，实际 {global_rules} 条",
            )

    def test_no_component_repatches_hidden_visibility(self):
        offenders: list[str] = []
        for stylesheet in sorted(CSS_DIR.glob("*.css")):
            source = css_without_comments(stylesheet)
            for match in COMPONENT_HIDDEN_PATCH.finditer(source):
                selector = " ".join(match.group(0).split("{")[0].split())
                if selector == "[hidden]":
                    # 全局规则本身——它就是被允许的那一条。
                    continue
                lineno = source.count("\n", 0, match.start()) + 1
                offenders.append(
                    f"static/css/{stylesheet.name}:{lineno} "
                    + " ".join(match.group(0).split())[:120]
                )
        self.assertEqual(
            offenders,
            [],
            "又开始逐组件打 `[hidden]{display:none}` 补丁了（#346 已用全局规则统一接住）：\n"
            + "\n".join(offenders),
        )

    def test_every_page_loads_the_shared_stylesheet(self):
        for page in PAGES:
            html = (ROOT / page).read_text(encoding="utf-8")
            self.assertIn(
                'href="/static/css/studio-foundation.css',
                html,
                f"{page} 没加载 studio-foundation.css，全局 [hidden] 规则对它无效",
            )


if __name__ == "__main__":
    unittest.main()
