"""Agent nodes API: session lifecycle, WebSocket streaming, command sending."""
import asyncio
import json
import logging
import time
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect

from app.core import ensure_agent_same_origin, ensure_same_origin_request
from app.constants import NODE_TYPE_AGENT
from app.services.agent_host import agent_host_manager
from app.services.agent_errors import agent_error
from app.routers.api_agent_tools import (
    _find_node, _snapshot_node, _apply_mutations, _apply_mutations_from_snapshot,
    _open_canvas, reset_generation_counts,
)

# Resolve the main module without re-importing it (isolated-module test design).
import sys as _sys
import os as _os
_app_main = _sys.modules.get(_os.environ.get('FIREFLY_MAIN_MODULE', '')) or _sys.modules.get('main') or _sys.modules['__main__']

# #240：agent-nodes 全部端点（含 WebSocket stream）接入同源/Host 守卫。
router = APIRouter(prefix="/api/projects/{project_id}/agent-nodes", tags=["agent-nodes"], dependencies=[Depends(ensure_agent_same_origin)])

logger = logging.getLogger(__name__)

# #335: 未知 agentCli 不得静默回落 pi——create 入口直接 400 拒绝。
AGENT_CLI_WHITELIST = ("pi", "codex")


def _validated_agent_cli(node: dict) -> str:
    """Empty/missing agentCli → default pi; explicit unknown value → 400 (#335)."""
    raw = str(node.get("agentCli") or "").strip()
    if not raw:
        return "pi"
    if raw not in AGENT_CLI_WHITELIST:
        raise HTTPException(status_code=400, detail={
            "error": "unknown agent_cli",
            "agent_cli": raw,
            "supported": list(AGENT_CLI_WHITELIST),
        })
    return raw


# #354：autonomy 合法值（与 :358 set_agent_autonomy 的校验同源）。
AGENT_AUTONOMY_WHITELIST = ("cautious", "standard", "autonomous")


def _apply_node_host(project_id: str, node_id: str, session) -> None:
    """#318 票 B：宿主选择 = 节点属性 agentCli（"pi" 默认 / "codex"），并恢复
    extSessionId（resume 链跨重启存活，ADR 0024 不变量）。当前轮进行中的进程
    不切宿主；未知值回退 pi。best-effort：节点不存在/非 agent 型不改会话。

    #354：model/autonomy 也从节点播种——节点是持久权威源（set_agent_model/
    set_agent_autonomy 已落 canvas.json），服务器重启后新建会话默认空值，
    不播种就会用默认模型/cautious spawn。
    """
    if session.is_running:
        return
    try:
        node = _find_node(project_id, node_id)
    except HTTPException:
        return
    if not isinstance(node, dict):
        return
    # #354：未运行时，会话的 model/autonomy 以节点为准（节点没配则保留会话现值）。
    node_model = str(node.get("model") or "").strip()
    if node_model:
        session.model = node_model
    node_autonomy = str(node.get("autonomy") or "").strip()
    if node_autonomy in AGENT_AUTONOMY_WHITELIST:
        session.autonomy = node_autonomy
    agent_cli = str(node.get("agentCli") or "").strip() or "pi"
    if agent_cli not in AGENT_CLI_WHITELIST:
        # #335: spawn/status 路径的兼容兑底保留，但必须留痕（create 入口已 400 拒绝）。
        logger.warning("#335 unknown agentCli %r on node %s; falling back to pi", agent_cli, node_id)
        agent_cli = "pi"
    session.agent_cli = agent_cli
    if not session.ext_session_id:
        ext = str(node.get("extSessionId") or "").strip()
        if ext:
            session.ext_session_id = ext


def _settings_user_edit(body: dict) -> bool:
    """Settings accept an explicit author flag, never client ownership or time."""
    reserved = {"createdBy", "agentNodeId", "userTouchedAt", "promptEditedByUser",
                "created_at", "updated_at", "createdAt", "updatedAt"}
    if reserved.intersection(body):
        raise agent_error(400, "INVALID_ARGUMENT", "Ownership and timestamps are server-owned",
                          "Remove ownership and timestamp fields; use user_edit=true for a manual change.")
    user_edit = body.get("user_edit", False)
    if type(user_edit) is not bool:
        raise agent_error(400, "INVALID_ARGUMENT", "user_edit must be a boolean",
                          "Omit user_edit or send the JSON boolean true or false.")
    return user_edit


