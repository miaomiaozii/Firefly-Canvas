"""Layering contract guard test (issue #163).

#152 added `body:has(.log-modal.open) .image-node { z-index:87 }` to keep
canvas nodes above the history drawer (z-index:86), but the rule lifted every
image node — including the generation group frame (.smart-group-node) — above
the prompt composer (z-index:30). With the drawer open (even collapsed), the
group frame's translucent backplate painted over the composer and swallows its
clicks: prompts could not be typed, parameters changed, or generation started.

#163 restores the documented layering contract (bottom → top):
连线 < 生成分组框（背板） < 普通节点 < 选中/拖动中的节点 < 提示词框 < 面板类浮层 < 历史抽屉

Static half of the contract, asserted on the CSS text:
1. the drawer-open node lift excludes the generation group frame
   (the group frame keeps its bottom-backboard z-index:0);
2. the composer is raised above the lifted nodes (88 > 87) while the
   drawer is open, so it stays the operable top canvas surface;
3. CONTEXT.md documents the 生成分组框 domain term (issue terminology gap).

The behavioral half (real headless-Chrome hit-testing of the overlap) lives
in tests/layering_contract_browser.py.
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CANVAS_CSS = (ROOT / "static" / "css" / "smart-canvas.css").read_text(encoding="utf-8")
CONTEXT_MD = (ROOT / "CONTEXT.md").read_text(encoding="utf-8")


def rule_body(css: str, selector: str) -> str:
    """Return the declaration block of the first rule for an exact top-level selector."""
    match = re.search(r"(?:^|\n)" + re.escape(selector) + r"\s*\{([^}]*)\}", css, re.M)
    if match is None:
        raise AssertionError(f"CSS rule not found: {selector}")
    return match.group(1)


def z_index(body: str) -> int:
    match = re.search(r"z-index\s*:\s*(\d+)", body)
    if match is None:
        raise AssertionError(f"z-index declaration not found in: {body!r}")
    return int(match.group(1))


class LayeringContractTests(unittest.TestCase):
    def test_group_frame_is_excluded_from_the_drawer_open_lift(self):
        """#163: 生成分组框 never joins the drawer-open node lift."""
        body = rule_body(
            CANVAS_CSS, "body:has(.log-modal.open) .image-node:not(.smart-group-node)"
        )
        self.assertEqual(z_index(body), 87)
        # The un-excluded #152 rule must be gone — it is what lifted group frames.
        self.assertIsNone(
            re.search(r"body:has\(\.log-modal\.open\)\s*\.image-node\s*\{", CANVAS_CSS),
            "bare `body:has(.log-modal.open) .image-node` lift still present",
        )

    def test_group_frame_keeps_the_bottom_backboard_layer(self):
        body = rule_body(CANVAS_CSS, ".image-node.smart-group-node")
        self.assertEqual(z_index(body), 0)

    def test_composer_stays_above_lifted_nodes_with_the_drawer_open(self):
        composer_body = rule_body(CANVAS_CSS, "body:has(.log-modal.open) .composer")
        composer_z = z_index(composer_body)
        lifted_body = rule_body(
            CANVAS_CSS, "body:has(.log-modal.open) .image-node:not(.smart-group-node)"
        )
        self.assertGreater(composer_z, z_index(lifted_body))
        # The base composer layer must stay above closed-drawer node states
        # (selected=10, dragging=12) so the contract holds with the drawer closed too.
        base_composer_z = z_index(rule_body(CANVAS_CSS, ".composer"))
        self.assertGreaterEqual(base_composer_z, 30)

    def test_context_md_documents_the_generation_group_frame_term(self):
        """#163 terminology gap: 生成分组框 / 组框 must be a domain term."""
        entry = re.search(r"\*\*生成分组框\*\*：\n(?P<def>.+?)\n_Avoid_:", CONTEXT_MD, re.S)
        self.assertIsNotNone(entry, "CONTEXT.md is missing the 生成分组框 term")
        definition = entry.group("def")
        self.assertIn("背板", definition, "term definition must state the backboard layering role")


if __name__ == "__main__":
    unittest.main()
