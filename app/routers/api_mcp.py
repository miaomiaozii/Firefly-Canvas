"""MCP (Model Context Protocol) streamable-HTTP endpoint for Firefly-Canvas.

ADR 0022: the whole canvas is exposed to external agents (Claude Code /
Codex / Cursor / VS Code) over a single streamable-HTTP MCP endpoint; an
external session is materialized as an Agent node on the canvas (决议 Q12-a)
and reuses the node-bound confirmation gate.

Protocol subset (pure JSON, no SSE — ADR 0022 decision):
- POST /api/mcp  : one JSON-RPC 2.0 message per request, one JSON response
- GET  /api/mcp  : 405 (SSE streams are not supported)
- batch (JSON array) → 400 + -32600 "MCP batch requests are not supported"
  (ComfyArt api/mcp.py:117-124 parity, issue #243 spec-compliance point)
- parse error → 400 + -32700; non-JSON-RPC → 400 + -32600
- unknown method → -32601; unknown tool in tools/call → tool result with
  isError (MCP TypeScript SDK convention, agent-recoverable)
- initialize negotiates from SUPPORTED_PROTOCOL_VERSIONS (default 2025-06-18);
  unsupported client versions are negotiated down to the default
- Mcp-Session-Id header: client-supplied id is echoed; otherwise a fresh
  uuid4 hex is minted and returned on every response

No new dependencies: the streamable-HTTP subset is implemented directly on
FastAPI (requirements.txt has no `mcp` package — ADR 0022 consequences;
the trade-off is documented in issue-243-result.md).

Security (ADR 0022 v1 boundary): loopback binding + the #240 same-origin
guard (ensure_agent_same_origin) on the whole router.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Callable, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from app.core import ensure_agent_same_origin
from app.project_sessions import open_project_ids
from app.routers.api_agent_tools import (
    _app_main,
    _find_node,
    _open_canvas,
    canvas_view_core,
    create_edge_core,
    create_node_core,
    crop_node_image_core,
    rotate_node_image_core,
    flip_node_image_core,
    batch_edit_core,
    delete_edge,
    delete_media,
    delete_node,
    focus_node_core,
    get_node,
    get_task_status,
    get_viewport_state,
    list_media,
    list_nodes,
    request_confirmation_core,
    set_zoom_core,
    trigger_generation_core,
    update_node_core,
    upload_media_core,
    view_media_core,
    wait_for_task_core,
)
from app.services.canvas_view import DEFAULT_VIEW_MAX_PX, MAX_VIEW_MAX_PX, VIEW_SLOW_TIMEOUT_S

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Protocol constants
# ---------------------------------------------------------------------------

SESSION_HEADER = "Mcp-Session-Id"
SUPPORTED_PROTOCOL_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18")
DEFAULT_PROTOCOL_VERSION = "2025-06-18"

#: Confirmation gate blocking wait (ADR 0022: request_confirmation default 60s).
CONFIRM_TIMEOUT_SECONDS = 60.0

#: Event aggregation interval — dirty flag + 3s timer, ComfyArt
#: api/bot_turns.py:26 (_PERSIST_INTERVAL_S=3.0) parity (issue #243 追加细节 3).
PERSIST_INTERVAL_S = 3.0

#: Destructive MCP tools that must pass the materialized confirmation gate.
CONFIRMATION_GATED_TOOLS = frozenset({"canvas.delete_node", "media.delete", "canvas.edit_crop", "canvas.edit_rotate", "canvas.edit_flip"})

#: #266 慢操作独立超时契约（ComfyArt SLOW_S=90s 模式）：慢视图工具与
#: generate.wait 的 600s 契约分开，单独 90s 超时；超时返回 isError 带下一步建议。
SLOW_TOOL_TIMEOUTS = {"canvas.view": VIEW_SLOW_TIMEOUT_S, "canvas.view_media": VIEW_SLOW_TIMEOUT_S}

EXTERNAL_AGENT_NODE_TYPE = "smart-agent"


class McpToolError(Exception):
    """User-facing tool execution error (rendered as an MCP isError result)."""


# ---------------------------------------------------------------------------
# Project arbitration (ADR 0022: ?project= + client_id 注册表 + 三级仲裁)
# ---------------------------------------------------------------------------


def _live_open_project_ids() -> list:
    """open_project_ids() minus projects that no longer exist in the store.

    Stale transport registrations (a harness that deletes its temp project
    without closing the WS, or a project deleted while a tab is still open)
    would otherwise hijack three-level arbitration and make every MCP tool
    call fail with 「项目不存在」 (409).
    """
    ids = open_project_ids()
    if not ids:
        return []
    store = _app_main.FIREFLY_PROJECT_STORE
    try:
        known = {str(p.get("id") or "") for p in store.list_projects()}
    except Exception:
        return ids
    return [pid for pid in ids if pid in known]


def resolve_project(explicit_project: str = "") -> str:
    """Three-level arbitration (ComfyArt api/mcp_commands.py `_target` parity).

    1. `?project=` explicit → the project must have an open canvas tab,
       otherwise error with "open the project first".
    2. not explicit + multiple projects open → error asking for `?project=`.
    3. not explicit + exactly one project open → use it.
    """
    explicit = str(explicit_project or "").strip()
    open_ids = _live_open_project_ids()
    if explicit:
        if explicit not in open_ids:
            raise ValueError(
                f"项目 {explicit} 没有打开的画布标签页；请先在浏览器中打开该项目，"
                f"或改用 ?project= 指定一个已打开的项目。"
            )
        return explicit
    if len(open_ids) > 1:
        raise ValueError(
            "多个项目同时打开，无法确定目标项目；请在 MCP 端点 URL 中显式指定 ?project=<project_id>。"
        )
    if len(open_ids) == 1:
        return open_ids[0]
    raise ValueError("没有打开的画布标签页；请先在浏览器中打开一个项目。")


async def ensure_external_agent_node(project_id: str, mcp_session_id: str) -> str:
    """Materialize the external MCP session as an Agent node (ADR 0022, Q12-a).

    Reuses the canvas' MCP-bound smart-agent node when present (rebinding it
    to the current session), otherwise creates one. Returns the node id —
    this node is the confirmation-gate binding for destructive tools and
    makes external-session activity observable on the canvas.
    """
    canvas = _open_canvas(project_id)
    for node in canvas.get("nodes", []):
        if (
            isinstance(node, dict)
            and str(node.get("type", "")) == EXTERNAL_AGENT_NODE_TYPE
            and node.get("mcp_session")
        ):
            if node.get("mcp_session") != mcp_session_id:
                node["mcp_session"] = mcp_session_id
                node["updated_at"] = _app_main.now_ms()
                await _apply_mutation_broadcast(project_id, [{"op": "upsert_node", "node": node}])
            return str(node.get("id", ""))
    node = {
        "id": f"mcp-agent-{uuid.uuid4().hex[:12]}",
        "type": EXTERNAL_AGENT_NODE_TYPE,
        "title": "MCP 外部 Agent",
        "x": 80,
        "y": 80,
        "mcp_session": mcp_session_id,
        "created_at": _app_main.now_ms(),
    }
    await _apply_mutation_broadcast(project_id, [{"op": "upsert_node", "node": node}])
    return node["id"]


async def _apply_mutation_broadcast(project_id: str, mutations: list) -> None:
    """Apply mutations with the shared retry+broadcast helper."""
    from app.routers.api_agent_tools import _apply_mutations

    await _apply_mutations(project_id, mutations)


async def _confirm_gate(project_id: str, agent_node_id: str, tool: str, args: dict, message: str) -> None:
    """Materialized confirmation gate (ADR 0022): destructive MCP tool calls
    block until the user approves/rejects on the canvas (default 60s).

    The confirm event is projected to the materialized Agent node's session;
    a human approves through the canvas TUI of the owning tab. On timeout the
    message distinguishes "tab closed" from "confirmation may still be
    pending" per the ADR timeout contract.
    """
    decision = await request_confirmation_core(
        project_id, agent_node_id, tool, args, message, CONFIRM_TIMEOUT_SECONDS
    )
    if decision.get("approved"):
        return
    reason = decision.get("reason")
    if reason == "rejected":
        raise McpToolError(f"用户在画布上拒绝了操作：{tool}")
    raise McpToolError(
        f"确认超时（{int(CONFIRM_TIMEOUT_SECONDS)} 秒）：{tool} 未获确认。"
        "画布标签页可能已关闭，或确认仍在等待——先调用 canvas.list_nodes 查看画布状态，再决定是否重试。"
    )


# ---------------------------------------------------------------------------
# Event recorder (dirty flag + 3s timer; in-memory v1, pluggable sink)
# ---------------------------------------------------------------------------


class McpEventRecorder:
    """Aggregate MCP tool events with a dirty flag + persist-interval timer.

    ComfyArt api/bot_turns.py:26 (_PERSIST_INTERVAL_S=3.0) parity. v1 keeps
    events in memory; a pluggable ``sink`` (e.g. ops.db via #250) receives
    the flushed batches.
    """

    def __init__(self, sink: Optional[Callable[[list], Any]] = None, persist_interval_s: float = PERSIST_INTERVAL_S):
        self._sink = sink
        self._persist_interval_s = float(persist_interval_s)
        self._buffer: list = []
        self._dirty = False
        self._scheduled = False
        self.flushed_batches: list = []

    def record(self, event: dict) -> None:
        """Buffer one event; the batch flushes after persist_interval_s."""
        event = dict(event)
        event.setdefault("ts", time.time())
        self._buffer.append(event)
        self._dirty = True
        self._maybe_schedule()

    def _maybe_schedule(self) -> None:
        if self._scheduled:
            return
        self._scheduled = True
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # no event loop (sync context) — flush() must be called
        loop.call_later(self._persist_interval_s, self._flush_sync)

    def _flush_sync(self) -> None:
        self._scheduled = False
        if not self._dirty:
            return
        batch = self._buffer
        self._buffer = []
        self._dirty = False
        if self._sink is not None:
            try:
                self._sink(batch)
            except Exception:
                logger.exception("MCP event sink failed")
        self.flushed_batches.append(batch)

    def flush(self) -> list:
        """Force an immediate flush; returns the flushed batch ([] when clean)."""
        before = len(self.flushed_batches)
        self._flush_sync()
        return self.flushed_batches[before] if len(self.flushed_batches) > before else []


# Module-level recorder singleton (v1: no-op persistence; #250 wires ops.db).
mcp_event_recorder = McpEventRecorder()


# ---------------------------------------------------------------------------
# Tool surface — the canvas agent tools minus the fake canvas.get_selection
# (ADR 0022 known gap: 摘除; semantics unchanged from canvas-tools.ts). 19 tools.
# ---------------------------------------------------------------------------

#: #266 公共参数片段（票面规范：公共参数片段 + additionalProperties:false，
#: ComfyArt §59.4 建议 5 的 _ID/_IDS/_REGION/_PLACE 模式在本仓的对应实现）
_PARAM_MAX_PX = {
    "type": "integer",
    "description": f"最长边像素，默认 {DEFAULT_VIEW_MAX_PX}，上限 {MAX_VIEW_MAX_PX}（ComfyArt maxPx 契约）",
    "minimum": 64,
    "maximum": MAX_VIEW_MAX_PX,
}
_PARAM_NODE_IDS = {"type": "array", "items": {"type": "string"}, "description": "节点 ID 列表"}
_PARAM_RECT = {
    "type": "object",
    "description": "选区（原图像素坐标，x/y 左上角 + width/height）",
    "properties": {
        "x": {"type": "number"}, "y": {"type": "number"},
        "width": {"type": "number"}, "height": {"type": "number"},
    },
    "required": ["x", "y", "width", "height"],
    "additionalProperties": False,
}

TOOLS = [
    {"name": "canvas.list_nodes", "description": "列出画布上所有节点（含连边摘要）", "inputSchema": {"type": "object", "properties": {}, "required": []}},
    {"name": "canvas.get_node", "description": "获取单个节点详情", "inputSchema": {"type": "object", "properties": {"node_id": {"type": "string"}}, "required": ["node_id"]}},
    {"name": "canvas.create_node", "description": "创建节点（node 对象需含 type；可含 id/title/x/y/prompt 等）", "inputSchema": {"type": "object", "properties": {"node": {"type": "object"}}, "required": ["node"]}},
    {"name": "canvas.update_node", "description": "更新节点属性（updates 为扁平字段对象）", "inputSchema": {"type": "object", "properties": {"node_id": {"type": "string"}, "updates": {"type": "object"}}, "required": ["node_id", "updates"]}},
    {"name": "canvas.delete_node", "description": "删除节点（破坏性：需画布确认门批准，阻塞最长 60s）", "inputSchema": {"type": "object", "properties": {"node_id": {"type": "string"}}, "required": ["node_id"]}},
    {"name": "canvas.connect", "description": "创建节点连线（edge 对象需含 from/to）", "inputSchema": {"type": "object", "properties": {"edge": {"type": "object"}}, "required": ["edge"]}},
    {"name": "canvas.disconnect", "description": "删除节点连线", "inputSchema": {"type": "object", "properties": {"from_node": {"type": "string"}, "to_node": {"type": "string"}}, "required": ["from_node", "to_node"]}},
    # #266 只读视图工具（ComfyArt §59.4 语义；只读不走确认门——#242 语义：写操作才需确认）
    {"name": "canvas.view", "description": "整页/选区节点媒体导出为一张图（contact sheet，base64 PNG）喂模型（只读）。慢操作独立 90s 超时契约：客户端请求超时应设为 >= 90s；超时时先调低 max_px 或改用 selection 缩小批量，再重试", "inputSchema": {"type": "object", "properties": {"scope": {"type": "string", "enum": ["page", "selection"], "description": "page=整页（所有带媒体节点）；selection=指定 node_ids"}, "node_ids": _PARAM_NODE_IDS, "max_px": _PARAM_MAX_PX}, "required": ["scope"], "additionalProperties": False}},
    {"name": "canvas.view_media", "description": "查看单节点媒体：图像下缩放，视频抽帧拼图（contact sheet）（只读）。慢操作独立 90s 超时契约：客户端请求超时应设为 >= 90s", "inputSchema": {"type": "object", "properties": {"node_id": {"type": "string"}, "max_px": _PARAM_MAX_PX, "frames": {"type": "integer", "description": "视频抽帧数 1..16，默认 4", "minimum": 1, "maximum": 16}}, "required": ["node_id"], "additionalProperties": False}},
    # #266 edit_* 首批（包装 canvas-image-editor.js 已有能力）：本地 PIL，写操作走确认门（#242 语义）
    {"name": "canvas.edit_crop", "description": "裁剪节点图像选区并覆写节点图（本地 PIL，不消耗外部生成；写操作：需画布确认门批准，阻塞最长 60s，原资源保留在资源库可恢复）", "inputSchema": {"type": "object", "properties": {"node_id": {"type": "string"}, "rect": _PARAM_RECT}, "required": ["node_id", "rect"], "additionalProperties": False}},
    # #266 canvas.batch_edit（§59.3）：选中 N 节点 + 一句 prompt = N 次生成（调既有生成通路，非本地处理；结果放各自旁边）
    {"name": "canvas.batch_edit", "description": "批量生成：把同一句 prompt 写入最多 10 个选中节点并逐节点触发一次生成（消耗外部生成任务，区别于 edit_* 的本地处理；结果放各自旁边 = 各节点自身输出）。返回各节点的 task_id，用 generate.status 轮询 / generate.wait 等待完成", "inputSchema": {"type": "object", "properties": {"node_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 10, "description": "节点 ID 列表（1..10）"}, "prompt": {"type": "string", "minLength": 1, "description": "批量 prompt，写入每个节点"}}, "required": ["node_ids", "prompt"], "additionalProperties": False}},
    # #266b edit_* 再扩两项（包装 canvas-image-editor.js 已有能力：rotate 是既有能力，flip 同族本地变换）：本地 PIL，写操作走确认门（#242 语义）
    {"name": "canvas.edit_rotate", "description": "旋转节点图像（90/180/270 逆时针）并覆写节点图（本地 PIL，不消耗外部生成；写操作：需画布确认门批准，阻塞最长 60s，原资源保留在资源库可恢复）", "inputSchema": {"type": "object", "properties": {"node_id": {"type": "string"}, "angle": {"type": "integer", "enum": [90, 180, 270], "description": "逆时针旋转角度，90/180/270 之一"}}, "required": ["node_id", "angle"], "additionalProperties": False}},
    {"name": "canvas.edit_flip", "description": "镜像节点图像（horizontal 左右 / vertical 上下）并覆写节点图（本地 PIL，不消耗外部生成；写操作：需画布确认门批准，阻塞最长 60s，原资源保留在资源库可恢复）", "inputSchema": {"type": "object", "properties": {"node_id": {"type": "string"}, "direction": {"type": "string", "enum": ["horizontal", "vertical"], "description": "horizontal=左右镜像，vertical=上下镜像"}}, "required": ["node_id", "direction"], "additionalProperties": False}},
    {"name": "generate.trigger", "description": "在节点上触发生成，立即返回 task_id（节点需已有 prompt）", "inputSchema": {"type": "object", "properties": {"node_id": {"type": "string"}}, "required": ["node_id"]}},
    {"name": "generate.status", "description": "查询生成任务状态", "inputSchema": {"type": "object", "properties": {"task_id": {"type": "string"}}, "required": ["task_id"]}},
    {"name": "generate.wait", "description": "等待生成任务完成。可阻塞数分钟：timeout_seconds 1..600（默认 120）；客户端请求超时必须设为 >= 600s（ADR 0022 超时契约）", "inputSchema": {"type": "object", "properties": {"task_id": {"type": "string"}, "timeout_seconds": {"type": "number", "description": "1..600，默认 120"}}, "required": ["task_id"]}},
    {"name": "media.upload", "description": "导入本机媒体文件到项目资源库（file_path 为服务器本机路径）", "inputSchema": {"type": "object", "properties": {"file_path": {"type": "string"}}, "required": ["file_path"]}},
    {"name": "media.list", "description": "列出项目媒体资源", "inputSchema": {"type": "object", "properties": {}, "required": []}},
    {"name": "media.delete", "description": "删除媒体资源（破坏性：需画布确认门批准，阻塞最长 60s）", "inputSchema": {"type": "object", "properties": {"asset_id": {"type": "string"}}, "required": ["asset_id"]}},
    {"name": "viewport.focus", "description": "画布视口聚焦到节点（定向下发给打开该项目的标签页）", "inputSchema": {"type": "object", "properties": {"node_id": {"type": "string"}}, "required": ["node_id"]}},
    {"name": "viewport.zoom", "description": "设置画布缩放级别（定向下发给打开该项目的标签页）", "inputSchema": {"type": "object", "properties": {"level": {"type": "number"}}, "required": ["level"]}},
    {"name": "viewport.state", "description": "读取当前视口状态（最近一次 focus/zoom 命令）", "inputSchema": {"type": "object", "properties": {}, "required": []}},
]

TOOL_NAMES = {t["name"] for t in TOOLS}


async def _impl_list_nodes(project_id, agent_node_id, args):
    return await list_nodes(project_id)


async def _impl_get_node(project_id, agent_node_id, args):
    return await get_node(project_id, str(args.get("node_id") or ""))


async def _impl_create_node(project_id, agent_node_id, args):
    return await create_node_core(project_id, args.get("node") or {})


async def _impl_update_node(project_id, agent_node_id, args):
    return await update_node_core(project_id, str(args.get("node_id") or ""), args.get("updates") or {})


async def _impl_delete_node(project_id, agent_node_id, args):
    node_id = str(args.get("node_id") or "")
    await _confirm_gate(project_id, agent_node_id, "canvas.delete_node", args, f"删除画布节点 {node_id}")
    return await delete_node(project_id, node_id)


async def _impl_connect(project_id, agent_node_id, args):
    return await create_edge_core(project_id, args.get("edge") or {})


async def _impl_disconnect(project_id, agent_node_id, args):
    return await delete_edge(project_id, str(args.get("from_node") or ""), str(args.get("to_node") or ""))


async def _impl_trigger(project_id, agent_node_id, args):
    return await trigger_generation_core(project_id, str(args.get("node_id") or ""))


async def _impl_status(project_id, agent_node_id, args):
    return await get_task_status(project_id, str(args.get("task_id") or ""))


async def _impl_wait(project_id, agent_node_id, args):
    timeout = args.get("timeout_seconds", 120)
    try:
        timeout = int(timeout)
    except (TypeError, ValueError):
        timeout = 120
    timeout = max(1, min(600, timeout))  # ADR 0022 超时契约：服务端上限 600s
    return await wait_for_task_core(project_id, str(args.get("task_id") or ""), timeout)


async def _impl_media_upload(project_id, agent_node_id, args):
    return await upload_media_core(project_id, str(args.get("file_path") or ""))


async def _impl_media_list(project_id, agent_node_id, args):
    return await list_media(project_id)


async def _impl_media_delete(project_id, agent_node_id, args):
    asset_id = str(args.get("asset_id") or "")
    await _confirm_gate(project_id, agent_node_id, "media.delete", args, f"删除媒体资源 {asset_id}")
    return await delete_media(project_id, asset_id)


async def _impl_viewport_focus(project_id, agent_node_id, args):
    return await focus_node_core(project_id, str(args.get("node_id") or ""))


async def _impl_viewport_zoom(project_id, agent_node_id, args):
    return await set_zoom_core(project_id, args.get("level"))


async def _impl_viewport_state(project_id, agent_node_id, args):
    return await get_viewport_state(project_id)


async def _impl_view(project_id, agent_node_id, args):
    try:
        return await canvas_view_core(project_id, str(args.get("scope") or "page"), args.get("node_ids"), args.get("max_px"))
    except HTTPException as exc:
        raise ValueError(str(exc.detail)) from exc


async def _impl_view_media(project_id, agent_node_id, args):
    try:
        return await view_media_core(project_id, str(args.get("node_id") or ""), args.get("max_px"), args.get("frames"))
    except HTTPException as exc:
        raise ValueError(str(exc.detail)) from exc


async def _impl_edit_crop(project_id, agent_node_id, args):
    # #242 确认门语义：写操作（覆写节点图）必须过画布确认门
    node_id = str(args.get("node_id") or "")
    await _confirm_gate(project_id, agent_node_id, "canvas.edit_crop", args, f"裁剪节点 {node_id} 的图像并覆写节点图（原资源保留在资源库）")
    try:
        return await crop_node_image_core(project_id, node_id, args.get("rect"))
    except HTTPException as exc:
        raise ValueError(str(exc.detail)) from exc


async def _impl_batch_edit(project_id, agent_node_id, args):
    # #266 §59.3：N 节点 + 一句 prompt = N 次生成（调既有生成通路；与 generate.trigger 一致不走确认门）
    try:
        return await batch_edit_core(project_id, args.get("node_ids"), args.get("prompt"))
    except HTTPException as exc:
        raise ValueError(str(exc.detail)) from exc


async def _impl_edit_rotate(project_id, agent_node_id, args):
    # #266b：#242 确认门语义：写操作（覆写节点图）必须过画布确认门（gate 先行，同 _impl_edit_crop 顺序）
    node_id = str(args.get("node_id") or "")
    await _confirm_gate(project_id, agent_node_id, "canvas.edit_rotate", args, f"旋转节点 {node_id} 的图像并覆写节点图（原资源保留在资源库）")
    try:
        return await rotate_node_image_core(project_id, node_id, args.get("angle"))
    except HTTPException as exc:
        raise ValueError(str(exc.detail)) from exc


async def _impl_edit_flip(project_id, agent_node_id, args):
    # #266b：#242 确认门语义：写操作（覆写节点图）必须过画布确认门（gate 先行，同 _impl_edit_crop 顺序）
    node_id = str(args.get("node_id") or "")
    await _confirm_gate(project_id, agent_node_id, "canvas.edit_flip", args, f"镜像节点 {node_id} 的图像并覆写节点图（原资源保留在资源库）")
    try:
        return await flip_node_image_core(project_id, node_id, args.get("direction"))
    except HTTPException as exc:
        raise ValueError(str(exc.detail)) from exc


TOOL_IMPLS = {
    "canvas.list_nodes": _impl_list_nodes,
    "canvas.get_node": _impl_get_node,
    "canvas.create_node": _impl_create_node,
    "canvas.update_node": _impl_update_node,
    "canvas.delete_node": _impl_delete_node,
    "canvas.connect": _impl_connect,
    "canvas.disconnect": _impl_disconnect,
    "generate.trigger": _impl_trigger,
    "generate.status": _impl_status,
    "generate.wait": _impl_wait,
    "media.upload": _impl_media_upload,
    "media.list": _impl_media_list,
    "media.delete": _impl_media_delete,
    "viewport.focus": _impl_viewport_focus,
    "viewport.zoom": _impl_viewport_zoom,
    "viewport.state": _impl_viewport_state,
    "canvas.view": _impl_view,
    "canvas.view_media": _impl_view_media,
    "canvas.edit_crop": _impl_edit_crop,
    "canvas.edit_rotate": _impl_edit_rotate,
    "canvas.edit_flip": _impl_edit_flip,
    "canvas.batch_edit": _impl_batch_edit,
}


# ---------------------------------------------------------------------------
# HTTP endpoint (streamable-HTTP subset, pure JSON)
# ---------------------------------------------------------------------------

router = APIRouter(tags=["mcp"], dependencies=[Depends(ensure_agent_same_origin)])


def _session_id_from(request: Request) -> str:
    provided = (request.headers.get(SESSION_HEADER) or "").strip()
    return provided or uuid.uuid4().hex


def _rpc_response(status_code: int, msg_id, sid: str, *, result: Any = None, error: Optional[dict] = None) -> JSONResponse:
    body = {"jsonrpc": "2.0", "id": msg_id}
    if error is not None:
        body["error"] = error
    else:
        body["result"] = result
    return JSONResponse(status_code=status_code, content=body, headers={SESSION_HEADER: sid})


def _initialize_response(payload: dict, msg_id, sid: str) -> JSONResponse:
    params = payload.get("params") or {}
    client_version = str(params.get("protocolVersion") or DEFAULT_PROTOCOL_VERSION)
    negotiated = client_version if client_version in SUPPORTED_PROTOCOL_VERSIONS else DEFAULT_PROTOCOL_VERSION
    result = {
        "protocolVersion": negotiated,
        "serverInfo": {"name": "firefly-canvas", "version": "1.0.0"},
        "capabilities": {"tools": {"listChanged": False}},
    }
    return _rpc_response(200, msg_id, sid, result=result)


async def _tools_call(payload: dict, msg_id, sid: str, request: Request) -> Response:
    params = payload.get("params") or {}
    if not isinstance(params, dict):
        params = {}
    name = str(params.get("name") or "")
    args = params.get("args")
    if not isinstance(args, dict):
        args = {}
    if name not in TOOL_IMPLS:
        # MCP TS SDK convention: unknown tool → tool result with isError,
        # so the calling agent can recover (not a JSON-RPC error).
        mcp_event_recorder.record({"session": sid, "tool": name, "ok": False, "error": f"Unknown tool: {name}"})
        return _rpc_response(
            200, msg_id, sid,
            result={"content": [{"type": "text", "text": f"Unknown tool: {name}"}], "isError": True},
        )
    started = time.time()
    slow_timeout = SLOW_TOOL_TIMEOUTS.get(name)
    try:
        project_id = resolve_project(str(request.query_params.get("project") or ""))
        node_id = await ensure_external_agent_node(project_id, sid)
        if slow_timeout is not None:
            # #266 慢操作独立超时契约（90s，区别于 generate.wait 的 600s）
            result = await asyncio.wait_for(TOOL_IMPLS[name](project_id, node_id, args), slow_timeout)
        else:
            result = await TOOL_IMPLS[name](project_id, node_id, args)
    except asyncio.TimeoutError:
        mcp_event_recorder.record({"session": sid, "tool": name, "ok": False, "error": f"timeout after {slow_timeout}s"})
        return _rpc_response(
            200, msg_id, sid,
            result={"content": [{"type": "text", "text": f"工具 {name} 超时（慢操作契约 {int(slow_timeout)} 秒）。先调用 canvas.list_nodes 查看画布状态，再调低 max_px 或改用 selection 作用域缩小批量后重试。"}], "isError": True},
        )
    except (McpToolError, ValueError) as exc:
        mcp_event_recorder.record({"session": sid, "tool": name, "ok": False, "error": str(exc)})
        return _rpc_response(200, msg_id, sid, result={"content": [{"type": "text", "text": str(exc)}], "isError": True})
    except Exception as exc:
        logger.exception("MCP tool %s failed", name)
        mcp_event_recorder.record({"session": sid, "tool": name, "ok": False, "error": str(exc)})
        return _rpc_response(
            200, msg_id, sid,
            result={"content": [{"type": "text", "text": f"工具 {name} 执行失败：{exc}"}], "isError": True},
        )
    mcp_event_recorder.record({
        "session": sid,
        "tool": name,
        "project": project_id,
        "ok": True,
        "duration_ms": int((time.time() - started) * 1000),
    })
    return _rpc_response(
        200, msg_id, sid,
        result={"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}], "isError": False},
    )


@router.post("/api/mcp")
async def mcp_endpoint(request: Request) -> Response:
    """MCP streamable-HTTP entry point (one JSON-RPC 2.0 message per POST)."""
    sid = _session_id_from(request)
    try:
        payload = await request.json()
    except Exception:
        return _rpc_response(400, None, sid, error={"code": -32700, "message": "Parse error"})

    if isinstance(payload, list):
        # ComfyArt api/mcp.py:117-124 parity（issue #243 spec 合规验收点）
        return _rpc_response(400, None, sid, error={"code": -32600, "message": "MCP batch requests are not supported"})

    if not isinstance(payload, dict) or payload.get("jsonrpc") != "2.0" or "method" not in payload:
        msg_id = payload.get("id") if isinstance(payload, dict) else None
        return _rpc_response(400, msg_id, sid, error={"code": -32600, "message": "Invalid JSON-RPC 2.0 message"})

    method = str(payload.get("method", ""))
    msg_id = payload.get("id") if "id" in payload else None
    is_notification = "id" not in payload

    try:
        if method == "initialize":
            return _initialize_response(payload, msg_id, sid)
        if method == "ping":
            if is_notification:
                return Response(status_code=202, headers={SESSION_HEADER: sid})
            return _rpc_response(200, msg_id, sid, result={})
        if method == "tools/list":
            if is_notification:
                return Response(status_code=202, headers={SESSION_HEADER: sid})
            return _rpc_response(200, msg_id, sid, result={"tools": TOOLS})
        if method == "tools/call":
            if is_notification:
                return Response(status_code=202, headers={SESSION_HEADER: sid})
            return await _tools_call(payload, msg_id, sid, request)
        if method.startswith("notifications/"):
            return Response(status_code=202, headers={SESSION_HEADER: sid})
        if is_notification:
            # Unknown notification methods are ignored per JSON-RPC.
            return Response(status_code=202, headers={SESSION_HEADER: sid})
        return _rpc_response(200, msg_id, sid, error={"code": -32601, "message": f"Method not found: {method}"})
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("MCP endpoint internal error")
        if is_notification:
            return Response(status_code=202, headers={SESSION_HEADER: sid})
        return _rpc_response(200, msg_id, sid, error={"code": -32603, "message": f"Internal error: {exc}"})


@router.get("/api/mcp")
async def mcp_endpoint_get() -> Response:
    """GET is not supported: the endpoint implements the pure-JSON subset
    (no SSE streams) per ADR 0022."""
    return Response(
        status_code=405,
        headers={"Allow": "POST"},
        media_type="application/json",
        content=json.dumps({"error": "Method Not Allowed", "message": "SSE streams are not supported; use POST /api/mcp"}),
    )
