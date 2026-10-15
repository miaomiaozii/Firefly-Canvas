"""画布前端源码聚合。

`smart-canvas.js` 正在按 #154 逐步拆分为多个 classic <script> 模块。任何按源码文本断言画布
前端行为的测试，都应通过 `canvas_source()` 读取完整前端，而不是只读 `smart-canvas.js`，
否则被拆走的代码会让断言假失败。

新增模块时：把文件名按 `static/smart-canvas.html` 中 <script> 的加载顺序加进 CANVAS_SCRIPTS。
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS_DIR = ROOT / "static" / "js"

# 顺序与 static/smart-canvas.html 里的 <script src> 一致（classic script，靠加载顺序共享全局词法作用域）。
CANVAS_SCRIPTS: tuple[str, ...] = (
    "canvas-core.js",
    "canvas-node-types.js",
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
    "smart-canvas.js",
)


def canvas_path(name: str) -> Path:
    return JS_DIR / name


def is_canvas_module(name: str) -> bool:
    """判断一个 /static/js/ 脚本是否属于按 #154 拆分的画布前端模块。"""
    return name.startswith("canvas-") or name in ("smart-canvas.js", "project-canvas-mutations.js")


def canvas_source(*names: str) -> str:
    """Concatenate canvas frontend sources (defaults to every module in load order)."""
    return "\n".join(canvas_path(name).read_text(encoding="utf-8") for name in (names or CANVAS_SCRIPTS))
