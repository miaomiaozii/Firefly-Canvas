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
    "canvas-node-types.js",
    "canvas-core.js",
    "canvas-state.js",
    "project-canvas-mutations.js",
    "canvas-layout-utils.js",
    "canvas-connections.js",
    # #223：下载层（接收响应 blob → 校验体积 → 落盘）从 smart-canvas.js 抽成独立模块。
    "canvas-download.js",
    "canvas-media-history.js",
    "canvas-undo.js",
    # #272：画布批注层（可持久画布对象；ops.db anno_* 租户前端；逻辑独立，smart-canvas.js 只接线 2 行）。
    "canvas-annotations.js",
    "canvas-node-ops.js",
    # #255：分数坐标骨架（0..1 公共坐标，crop/mask/brush 换算地基）。
    "canvas-geometry.js",
    # #246：freeSpot 落点扫描（右移碰撞扫描 + 200 步上限 + 批量行排）。
    "canvas-placement.js",
    # #255：ComfyArt vectorPath.ts MIT 移植（路径归一化 / 缩放纯函数）。
    "canvas-vector-path.js",
    "canvas-image-editor.js",
    # #255：图像分层 + 图层面板（layers.ts MIT 移植 + store/合成/面板）。
    "canvas-layers.js",
    # #253：拖拽对齐吸附纯函数 + 参考线 overlay（零核心引用）。
    "canvas-snap.js",
    # #253：节点就地快速重命名 + 右键操作卡可见性纯函数（零核心引用）。
    "canvas-quick-edit.js",
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
