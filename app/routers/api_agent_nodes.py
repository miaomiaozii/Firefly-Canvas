"""Agent nodes API: session lifecycle, WebSocket streaming, command sending."""
import asyncio
import json
import time
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Header, HTTPException, Query, WebSocket, WebSocketDisconnect

from app.services.agent_host import agent_host_manager
from app.routers.api_agent_tools import _find_node, _apply_mutations

# Resolve the main module without re-importing it (isolated-module test design).
import sys as _sys
import os as _os
_app_main = _sys.modules.get(_os.environ.get('FIREFLY_MAIN_MODULE', '')) or _sys.modules.get('main') or _sys.modules['__main__']

router = APIRouter(prefix="/api/projects/{project_id}/agent-nodes", tags=["agent-nodes"])


def _update_node_field(project_id: str, node_id: str, field: str, value: Any):
    """Update a single field on a node and persist (reuses retry-on-conflict helper)."""
    node = _find_node(project_id, node_id)
    node[field] = value
    node["updated_at"] = _app_main.now_ms()
    _apply_mutations(project_id, [{"op": "upsert_node", "node": node}])
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
    # Persist to node attributes
    node = _update_node_field(project_id, node_id, "model", model)

    # If session is running, switch model via RPC
    session = await agent_host_manager.get_or_create_session(project_id, node_id)
    if session.is_running:
        await agent_host_manager.set_model(session, model)
    else:
        # Update session model for next spawn
        session.model = model

    return {
        "success": True,
        "node_id": node_id,
        "model": model,
        "is_running": session.is_running,
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
    node = _update_node_field(project_id, node_id, "autonomy", autonomy)

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
    _apply_mutations(project_id, [{"op": "upsert_node", "node": node}])

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
    _apply_mutations(project_id, [{"op": "upsert_node", "node": node}])

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

    # If session is not running, try to start it
    if not session.is_running:
        success = await agent_host_manager.spawn_session(session)
        if not success:
            await websocket.send_json({"type": "error", "message": "Failed to start agent session"})
            await websocket.close()
            return

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
