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
from functools import partial
import json
import logging
import sqlite3
import time
import uuid
from typing import Any, Callable, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from app.core import ensure_agent_same_origin
from app.services.agent_errors import (
    agent_error,
    agent_error_response_payload,
    format_agent_error_text,
    translate_detail,
)
from app.project_sessions import open_project_ids
from app.routers.api_agent_plan import plan_core
from app.routers.api_agent_tools import (
    _app_main,
    _find_node,
    _validate_project,
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
    list_nodes_core,
    request_confirmation_core,
    set_zoom_core,
    trigger_generation_core,
    update_node_core,
    upload_media_args,
    view_media_core,
    wait_for_task_core,
)
from app.services import agent_batch
from app.services.agent_node_state import ownership_fields
from app.services.agent_ops_context import agent_ops_scope
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
    from app.routers.api_agent_tools import _apply_mutations_from_snapshot

    _validate_project(project_id)
    # Mint once, not on each CAS attempt. A concurrent carrier creation is
    # detected in the next snapshot and reused instead of making a duplicate.
    new_node_id = f"mcp-agent-{uuid.uuid4().hex[:12]}"
    now = _app_main.now_ms()
    new_node = {
        "id": new_node_id,
        "type": EXTERNAL_AGENT_NODE_TYPE,
        "title": "MCP 外部 Agent",
        "x": 80,
        "y": 80,
        "mcp_session": mcp_session_id,
        "created_at": now,
        **ownership_fields(new_node_id),
    }
    bound_node_id = new_node_id

    def build_mutations(canvas):
        nonlocal bound_node_id
        for node in canvas.get("nodes", []):
            if (
                isinstance(node, dict)
                and str(node.get("type", "")) == EXTERNAL_AGENT_NODE_TYPE
                and node.get("mcp_session")
            ):
                bound_node_id = str(node.get("id", ""))
                if node.get("mcp_session") == mcp_session_id:
                    return []
                # Rebind only session metadata on the latest node. Never
                # reinitialize ownership/protection or replay a stale upsert.
                rebound = {**node, "mcp_session": mcp_session_id, "updated_at": now}
                return [{"op": "upsert_node", "node": rebound}]
        bound_node_id = new_node_id
        return [{"op": "upsert_node", "node": new_node}]

    await _apply_mutations_from_snapshot(project_id, build_mutations, trusted_agent_bindings=True)
    return bound_node_id


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

from app.services import agent_layout  # noqa: E402
from app.services.agent_tools_manifest import LAYOUT_TOOLS, PLAN_TOOLS, TOOLS, WRITE_TOOLS  # noqa: E402

TOOL_NAMES = {t["name"] for t in TOOLS}


async def _impl_list_nodes(project_id, agent_node_id, args):
    # None means omitted/all in the shared core; JSON null is not omission.
    if "fields" in args and args["fields"] is None:
        raise agent_error(400, "INVALID_ARGUMENT", "fields 必须是数组，不能为 null",
                          "省略 fields 返回全部；传 [] 只返回 summary。")
    return await list_nodes_core(
        project_id, group_id=args.get("group_id"), fields=args.get("fields"),
        created_by=args.get("created_by"),
    )


async def _impl_get_node(project_id, agent_node_id, args):
    return await get_node(project_id, str(args.get("node_id") or ""))


async def _impl_create_node(project_id, agent_node_id, args):
    # #356 D11：平铺参数为主；旧 `node` 自由对象兼容一版（已弃用）。
    node = args.get("node")
    if not isinstance(node, dict) or not node:
        node = {k: v for k, v in args.items() if v is not None}
    if "layoutFrame" in args:
        node = {**node, "layoutFrame": args["layoutFrame"]}
    # Presence is rejected by the core, even for null or mixed legacy/flat args.
    # Do not erase evidence of a reserved-field override while adapting shapes.
    node = {**node, **{key: args[key] for key in ownership_fields() if key in args}}
    return await create_node_core(project_id, node, agent_node_id=agent_node_id or "")


async def _impl_update_node(project_id, agent_node_id, args):
    if "layoutFrame" in args:
        raise agent_layout.invalid("layoutFrame is assigned only by canvas.group_create")
    # #356 D11：平铺字段为主；旧 `updates` 自由对象兼容一版（已弃用）。
    updates = args.get("updates")
    if not isinstance(updates, dict) or not updates:
        updates = {k: v for k, v in args.items() if k != "node_id" and (v is not None or k == "runSettings")}
    elif "runSettings" in args:
        raise HTTPException(400, "Do not mix flat runSettings with the legacy updates object")
    updates = {**updates, **{key: args[key] for key in ownership_fields() if key in args}}
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


