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
    outpaint_node_image_core,
    erase_node_image_core,
    batch_edit_core,
    create_edge_core,
    delete_edge_core,
    delete_media_core,
    delete_node_core,
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
from app.services.canvas_view import VIEW_SLOW_TIMEOUT_S

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Protocol constants
# ---------------------------------------------------------------------------

SESSION_HEADER = "Mcp-Session-Id"
SUPPORTED_PROTOCOL_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18")
DEFAULT_PROTOCOL_VERSION = "2025-06-18"

#: Confirmation gate blocking wait (#357: agent 写操作确认等待 90s > 60s，
#: 与 REST 侧 AGENT_WRITE_CONFIRM_TIMEOUT_SECONDS 同口径；ADR 0022 原 default 60s 已被 #357 覆写)。
CONFIRM_TIMEOUT_SECONDS = 90.0

#: Event aggregation interval — dirty flag + 3s timer, ComfyArt
#: api/bot_turns.py:26 (_PERSIST_INTERVAL_S=3.0) parity (issue #243 追加细节 3).
PERSIST_INTERVAL_S = 3.0

#: Destructive MCP tools that must pass the materialized confirmation gate.
CONFIRMATION_GATED_TOOLS = frozenset({"canvas.delete_node", "canvas.disconnect", "media.delete", "canvas.edit_crop", "canvas.edit_rotate", "canvas.edit_flip", "canvas.edit_outpaint", "canvas.edit_erase"})

#: #266 慢操作独立超时契约（ComfyArt SLOW_S=90s 模式）：慢视图工具与
#: generate.wait 的 600s 契约分开，单独 90s 超时；超时返回 isError 带下一步建议。
SLOW_TOOL_TIMEOUTS = {"canvas.view": VIEW_SLOW_TIMEOUT_S, "canvas.view_media": VIEW_SLOW_TIMEOUT_S}

EXTERNAL_AGENT_NODE_TYPE = "smart-agent"


class McpToolError(Exception):
    """User-facing tool execution error (rendered as an MCP isError result)."""


def _tool_result_content_blocks(result) -> list:
    """#352: 视图类工具的 image_data_url 拆成 MCP image 内容块，文本块仅含元数据。

    canvas.view / canvas.view_media 返回 {"image_data_url": "data:image/png;base64,…", …}；
    按 MCP 约定拆为 {"type":"image","data","mimeType"} + JSON 元数据 text 块，
    避免把几十 KB–MB 的 base64 塞进 text 撑爆模型上下文。
    其余工具保持原样（单 text 块 JSON）。
    """
    if isinstance(result, dict) and "image_data_url" in result:
        data_url = str(result.get("image_data_url") or "")
        header, sep, b64 = data_url.partition(",")
        mime = "image/png"
        if sep and header.startswith("data:") and ";" in header:
            mime = header[5:].split(";")[0] or "image/png"
        meta = {k: v for k, v in result.items() if k != "image_data_url"}
        blocks = []
        if sep:
            blocks.append({"type": "image", "data": b64, "mimeType": mime})
        blocks.append({"type": "text", "text": json.dumps(meta, ensure_ascii=False)})
        return blocks
    return [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]


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
    block until the user approves/rejects on the canvas (#357 起为 90s，
    见 CONFIRM_TIMEOUT_SECONDS；ADR 原文 default 60s 已被覆写)。

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
# Tool surface — 单一来源清单（#356，ADR 0022）：TOOLS 定义在
# app/services/agent_tools_manifest.py；pi 扩展（agent_extensions/canvas-tools.ts）
# 的工具注册段由 tools/gen_canvas_tools.py 从同一清单 codegen。此处只做 MCP 侧
# 实现绑定。清单改动会由 tests/test_tool_surface_consistency.py 的 codegen
# freshness 测试钉住（重跑生成器 diff 必须为空）。
# ---------------------------------------------------------------------------

from app.services.agent_tools_manifest import TOOLS, WRITE_TOOLS  # noqa: E402

TOOL_NAMES = {t["name"] for t in TOOLS}


async def _impl_list_nodes(project_id, agent_node_id, args):
    return await list_nodes(project_id)


async def _impl_get_node(project_id, agent_node_id, args):
    return await get_node(project_id, str(args.get("node_id") or ""))


async def _impl_create_node(project_id, agent_node_id, args):
    # #356 D11：平铺参数为主；旧 `node` 自由对象兼容一版（已弃用）。
    node = args.get("node")
    if not isinstance(node, dict) or not node:
        node = {k: v for k, v in args.items() if v is not None}
    return await create_node_core(project_id, node, agent_node_id=agent_node_id or "")


async def _impl_update_node(project_id, agent_node_id, args):
    # #356 D11：平铺字段为主；旧 `updates` 自由对象兼容一版（已弃用）。
    updates = args.get("updates")
    if not isinstance(updates, dict) or not updates:
        updates = {k: v for k, v in args.items() if k != "node_id" and v is not None}
    return await update_node_core(project_id, str(args.get("node_id") or ""), updates)


async def _impl_delete_node(project_id, agent_node_id, args):
    node_id = str(args.get("node_id") or "")
    await _confirm_gate(project_id, agent_node_id, "canvas.delete_node", args, f"删除画布节点 {node_id}")
    return await delete_node_core(project_id, node_id)


