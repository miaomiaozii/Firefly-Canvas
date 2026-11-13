"""会话框/参考框（Refs #323 票 E）——frame 落点数学与素材解析的纯函数层。

frame = 现有 smart-group 节点（{id,type:'smart-group',x,y,w,h,title,items:[nodeId...]}，
static/js/canvas-node-ops.js:147 同构），不造新节点类型。本模块只做两件事：
  1. compute_frame_slot：按网格给 frame 内第 index 个成员算画布落点；容量不足时
     定义 frame 自动长高（监理钉②：行为必须显式并单测）。
  2. resolve_ref_members：从 agent 节点的 refFrameId 解析参考框成员（含护栏计数），
     发送侧（agent_host.send_prompt）据此注入。

前端镜像：static/js/agent-frame.js（computeFrameSlot 与本文件同步改，
tests/test_agent_frame.py 断言两份常数表一致）。
"""

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