async def _impl_place_media(project_id, agent_node_id, args):
    from app.routers.api_agent_tools import place_media_core
    return await place_media_core(project_id, args, agent_node_id=agent_node_id)


async def _impl_attach_refs(project_id, agent_node_id, args):
    from app.routers.api_agent_tools import attach_refs_core
    return await attach_refs_core(project_id, args, agent_node_id=agent_node_id)


async def _impl_set_ref_order(project_id, agent_node_id, args):
    from app.routers.api_agent_tools import set_ref_order_core
    return await set_ref_order_core(project_id, args, agent_node_id=agent_node_id)


async def _impl_search_projects(project_id, agent_node_id, args):
    from app.routers.api_search import get_searcher
    query = str(args.get("query") or "").strip()
    if not query:
        # #391：统一形状（状态码 400 不变），文案进 message，不再经通用翻译丢信息
        from app.services.agent_errors import agent_error
        raise agent_error(400, "INVALID_ARGUMENT", "query 不能为空",
                          "传 query 参数（search.projects 需要搜索词）")
    limit = int(args.get("limit") or 50)
    store = _app_main.FIREFLY_PROJECT_STORE
    return await asyncio.to_thread(get_searcher(store).search, query, limit=limit)


async def _prepare_trigger(project_id, args):
    from app.routers.api_agent_tools import prepare_generation
    from app.services.agent_refs import require_object
    require_object(args, "generate.trigger", required=("node_id",))
    return await prepare_generation(project_id, args["node_id"])


async def _impl_trigger(project_id, agent_node_id, args, *, plan=None, retry_caller=None):
    from app.routers.api_agent_tools import enforce_generation_limit
    if plan is None:
        plan = await _prepare_trigger(project_id, args)
    node_id = args["node_id"]
    count = plan.request.count
    await _confirm_gate(project_id, agent_node_id, "generate.trigger", {"node_id": node_id, "n": count},
                        f"节点 {node_id} 生成 {count} 张图片（消耗外部生成额度）")
    enforce_generation_limit(project_id, agent_node_id, count=count)
    return await trigger_generation_core(project_id, node_id, agent_node_id=agent_node_id or "", plan=plan,
                                         retry_caller=retry_caller)


async def _impl_retry(caller, args):
    from app.services.agent_generation_retry import execute
    return await execute(caller, args)


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
    return await upload_media_args(project_id, args)


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
    except HTTPException:
        raise  # #391：交给 tool/call 统一形状出口，不再 str() 压平


async def _impl_view_media(project_id, agent_node_id, args):
    try:
        return await view_media_core(project_id, str(args.get("node_id") or ""), args.get("max_px"), args.get("frames"))
    except HTTPException:
        raise  # #391：交给 tool/call 统一形状出口，不再 str() 压平


async def _impl_edit_crop(project_id, agent_node_id, args):
    # #242 确认门语义：写操作（覆写节点图）必须过画布确认门
    node_id = str(args.get("node_id") or "")
    await _confirm_gate(project_id, agent_node_id, "canvas.edit_crop", args, f"裁剪节点 {node_id} 的图像并覆写节点图（原资源保留在资源库）")
    try:
        return await crop_node_image_core(project_id, node_id, args.get("rect"))
    except HTTPException:
        raise  # #391：交给 tool/call 统一形状出口，不再 str() 压平


async def _impl_batch_prompt_edit(project_id, agent_node_id, args, *, binding=None):
    from app.routers.api_agent_tools import batch_prompt_edit_core
    from app.services.agent_batch_prompt import parse
    return await batch_prompt_edit_core(project_id, agent_node_id, parse(args), binding=binding)


async def _impl_batch_edit(project_id, agent_node_id, args):
    from app.routers.api_agent_tools import confirm_batch_prompt, enforce_generation_limit, legacy_batch_prompt_counts, prepare_batch_generation
    from app.services.agent_refs import require_object
    require_object(args, "batch_edit", required=("node_ids", "prompt"))
    node_ids = args["node_ids"]
    prompt, plans = await prepare_batch_generation(project_id, node_ids, args["prompt"])
    count = sum(plan.request.count for plan in plans)
    edited, human = legacy_batch_prompt_counts(plans, prompt)
    await confirm_batch_prompt(project_id, agent_node_id, "canvas.batch_edit", edited, human, images=count)
    enforce_generation_limit(project_id, agent_node_id, count=count)
    return await batch_edit_core(project_id, node_ids, prompt, agent_node_id=agent_node_id or "", plans=plans)


