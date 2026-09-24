"""Agent canvas tools API endpoints.

These endpoints are called by the Pi agent's canvas-tools extension via localhost HTTP.
They execute canvas mutations through the existing project store.
"""
import asyncio
import time
from fastapi import APIRouter, Header, HTTPException, Query, Request
import json
import uuid

import sys as _sys
import os as _os
_app_main = _sys.modules.get(_os.environ.get('FIREFLY_MAIN_MODULE', '')) or _sys.modules.get('main') or _sys.modules['__main__']
from app.core import CANVAS_TASKS, CANVAS_TASK_LOCK
from app.core import OnlineImageRequest, AIReference
from app.services.misc import build_online_image_result
from firefly_generation import ImageGenerationRequest, ModelRoutingError

router = APIRouter(prefix="/api/projects/{project_id}/canvas", tags=["agent-tools"])

generate_router = APIRouter(prefix="/api/projects/{project_id}/generate", tags=["agent-generate"])

media_router = APIRouter(prefix="/api/projects/{project_id}/media", tags=["agent-media"])

viewport_router = APIRouter(prefix="/api/projects/{project_id}/viewport", tags=["agent-viewport"])

confirm_router = APIRouter(prefix="/api/projects/{project_id}/agent", tags=["agent-confirm"])

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


def _apply_mutations(project_id: str, mutations: list):
    """Apply canvas mutations with retry on revision conflict."""
    store = _app_main.FIREFLY_PROJECT_STORE
    for attempt in range(8):
        try:
            # Get the current revision directly from the canvas
            canvas = store.canvas(project_id)
            revision = int(canvas.get("revision") or 0)
            result = store.apply_mutations(project_id, mutations, base_revision=revision)
            return result
        except Exception as e:
            if "revision" in str(e).lower() and attempt < 7:
                continue
            raise HTTPException(status_code=409, detail=str(e))
    raise HTTPException(status_code=409, detail="Canvas mutation failed after retries")


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


@router.get("/selection")
async def get_selection(project_id: str, x_agent_project: str = Header(default="")):
    """Get the currently selected nodes."""
    # Selection is client-side state; for agent purposes, return empty
    # In a real implementation, this would query the active browser session
    return {"selected": []}


@router.post("/nodes")
async def create_node(project_id: str, request: Request, x_agent_project: str = Header(default="")):
    """Create a new node on the canvas."""
    _validate_project(project_id)
    body = await request.json()
    node = body.get("node", {})
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

    _apply_mutations(project_id, [{"op": "upsert_node", "node": node}])
    return {"success": True, "node_id": node["id"]}


@router.patch("/nodes/{node_id}")
async def update_node(project_id: str, node_id: str, request: Request, x_agent_project: str = Header(default="")):
    """Update an existing node's properties."""
    body = await request.json()

    # Get current node
    target_node = _find_node(project_id, node_id)

    # Apply updates
    for key, value in body.items():
        if key not in ("id", "type", "created_at"):  # Don't allow changing identity
            target_node[key] = value
    target_node["updated_at"] = _app_main.now_ms()

    _apply_mutations(project_id, [{"op": "upsert_node", "node": target_node}])
    return {"success": True, "node_id": node_id}


@router.delete("/nodes/{node_id}")
async def delete_node(project_id: str, node_id: str, x_agent_project: str = Header(default="")):
    """Delete a node from the canvas."""
    _validate_project(project_id)
    _apply_mutations(project_id, [{"op": "remove_node", "node_id": node_id}])
    return {"success": True, "node_id": node_id}


@router.post("/edges")
async def create_edge(project_id: str, request: Request, x_agent_project: str = Header(default="")):
    """Create a connection between two nodes."""
    _validate_project(project_id)
    body = await request.json()
    edge = body.get("edge", {})
    if not isinstance(edge, dict):
        raise HTTPException(status_code=400, detail="edge must be an object")

    if not edge.get("id"):
        edge["id"] = f"edge-{uuid.uuid4().hex[:12]}"
    if not edge.get("from") or not edge.get("to"):
        raise HTTPException(status_code=400, detail="edge.from and edge.to are required")

    _apply_mutations(project_id, [{"op": "upsert_edge", "edge": edge}])
    return {"success": True, "edge_id": edge["id"]}


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

    _apply_mutations(project_id, [{"op": "remove_edge", "edge_id": target_edge.get("id", "")}])
    return {"success": True, "from": from_node, "to": to_node}


# =============================================================================
# Generate tools
# =============================================================================


@generate_router.post("/trigger")
async def trigger_generation(project_id: str, request: Request, x_agent_project: str = Header(default="")):
    """Trigger generation on a node, returns task ID."""
    body = await request.json()
    node_id = body.get("node_id")
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
        if status["status"] in ("succeeded", "failed", "timeout", "cancelled", "jimeng_pending"):
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


# =============================================================================
# Media tools
# =============================================================================


@media_router.post("/upload")
async def upload_media(project_id: str, request: Request, x_agent_project: str = Header(default="")):
    """Upload a media file to the project library."""
    _validate_project(project_id)
    body = await request.json()
    file_path = body.get("file_path")
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
    node_id = body.get("node_id")
    if not node_id:
        raise HTTPException(status_code=400, detail="node_id is required")

    # Get the node's position
    target_node = _find_node(project_id, node_id)

    # Store the viewport state
    VIEWPORT_STATE[project_id] = {
        "focus_node_id": node_id,
        "x": target_node.get("x", 0),
        "y": target_node.get("y", 0),
        "scale": 1.0,
        "updated_at": time.time(),
    }

    return {"success": True, "node_id": node_id}


@viewport_router.post("/zoom")
async def set_zoom(project_id: str, request: Request, x_agent_project: str = Header(default="")):
    """Set the canvas zoom level."""
    _validate_project(project_id)
    body = await request.json()
    level = body.get("level")
    if level is None:
        raise HTTPException(status_code=400, detail="level is required")

    # Store the viewport state
    current = VIEWPORT_STATE.get(project_id, {})
    VIEWPORT_STATE[project_id] = {
        **current,
        "scale": float(level),
        "updated_at": time.time(),
    }

    return {"success": True, "level": level}


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
    node_id = body.get("node_id")
    tool = body.get("tool", "")
    params = body.get("params", {})
    message = body.get("message", "")
    timeout = body.get("timeout_seconds", 60)

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
            await asyncio.wait_for(done_event.wait(), timeout=float(timeout))
        except asyncio.TimeoutError:
            result["approved"] = False
            result["reason"] = "timeout"

        return {"approved": result["approved"], "reason": result["reason"]}
    finally:
        # Clean up the temporary handler
        if handle_confirm_response in session.confirm_handlers:
            session.confirm_handlers.remove(handle_confirm_response)
