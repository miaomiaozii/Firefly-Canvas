"""Agent canvas tools API endpoints.

These endpoints are called by the Pi agent's canvas-tools extension via localhost HTTP.
They execute canvas mutations through the existing project store.
"""
import asyncio
import time
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
import json
import uuid

import sys as _sys
import os as _os
_app_main = _sys.modules.get(_os.environ.get('FIREFLY_MAIN_MODULE', '')) or _sys.modules.get('main') or _sys.modules['__main__']
from app.core import CANVAS_TASKS, CANVAS_TASK_LOCK, ensure_agent_same_origin
from app.core import OnlineImageRequest, AIReference
from app.services.misc import build_online_image_result
from firefly_generation import ImageGenerationRequest, ModelRoutingError

# #240：agent-tools 系列端点接入同源/Host 守卫（与现有 API 一致；
# 无 Origin 的本机回环 sidecar 调用按票面模型放行）。
router = APIRouter(prefix="/api/projects/{project_id}/canvas", tags=["agent-tools"], dependencies=[Depends(ensure_agent_same_origin)])

generate_router = APIRouter(prefix="/api/projects/{project_id}/generate", tags=["agent-generate"], dependencies=[Depends(ensure_agent_same_origin)])

media_router = APIRouter(prefix="/api/projects/{project_id}/media", tags=["agent-media"], dependencies=[Depends(ensure_agent_same_origin)])

viewport_router = APIRouter(prefix="/api/projects/{project_id}/viewport", tags=["agent-viewport"], dependencies=[Depends(ensure_agent_same_origin)])

confirm_router = APIRouter(prefix="/api/projects/{project_id}/agent", tags=["agent-confirm"], dependencies=[Depends(ensure_agent_same_origin)])

# Global viewport state per project
VIEWPORT_STATE = {}


def _validate_project(project_id: str):
    """Validate that the project exists and return the store."""
    store = _app_main.FIREFLY_PROJECT_STORE
    if store is None:
        raise HTTPException(status_code=500, detail="Project store not initialized")
    try:
        store.open_project(project_id)
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")


async def _apply_mutations(project_id: str, mutations: list):
    """Apply canvas mutations with retry on revision conflict.

    #243: after a successful commit, broadcast ``canvas_updated`` so every
    other open tab re-syncs in real time (ADR 0022 known gap: agent-tools
    mutations used to be invisible to other tabs until manual reload).
    """
    from firefly_projects import RevisionConflict

    store = _app_main.FIREFLY_PROJECT_STORE
    for attempt in range(8):
        try:
            # Get the current revision directly from the canvas
            canvas = store.canvas(project_id)
            revision = int(canvas.get("revision") or 0)
            result = store.apply_mutations(project_id, mutations, base_revision=revision)
            await _broadcast_canvas_updated(project_id)
            return result
        except RevisionConflict:
            # Revision conflict: another writer updated the canvas.
            # Retry with the latest revision (up to 8 attempts).
            if attempt < 7:
                continue
            raise HTTPException(status_code=409, detail="Canvas mutation failed after retries")
        except Exception as e:
            raise HTTPException(status_code=409, detail=str(e))


async def _broadcast_canvas_updated(project_id: str) -> None:
    """Best-effort ``canvas_updated`` broadcast after an agent-tools mutation."""
    manager = getattr(_app_main, "manager", None)
    if manager is None:
        return
    try:
        canvas = _app_main.FIREFLY_PROJECT_STORE.canvas(project_id)
        await manager.broadcast_canvas_updated(project_id, int(canvas.get("updated_at") or _app_main.now_ms()))
    except Exception:
        pass  # broadcast is best-effort; never fail the mutation because of it