async def _impl_edit_rotate(project_id, agent_node_id, args):
    # #266b：#242 确认门语义：写操作（覆写节点图）必须过画布确认门（gate 先行，同 _impl_edit_crop 顺序）
    node_id = str(args.get("node_id") or "")
    await _confirm_gate(project_id, agent_node_id, "canvas.edit_rotate", args, f"旋转节点 {node_id} 的图像并覆写节点图（原资源保留在资源库）")
    try:
        return await rotate_node_image_core(project_id, node_id, args.get("angle"))
    except HTTPException:
        raise  # #391：交给 tool/call 统一形状出口，不再 str() 压平


async def _impl_edit_flip(project_id, agent_node_id, args):
    # #266b：#242 确认门语义：写操作（覆写节点图）必须过画布确认门（gate 先行，同 _impl_edit_crop 顺序）
    node_id = str(args.get("node_id") or "")
    await _confirm_gate(project_id, agent_node_id, "canvas.edit_flip", args, f"镜像节点 {node_id} 的图像并覆写节点图（原资源保留在资源库）")
    try:
        return await flip_node_image_core(project_id, node_id, args.get("direction"))
    except HTTPException:
        raise  # #391：交给 tool/call 统一形状出口，不再 str() 压平


async def _impl_edit_outpaint(project_id, agent_node_id, args):
    # #289：#242 确认门语义：写操作（覆写节点图）必须过画布确认门（gate 先行，同 _impl_edit_crop 顺序）
    node_id = str(args.get("node_id") or "")
    await _confirm_gate(project_id, agent_node_id, "canvas.edit_outpaint", args, f"红底扩图节点 {node_id}：扩大画布（#ff0044 填充外区）并覆写节点图（原资源保留在资源库）")
    try:
        return await outpaint_node_image_core(project_id, node_id, args.get("outpaint_size"), args.get("offset"))
    except HTTPException:
        raise  # #391：交给 tool/call 统一形状出口，不再 str() 压平


async def _impl_edit_erase(project_id, agent_node_id, args):
    # #290: #242 确认门语义：写操作（覆写节点图）必须过画布确认门（gate 先行，同 _impl_edit_crop 顺序）
    node_id = str(args.get("node_id") or "")
    await _confirm_gate(project_id, agent_node_id, "canvas.edit_erase", args, f"擦除节点 {node_id} 选区图像并覆写节点图（原资源保留在资源库）")
    try:
        return await erase_node_image_core(project_id, node_id, args.get("rect"), args.get("prompt"))
    except HTTPException:
        raise  # #391：交给 tool/call 统一形状出口，不再 str() 压平


async def _impl_ops_list(project_id, agent_node_id, args):
    from app.routers.api_agent_ops import list_ops_core
    return await list_ops_core(project_id, args)


async def _impl_ops_undo(project_id, agent_node_id, args):
    from app.routers.api_agent_ops import undo_ops_core
    if "turn_id" in args and args["turn_id"] is None:
        raise HTTPException(status_code=400, detail={
            "code": "INVALID_ARGUMENT", "message": "未提供轮次时不支持按轮撤销。",
            "hint": "调用 ops.list 并按 op_id 逐条撤销。", "retryable": False,
        })
    return await undo_ops_core(project_id, args)


async def _impl_layout(project_id, agent_node_id, args, *, tool, hosted=False):
    from app.routers.api_agent_tools import layout_tool_core
    return await layout_tool_core(project_id, agent_node_id, tool, args, hosted=hosted)


_MEDIA_REF_TOOL_IMPLS = {
    "canvas.place_media": _impl_place_media,
    "canvas.attach_refs": _impl_attach_refs,
    "canvas.set_ref_order": _impl_set_ref_order,
}

TOOL_IMPLS = {
    "ops.list": _impl_ops_list,
    "ops.undo": _impl_ops_undo,
    "canvas.list_nodes": _impl_list_nodes,
    "canvas.get_node": _impl_get_node,
    "canvas.create_node": _impl_create_node,
    "canvas.update_node": _impl_update_node,
    "canvas.delete_node": _impl_delete_node,
    "canvas.connect": _impl_connect,
    "canvas.disconnect": _impl_disconnect,
    **_MEDIA_REF_TOOL_IMPLS,
    **{tool: partial(_impl_layout, tool=tool) for tool in LAYOUT_TOOLS},
    "canvas.search_projects": _impl_search_projects,
    "generate.trigger": _impl_trigger,
    "generate.retry": _impl_retry,
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
    "canvas.batch_prompt_edit": _impl_batch_prompt_edit,
    "canvas.batch_edit": _impl_batch_edit,
}

