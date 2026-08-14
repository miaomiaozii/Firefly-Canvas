import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class LongContentReaderUiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (ROOT / "static" / "smart-canvas.html").read_text(encoding="utf-8")
        cls.source = (ROOT / "static" / "js" / "smart-canvas.js").read_text(encoding="utf-8")
        cls.styles = (ROOT / "static" / "css" / "smart-canvas.css").read_text(encoding="utf-8")
        cls.i18n = (ROOT / "static" / "js" / "i18n" / "smart-canvas.js").read_text(encoding="utf-8")

    def test_readonly_reader_is_a_single_accessible_dialog(self):
        self.assertIn('id="longContentReader"', self.html)
        self.assertIn('role="dialog" aria-modal="true"', self.html)
        self.assertIn('aria-labelledby="longContentReaderTitle"', self.html)
        self.assertIn('id="longContentReaderBody"', self.html)
        self.assertIn('aria-readonly="true"', self.html)
        self.assertIn('id="longContentReaderClose"', self.html)
        self.assertNotIn('id="longContentReaderBody" contenteditable', self.html)

    def test_readonly_reader_has_one_deep_interface_and_never_writes_project_state(self):
        self.assertIn("function openLongContentReader({title, text, trigger=null, triggerIdentity=null}){", self.source)
        self.assertIn("function closeLongContentReader({restoreFocus=true}={}){", self.source)
        self.assertIn("longContentReaderBody.textContent = String(text || '');", self.source)
        self.assertIn("function restoreLongContentTriggerFocus(identity, fallback){", self.source)
        self.assertNotIn("longContentReaderBody.contentEditable", self.source)
        self.assertNotIn("longContentReaderBody.innerHTML =", self.source)

    def test_prompt_node_flow_keeps_the_original_textarea_dom(self):
        self.assertIn('class="prompt-node-flow-toggle prompt-node-control"', self.source)
        self.assertIn("function enterPromptNodeFlow(nodeId, trigger=null){", self.source)
        self.assertIn("function exitPromptNodeFlow(){", self.source)
        self.assertIn("promptNodeFlowView.textarea = textarea;", self.source)
        self.assertIn("promptNodeFlowView.element = nodeElement;", self.source)
        self.assertIn("keepEls.add(promptNodeFlowView.element);", self.source)
        self.assertNotIn("promptNodeFlowView.textarea.cloneNode", self.source)
        self.assertNotIn("longContentReaderBody.value = node.text", self.source)

    def test_all_readonly_sources_have_explicit_native_button_triggers(self):
        self.assertIn('data-long-content-open="input-prompt"', self.source)
        self.assertIn('data-long-content-open="history-prompt"', self.source)
        self.assertIn('data-long-content-open="history-error"', self.source)
        self.assertIn("smartLogEntryById(logId)", self.source)
        self.assertIn("bindLongContentHistoryTriggers", self.source)

    def test_escape_and_focus_restoration_are_owned_by_the_active_session(self):
        self.assertIn("function closeActiveLongContentSession(){", self.source)
        self.assertIn("if(event.key === 'Escape' && closeActiveLongContentSession())", self.source)
        self.assertIn("data-canvas-log-id", self.source)
        self.assertIn("renderSmartHistoryWindow(true);", self.source)
        self.assertIn("focus({preventScroll:true})", self.source)

    def test_long_content_modes_have_responsive_focus_styles_and_translations(self):
        for selector in (
            "body.prompt-node-flow-active",
            ".image-node.prompt-smart-node.prompt-node-flow",
            ".long-content-reader.open",
            ".long-content-reader-body",
            ".long-content-trigger",
        ):
            self.assertIn(selector, self.styles)
        for key in (
            "smart.readFullContent",
            "smart.longContentClose",
            "smart.promptNodeFlowEnter",
            "smart.promptNodeFlowExit",
            "smart.connectionPromptFull",
            "smart.historyPromptFull",
            "smart.generationErrorFull",
        ):
            self.assertIn(f'"{key}"', self.i18n)


if __name__ == "__main__":
    unittest.main()