async def _dispatch_viewport_command(project_id: str, command: dict) -> bool:
    """#243: viewport commands go to ONE owning tab, targeted (not broadcast).

    Resolves the tab's client_id by walking the manager's live connections
    against the project_sessions registry (the registry is keyed by
    id(token), so object-keyed lookup goes through session_project), then
    uses send_personal_message (ADR 0022: 命令单 client 定向下发). Returns
    False when the tab is not (or no longer) connected — the viewport state
    was still updated server-side.
    """
    from app.project_sessions import session_project
    manager = getattr(_app_main, "manager", None)
    if manager is None:
        return False
    for connection in list(manager.active_connections):
        if session_project(connection) != project_id:
            continue
        client_id = manager.connection_clients.get(connection)
        if not client_id:
            continue
        message = {"type": "canvas_viewport_command", "project_id": project_id, **command}
        await manager.send_personal_message(message, client_id)
        return True
    return False


def _open_canvas(project_id: str) -> dict:
    """Validate the project and return its canvas dict."""
    _validate_project(project_id)
    store = _app_main.FIREFLY_PROJECT_STORE
    opened = store.open_project(project_id)
    return opened.get("canvas", {})


def _find_node(project_id: str, node_id: str) -> dict:
    """Find a node by ID on the canvas, or raise 404."""
    canvas = _open_canvas(project_id)
    for node in canvas.get("nodes", []):
        if isinstance(node, dict) and str(node.get("id", "")) == node_id:
            return node
    raise HTTPException(status_code=404, detail=f"Node not found: {node_id}")


@router.get("/nodes")
async def list_nodes(project_id: str, x_agent_project: str = Header(default="")):
    """List all nodes on the canvas with summary info."""
    canvas = _open_canvas(project_id)
    nodes = canvas.get("nodes", [])
    connections = canvas.get("connections", [])

    # Build summary for each node
    summaries = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id", ""))
        # Find connections for this node
        incoming = [e for e in connections if isinstance(e, dict) and str(e.get("to", "")) == node_id]
        outgoing = [e for e in connections if isinstance(e, dict) and str(e.get("from", "")) == node_id]
        summaries.append({
            "id": node_id,
            "type": str(node.get("type", "")),
            "title": str(node.get("title", "") or node.get("text", "")[:50]),
            "x": node.get("x", 0),
            "y": node.get("y", 0),
            "incoming": [str(e.get("from", "")) for e in incoming],
            "outgoing": [str(e.get("to", "")) for e in outgoing],
            "status": str(node.get("status", "") or node.get("generationState", "")),
        })
    return {"nodes": summaries, "count": len(summaries)}


@router.get("/nodes/{node_id}")
async def get_node(project_id: str, node_id: str, x_agent_project: str = Header(default="")):
    """Get detailed information about a specific node."""
    return _find_node(project_id, node_id)


@router.post("/nodes")
async def create_node(project_id: str, request: Request, x_agent_project: str = Header(default="")):
    """Create a new node on the canvas."""
    body = await request.json()
    return await create_node_core(project_id, body.get("node", {}))


@router.patch("/nodes/{node_id}")
async def update_node(project_id: str, node_id: str, request: Request, x_agent_project: str = Header(default="")):
    """Update an existing node's properties."""
    body = await request.json()
    return await update_node_core(project_id, node_id, body)


@router.delete("/nodes/{node_id}")
async def delete_node(project_id: str, node_id: str, x_agent_project: str = Header(default="")):
    """Delete a node from the canvas."""
    _validate_project(project_id)
    await _apply_mutations(project_id, [{"op": "remove_node", "node_id": node_id}])
    return {"success": True, "node_id": node_id}


@router.post("/edges")
async def create_edge(project_id: str, request: Request, x_agent_project: str = Header(default="")):
    """Create a connection between two nodes."""
    body = await request.json()
    return await create_edge_core(project_id, body.get("edge", {}))


@router.delete("/edges/{from_node}/{to_node}")
async def delete_edge(project_id: str, from_node: str, to_node: str, x_agent_project: str = Header(default="")):
    """Remove a connection between two nodes."""
    _validate_project(project_id)
    # Find the edge first
    store = _app_main.FIREFLY_PROJECT_STORE
    opened = store.open_project(project_id)
    canvas = opened.get("canvas", {})
    connections = canvas.get("connections", [])
    target_edge = None
    for edge in connections:
        if isinstance(edge, dict) and str(edge.get("from", "")) == from_node and str(edge.get("to", "")) == to_node:
            target_edge = edge
            break
    if target_edge is None:
        raise HTTPException(status_code=404, detail=f"Edge not found: {from_node} -> {to_node}")

    await _apply_mutations(project_id, [{"op": "remove_edge", "edge_id": target_edge.get("id", "")}])
    return {"success": True, "from": from_node, "to": to_node}