async def _impl_connect(project_id, agent_node_id, args):
    # #356 D12：平铺 from_node/to_node/kind 为主；旧 `edge` 对象兼容一版（已弃用）。
    edge = args.get("edge")
    if not isinstance(edge, dict) or not edge:
        edge = {"from": str(args.get("from_node") or ""), "to": str(args.get("to_node") or "")}
        if args.get("kind"):
            edge["kind"] = str(args["kind"])
    return await create_edge_core(project_id, edge)


async def _impl_disconnect(project_id, agent_node_id, args):
    # #357：删连线在 MCP 侧同样过确认门（REST 侧已在统一入口过门）。
    await _confirm_gate(project_id, agent_node_id, "canvas.delete_edge", args,
                        f"删除连线 {args.get('from_node')} → {args.get('to_node')}")
    return await delete_edge_core(project_id, str(args.get("from_node") or ""), str(args.get("to_node") or ""))


async def _impl_search_projects(project_id, agent_node_id, args):
    from app.routers.api_search import get_searcher
    query = str(args.get("query") or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="query required")
    limit = int(args.get("limit") or 50)
    store = _app_main.FIREFLY_PROJECT_STORE
    return await asyncio.to_thread(get_searcher(store).search, query, limit=limit)


async def _impl_trigger(project_id, agent_node_id, args):
    # #351：花钱工具走服务端确认门（cautious/默认必确认；autonomous 自动放行，
    # SAFETY_FLOOR 不含 generate——用户拍板「按自主级别走门」）+ 会话生成限额。
    await _confirm_gate(project_id, agent_node_id, "generate.trigger", args,
                        f"节点 {args.get('node_id', '')} 生成图片（消耗外部生成额度）")
    try:
        from app.routers.api_agent_tools import enforce_generation_limit
        enforce_generation_limit(project_id, agent_node_id, count=1)
    except HTTPException as exc:
        raise ValueError(str(exc.detail)) from exc
    try:
        return await trigger_generation_core(project_id, str(args.get("node_id") or ""))
    except HTTPException as exc:
        raise ValueError(str(exc.detail)) from exc


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
    return await delete_media_core(project_id, asset_id)


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
    # #266 §59.3：N 节点 + 一句 prompt = N 次生成（调既有生成通路）。
    # #351：改走确认门 + 按张数计会话生成限额（旧注释「不走门」作废）。
    node_ids = args.get("node_ids") or []
    await _confirm_gate(project_id, agent_node_id, "canvas.batch_edit", args,
                        f"批量生成 {len(node_ids)} 个节点（消耗外部生成额度）")
    try:
        from app.routers.api_agent_tools import enforce_generation_limit
        enforce_generation_limit(project_id, agent_node_id, count=max(1, len(node_ids)))
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


async def _impl_edit_outpaint(project_id, agent_node_id, args):
    # #289：#242 确认门语义：写操作（覆写节点图）必须过画布确认门（gate 先行，同 _impl_edit_crop 顺序）
    node_id = str(args.get("node_id") or "")
    await _confirm_gate(project_id, agent_node_id, "canvas.edit_outpaint", args, f"红底扩图节点 {node_id}：扩大画布（#ff0044 填充外区）并覆写节点图（原资源保留在资源库）")
    try:
        return await outpaint_node_image_core(project_id, node_id, args.get("outpaint_size"), args.get("offset"))
    except HTTPException as exc:
        raise ValueError(str(exc.detail)) from exc


async def _impl_edit_erase(project_id, agent_node_id, args):
    # #290: #242 确认门语义：写操作（覆写节点图）必须过画布确认门（gate 先行，同 _impl_edit_crop 顺序）
    node_id = str(args.get("node_id") or "")
    await _confirm_gate(project_id, agent_node_id, "canvas.edit_erase", args, f"擦除节点 {node_id} 选区图像并覆写节点图（原资源保留在资源库）")
    try:
        return await erase_node_image_core(project_id, node_id, args.get("rect"), args.get("prompt"))
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
    "canvas.search_projects": _impl_search_projects,
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
    "canvas.edit_outpaint": _impl_edit_outpaint,
    "canvas.edit_erase": _impl_edit_erase,
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
    # MCP 标准字段是 params.arguments；旧实现只读 params.args → 带参工具对
    # 标准客户端（claude/codex/SDK）一直静默收空 args（e2e 实抓：get_node
    # 永远「Node not found: 」空名）。兼容读两者，标准优先。
    args = params.get("arguments")
    if not isinstance(args, dict):
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
        # #350（ADR 0024 §3）：&node= 精确绑定归属节点——确认卡片/工具调用都落在
        # 发起节点上（票面：卡片曾落到看不见的外部节点）。只读工具绝不 upsert 画布
        # 节点；仅写类工具无 node= 时回退共享外部节点（无 node= 才用共享节点）。
        node_param = str(request.query_params.get("node") or "")
        if node_param:
            node_id = node_param
        elif name in WRITE_TOOLS:
            node_id = await ensure_external_agent_node(project_id, sid)
        else:
            node_id = ""
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
        result={"content": _tool_result_content_blocks(result), "isError": False},
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
