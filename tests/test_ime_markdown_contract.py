import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SMART = (ROOT / "static" / "js" / "smart-canvas.js").read_text(encoding="utf-8")
TUI = (ROOT / "static" / "js" / "canvas-agent-tui.js").read_text(encoding="utf-8")

GUARD_FORMS = ("imeComposing(event)", "!event.isComposing && event.keyCode !== 229")


class ImeMarkdownContractTests(unittest.TestCase):
    def test_ime_helper_defined(self):
        self.assertRegex(
            SMART,
            r"function imeComposing\(event\)\{ return Boolean\(event && \(event\.isComposing \|\| event\.keyCode === 229\)\); \}",
        )

    def test_no_unguarded_text_enter_handlers(self):
        # 文本输入框的 Enter 分支必须带 IME 守卫；
        # "Enter 或空格 activate" 的按钮/列表语义（条件里含 "' '"）不属于文本输入。
        unguarded = []
        for match in re.finditer(r"if\(event\.key === 'Enter'([^)]*)\)", SMART):
            cond = match.group(1)
            if any(form in cond for form in GUARD_FORMS):
                continue
            if "' '" in cond:
                continue
            unguarded.append(SMART.count("\n", 0, match.start()) + 1)
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
