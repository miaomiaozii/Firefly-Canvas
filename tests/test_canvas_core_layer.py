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

# 棘轮上限：模块 -> smart-canvas.js 的反向引用处数，按模块记。只能降。
# 2026-10-15 实测：下沉前 1836；#173 三步下沉后 654（降 64%）。
# 按模块记是为了让回归可归因：以前只有一个全局单值，红了也不知道是谁。
# 口径（见 reverse_references）：去注释行，且排除该模块自己声明过的名字。
# 不去掉后者会在 smart-canvas.js 新增一个常见顶层名时凭空打红：
# 实测只加一行 `let node = null;`、不改任何模块，计数就从 686 到 1504。
# 剩下的主要是 render / scheduleSave / shell / world 这类行为枢纽与 DOM 节点，
# 它们本来就该被模块调用；要继续降，得先判明哪些该注入、哪些该留在核心。
REVERSE_REFERENCE_CEILINGS = {
    "canvas-generation-cascade.js": 160,
    "canvas-media-history.js": 125,
    "canvas-generation-tasks.js": 124,
    "canvas-image-editor.js": 110,
    "canvas-node-ops.js": 63,
    "canvas-connections.js": 46,
    "canvas-undo.js": 14,
    "canvas-agent-tui.js": 12,
    "canvas-state.js": 0,
    "project-canvas-mutations.js": 0,
    "canvas-layout-utils.js": 0,
    # canvas-core.js 是最早加载的那层，它不该反向引用任何东西；0 是设约束，不是实测巧合。
    "canvas-core.js": 0,
    # #184 新增的节点类型常量层：只声明常量，不引用 smart-canvas.js 的任何顶层名；
    # 必须紧跟 canvas-core.js 之后加载（见 test_canvas_core_is_loaded_first_among_canvas_scripts）。
    "canvas-node-types.js": 0,
}


def _top_level_names(text: str) -> set[str]:
    return {
        m.group(1)
        for m in re.finditer(r"^(?:function|const|let|var|class)\s+([A-Za-z_$][\w$]*)", text, re.M)
    }


def _is_canvas_script(name: str) -> bool:
    from tests.canvas_frontend_sources import is_canvas_module

    return is_canvas_module(name)


def _script_order() -> list[str]:
    return [
        Path(src).name
        for src in re.findall(r'<script src="/static/js/([^"?]+)', HTML.read_text(encoding="utf-8"))
    ]


def _strip_comment_lines(text: str) -> str:
    return "\n".join(
        line
        for line in text.splitlines()
        if not line.strip().startswith(("//", "/*", "*"))
    )


_DECL = re.compile(r"(?:const|let|var|class|function)\s+([A-Za-z_$][\w$]*)")
_ARROW_ARG = re.compile(r"([A-Za-z_$][\w$]*)\s*=>")
_FUNC_PARAMS = re.compile(r"function\s*[A-Za-z_$][\w$]*\s*\(([^)]*)\)")
_ARROW_PARAMS = re.compile(r"\(([^()]*)\)\s*=>")


def _locally_declared_names(text: str) -> set[str]:
    """一个文件里任何位置声明过的名字（顶层、局部、函数参数、箭头参数）。"""
    names: set[str] = set()
    for rx in (_DECL, _ARROW_ARG):
        names.update(m.group(1) for m in rx.finditer(text))
    for rx in (_FUNC_PARAMS, _ARROW_PARAMS):
        for m in rx.finditer(text):
            for word in m.group(1).split(","):
                word = word.strip().split("=")[0].strip()
                if word:
                    names.add(word)
    return names


def reverse_references() -> dict[str, int]:
    """每个已拆出模块引用 smart-canvas.js 顶层定义的处数。

    只算真耦合：去掉注释行，并且排除该模块自己声明过的同名标识符。
    不排除同名局部变量时，这个指标会把模块内部的 `node`/`item` 之类
    误计成对核心的耦合。
    """
    core = _top_level_names((JS_DIR / "smart-canvas.js").read_text(encoding="utf-8"))
    counts: dict[str, int] = {}
    for module in _canvas_modules():
        path = JS_DIR / module
        if not path.exists():
            counts[module] = 0
            continue
        text = _strip_comment_lines(path.read_text(encoding="utf-8"))
        local = _locally_declared_names(text)
        total = 0
        for name in core:
            if name in local:
                continue
            total += len(re.findall(r"\b" + re.escape(name) + r"\b", text))
        counts[module] = total
    return counts