@router.post("/view")
async def canvas_view(project_id: str, request: Request, x_agent_project: str = Header(default="")):
    """#266 canvas.view: 整页/选区媒体导出为一张图喂模型（只读，contact sheet base64）。"""
    body = await request.json()
    return await canvas_view_core(project_id, body.get("scope") or "page", body.get("node_ids"), body.get("max_px"))


@router.post("/view_media")
async def canvas_view_media(project_id: str, request: Request, x_agent_project: str = Header(default="")):
    """#266 canvas.view_media: 查看单节点媒体（图像下缩放 / 视频抽帧拼图，只读）。"""
    body = await request.json()
    return await view_media_core(project_id, body.get("node_id"), body.get("max_px"), body.get("frames"))


# =============================================================================
# Core (Request-free) implementations — shared by the HTTP endpoints above and
# the MCP streamable-HTTP endpoint (app/routers/api_mcp.py, #243 / ADR 0022).
# =============================================================================


async def create_node_core(project_id: str, node: dict) -> dict:
    """Create a node on the canvas. `node` must be a dict; `type` is required."""
    _validate_project(project_id)
    if not isinstance(node, dict):
        raise HTTPException(status_code=400, detail="node must be an object")

    # Ensure node has required fields
    if not node.get("id"):
        node["id"] = f"node-{uuid.uuid4().hex[:12]}"
    if not node.get("type"):
        raise HTTPException(status_code=400, detail="node.type is required")
    if "x" not in node:
        node["x"] = 0
    if "y" not in node:
        node["y"] = 0
    node["created_at"] = _app_main.now_ms()

    await _apply_mutations(project_id, [{"op": "upsert_node", "node": node}])
    return {"success": True, "node_id": node["id"]}


async def update_node_core(project_id: str, node_id: str, updates: dict) -> dict:
    """Update an existing node's properties (`updates` is a flat field dict)."""
    if not isinstance(updates, dict):
        raise HTTPException(status_code=400, detail="updates must be an object")
    # Get current node
    target_node = _find_node(project_id, node_id)

    # Apply updates
    for key, value in updates.items():
        if key not in ("id", "type", "created_at"):  # Don't allow changing identity
            target_node[key] = value
    target_node["updated_at"] = _app_main.now_ms()

    await _apply_mutations(project_id, [{"op": "upsert_node", "node": target_node}])
    return {"success": True, "node_id": node_id}


async def create_edge_core(project_id: str, edge: dict) -> dict:
    """Create a connection between two nodes. `edge` needs `from` and `to`."""
    _validate_project(project_id)
    if not isinstance(edge, dict):
        raise HTTPException(status_code=400, detail="edge must be an object")

    if not edge.get("id"):
        edge["id"] = f"edge-{uuid.uuid4().hex[:12]}"
    if not edge.get("from") or not edge.get("to"):
        raise HTTPException(status_code=400, detail="edge.from and edge.to are required")

    await _apply_mutations(project_id, [{"op": "upsert_edge", "edge": edge}])
    return {"success": True, "edge_id": edge["id"]}


# =============================================================================
# Generate tools
# =============================================================================


@generate_router.post("/trigger")
async def trigger_generation(project_id: str, request: Request, x_agent_project: str = Header(default="")):
    """Trigger generation on a node, returns task ID."""
    body = await request.json()
    return await trigger_generation_core(project_id, body.get("node_id"))


