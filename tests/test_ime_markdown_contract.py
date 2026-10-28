import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SMART = (ROOT / "static" / "js" / "smart-canvas.js").read_text(encoding="utf-8")
TUI = (ROOT / "static" / "js" / "canvas-agent-tui.js").read_text(encoding="utf-8")

# 注意：条件内正则只追到第一个 ")"，imeComposing(event) 的尾括号会落在 cond 外，
# 所以守卫形式匹配用前缀（"imeComposing("）而不是带尾括号的全串。
GUARD_FORMS = ("imeComposing(", "!event.isComposing && event.keyCode !== 229")


class ImeMarkdownContractTests(unittest.TestCase):
    def test_ime_helper_defined(self):
        self.assertRegex(
            SMART,
            r"function imeComposing\(event\)\{ return Boolean\(event && \(event\.isComposing \|\| event\.keyCode === 229\)\); \}",
        )

    def test_no_unguarded_text_enter_handlers(self):
        # 逐行检查（多行嵌套括号用正则截条件体会漏判，行级包含最稳）：
        # 含 event.key === 'Enter' 的行必须带守卫；按钮/列表「Enter 或空格 activate」
        # 语义（同行含 "' '"）不属于文本输入，放行。
        unguarded = []
        for index, line in enumerate(SMART.splitlines(), start=1):
            if "event.key === 'Enter'" not in line:
                continue
            if "' '" in line:
                continue
            if any(form in line for form in GUARD_FORMS):
                continue
            unguarded.append(index)
        self.assertEqual(unguarded, [])

    def test_other_files_enter_guarded(self):
        for name in ("canvas-agent-tui.js", "canvas-image-editor.js", "canvas-media-history.js"):
            text = (ROOT / "static" / "js" / name).read_text(encoding="utf-8")
            enter_lines = [ln for ln in text.splitlines() if "event.key === 'Enter'" in ln and "addEventListener" not in ln]
            self.assertTrue(enter_lines, f"no Enter handlers found in {name}")
            for line in enter_lines:
                ok = any(form in line for form in GUARD_FORMS)
                self.assertTrue(ok, f"{name}: unguarded Enter line: {line.strip()[:80]}")

    def test_markdown_fence_placeholder_flow(self):
        self.assertIn("mdBlocks.push", TUI)
        # 恢复必须发生在 \n→<br> 之后：restore 的 replace 位于函数末尾。
        restore = TUI.index("\\u0000MDB(\\d+)")
        br = TUI.index("html.replace(/\\n/g, '<br>')")
        self.assertGreater(restore, br)
        # 未闭合围栏兜底：(?:```|$)
        self.assertIn("(?:```|$)", TUI)


if __name__ == "__main__":
    unittest.main()