TOOL_IMPLS.update({name: partial(plan_core, tool=name) for name in PLAN_TOOLS})
TOOL_IMPLS.update({name: partial(agent_batch.call, name) for name in agent_batch.TOOLS})


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


def _mcp_error_result(name: str, detail: dict) -> dict:
    """#391：MCP isError 统一形状——text 单行可读，structuredContent 结构化。
    与 REST 共用 translate_detail，两路同形（spec §错误形状）。"""
    return {
        "content": [{"type": "text", "text": format_agent_error_text(detail)}],
        "isError": True,
        "structuredContent": detail,
    }


async def _tools_call(payload: dict, msg_id, sid: str, request: Request) -> Response:
    params = payload.get("params") or {}
    if not isinstance(params, dict):
        params = {}
    name = str(params.get("name") or "")
    args = params.get("arguments", params.get("args", {}))
    if name not in LAYOUT_TOOLS | PLAN_TOOLS | agent_batch.TOOLS | {"canvas.batch_prompt_edit"} and not isinstance(args, dict):
        args = params.get("args")
        if not isinstance(args, dict):
            args = {}
    if name not in TOOL_IMPLS:
        # MCP clients can recover from a tool result with isError.
        mcp_event_recorder.record({"session": sid, "tool": name, "ok": False, "error": f"Unknown tool: {name}"})
        return _rpc_response(
            200, msg_id, sid,
            result=_mcp_error_result(name, translate_detail(400, f"Unknown tool: {name}")),
        )
    if name in _MEDIA_REF_TOOL_IMPLS:
        # Identity and turn come from MCP binding/query, not the JSON tool arguments.
        args = {key: value for key, value in args.items() if key not in {"agent_node_id", "turn_id"}}
    started = time.time()
    slow_timeout = SLOW_TOOL_TIMEOUTS.get(name)
    turn_lease = None
    try:
        writes = name in WRITE_TOOLS
        if name == "canvas.batch_prompt_edit":
            from app.services.agent_batch_prompt import parse
            writes = isinstance(parse(args), str)
        if name in LAYOUT_TOOLS:
            agent_layout.validate_args(name, args)
        attachment_archive = name == "media.upload" and "attachment_id" in args
        if (name in PLAN_TOOLS or attachment_archive) and not (
            request.query_params.get("project") and request.query_params.get("node")
        ):
            from app.services.agent_plan import file_not_allowed, stale
            raise file_not_allowed() if name == "plan.import_excel" or attachment_archive else stale()
        if name in PLAN_TOOLS and not isinstance(args, dict):
            from app.services.agent_plan import file_not_allowed
            if name == "plan.import_excel":
                raise file_not_allowed()
            raise ValueError("Expected a JSON object")
        project_id = resolve_project(str(request.query_params.get("project") or ""))
        caller = request.query_params.get("caller")
        if caller not in (None, "external", "hosted"):
            raise ValueError("caller must be hosted or external when supplied")
        # MCP session and JSON-RPC identifiers are not logical user turns.
        turn_id = request.query_params.get("turn_id")
        if turn_id is not None and not turn_id.strip():
            raise ValueError("turn_id must be a nonempty string when supplied")
        node_param = str(request.query_params.get("node") or "")
        if caller == "hosted":
            if not node_param.strip():
                raise ValueError("node is required for hosted caller")
            if turn_id is not None:
                raise ValueError("turn_id cannot be supplied for hosted caller")
            if writes:
                from app.services.agent_host import agent_host_manager
                # #393: caller is a spoofable attribution label, not authentication.
                turn_id = agent_host_manager.get_current_turn_id(project_id, node_param)
                if not turn_id:
                    raise ValueError("hosted write requires an active turn")
        if name in agent_batch.TOOLS:
            # A10 cannot create/rebind a carrier or reserve an external turn before approval.
            binding = (agent_batch.Caller(project_id, node_param) if caller == 'hosted' else
                       agent_batch.external_caller(project_id, node_param, request.headers.get(SESSION_HEADER)))
            result = await TOOL_IMPLS[name](binding, args)
            return _rpc_response(200, msg_id, sid, result={'content': _tool_result_content_blocks(result), 'isError': False})
        retry_caller = None
        if name == 'generate.retry':
            from app.services import agent_generation_retry
            if not request.query_params.get('project') or not node_param:
                raise agent_generation_retry.agent_refs.invalid('Retry requires an explicit project and existing Agent node')
            retry_caller = (agent_batch.Caller(project_id, node_param) if caller == 'hosted' else
                            agent_batch.external_caller(project_id, node_param, request.headers.get(SESSION_HEADER)))
            await asyncio.to_thread(agent_generation_retry.prepare, retry_caller, agent_generation_retry.arguments(args))
        # Reject unowned input before even creating an external carrier or reserving a turn (#454).
        trigger_plan = await _prepare_trigger(project_id, args) if name == 'generate.trigger' else None
        # #350: explicit node binds attribution; reads must not materialize external nodes.
        if node_param:
            node_id = node_param
        elif writes:
            node_id = await ensure_external_agent_node(project_id, sid)
        else:
            node_id = ""
        if caller != 'hosted' and writes and turn_id is not None:
            from app.services.agent_ops_store import begin_turn, mark_turn_lookup_failed
            from firefly_opsdb import OpsDb
            db = OpsDb(_app_main.FIREFLY_PROJECT_STORE.root)
            try:
                if begin_turn(db, project_id, turn_id, sid, external=True, reserve=True):
                    logger.warning('External turn ID %s was pruned; recording an individual operation', turn_id)
                    turn_id = None
                else:
                    turn_lease = (db, project_id, turn_id)
            except (OSError, sqlite3.Error, KeyError):
                logger.warning('External turn %s lookup failed; undo disabled for this turn', turn_id, exc_info=True)
                mark_turn_lookup_failed(db, project_id, turn_id)
        try:
            with agent_ops_scope(project_id=project_id, agent_node_id=node_id or None,
                                 turn_id=turn_id, tool=name, args=args):
                if trigger_plan is not None:
                    binding = agent_batch.Caller(project_id, node_id, '' if caller == 'hosted' else sid)
                    result = await TOOL_IMPLS[name](project_id, node_id, args, plan=trigger_plan, retry_caller=binding)
                elif retry_caller is not None:
                    result = await TOOL_IMPLS[name](retry_caller, args)
                elif name in LAYOUT_TOOLS:
                    result = await TOOL_IMPLS[name](project_id, node_id, args, hosted=caller == 'hosted')
                elif name == "canvas.batch_prompt_edit":
                    binding = ("agent", node_param) if node_param else ("mcp", sid)
                    result = await TOOL_IMPLS[name](project_id, node_id, args, binding=binding)
                elif name in PLAN_TOOLS:
                    binding = "hosted" if caller == "hosted" else ("mcp", sid)
                    result = await TOOL_IMPLS[name](project_id, node_id, args, binding=binding)
                elif slow_timeout is not None:
                    result = await asyncio.wait_for(TOOL_IMPLS[name](project_id, node_id, args), slow_timeout)
                else:
                    result = await TOOL_IMPLS[name](project_id, node_id, args)
        finally:
            if turn_lease:
                from app.services.agent_ops_store import release_turn
                release_turn(*turn_lease)
    except asyncio.TimeoutError:
        mcp_event_recorder.record({"session": sid, "tool": name, "ok": False, "error": f"timeout after {slow_timeout}s"})
        return _rpc_response(
            200, msg_id, sid,
            result=_mcp_error_result(name, agent_error(
                504, "TOOL_TIMEOUT",
                f"工具 {name} 超时（慢操作契约 {int(slow_timeout)} 秒）",
                "先调用 canvas.list_nodes 查看画布状态，再调低 max_px 或改用 selection 作用域缩小批量后重试。",
                retryable=True).detail),
        )
    except HTTPException as exc:
        mcp_event_recorder.record({"session": sid, "tool": name, "ok": False, "error": str(exc.detail)})
        return _rpc_response(200, msg_id, sid,
                             result=_mcp_error_result(name, agent_error_response_payload(exc)))
    except (McpToolError, ValueError) as exc:
        mcp_event_recorder.record({"session": sid, "tool": name, "ok": False, "error": str(exc)})
        return _rpc_response(200, msg_id, sid,
                             result=_mcp_error_result(name, translate_detail(400, str(exc))))
    except Exception as exc:
        logger.exception("MCP tool %s failed", name)
        mcp_event_recorder.record({"session": sid, "tool": name, "ok": False, "error": str(exc)})
        from app.services.agent_errors import agent_error as _ae
        return _rpc_response(
            200, msg_id, sid,
            result=_mcp_error_result(name, _ae(500, "INTERNAL_ERROR",
                                               f"工具 {name} 执行失败：{exc}",
                                               "服务端内部错误；如重复出现请报告用户，不要盲目重试。").detail),
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
