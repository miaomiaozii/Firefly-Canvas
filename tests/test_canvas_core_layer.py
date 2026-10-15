"""#173 核心下沉层守卫。

实测背景（见 issue #173 评论）：#154 的 A–E 批次把约 9.4K 行代码移出
`smart-canvas.js`，但那些代码依赖的东西全留在原地。11 个已拆出模块反向引用
`smart-canvas.js` 的顶层定义共 229 个名字 / 1836 处，其中 `canvas-media-history.js`
一个文件就回调 721 次。模块看着独立，实际每个都往核心打上百个洞。

本测试锁两件事：
1. 纯工具与共享状态必须住在最早加载的 `canvas-core.js`，而不是 `smart-canvas.js`；
2. 反向引用总数只能降不能升（棘轮）。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS_DIR = ROOT / "static" / "js"
HTML = ROOT / "static" / "smart-canvas.html"

# 必须住在 canvas-core.js 的名字：无状态工具 + 跨模块共享的可变状态。
CORE_NAMES = (
    "tr",
    "escapeHtml",
    "escapeAttr",
    "uid",
    "nowMs",
    "selectedNode",
    "isSmartImageNode",
    "isSmartGroupNode",
    "nodeScale",
    "MEDIA_GROUP_DEFAULT_SCALE",
    "MEDIA_GROUP_PREVIOUS_DEFAULT_SCALE",
    "cloneSmartSettings",
    "resultMediaUrls",
    "canvas",
    "settings",
    "params",
)

# 必须住在自己模块里的名字：模块自己的状态，不该留在核心。
MODULE_OWNED = {
    "canvas-media-history.js": (
        "smartHistoryModal",
        "smartHistoryList",
        "smartHistoryCollapse",
        "smartHistoryPurgeTrash",
        "smartHistoryVirtualState",
    ),
    "canvas-connections.js": ("connectionEraseState",),
}

# 棘轮上限：模块 -> smart-canvas.js 的反向引用处数。只能降。
# 2026-10-15 实测：下沉前 1836；三步下沉后见下方常量。
REVERSE_REFERENCE_CEILING = 1836

MODULES = (
    "canvas-state.js",
    "project-canvas-mutations.js",
    "canvas-layout-utils.js",
    "canvas-connections.js",
    "canvas-media-history.js",
    "canvas-undo.js",
    "canvas-node-ops.js",
    "canvas-image-editor.js",
    "canvas-generation-cascade.js",
    "canvas-generation-tasks.js",
    "canvas-agent-tui.js",
    "canvas-core.js",
)


def _top_level_names(text: str) -> set[str]:
    return {
        m.group(1)
        for m in re.finditer(r"^(?:function|const|let|var|class)\s+([A-Za-z_$][\w$]*)", text, re.M)
    }


def _script_order() -> list[str]:
    return [
        Path(src).name
        for src in re.findall(r'<script src="/static/js/([^"?]+)', HTML.read_text(encoding="utf-8"))
    ]


def reverse_references() -> int:
    """已拆出模块引用 smart-canvas.js 顶层定义的处数。"""
    core = _top_level_names((JS_DIR / "smart-canvas.js").read_text(encoding="utf-8"))
    total = 0
    for module in MODULES:
        path = JS_DIR / module
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for name in core:
            total += len(re.findall(r"\b" + re.escape(name) + r"\b", text))
    return total


class CanvasCoreLayerTests(unittest.TestCase):
    def test_canvas_core_is_loaded_first_among_canvas_scripts(self):
        order = _script_order()
        canvas_scripts = [name for name in order if name.startswith("canvas-")]
        self.assertIn("canvas-core.js", canvas_scripts)
        self.assertEqual(canvas_scripts[0], "canvas-core.js")
        self.assertEqual(canvas_scripts[-1], "smart-canvas.js")

    def test_canvas_core_is_first_in_the_test_aggregator(self):
        from tests.canvas_frontend_sources import CANVAS_SCRIPTS

        canvas_scripts = [name for name in CANVAS_SCRIPTS if name.startswith("canvas-")]
        self.assertEqual(canvas_scripts[0], "canvas-core.js")

    def test_shared_helpers_live_in_canvas_core_not_smart_canvas(self):
        core_text = (JS_DIR / "canvas-core.js").read_text(encoding="utf-8")
        core_names = _top_level_names(core_text)
        smart_names = _top_level_names((JS_DIR / "smart-canvas.js").read_text(encoding="utf-8"))
        missing = sorted(name for name in CORE_NAMES if name not in core_names)
        still_in_smart_canvas = sorted(name for name in CORE_NAMES if name in smart_names)
        self.assertEqual([], missing)
        self.assertEqual([], still_in_smart_canvas)

    def test_module_owned_state_lives_in_its_own_module(self):
        smart_names = _top_level_names((JS_DIR / "smart-canvas.js").read_text(encoding="utf-8"))
        for module, names in MODULE_OWNED.items():
            owned = _top_level_names((JS_DIR / module).read_text(encoding="utf-8"))
            missing = sorted(name for name in names if name not in owned)
            self.assertEqual([], missing, f"{module} 缺少自己的状态定义")
            leaked = sorted(name for name in names if name in smart_names)
            self.assertEqual([], leaked, f"{module} 的状态仍留在 smart-canvas.js")

    def test_reverse_reference_count_does_not_grow(self):
        count = reverse_references()
        self.assertLessEqual(
            count,
            REVERSE_REFERENCE_CEILING,
            f"模块反向引用 smart-canvas.js 的处数从 {REVERSE_REFERENCE_CEILING} 升到 {count}；"
            "按功能继续拆分之前，先把被回调的名字下沉到 canvas-core.js。",
        )


if __name__ == "__main__":
    unittest.main()