def _canvas_modules() -> tuple[str, ...]:
    """已拆出模块清单，从 `CANVAS_SCRIPTS` 推导，不再手拄。

    以前 `MODULES` 是手写的，与 `canvas_frontend_sources.is_canvas_module` 之间
    没有任何一致性守卫：新增一个模块只改了 HTML 和 `CANVAS_SCRIPTS`，
    就会静默地不参与反向引用计数。
    """
    from tests.canvas_frontend_sources import CANVAS_SCRIPTS

    return tuple(name for name in CANVAS_SCRIPTS if name != "smart-canvas.js")


class CanvasCoreLayerTests(unittest.TestCase):
    def test_module_ceilings_cover_exactly_the_derived_module_set(self):
        # 新增模块时，这里会先红，逼你显式给它记一个上限，而不是静默少计。
        self.assertEqual(sorted(_canvas_modules()), sorted(REVERSE_REFERENCE_CEILINGS))

    def test_canvas_core_is_loaded_first_among_canvas_scripts(self):
        order = _script_order()
        canvas_scripts = [name for name in order if _is_canvas_script(name)]
        self.assertIn("canvas-core.js", canvas_scripts)
        # canvas-node-types.js 是唯一可以排在它前面的脚本：canvas-core.js 里的
        # isSmartImageNode/isSmartGroupNode 要读 NODE_TYPE_*（见 test_node_type_constants）。
        self.assertEqual(
            canvas_scripts[:canvas_scripts.index("canvas-core.js")],
            [n for n in canvas_scripts[:canvas_scripts.index("canvas-core.js")] if n == "canvas-node-types.js"],
            "only canvas-node-types.js may load before canvas-core.js; got: %r" % canvas_scripts)
        self.assertEqual(canvas_scripts[-1], "smart-canvas.js")

    def test_canvas_core_is_first_in_the_test_aggregator(self):
        from tests.canvas_frontend_sources import CANVAS_SCRIPTS

        canvas_scripts = [name for name in CANVAS_SCRIPTS if name.startswith("canvas-")]
        self.assertIn(canvas_scripts[0], ("canvas-core.js", "canvas-node-types.js"))
        self.assertEqual(canvas_scripts[1], "canvas-core.js")

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

    def test_no_top_level_name_is_declared_twice_across_loaded_scripts(self):
        # classic 脚本共享同一个全局词法作用域：同一个 `let`/`const` 名字在两个文件里
        # 各声明一次，不会在 Python 侧报错，而是整份 smart-canvas.js 解析失败，
        # 表现是画布一个节点都不渲染。#173 下沉过程中实测踩过这个坑。
        # 扫描面是 smart-canvas.html 加载的全部脚本，不只画布模块：
        # 跟 i18n.js 这类非画布脚本撞名，坏法一模一样。
        seen: dict[str, list[str]] = {}
        for name in _script_order():
            path = JS_DIR / name
            if not path.exists():
                continue
            for m in re.finditer(r"^(?:function|const|let|var|class)\s+([A-Za-z_$][\w$]*)", path.read_text(encoding="utf-8"), re.M):
                seen.setdefault(m.group(1), []).append(name)
        duplicates = {name: files for name, files in seen.items() if len(files) > 1}
        self.assertEqual({}, duplicates)

    def test_reverse_reference_count_does_not_grow(self):
        counts = reverse_references()
        self.assertEqual(sorted(REVERSE_REFERENCE_CEILINGS), sorted(counts))
        regressions = [
            f"{module}: {counts[module]} > {REVERSE_REFERENCE_CEILINGS[module]}"
            for module in sorted(counts)
            if counts[module] > REVERSE_REFERENCE_CEILINGS[module]
        ]
        self.assertEqual(
            [],
            regressions,
            "模块反向引用 smart-canvas.js 的处数上升；按功能继续拆分之前，"
            "先把被回调的名字下沉到 canvas-core.js。",
        )


if __name__ == "__main__":
    unittest.main()
