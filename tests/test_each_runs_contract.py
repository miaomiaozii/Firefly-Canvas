import re
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "static" / "js" / "canvas-generation-tasks.js"


import re as _re

def _function_body(text: str, name: str) -> str:
    match = _re.search(rf"^(?:async )?function {name}\(", text, _re.M)
    assert match, f"function {name} not found at top level"
    start = match.start()
    nxt = _re.search(r"^(?:async )?function ", text[match.end():], _re.M)
    end = match.end() + nxt.start() if nxt else len(text)
    return text[start:end]


class EachRunsContractTests(unittest.TestCase):
    def setUp(self):
        self.text = SOURCE.read_text(encoding="utf-8")

    def test_expander_is_pure(self):
        body = _function_body(self.text, "expandEachRuns")
        for forbidden in ("canvasState", "canvasCore", "document.", "fetch("):
            self.assertNotIn(forbidden, body)
        self.assertIn("ctx.nodeRect", body)

    def test_anchor_is_right_of_ref_node(self):
        self.assertIn("const EACH_RUN_RESULT_GAP = 140;", self.text)
        body = _function_body(self.text, "expandEachRuns")
        self.assertRegex(
            re.sub(r"\s+", " ", body),
            r"\(Number\(rect\.x\) \|\| 0\) \+ \(Number\(rect\.width\) \|\| 0\) \+ EACH_RUN_RESULT_GAP",
        )

    def test_orchestrator_undo_once_and_single_ref(self):
        body = _function_body(self.text, "runEachImageGeneration")
        self.assertEqual(len(re.findall(r"\bpushUndo\(\)", body)), 1)
        self.assertEqual(len(re.findall(r"createImageNodeAt\(", body)), 1)
        self.assertIn("imageRefs.slice(0, 1)", body)
        self.assertIn("runApiGeneration(", body)
        self.assertIn("skipUndo:true", body)


if __name__ == "__main__":
    unittest.main()
