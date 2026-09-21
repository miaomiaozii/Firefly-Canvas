"""Agent nodes API: session lifecycle, WebSocket streaming, command sending."""
import asyncio
import json
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Header, HTTPException, Query, WebSocket, WebSocketDisconnect

import main
from app.services.agent_host import agent_host_manager

router = APIRouter(prefix="/api/projects/{project_id}/agent-nodes", tags=["agent-nodes"])


@router.post("")
async def create_agent_node(
    project_id: str,
    node_id: str = Body(...),
    model: str = Body(default=""),
    x_agent_project: str = Header(default=""),
):
    """Create or resume an agent node session."""
    session = await agent_host_manager.get_or_create_session(project_id, node_id, model)

    # If not running, spawn it
    if not session.is_running:
        success = await agent_host_manager.spawn_session(session, model)
        if not success:
            raise HTTPException(status_code=500, detail="Failed to start agent session")

    return {
        "success": True,
        "node_id": node_id,
        "session_id": session.session_id,
        "status": session.status,
        "model": session.model or model,
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
        "is_running": session.is_running,
        "started_at": session._started_at,
        "last_activity": session._last_activity,
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