async def _update_node_settings(project_id: str, node_id: str, build_fields, *, user_edit: bool = False):
    """Rebuild the settings patch from each CAS snapshot, preserving ownership."""
    _open_canvas(project_id)  # Preserve project-not-found behavior before the CAS loop.

    def build(canvas):
        node = _snapshot_node(canvas, node_id)
        fields = build_fields(node)
        if all(key in node and node[key] == value for key, value in fields.items()):
            return []
        updated = {**node, **fields, "updated_at": _app_main.now_ms()}
        if user_edit:
            updated["userTouchedAt"] = updated["updated_at"]
        return [{"op": "upsert_node", "node": updated}]

    await _apply_mutations_from_snapshot(project_id, build, source="")


async def _update_node_field(project_id: str, node_id: str, field: str, value: Any, *, user_edit: bool = False):
    await _update_node_settings(project_id, node_id, lambda _node: {field: value}, user_edit=user_edit)


def _settings_canvas(project_id: str, *, allow_missing: bool = False):
    """Return a fresh coherent snapshot; only session-only models allow absence."""
    try:
        return _open_canvas(project_id)
    except HTTPException as exc:
        if not allow_missing or exc.status_code != 404:
            raise
        return None


async def _upsert_agent_canvas_node(project_id: str, node_id: str, agent_cli: str) -> None:
    """#332: agent-nodes create must persist the node into canvas.json——此前
    API 建的节点只存在于运行内存，进程重启整体蒸发、agent-events 成孤儿。
    已存在的节点绝不动（位置/字段以画布为准）；缺失时写入最小形状，走既有
    canvas 变更通道带 revision+broadcast。
    评审修正：项目不存在必须 404 原样上报（不得被吞成 409）；幻影节点错位
    网格摆放（不再全部堆原点）；spawn 失败时节点保留属预期——卡片可再启动，
    丢节点才是 #332 的病根。"""
    canvas = _open_canvas(project_id)
    if any(isinstance(n, dict) and str(n.get("id", "")) == node_id for n in canvas.get("nodes", [])):
        return
    slot = len(canvas.get("nodes", []))
    now = _app_main.now_ms()
    await _apply_mutations(project_id, [{"op": "upsert_node", "node": {
        "id": node_id,
        "type": NODE_TYPE_AGENT,
        "kind": "agent",
        "title": f"Agent {node_id}",
        "x": 80 + (slot % 10) * 80,
        "y": 80 + (slot // 10) * 80,
        "agentCli": agent_cli,
        "created_at": now,
        "updated_at": now,
    }}])


@router.post("")
async def create_agent_node(
    project_id: str,
    node_id: str = Body(...),
    model: str = Body(default=""),
    autonomy: str = Body(default=""),
    agent_cli: str = Body(default="", alias="agentCli"),
    x_agent_project: str = Header(default=""),
):
    """Create or resume an agent node session.

    If model/autonomy are not provided, reads them from the node's attributes.
    """
    # #343：创建入口严格校验——node_id/model 来自请求 body（可被 agent 工具链
    # 间接写入），不合白名单直接 400，先于任何会话/落盘/spawn。
    from app.services.agent_host import validate_spawn_arg as _v343
    try:
        _v343("node_id", node_id)
        _v343("model", model, model=True)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # Read node attributes if not explicitly provided
    if not model:
        try:
            node = _find_node(project_id, node_id)
            model = node.get("model", "") or ""
        except HTTPException:
            pass  # Node not found or not yet created; use default

    if not autonomy:
        try:
            node = _find_node(project_id, node_id)
            autonomy = node.get("autonomy", "") or "cautious"
        except HTTPException:
            autonomy = "cautious"

    # #335: 节点显式声明了未知 agentCli → 400，先于任何会话/spawn。
    # #332: body 可直接带 agentCli（幻影节点无 canvas 属性可读）；同样先验后落。
    # 评审修正：已有节点与 body agentCli 冲突→400（不得静默无视）；宿主权威=canvas 节点属性。
    host_node = None
    try:
        host_node = _find_node(project_id, node_id)
    except HTTPException:
        pass
    requested_cli = (agent_cli or "").strip()
    if requested_cli:
        _validated_agent_cli({"agentCli": requested_cli})
    if isinstance(host_node, dict):
        node_cli = _validated_agent_cli(host_node)
        if requested_cli and requested_cli != node_cli:
            raise HTTPException(status_code=400, detail={
                "error": "agentCli conflicts with existing node attribute",
                "node_agent_cli": node_cli,
                "body_agent_cli": requested_cli,
                "hint": "PATCH the canvas node to change host, or omit agentCli",
            })
        resolved_cli = node_cli
    else:
        resolved_cli = requested_cli or "pi"
        # #332: 节点不在 canvas 时先落盘（先于 spawn），重启不蒸发。
        await _upsert_agent_canvas_node(project_id, node_id, resolved_cli)

    session = await agent_host_manager.get_or_create_session(project_id, node_id, model, autonomy)

    # Update session model/autonomy if they were read from node attributes
    if model and not session.model:
        session.model = model
    if autonomy and session.autonomy == "cautious" and autonomy != "cautious":
        session.autonomy = autonomy
    _apply_node_host(project_id, node_id, session)

    # If not running, spawn it
    if not session.is_running:
        success = await agent_host_manager.spawn_session(session, model, autonomy)
        if not success:
            raise HTTPException(status_code=500, detail="Failed to start agent session")

    return {
        "success": True,
        "node_id": node_id,
        "session_id": session.session_id,
        "status": session.status,
        "model": session.model or model,
        "autonomy": session.autonomy or autonomy,
        # 评审修正：回显=会话实际宿主（_apply_node_host 后的真值），不是请求值。
        "agentCli": getattr(session, "agent_cli", None) or resolved_cli,
    }


@router.delete("/{node_id}")
async def stop_agent_node(
    project_id: str,
    node_id: str,
    x_agent_project: str = Header(default=""),
):
    """Stop an agent node session."""
    session = await agent_host_manager.get_or_create_session(project_id, node_id)
    if session.is_running:
        await agent_host_manager.stop_session(session)
    # #351 review 裁定：DELETE agent 节点必须清该节点的会话生成额度计数，
    # 同 node_id 重建后额度从 0 开始（否则与 429 文案「请新开会话」矛盾）。
    reset_generation_counts(project_id, node_id)
    return {"success": True, "node_id": node_id, "status": "stopped"}


@router.get("/external-cli-snippet")
async def external_cli_snippet(
    project_id: str,
    node: str = Query(default="", max_length=200),
    x_agent_project: str = Header(default=""),
):
    """#325 票 G（spec P5）：外部 CLI 接管命令 snippet。

    基址取 server 运行时实际监听值（resolve_agent_api_base：
    FIREFLY_AGENT_API_BASE → FIREFLY_PORT → 3000，#279 同源要求，禁硬编码）。
    与 P1 的 &node= 绑定共用：外部 CLI 会话绑定到该节点（ADR 0022 三级仲裁）。
    """
    if not node:
        raise HTTPException(status_code=400, detail="node query parameter is required")
    _find_node(project_id, node)  # 404 for missing project/node
    from app.services.agent_host import resolve_agent_api_base
    base = resolve_agent_api_base().rstrip("/")
    mcp_url = f"{base}/api/mcp?project={project_id}&node={node}"
    codex_cmd = f"codex exec -c 'mcp_servers.firefly.url=\"{mcp_url}\"' \"列出画布节点\""
    claude_cmd = f"claude mcp add --transport http firefly \"{mcp_url}\""
    return {
        "success": True,
        "node_id": node,
        "mcp_url": mcp_url,
        "codex_cmd": codex_cmd,
        "claude_cmd": claude_cmd,
    }


@router.get("/{node_id}/status")
async def get_agent_status(
    project_id: str,
    node_id: str,
    x_agent_project: str = Header(default=""),
):
    """Get the current status of an agent node."""
    session = await agent_host_manager.get_or_create_session(project_id, node_id)
    return {
        "node_id": node_id,
        "status": session.status,
        "session_id": session.session_id,
        "model": session.model,
        "autonomy": session.autonomy,
        "is_running": session.is_running,
        "started_at": session._started_at,
        "last_activity": session._last_activity,
    }


@router.get("/chat-models")
async def list_chat_models(project_id: str):
    """#285：只读列出 pi 的会话模型池（~/.pi/agent/models.json + settings.json）。

    此前 Agent 模型下拉列的是 Firefly 图片生成模型（选中即会话无响应）。
    返回 [{id: "provider/modelId", name, provider, model}] + current（settings 默认）。
    只收会话（chat）模型：pi 模型 type 为 image/classifier 的一律剔除（type 缺省按 chat，
    与 pi provider-composer 一致）——图片模型不得混入。绝不写 ~/.pi。
    FIREFLY_PI_AGENT_DIR 可覆盖目录（测试用）。
    """
    import pathlib
    agent_dir = pathlib.Path(_os.environ.get("FIREFLY_PI_AGENT_DIR", "")) if _os.environ.get("FIREFLY_PI_AGENT_DIR") \
        else pathlib.Path.home() / ".pi" / "agent"
    models: List[Dict[str, str]] = []
    current = ""
    try:
        data = json.loads((agent_dir / "models.json").read_text(encoding="utf-8"))
        for provider_id, provider in (data.get("providers") or {}).items():
            for item in (provider.get("models") or []):
                if not isinstance(item, dict) or not item.get("id"):
                    continue
                # #285：图片模型不得混入。pi 的模型 type 只有 "chat"/"image"/"classifier"
                # （见 pi toModelType），且 type 缺省即按 chat 处理（provider-composer 的
                # `entry.type ?? "chat"`）。会话模型池只收 chat：image（如 qwen-image-2.1-pro
                # 图像端点）与 classifier 选中即会话无响应，一律剔除。
                if (item.get("type") or "chat") != "chat":
                    continue
                models.append({
                    "id": f"{provider_id}/{item['id']}",
                    "name": item.get("name") or item["id"],
                    "provider": provider_id,
                    "model": item["id"],
                })
    except (OSError, ValueError):
        models = []
    try:
        settings = json.loads((agent_dir / "settings.json").read_text(encoding="utf-8"))
        if settings.get("defaultProvider") and settings.get("defaultModel"):
            current = f"{settings['defaultProvider']}/{settings['defaultModel']}"
    except (OSError, ValueError):
        pass
    return {"models": models, "current": current}


@router.post("/{node_id}/model")
async def set_agent_model(
    project_id: str,
    node_id: str,
    body: dict = Body(...),
    x_agent_project: str = Header(default=""),
):
    """Set the model for an agent node.

    If the session is running, switches the model via pi RPC set_model command.
    Always persists the model to the node's attributes.
    """
    user_edit = _settings_user_edit(body)
    model = body.get("model", "")
    # #343：模型名会进宿主 argv（--model / -m），不合白名单直接 400。
    from app.services.agent_host import validate_spawn_arg as _v343
    try:
        _v343("model", model, model=True)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # Session-only model changes remain supported, but a durable target deleted
    # during CAS is an error, not a successful session-only request.
    persisted = True
    try:
        _find_node(project_id, node_id)
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        persisted = False
    session = await agent_host_manager.get_or_create_session(project_id, node_id)
    if session.is_running:
        await agent_host_manager.set_model(session, model)
    else:
        # Update session model for next spawn
        session.model = model

    if persisted:
        await _update_node_field(project_id, node_id, "model", model, user_edit=user_edit)

    return {
        "success": True,
        "node_id": node_id,
        "model": model,
        "is_running": session.is_running,
        "persisted": persisted,
        "canvas": _settings_canvas(project_id, allow_missing=not persisted),
    }


@router.post("/{node_id}/autonomy")
async def set_agent_autonomy(
    project_id: str,
    node_id: str,
    body: dict = Body(...),
    x_agent_project: str = Header(default=""),
):
    """Set the autonomy level for an agent node.

    Valid values: cautious, standard, autonomous.
    Persists to node attributes. For running sessions, the confirmation gate
    scope changes immediately (next tool call); the system prompt suffix takes
    effect on next spawn.
    """
    user_edit = _settings_user_edit(body)
    autonomy = body.get("autonomy", "")
    if autonomy not in ("cautious", "standard", "autonomous"):
        raise HTTPException(status_code=400, detail=f"Invalid autonomy level: {autonomy}")

    # Persist to node attributes
    await _update_node_field(project_id, node_id, "autonomy", autonomy, user_edit=user_edit)

    # Update session autonomy (affects confirm gate scope immediately)
    session = await agent_host_manager.get_or_create_session(project_id, node_id)
    session.autonomy = autonomy

    return {
        "success": True,
        "node_id": node_id,
        "autonomy": autonomy,
        "canvas": _settings_canvas(project_id),
    }


@router.post("/{node_id}/prompt")
async def send_agent_prompt(
    project_id: str,
    node_id: str,
    message: str = Body(...),
    images: List[str] = Body(default=[]),
    x_agent_project: str = Header(default=""),
):
    """Send a prompt to the agent."""
    # #388：节点必须已存在于 canvas——幽灵 node_id 不得 get_or_create+spawn。
    # （正式创建入口是 POST /agent-nodes，它对不存在节点会 _upsert_agent_canvas_node 落盘。）
    _find_node(project_id, node_id)  # 404 for missing project/node
    session = await agent_host_manager.get_or_create_session(project_id, node_id)
    _apply_node_host(project_id, node_id, session)
    if not session.is_running:
        success = await agent_host_manager.spawn_session(session)
        if not success:
            raise HTTPException(status_code=500, detail="Failed to start agent session")

    await agent_host_manager.send_prompt(session, message, images)
    return {"success": True, "node_id": node_id}


@router.post("/{node_id}/steer")
async def send_agent_steer(
    project_id: str,
    node_id: str,
    message: str = Body(...),
    x_agent_project: str = Header(default=""),
):
    """Send a steer command to redirect the agent's current turn."""
    session = await agent_host_manager.get_or_create_session(project_id, node_id)
    if not session.is_running:
        raise HTTPException(status_code=400, detail="Agent is not running")

    await agent_host_manager.send_steer(session, message)
    return {"success": True, "node_id": node_id}


@router.post("/{node_id}/follow_up")
async def send_agent_follow_up(
    project_id: str,
    node_id: str,
    message: str = Body(...),
    x_agent_project: str = Header(default=""),
):
    """Queue a follow-up message for after the current turn."""
    session = await agent_host_manager.get_or_create_session(project_id, node_id)
    if not session.is_running:
        raise HTTPException(status_code=400, detail="Agent is not running")

    await agent_host_manager.send_follow_up(session, message)
    return {"success": True, "node_id": node_id}


@router.post("/{node_id}/abort")
async def send_agent_abort(
    project_id: str,
    node_id: str,
    x_agent_project: str = Header(default=""),
):
    """Abort the agent's current turn."""
    session = await agent_host_manager.get_or_create_session(project_id, node_id)
    if not session.is_running:
        raise HTTPException(status_code=400, detail="Agent is not running")

    await agent_host_manager.send_abort(session)
    return {"success": True, "node_id": node_id}


@router.post("/{node_id}/confirm")
async def post_agent_node_confirm(
    project_id: str,
    node_id: str,
    request: Request,
    x_agent_project: str = Header(default=""),
):
    """HTTP fallback for the TUI confirm gate (#241).

    Mirrors the WS "confirm" command handled in ``agent_event_stream`` below:
    when the TUI's WebSocket stream is not connected, the confirm buttons
    fall back to this route. The pre-refactor route at this URL was dropped,
    leaving the TUI fallback to 404 silently — both paths now share the same
    ``confirm_handlers`` state machine.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    confirm_id = str(body.get("confirm_id", ""))
    approved = bool(body.get("approved", False))

    session = await agent_host_manager.get_or_create_session(project_id, node_id)
    # Same state machine as the WS path: dispatch to every confirm handler
    # (each handler checks its own confirm_id and no-ops on mismatch).
    for handler in session.confirm_handlers:
        try:
            await handler(confirm_id, approved)
        except Exception:
            pass

    return {"ok": True, "confirm_id": confirm_id, "approved": approved}


@router.get("/{node_id}/fork-messages")
async def get_fork_messages(
    request: Request,
    project_id: str,
    node_id: str,
    x_agent_project: str = Header(default=""),
):
    """Get the list of user messages available for forking."""
    # #342：get_fork_messages 会向存活 `pi --mode rpc` 进程 stdin 写入 RPC 帧
    # （超时时还会 kill 整棵进程树后重生），属副作用 GET，要求显式同源头（Origin/Referer）。
    ensure_same_origin_request(request)
    session = await agent_host_manager.get_or_create_session(project_id, node_id)
    if not session.is_running:
        raise HTTPException(status_code=400, detail="Agent is not running")

    messages = await agent_host_manager.get_fork_messages(session)
    return {"success": True, "node_id": node_id, "messages": messages}


@router.get("/{node_id}/events")
async def get_agent_node_events(
    project_id: str, node_id: str, since: int = Query(0, ge=0)
):
    """#318 票 C：事件回放日志（seq>since 升序）。刷新/重连重建用；
    前端先挂缓冲→GET→按 seq 去重续 live 流（竞态在客户端消解）。"""
    try:
        return agent_host_manager.load_events(project_id, node_id, since)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"events log read failed: {e}")


@router.post("/{node_id}/fork")
async def fork_session(
    project_id: str,
    node_id: str,
    entry_id: str = Body(...),
    name: str = Body(default=""),
    x_agent_project: str = Header(default=""),
    request: Request = None,
):
    """Fork the conversation from a specific user message.

    Creates a new branch and persists the branch list to the node's attributes.
    """
    user_edit = _settings_user_edit(await request.json() if request is not None else {})
    session = await agent_host_manager.get_or_create_session(project_id, node_id)
    if not session.is_running:
        raise HTTPException(status_code=400, detail="Agent is not running")

    _find_node(project_id, node_id)  # Fail before RPC if the target is already gone.
    # Capture the pre-fork host identity once; the RPC changes the session.
    original = {
        "id": "branch-original",
        "name": "原始",
        "session_file": session.session_file,
        "session_id": session.session_id,
        "fork_from_entry_id": None,
        "created_at": _app_main.now_ms(),
    }

    # Perform the fork
    fork_info = await agent_host_manager.fork(session, entry_id)
    if fork_info.get("cancelled"):
        raise HTTPException(status_code=409, detail="Fork was cancelled")

    # Build the new branch record
    import time as _time
    branch_id = f"branch-{uuid.uuid4().hex[:8]}"
    branch = {
        "id": branch_id,
        "name": name or f"分支 {int(_time.time() * 1000) % 10000}",
        "session_file": fork_info.get("session_file"),
        "session_id": fork_info.get("session_id"),
        "fork_from_entry_id": entry_id,
        "created_at": _app_main.now_ms(),
    }
    def build_fields(node):
        branches = list(node.get("branches") or [])
        if not branches:
            branches.append(original)
        branches.append(branch)
        return {"branches": branches, "current_branch": branch_id}

    await _update_node_settings(project_id, node_id, build_fields, user_edit=user_edit)
    canvas = _settings_canvas(project_id)
    node = _snapshot_node(canvas, node_id)
    return {
        "success": True,
        "node_id": node_id,
        "branch": branch,
        "branches": node["branches"],
        "current_branch": node["current_branch"],
        "canvas": canvas,
    }


@router.post("/{node_id}/switch-branch")
async def switch_branch(
    project_id: str,
    node_id: str,
    branch_id: Any = Body(...),
    x_agent_project: str = Header(default=""),
    request: Request = None,
):
    """Switch branches; accept the legacy JSON string as well as an object."""
    body = branch_id if isinstance(branch_id, dict) else {}
    user_edit = _settings_user_edit(body)
    branch_id = body.get("branch_id", "") if body else branch_id
    if not isinstance(branch_id, str) or not branch_id:
        raise agent_error(400, "INVALID_ARGUMENT", "branch_id must be a non-empty string",
                          "Send the ID of an existing branch.")
    session = await agent_host_manager.get_or_create_session(project_id, node_id)
    if not session.is_running:
        raise HTTPException(status_code=400, detail="Agent is not running")

    # Find the branch in node attributes
    node = _find_node(project_id, node_id)
    branches = node.get("branches") or []
    branch = None
    for b in branches:
        if b.get("id") == branch_id:
            branch = b
            break
    if branch is None:
        raise HTTPException(status_code=404, detail=f"Branch not found: {branch_id}")

    # Switch to the branch's session file
    session_file = branch.get("session_file")
    if session_file:
        await agent_host_manager.switch_branch(session, session_file)

    def build_fields(node):
        if not any(b.get("id") == branch_id for b in node.get("branches") or []):
            raise agent_error(404, "NOT_FOUND", f"Branch not found: {branch_id}",
                              "Read the latest canvas and choose an existing branch.")
        return {"current_branch": branch_id}

    await _update_node_settings(project_id, node_id, build_fields, user_edit=user_edit)
    canvas = _settings_canvas(project_id)
    node = _snapshot_node(canvas, node_id)
    branch = next((b for b in node.get("branches") or [] if b.get("id") == branch_id), branch)
    return {
        "success": True,
        "node_id": node_id,
        "current_branch": node["current_branch"],
        "branch": branch,
        "canvas": canvas,
    }


@router.websocket("/{node_id}/stream")
async def agent_event_stream(
    websocket: WebSocket,
    project_id: str,
    node_id: str,
):
    """WebSocket endpoint for streaming agent events to the frontend."""
    await websocket.accept()

    session = await agent_host_manager.get_or_create_session(project_id, node_id)
    # #318 票 B：宿主选择读节点属性 agentCli（默认 pi）；session 事件带宿主标签
    # 供前端渲染（票 D 设置面板的只读展示面）。
    _apply_node_host(project_id, node_id, session)

    # #317：浏览画布不再自动拉起 pi 进程——stream 只监听；进程在用户首次发送（REST /prompt，
    # 或 WS prompt 指令兜底路径）时按需 spawn。此前每次渲染→连 WS→spawn，纯浏览也会烧资源/费用。

    # Register event handler for this WebSocket connection
    async def ws_event_handler(sess, event):
        try:
            await websocket.send_json(event)
        except Exception:
            # WebSocket closed
            pass

    agent_host_manager.on_event(session, ws_event_handler)

    # Send a session-confirmation event so the client can leave the transient
    # 'connecting' state. The pi subprocess only emits agent lifecycle events
    # (agent_start / turn_start / message_update ...) and never a 'session'
    # event, so without this the node's status badge is stuck on "连接中" until
    # the user happens to send a prompt. Emit it after the handler is
    # registered (so it can't be missed) and before the receive loop.
    await websocket.send_json({
        "type": "session",
        "session_id": session.session_id,
        "status": session.status,
        "model": session.model,
        "autonomy": session.autonomy,
        "agentCli": session.agent_cli,
        "extSessionId": session.ext_session_id,
    })

    try:
        while True:
            # Receive commands from frontend
            data = await websocket.receive_text()
            try:
                command = json.loads(data)
            except json.JSONDecodeError:
                continue

            cmd_type = command.get("type", "")

            if cmd_type == "prompt":
                message = command.get("message", "")
                images = command.get("images", [])
                if message:
                    # #388：WS 兜底路径同样不得为不存在节点创建/spawn（与 REST /prompt 同口径）。
                    try:
                        _find_node(project_id, node_id)
                    except Exception:
                        await websocket.send_json({"type": "error",
                                                   "message": f"Node not found: {node_id}"})
                        continue
                    if not session.is_running: # #317：WS 兜底路径也按需拉起（主路径是 REST /prompt）
                        if not await agent_host_manager.spawn_session(session):
                            await websocket.send_json({"type": "error", "message": "Failed to start agent session"})
                            continue
                    await agent_host_manager.send_prompt(session, message, images)

            elif cmd_type == "steer":
                message = command.get("message", "")
                if message:
                    await agent_host_manager.send_steer(session, message)

            elif cmd_type == "follow_up":
                message = command.get("message", "")
                if message:
                    await agent_host_manager.send_follow_up(session, message)

            elif cmd_type == "abort":
                await agent_host_manager.send_abort(session)

            elif cmd_type == "confirm":
                # Handle confirmation responses
                confirm_id = command.get("confirm_id", "")
                approved = command.get("approved", False)
                # Dispatch to confirm handlers
                for handler in session.confirm_handlers:
                    try:
                        await handler(confirm_id, approved)
                    except Exception:
                        pass

    except WebSocketDisconnect:
        pass
    finally:
        # Clean up handler
        if ws_event_handler in session.event_handlers:
            session.event_handlers.remove(ws_event_handler)
