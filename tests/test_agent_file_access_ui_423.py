"""Static contracts for #423 Codex read-only and #445 guarded Pi file access."""
from html.parser import HTMLParser
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "static/global-settings.html"
TRANSLATIONS = ROOT / "static/js/i18n/studio.js"


class SectionParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.depth = 0
        self.sections = 0
        self.elements = []
        self.text = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "section" and attrs.get("aria-labelledby") == "agentFileAccessTitle":
            self.sections += 1
            self.depth = 1
        elif self.depth:
            self.depth += 1
        if self.depth:
            self.elements.append((tag, attrs))

    def handle_endtag(self, tag):
        if self.depth:
            self.depth -= 1

    def handle_data(self, data):
        if self.depth and data.strip():
            self.text.append(data.strip())


class AgentFileAccessUiTests(unittest.TestCase):
    def test_section_is_static_and_discloses_each_agent(self):
        parser = SectionParser()
        parser.feed(HTML.read_text(encoding="utf-8"))
        self.assertEqual(parser.sections, 1)
        self.assertEqual(parser.depth, 0)
        self.assertEqual(parser.text, [
            "Agent 文件访问", "Codex",
            "当前：只读，Agent 不能修改文件（包括自己的工作目录）。写入工作目录：未验证，暂不开放。",
            "Pi",
            "工作目录固定为当前节点的 agent_nodes/{node_id}/sessions；受限 read/write/edit 仅可访问该目录，目录外访问被拒绝。模型不能写入 system-prompt.txt 或该目录根层的原生会话 .jsonl；未开放 bash、PowerShell 或 codemode。",
            "这是工具 I/O 守卫，不是操作系统沙盒，不能防止其他进程并发替换重解析点（reparse points）。",
        ])
        for tag, attrs in parser.elements:
            self.assertNotIn(tag, {"button", "input", "select", "textarea", "a", "form"})
            self.assertFalse(any(key.startswith("on") for key in attrs))
            if tag == "h2":
                self.assertEqual(attrs.get("tabindex"), "-1")
            else:
                self.assertNotIn("tabindex", attrs)
            self.assertNotIn(attrs.get("role"), {"button", "switch", "checkbox", "radio"})
        keys = [attrs["data-i18n"] for _, attrs in parser.elements if "data-i18n" in attrs]
        self.assertEqual(keys, ["globalSettings.agentFileAccess", "globalSettings.codexFileAccess", "globalSettings.piFileAccess", "globalSettings.piFileAccessBoundary"])

    def test_theme_save_keeps_server_preferences_in_client_state(self):
        script = (ROOT / "static/js/global-settings.js").read_text(encoding="utf-8")
        self.assertIn("const saved = await requestJson('/api/global-settings/preferences'", script)
        self.assertIn("state.preferences = saved.preferences;", script)

    def test_translation_does_not_override_disclosure(self):
        translations = TRANSLATIONS.read_text(encoding="utf-8")
        expected = {
            "agentFileAccess": ("Agent 文件访问", "Agent file access"),
            "codexFileAccess": (
                "当前：只读，Agent 不能修改文件（包括自己的工作目录）。写入工作目录：未验证，暂不开放。",
                "Current: read-only. The Agent cannot modify files, including its own working directory. Writing to the working directory: unverified and unavailable for now.",
            ),
            "piFileAccess": (
                "工作目录固定为当前节点的 agent_nodes/{node_id}/sessions；受限 read/write/edit 仅可访问该目录，目录外访问被拒绝。模型不能写入 system-prompt.txt 或该目录根层的原生会话 .jsonl；未开放 bash、PowerShell 或 codemode。",
                "The working directory is fixed to this node's agent_nodes/{node_id}/sessions. Guarded read/write/edit can access only this directory; outside access is refused. The model cannot write system-prompt.txt or native session .jsonl files at this directory's root. bash, PowerShell, and codemode are not enabled.",
            ),
            "piFileAccessBoundary": (
                "这是工具 I/O 守卫，不是操作系统沙盒，不能防止其他进程并发替换重解析点（reparse points）。",
                "This is a tool I/O guard, not an OS sandbox. It cannot prevent other processes from concurrently swapping reparse points.",
            ),
        }
        for key, texts in expected.items():
            with self.subTest(key=key):
                matches = re.findall(r'"globalSettings\.' + key + r'":\s*\{\s*zh:\s*"([^"]*)",\s*en:\s*"([^"]*)"\s*\}', translations)
                self.assertEqual(matches, [texts])
        self.assertIn('/static/js/i18n/studio.js', (ROOT / "static/js/i18n.js").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
