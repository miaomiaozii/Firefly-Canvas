"""Agent nodes API: session lifecycle, WebSocket streaming, command sending."""
import asyncio
import json
import time
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect

from app.core import ensure_agent_same_origin
from app.services.agent_host import agent_host_manager
from app.routers.api_agent_tools import _find_node, _apply_mutations

# Resolve the main module without re-importing it (isolated-module test design).
import sys as _sys
import os as _os
_app_main = _sys.modules.get(_os.environ.get('FIREFLY_MAIN_MODULE', '')) or _sys.modules.get('main') or _sys.modules['__main__']

# #240：agent-nodes 全部端点（含 WebSocket stream）接入同源/Host 守卫。
router = APIRouter(prefix="/api/projects/{project_id}/agent-nodes", tags=["agent-nodes"], dependencies=[Depends(ensure_agent_same_origin)])


def _apply_node_host(project_id: str, node_id: str, session) -> None:
    """#318 票 B：宿主选择 = 节点属性 agentCli（"pi" 默认 / "codex"），并恢复
    extSessionId（resume 链跨重启存活，ADR 0024 不变量）。当前轮进行中的进程
    不切宿主；未知值回退 pi。best-effort：节点不存在/非 agent 型不改会话。"""
    if session.is_running:
        return
    try:
        node = _find_node(project_id, node_id)
    except HTTPException:
        return
    if not isinstance(node, dict):
        return
    agent_cli = str(node.get("agentCli") or "").strip() or "pi"
    if agent_cli not in ("pi", "codex"):
        agent_cli = "pi"
    session.agent_cli = agent_cli
    if not session.ext_session_id:
        ext = str(node.get("extSessionId") or "").strip()
        if ext:
            session.ext_session_id = ext


async def _update_node_field(project_id: str, node_id: str, field: str, value: Any):
    """Update a single field on a node and persist (reuses retry-on-conflict helper)."""
    node = _find_node(project_id, node_id)
    node[field] = value
    node["updated_at"] = _app_main.now_ms()
    await _apply_mutations(project_id, [{"op": "upsert_node", "node": node}])
    return node


@router.post("")
async def create_agent_node(
    project_id: str,
    node_id: str = Body(...),
    model: str = Body(default=""),
    autonomy: str = Body(default=""),
    x_agent_project: str = Header(default=""),
):
    """Create or resume an agent node session.

    If model/autonomy are not provided, reads them from the node's attributes.
    """
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
    return {"success": True, "node_id": node_id, "status": "stopped"}


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
    model = body.get("model", "")
    # #285：会话优先。WS spawn 的会话不在持久画布 nodes 里，此前
    # _update_node_field 先执行且找不到节点直接 404（用户实测 "Node not found:
    # agent_probe2"），后面的 set_model 永远执行不到——选模型“无效”的根因。
    session = await agent_host_manager.get_or_create_session(project_id, node_id)
    if session.is_running:
        await agent_host_manager.set_model(session, model)
    else:
        # Update session model for next spawn
        session.model = model

    # 持久化 best-effort：节点不在画布（纯会话）时不报错，只记 persisted=False。
    persisted = True
    try:
        await _update_node_field(project_id, node_id, "model", model)
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        persisted = False

    return {
        "success": True,
        "node_id": node_id,
        "model": model,
        "is_running": session.is_running,
        "persisted": persisted,
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
    autonomy = body.get("autonomy", "")
    if autonomy not in ("cautious", "standard", "autonomous"):
        raise HTTPException(status_code=400, detail=f"Invalid autonomy level: {autonomy}")

    # Persist to node attributes
    node = await _update_node_field(project_id, node_id, "autonomy", autonomy)

    # Update session autonomy (affects confirm gate scope immediately)
    session = await agent_host_manager.get_or_create_session(project_id, node_id)
    session.autonomy = autonomy

    return {
        "success": True,
        "node_id": node_id,
        "autonomy": autonomy,
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
    project_id: str,
    node_id: str,
    x_agent_project: str = Header(default=""),
):
    """Get the list of user messages available for forking."""
    session = await agent_host_manager.get_or_create_session(project_id, node_id)
    if not session.is_running:
        raise HTTPException(status_code=400, detail="Agent is not running")

    messages = await agent_host_manager.get_fork_messages(session)
    return {"success": True, "node_id": node_id, "messages": messages}


@router.post("/{node_id}/fork")
async def fork_session(
    project_id: str,
    node_id: str,
    entry_id: str = Body(...),
    name: str = Body(default=""),
    x_agent_project: str = Header(default=""),
):
    """Fork the conversation from a specific user message.

    Creates a new branch and persists the branch list to the node's attributes.
    """
    session = await agent_host_manager.get_or_create_session(project_id, node_id)
    if not session.is_running:
        raise HTTPException(status_code=400, detail="Agent is not running")

    # Get existing branches from node attributes
    node = _find_node(project_id, node_id)
    branches = node.get("branches") or []

    # Track the original branch on the first fork (so it can be switched back to)
    if not branches:
        branches.append({
            "id": "branch-original",
            "name": "原始",
            "session_file": session.session_file,
            "session_id": session.session_id,
            "fork_from_entry_id": None,
            "created_at": _app_main.now_ms(),
        })

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
    branches.append(branch)

    # Persist updated branches list and current_branch
    node["branches"] = branches
    node["current_branch"] = branch_id
    node["updated_at"] = _app_main.now_ms()
    await _apply_mutations(project_id, [{"op": "upsert_node", "node": node}])

    return {
        "success": True,
        "node_id": node_id,
        "branch": branch,
        "branches": branches,
        "current_branch": branch_id,
    }


@router.post("/{node_id}/switch-branch")
async def switch_branch(
    project_id: str,
    node_id: str,
    branch_id: str = Body(...),
    x_agent_project: str = Header(default=""),
):
    """Switch to a different conversation branch."""
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

    # Update current_branch in node attributes
    node["current_branch"] = branch_id
    node["updated_at"] = _app_main.now_ms()
    await _apply_mutations(project_id, [{"op": "upsert_node", "node": node}])

    return {
        "success": True,
        "node_id": node_id,
        "current_branch": branch_id,
        "branch": branch,
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