async def trigger_generation_core(project_id: str, node_id: str) -> dict:
    """Trigger generation on a node, returns task ID (Request-free core)."""
    _validate_project(project_id)
    if not node_id:
        raise HTTPException(status_code=400, detail="node_id is required")

    target_node = _find_node(project_id, node_id)

    node_type = str(target_node.get("type", ""))
    prompt = str(target_node.get("prompt", "") or target_node.get("text", ""))
    if not prompt:
        raise HTTPException(status_code=400, detail=f"Node {node_id} has no prompt")

    # Route through model routing (ADR 0006): the router picks provider + model
    # from the node's generation parameters; no hardcoded provider/size.
    reference_asset_ids = [str(a) for a in (target_node.get("reference_asset_ids") or [])]
    gen_request = ImageGenerationRequest(
        prompt=prompt,
        size=str(target_node.get("generation_size") or "1024x1024"),
        width=target_node.get("width"),
        height=target_node.get("height"),
        aspect_ratio=str(target_node.get("aspect_ratio") or "1:1"),
        resolution=str(target_node.get("generation_resolution") or "1k"),
        reference_asset_ids=tuple(reference_asset_ids),
        media_type="image",
        quality="auto",
        count=1,
    )
    try:
        route = _app_main.route_image_generation(
            gen_request, _app_main.load_api_providers(), provider_id="", model=""
        )
    except ModelRoutingError as exc:
        raise HTTPException(status_code=400, detail=exc.to_dict()) from exc

    # Convert reference asset IDs to image references for the provider call.
    reference_images = []
    if reference_asset_ids:
        try:
            ref_records = await asyncio.to_thread(
                _app_main.project_image_references, project_id, reference_asset_ids
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        reference_images = [
            AIReference(
                url=str(r.get("url") or ""),
                name=str(r.get("name") or ""),
                kind=str(r.get("kind") or "image"),
                mime=str(r.get("mime") or "image/png"),
            )
            for r in ref_records
        ]

    payload = {
        "prompt": prompt,
        "provider_id": route.provider_id,
        "model": route.model,
        "size": gen_request.size,
        "quality": "auto",
        "n": 1,
        "reference_images": reference_images,
    }

    # Create the task using the existing API
    task_id = f"agent_img_{uuid.uuid4().hex}"
    with CANVAS_TASK_LOCK:
        CANVAS_TASKS[task_id] = {
            "id": task_id,
            "type": "agent-image",
            "status": "queued",
            "project_id": project_id,
            "node_id": node_id,
            "node_type": node_type,
            "created_at": time.time(),
            "updated_at": time.time(),
            "result": None,
            "error": "",
        }

    # Run the generation in the background
    asyncio.create_task(_run_agent_generation(task_id, project_id, node_id, payload))

    return {"task_id": task_id, "type": "image", "node_id": node_id}


async def wait_for_task_core(project_id: str, task_id: str, timeout: int = 120) -> dict:
    """Wait for a generation task to complete (Request-free core; 1..600s)."""
    _validate_project(project_id)

    deadline = time.time() + timeout
    last_status = None

    while time.time() < deadline:
        try:
            status = await get_task_status(project_id, task_id)
        except HTTPException as e:
            if e.status_code == 404:
                # Task not found yet, keep waiting
                await asyncio.sleep(0.5)
                continue
            raise

        last_status = status
        if status["status"] in ("succeeded", "failed", "timeout", "cancelled"):
            return status

        # Poll with backoff
        await asyncio.sleep(min(1.0, (deadline - time.time()) / 2))

    # Timeout reached
    return {
        "task_id": task_id,
        "status": "timeout",
        "error": f"Wait timed out after {timeout}s (last status: {last_status.get('status', 'unknown') if last_status else 'not found'})",
        "last_status": last_status,
    }


async def _run_agent_generation(task_id: str, project_id: str, node_id: str, payload: dict):
    """Run generation in the background and update task status."""
    try:
        # Use the existing image generation function
        result = await build_online_image_result(
            OnlineImageRequest(**payload)
        )
        with CANVAS_TASK_LOCK:
            CANVAS_TASKS[task_id].update({
                "status": "succeeded",
                "result": result,
                "error": "",
                "updated_at": time.time(),
            })
    except Exception as exc:
        with CANVAS_TASK_LOCK:
            CANVAS_TASKS[task_id].update({
                "status": "failed",
                "error": str(exc),
                "updated_at": time.time(),
            })


@generate_router.get("/tasks/{task_id}")
async def get_task_status(project_id: str, task_id: str, x_agent_project: str = Header(default="")):
    """Query the status of a generation task."""
    _validate_project(project_id)

    # Check in-memory task store first
    with CANVAS_TASK_LOCK:
        task = dict(CANVAS_TASKS.get(task_id) or {})

    if task:
        # Normalize the response
        result = task.get("result")
        return {
            "task_id": task_id,
            "status": task.get("status", "unknown"),
            "type": task.get("type", ""),
            "error": task.get("error", ""),
            "result": result,
            "updated_at": task.get("updated_at"),
        }

    raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")


@generate_router.get("/tasks/{task_id}/wait")
async def wait_for_task(project_id: str, task_id: str, timeout: int = Query(default=120, ge=1, le=600), x_agent_project: str = Header(default="")):
    """Wait for a generation task to complete (with timeout)."""
    return await wait_for_task_core(project_id, task_id, timeout)


# =============================================================================
# Media tools
# =============================================================================


@media_router.post("/upload")
async def upload_media(project_id: str, request: Request, x_agent_project: str = Header(default="")):
    """Upload a media file to the project library."""
    body = await request.json()
    return await upload_media_core(project_id, body.get("file_path"))


async def upload_media_core(project_id: str, file_path: str) -> dict:
    """Import a local media file into the project library (Request-free core)."""
    _validate_project(project_id)
    if not file_path:
        raise HTTPException(status_code=400, detail="file_path is required")

    import os
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail=f"File not found: {file_path}")

    # Use the store directly to import the media file
    store = _app_main.FIREFLY_PROJECT_STORE
    try:
        record = store.import_media_file(
            project_id,
            file_path,
            os.path.basename(file_path),
            "",
            source_info={"locator": file_path, "name": os.path.basename(file_path), "type": "local_output"}
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {"success": True, "asset_id": record["asset_id"], "media": record}


@media_router.get("/list")
async def list_media(project_id: str, x_agent_project: str = Header(default="")):
    """List all media assets in the project."""
    _validate_project(project_id)

    # Use the existing list endpoint
    from app.routers.api_project_library import list_project_media

    response = await list_project_media(project_id)
    return response


@media_router.delete("/{asset_id}")
async def delete_media(project_id: str, asset_id: str, x_agent_project: str = Header(default="")):
    """Delete a media file from the project."""
    _validate_project(project_id)

    store = _app_main.FIREFLY_PROJECT_STORE
    try:
        store.delete_media(project_id, asset_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {"success": True, "asset_id": asset_id}


# =============================================================================
# Viewport tools
# =============================================================================


@viewport_router.post("/focus")
async def focus_node(project_id: str, request: Request, x_agent_project: str = Header(default="")):
    """Scroll canvas to focus on a node."""
    body = await request.json()
    return await focus_node_core(project_id, body.get("node_id"))


@viewport_router.post("/zoom")
async def set_zoom(project_id: str, request: Request, x_agent_project: str = Header(default="")):
    """Set the canvas zoom level."""
    body = await request.json()
    return await set_zoom_core(project_id, body.get("level"))


async def focus_node_core(project_id: str, node_id: str) -> dict:
    """Focus the canvas viewport on a node (Request-free core).

    #243: besides updating the server-side viewport state, the command is
    dispatched TARGETED to the single tab that has the project open (not
    broadcast, ADR 0022). Frontend consumption of `canvas_viewport_command`
    is a follow-up (smart-canvas.js is in a separate queue).
    """
    _validate_project(project_id)
    if not node_id:
        raise HTTPException(status_code=400, detail="node_id is required")

    # Get the node's position
    target_node = _find_node(project_id, node_id)

    # Store the viewport state
    x = target_node.get("x", 0)
    y = target_node.get("y", 0)
    VIEWPORT_STATE[project_id] = {
        "focus_node_id": node_id,
        "x": x,
        "y": y,
        "scale": 1.0,
        "updated_at": time.time(),
    }

    dispatched = await _dispatch_viewport_command(project_id, {"command": "focus", "node_id": node_id, "x": x, "y": y})
    return {"success": True, "node_id": node_id, "dispatched": dispatched}


async def set_zoom_core(project_id: str, level) -> dict:
    """Set the canvas zoom level (Request-free core). See focus_node_core."""
    _validate_project(project_id)
    if level is None:
        raise HTTPException(status_code=400, detail="level is required")

    # Store the viewport state
    current = VIEWPORT_STATE.get(project_id, {})
    VIEWPORT_STATE[project_id] = {
        **current,
        "scale": float(level),
        "updated_at": time.time(),
    }

    dispatched = await _dispatch_viewport_command(project_id, {"command": "zoom", "level": float(level)})
    return {"success": True, "level": level, "dispatched": dispatched}


@viewport_router.get("/state")
async def get_viewport_state(project_id: str, x_agent_project: str = Header(default="")):
    """Get the current viewport state."""
    _validate_project(project_id)
    return VIEWPORT_STATE.get(project_id, {"scale": 1.0, "x": 0, "y": 0})


# =============================================================================
# Confirmation gate
# =============================================================================


@confirm_router.post("/confirm")
async def request_confirmation(project_id: str, request: Request, x_agent_project: str = Header(default="")):
    """Request user confirmation for a destructive operation.

    Called by the canvas-tools extension before executing destructive tools.
    Sends a confirm event to the frontend via the agent's WS stream and blocks
    waiting for the user's decision (with timeout).

    For nodes with autonomy="autonomous", destructive tools are auto-approved
    (except safety-floor tools which always require confirmation).

    Body: {"node_id": "...", "tool": "...", "params": {...}, "message": "..."}
    Returns: {"approved": true} or {"approved": false, "reason": "timeout"|"rejected"}
    """
    _validate_project(project_id)
    body = await request.json()
    return await request_confirmation_core(
        project_id,
        body.get("node_id"),
        body.get("tool", ""),
        body.get("params", {}),
        body.get("message", ""),
        body.get("timeout_seconds", 60),
    )


async def request_confirmation_core(
    project_id: str,
    node_id: str,
    tool: str,
    params: dict,
    message: str,
    timeout_seconds: float = 60,
) -> dict:
    """Confirmation gate core (Request-free; shared by HTTP + MCP, #243).

    Blocks until the user confirms/rejects on the canvas or times out.
    Returns {"approved": bool, "reason": "rejected" | "timeout" | None}.
    """
    if not node_id:
        raise HTTPException(status_code=400, detail="node_id is required")

    from app.services.agent_host import agent_host_manager, SAFETY_FLOOR_TOOLS

    # Get the agent session
    session = await agent_host_manager.get_or_create_session(project_id, node_id)

    # Check autonomy level: auto-approve destructive tools in autonomous mode
    # (except safety-floor tools which always require confirmation)
    autonomy = session.autonomy or "cautious"
    if autonomy == "autonomous" and tool not in SAFETY_FLOOR_TOOLS:
        return {"approved": True}

    # Generate a unique confirm ID
    confirm_id = f"confirm-{uuid.uuid4().hex[:12]}"

    # Create a completion event and result holder
    loop = asyncio.get_event_loop()
    done_event = asyncio.Event()
    result = {"approved": False, "reason": "timeout"}

    # Register a temporary confirm handler
    async def handle_confirm_response(cid, approved):
        if cid == confirm_id:
            result["approved"] = approved
            result["reason"] = "rejected" if not approved else None
            done_event.set()

    agent_host_manager.on_confirm(session, handle_confirm_response)

    try:
        # Send the confirm event to the frontend via the session's event handlers
        confirm_event = {
            "type": "confirm",
            "confirm_id": confirm_id,
            "tool": tool,
            "params": params,
            "message": message,
        }
        for handler in session.event_handlers:
            try:
                await handler(session, confirm_event)
            except Exception:
                pass

        # Wait for the user's decision (with timeout)
        try:
            await asyncio.wait_for(done_event.wait(), timeout=float(timeout_seconds))
        except asyncio.TimeoutError:
            result["approved"] = False
            result["reason"] = "timeout"

        return {"approved": result["approved"], "reason": result["reason"]}
    finally:
        # Clean up the temporary handler
        if handle_confirm_response in session.confirm_handlers:
            session.confirm_handlers.remove(handle_confirm_response)


# =============================================================================
# Canvas view tools (#266) — read-only; ADR 0022 双面上线（REST 端点 + MCP TOOLS）。
# 慢操作独立 90s 超时契约由 api_mcp.SLOW_TOOL_TIMEOUTS 施加（ComfyArt SLOW_S=90s 模式）。
# =============================================================================


def _load_image_or_none(path: str):
    """Open a local media file as an Image, or None when unreadable."""
    try:
        from PIL import Image

        with Image.open(path) as img:
            return img.copy()
    except Exception:
        return None


def _video_frame_or_none(path: str, width: int):
    """Extract one video preview frame (ffmpeg), or None when unavailable."""
    try:
        from app.services.generation import generate_video_preview_image

        return generate_video_preview_image(path, width)
    except Exception:
        return None


def _find_node_for_view(project_id: str, node_id: str) -> dict:
    """_find_node with the #266 error-text contract (next-step hint)."""
    if not node_id:
        raise HTTPException(status_code=400, detail="node_id 必填；先调用 canvas.list_nodes 获取节点 ID。")
    try:
        return _find_node(project_id, node_id)
    except HTTPException:
        raise HTTPException(status_code=404, detail=f"节点 {node_id} 不存在；先调用 canvas.list_nodes 查看画布状态。") from None


async def canvas_view_core(project_id: str, scope: str = "page", node_ids=None, max_px=None) -> dict:
    """#266 canvas.view: 整页/选区导出为一张 contact sheet（base64 PNG）喂模型（只读）。

    ComfyArt §59.4 契约：max_px = min(2048, args.max_px || 1024)；视频资产用单帧
    预览图代表（无 ffmpeg 时摘除）；错误文案带下一步建议（票面规范）。
    """
    from app.services.canvas_view import clamp_view_max_px, compose_contact_sheet

    scope = str(scope or "page").strip().lower()
    if scope not in ("page", "selection"):
        raise HTTPException(status_code=400, detail="scope 必须是 page 或 selection；先调用 canvas.list_nodes 获取节点 ID。")
    px = clamp_view_max_px(max_px)
    canvas = _open_canvas(project_id)
    nodes = [n for n in canvas.get("nodes", []) if isinstance(n, dict)]
    if scope == "selection":
        wanted = [str(x) for x in (node_ids or []) if str(x).strip()]
        if not wanted:
            raise HTTPException(status_code=400, detail="selection 作用域需要 node_ids；先调用 canvas.list_nodes 获取节点 ID。")
        by_id = {str(n.get("id", "")): n for n in nodes}
        selected = []
        for nid in wanted:
            node = by_id.get(nid)
            if node is None:
                raise HTTPException(status_code=404, detail=f"节点 {nid} 不存在；先调用 canvas.list_nodes 查看画布状态。")
            selected.append(node)
    else:
        selected = nodes
    store = _app_main.FIREFLY_PROJECT_STORE
    items = []
    for node in selected:
        for image in node.get("images") or []:
            if not isinstance(image, dict):
                continue
            asset_id = str(image.get("asset_id") or "")
            if not asset_id:
                continue
            kind = str(image.get("kind") or "")
            try:
                record = await asyncio.to_thread(store.media_record, project_id, asset_id)
            except Exception:
                continue  # dangling asset 引用——跳过，不整单失败
            path = str(record.get("path") or "")
            if not path:
                continue
            if kind == "video":
                tile = await asyncio.to_thread(_video_frame_or_none, path, 256)
                if tile is None:
                    continue  # 无 ffmpeg/不可读——可用 canvas.view_media 查看 ffmpeg 下一步提示
                tile_kind = kind
            else:
                tile = await asyncio.to_thread(_load_image_or_none, path)
                if tile is None:
                    continue
                tile_kind = "image"
            items.append({
                "node_id": str(node.get("id", "")),
                "asset_id": asset_id,
                "kind": tile_kind,
                "name": str(image.get("name") or record.get("name") or ""),
                "tile": tile,
            })
    if not items:
        if scope == "selection":
            raise HTTPException(status_code=404, detail="选区节点没有可查看的媒体；先调用 canvas.list_nodes 查看节点媒体，或先调用 media.upload 导入图片。")
        raise HTTPException(status_code=400, detail="画布上没有可查看的媒体节点；先调用 media.upload 导入图片，或先调用 canvas.list_nodes 查看节点状态。")
    sheet = await asyncio.to_thread(compose_contact_sheet, [(it["node_id"], it.pop("tile")) for it in items], px)
    import base64

    from local_edit import encode_png

    data_url = "data:image/png;base64," + base64.b64encode(encode_png(sheet)).decode("ascii")
    return {
        "image_data_url": data_url,
        "width": sheet.width,
        "height": sheet.height,
        "max_px": px,
        "node_count": len({it["node_id"] for it in items}),
        "items": items,
    }


async def view_media_core(project_id: str, node_id, max_px=None, frames=None) -> dict:
    """#266 canvas.view_media: 查看单节点媒体（图像下缩放 / 视频抽帧拼图，只读）。

    视频抽帧复用 generate_video_preview_image 链路（均匀采样，contact sheet 拼贴）；
    全部帧失败时报错带 ffmpeg 下一步建议（票面规范）。
    """
    from app.services.canvas_view import (
        MAX_VIDEO_FRAMES,
        clamp_view_max_px,
        fit_tile,
        video_contact_sheet,
        video_duration_seconds,
        video_frame_times,
    )

    node = _find_node_for_view(project_id, str(node_id or ""))
    store = _app_main.FIREFLY_PROJECT_STORE
    px = clamp_view_max_px(max_px)
    try:
        n_frames = max(1, min(MAX_VIDEO_FRAMES, int(frames or 4)))
    except (TypeError, ValueError):
        n_frames = 4
    images = [it for it in (node.get("images") or []) if isinstance(it, dict) and str(it.get("asset_id") or "")]
    if not images:
        raise HTTPException(status_code=400, detail=f"节点 {node_id} 没有可查看的媒体；先调用 media.upload 导入图片，或先调用 canvas.list_nodes 查看节点状态。")
    image_items = [it for it in images if str(it.get("kind") or "") != "video"]
    picked = image_items[0] if image_items else images[0]
    is_video = str(picked.get("kind") or "") == "video"
    try:
        record = await asyncio.to_thread(store.media_record, project_id, str(picked["asset_id"]))
    except Exception:
        raise HTTPException(status_code=404, detail=f"节点 {node_id} 的媒体资源不存在；先调用 media.list 查看资源库。") from None
    path = str(record.get("path") or "")
    if is_video:
        from app.services.generation import generate_video_preview_image

        duration = await asyncio.to_thread(video_duration_seconds, path)
        sheet = await asyncio.to_thread(video_contact_sheet, path, generate_video_preview_image, duration, n_frames, max(256, px // 2), px)
        if sheet is None:
            raise HTTPException(status_code=400, detail=f"视频抽帧失败（节点 {node_id}）；请检查服务器已安装 ffmpeg 且媒体文件可播放，然后重试；也可先调用 canvas.list_nodes 查看节点状态。")
        kind, frames_used = "video", len(video_frame_times(duration, n_frames))
    else:
        src = await asyncio.to_thread(_load_image_or_none, path)
        if src is None:
            raise HTTPException(status_code=400, detail=f"节点 {node_id} 的媒体无法读取；请检查媒体文件是否损坏，或先调用 canvas.list_nodes 查看节点状态。")
        sheet, kind, frames_used = fit_tile(src, px, px), "image", 1
    import base64

    from local_edit import encode_png

    data_url = "data:image/png;base64," + base64.b64encode(encode_png(sheet)).decode("ascii")
    return {
        "kind": kind,
        "image_data_url": data_url,
        "width": sheet.width,
        "height": sheet.height,
        "frames": frames_used,
        "asset_id": str(picked["asset_id"]),
        "name": str(picked.get("name") or record.get("name") or ""),
    }
