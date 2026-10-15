"""Layering contract guard test (issues #163 + #164).

#152 added `body:has(.log-modal.open) .image-node { z-index:87 }` so canvas
nodes would stay above the history drawer (z-index:86). #163 excluded the
generation group frame (.smart-group-node) from that lift and raised the
prompt composer to 88 while the drawer was open.

#164 shows both drawer-open rules were wrong in a second way: they made the
layering contract *state dependent*. `body:has(.log-modal.open) .image-node`
has specificity (0,4,1), so it also flattened the selected (10) and dragging
(12) node states onto 87 — with the drawer open, a selected or dragged node
was no longer above an ordinary node, and painting order fell back to DOM
order. `body:has(.log-modal.open) .composer { z-index:88 }` also crushed the
prompt-flow composer (.composer.prompt-flow, z-index:120) down to 88.

The contract is now one single ladder that holds with the drawer closed,
open and collapsed (bottom → top):
连线 < 生成分组框（背板） < 普通节点 < 选中节点 < 拖动中的节点 < 提示词框
< 面板类浮层 < 历史抽屉 < 提示词流

The drawer stays the topmost surface over the canvas — keeping the prompt
composer reachable is done by panning the canvas (see
keepComposerReachableByPanningCanvas), never by re-stacking it above the drawer.

Static half of the contract, asserted on the CSS text:
1. no drawer-open rule restacks canvas nodes or the composer at all;
2. the generation group frame keeps its bottom-backboard layer;
3. one single z-index ladder, independent of the drawer state.

The behavioral half (real headless-Chrome hit-testing of the overlap, in all
three drawer states) lives in tests/layering_contract_browser.py, and the
real-drag screenshot acceptance in tests/layering_drag_browser.py.
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


def layer_z(css: str, selector: str) -> int:
    """z-index declared by an exact top-level selector (any of its rules)."""
    bodies = re.findall(
        r"(?:^|\n)" + re.escape(selector) + r"\s*\{([^}]*)\}", css, re.M
    )
    if not bodies:
        raise AssertionError(f"CSS rule not found: {selector}")
    for body in bodies:
        match = re.search(r"z-index\s*:\s*(\d+)", body)
        if match:
            return int(match.group(1))
    raise AssertionError(f"no z-index declared for selector: {selector}")


class LayeringContractTests(unittest.TestCase):
    def test_no_drawer_open_rule_restacks_canvas_layers(self):
        """#164: the layering contract must not depend on the drawer state."""
        offenders = [
            line.strip()
            for line in CANVAS_CSS.splitlines()
            if ".log-modal" in line and re.search(r"\bz-index\s*:", line)
            and (".image-node" in line or ".composer" in line)
        ]
        self.assertEqual(
            offenders,
            [],
            "drawer-open rules must not restack canvas nodes or the composer: "
            + "; ".join(offenders),
        )
        # The un-excluded #152 lift and the #163 composer lift must both be gone.
        self.assertIsNone(
            re.search(r"body:has\(\.log-modal\.open\)\s*\.image-node\s*\{", CANVAS_CSS),
            "bare `body:has(.log-modal.open) .image-node` lift still present",
        )
        self.assertIsNone(
            re.search(
                r"body:has\(\.log-modal\.open\)\s*\.image-node:not\(\.smart-group-node\)\s*\{",
                CANVAS_CSS,
            ),
            "#152 drawer-open node lift still present (flattens selected/dragging)",
        )
        self.assertIsNone(
            re.search(r"body:has\(\.log-modal\.open\)\s*\.composer\s*\{", CANVAS_CSS),
            "drawer-open composer lift still present (crushes prompt-flow 120)",
        )

    def test_group_frame_keeps_the_bottom_backboard_layer(self):
        """生成分组框 stays the bottom backboard in every drawer state."""
        self.assertEqual(layer_z(CANVAS_CSS, ".image-node"), 2)
        self.assertEqual(layer_z(CANVAS_CSS, ".image-node.smart-group-node"), 0)

    def test_single_z_index_ladder_holds_in_every_drawer_state(self):
        """One ladder: 组框 < 普通节点 < 选中 < 拖动中 < 提示词框 < 历史抽屉 < 提示词流."""
        ladder = {
            ".image-node.smart-group-node": layer_z(CANVAS_CSS, ".image-node.smart-group-node"),
            ".image-node": layer_z(CANVAS_CSS, ".image-node"),
            ".image-node.selected:not(.smart-group-node)": layer_z(
                CANVAS_CSS, ".image-node.selected:not(.smart-group-node)"
            ),
            ".image-node.dragging:not(.smart-group-node)": layer_z(
                CANVAS_CSS, ".image-node.dragging:not(.smart-group-node)"
            ),
            ".composer": layer_z(CANVAS_CSS, ".composer"),
            ".log-modal": layer_z(CANVAS_CSS, ".log-modal"),
            ".composer.prompt-flow": layer_z(CANVAS_CSS, ".composer.prompt-flow"),
        }
        expected = {
            ".image-node.smart-group-node": 0,
            ".image-node": 2,
            ".image-node.selected:not(.smart-group-node)": 10,
            ".image-node.dragging:not(.smart-group-node)": 12,
            ".composer": 30,
            ".log-modal": 86,
            ".composer.prompt-flow": 120,
        }
        self.assertEqual(ladder, expected)
        ordered = [ladder[key] for key in expected]
        self.assertEqual(
            ordered, sorted(ordered), f"z-index ladder is not monotonic: {ladder}"
        )

    def test_context_md_documents_the_generation_group_frame_term(self):
        """#163 terminology gap: 生成分组框 / 组框 must be a domain term."""
        entry = re.search(r"\*\*生成分组框\*\*：\n(?P<def>.+?)\n_Avoid_:", CONTEXT_MD, re.S)
        self.assertIsNotNone(entry, "CONTEXT.md is missing the 生成分组框 term")
        definition = entry.group("def")
        self.assertIn("背板", definition, "term definition must state the backboard layering role")


if __name__ == "__main__":
    unittest.main()
