"""会话框/参考框（Refs #323 票 E）——frame 落点数学与素材解析的纯函数层。

frame = 现有 smart-group 节点（{id,type:'smart-group',x,y,w,h,title,items:[nodeId...]}，
static/js/canvas-node-ops.js 同构），不造新节点类型。兼管 Agent 创建卡片的落位
和参考框的素材解析；落位不写画布，由调用方合并到同一次变更。

前端镜像：static/js/agent-frame.js（computeFrameSlot 与本文件同步改，
tests/test_agent_frame.py 断言两份常数表一致）。
"""

from math import isfinite

from app.constants import (
    NODE_TYPE_AGENT, NODE_TYPE_CONVERSATION, NODE_TYPE_GROUP, NODE_TYPE_IMAGE,
    NODE_TYPE_LOCAL_EDIT, NODE_TYPE_PROMPT, NODE_TYPE_TASK,
)


# 网格常数（前端 agent-frame.js 同步，测试锁定）
FRAME_PAD = 20        # 框内边距
FRAME_HEADER = 40     # group 标题带高度（成员落点避开标题）
CELL_W = 240
CELL_H = 140
CELL_GAP = 16
REF_MAX_IMAGES = 8            # 每轮注入素材上限（监理 token 钉）
REF_MAX_IMAGE_BYTES = 5_000_000  # 单图 >5MB 跳过


def frame_capacity(frame: dict) -> int:
    """frame 当前尺寸能容纳的成员数（网格口径与 compute_frame_slot 完全一致）。"""
    w = max(0, int(frame.get("w") or 0))
    h = max(0, int(frame.get("h") or 0))
    cols = (w - 2 * FRAME_PAD + CELL_GAP) // (CELL_W + CELL_GAP)
    rows = (h - FRAME_HEADER - 2 * FRAME_PAD + CELL_GAP) // (CELL_H + CELL_GAP)
    return max(0, cols) * max(0, rows)


def compute_frame_slot(frame: dict, index: int) -> tuple[int, int, int]:
    """frame 内第 index 个（0 基）成员的 (dx, dy, new_h)。

    dx/dy 为相对 frame.x/frame.y 的偏移；new_h 为容纳该成员所需的 frame 高度
    （不足时按整行向下扩张，超出则不缩小——自动生长语义，钉②）。
    """
    w = max(0, int(frame.get("w") or 0))
    h = max(0, int(frame.get("h") or 0))
    cols = max(1, (w - 2 * FRAME_PAD + CELL_GAP) // (CELL_W + CELL_GAP))
    col = index % cols
    row = index // cols
    dx = FRAME_PAD + col * (CELL_W + CELL_GAP)
    dy = FRAME_HEADER + FRAME_PAD + row * (CELL_H + CELL_GAP)
    needed_h = dy + CELL_H + FRAME_PAD
    return dx, dy, max(h, needed_h)


# Match initial card sizes from imageLayout; later user resize/layout can change them.
_NODE_SIZES = {
    NODE_TYPE_AGENT: (480, 360),
    NODE_TYPE_PROMPT: (316, 240),
    NODE_TYPE_TASK: (300, 236),
    NODE_TYPE_CONVERSATION: (390, 430),
    NODE_TYPE_GROUP: (340, 286),
    NODE_TYPE_LOCAL_EDIT: (400, 520),
}
_MEDIA_TYPES = {NODE_TYPE_IMAGE, "image", "smart-video", "video"}
# The grouped placeholder layout is 136px square, but .image-node.empty-node
# has a CSS min-height of 178px. Reserve the visible card or consecutive rows overlap.
_EMPTY_IMAGE_MIN_H = 178


def coordinate(value, default=0):
    return value if type(value) in (int, float) and isfinite(value) else default


def node_size(node: dict, *, grouped: bool = False) -> tuple[int, int]:
    kind = str(node.get("type") or "")
    if kind in _MEDIA_TYPES:
        if node.get("images"):
            fallback = (260, 220)
        else:
            fallback = (136, _EMPTY_IMAGE_MIN_H) if grouped else (292, 292)
    else:
        fallback = _NODE_SIZES.get(kind, (CELL_W, CELL_H))
    w = coordinate(node.get("w"))
    h = coordinate(node.get("h"))
    w = round(w) if w > 24 else fallback[0]
    h = round(h) if h > 24 else fallback[1]
    if kind in (NODE_TYPE_IMAGE, "image") and not node.get("images"):
        h = max(h, _EMPTY_IMAGE_MIN_H)
    return w, h


def right_of_canvas(nodes) -> int | float:
    # Initial placement clears existing cards (including legacy cards without stored widths).
    right = max((coordinate(n.get("x")) + node_size(n)[0] for n in nodes), default=0)
    return max(0, right) + FRAME_HEADER


def place_in_frame(frame: dict, node: dict, nodes: dict) -> tuple[dict, dict]:
    """Append in grid order without covering an existing member, even if it moved."""
    items = list(frame.get("items") or [])
    frame_w, frame_h = node_size(frame)
    fw = max(frame_w, node["w"] + 2 * FRAME_PAD)
    grown = {**frame, "w": fw, "h": frame_h}
    fx, fy = coordinate(frame.get("x")), coordinate(frame.get("y"))
    boxes = []
    for identity in items:
        if identity in nodes:
            member = nodes[identity]
            x, y = coordinate(member.get("x")), coordinate(member.get("y"))
            w, h = node_size(member, grouped=True)
            boxes.append((x, y, x + w, y + h))

    cols = max(1, (int(fw) - 2 * FRAME_PAD + CELL_GAP) // (CELL_W + CELL_GAP))
    # Skip grid columns in which the card would protrude past the frame.
    last_col = int(fw - 2 * FRAME_PAD - node["w"]) // (CELL_W + CELL_GAP)
    index = len(items)
    while True:
        if index % cols > last_col:
            index += cols - index % cols
        dx, dy, grid_h = compute_frame_slot(grown, index)
        x, y = fx + dx, fy + dy
        if not any(x < r + CELL_GAP and x + node["w"] + CELL_GAP > l
                   and y < b + CELL_GAP and y + node["h"] + CELL_GAP > t
                   for l, t, r, b in boxes):
            return ({**node, "x": x, "y": y},
                    {**grown, "h": max(grid_h, dy + node["h"] + FRAME_PAD), "items": [*items, node["id"]]})
        index += 1


def next_to_source(source: dict, node: dict, nodes, memberships: set) -> dict:
    """Keep ungrouped results near their source without stacking repeat runs."""
    x = coordinate(source.get("x")) + node_size(source)[0] + FRAME_HEADER
    y = coordinate(source.get("y"))
    # Walk horizontally past only the cards that actually obstruct this row.
    for left, right in sorted(
        (coordinate(other.get("x")), coordinate(other.get("x")) + w)
        for other in nodes
        for oy, (w, h) in [(coordinate(other.get("y")), node_size(other, grouped=other["id"] in memberships))]
        if y < oy + h + CELL_GAP and y + node["h"] + CELL_GAP > oy
    ):
        if x < right + CELL_GAP and x + node["w"] + CELL_GAP > left:
            x = right + CELL_GAP
    return {**node, "x": x, "y": y}
