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
from app.core import OnlineImageRequest
from app.services.misc import build_online_image_result

router = APIRouter(prefix="/api/projects/{project_id}/canvas", tags=["agent-tools"])

generate_router = APIRouter(prefix="/api/projects/{project_id}/generate", tags=["agent-generate"])

media_router = APIRouter(prefix="/api/projects/{project_id}/media", tags=["agent-media"])

viewport_router = APIRouter(prefix="/api/projects/{project_id}/viewport", tags=["agent-viewport"])

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


@router.get("/nodes")
async def list_nodes(project_id: str, x_agent_project: str = Header(default="")):
    """List all nodes on the canvas with summary info."""
    _validate_project(project_id)
    store = _app_main.FIREFLY_PROJECT_STORE
    opened = store.open_project(project_id)
    canvas = opened.get("canvas", {})
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
    _validate_project(project_id)
    store = _app_main.FIREFLY_PROJECT_STORE
    opened = store.open_project(project_id)
    canvas = opened.get("canvas", {})
    nodes = canvas.get("nodes", [])
    for node in nodes:
        if isinstance(node, dict) and str(node.get("id", "")) == node_id:
            return node
    raise HTTPException(status_code=404, detail=f"Node not found: {node_id}")


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
    _validate_project(project_id)
    body = await request.json()

    # Get current node
    store = _app_main.FIREFLY_PROJECT_STORE
    opened = store.open_project(project_id)
    canvas = opened.get("canvas", {})
    nodes = canvas.get("nodes", [])
    target_node = None
    for node in nodes:
        if isinstance(node, dict) and str(node.get("id", "")) == node_id:
            target_node = node
            break
    if target_node is None:
        raise HTTPException(status_code=404, detail=f"Node not found: {node_id}")

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
    _validate_project(project_id)
    body = await request.json()
    node_id = body.get("node_id")
    if not node_id:
        raise HTTPException(status_code=400, detail="node_id is required")

    # Get the node
    store = _app_main.FIREFLY_PROJECT_STORE
    opened = store.open_project(project_id)
    canvas = opened.get("canvas", {})
    nodes = canvas.get("nodes", [])
    target_node = None
    for node in nodes:
        if isinstance(node, dict) and str(node.get("id", "")) == node_id:
            target_node = node
            break
    if target_node is None:
        raise HTTPException(status_code=404, detail=f"Node not found: {node_id}")

    node_type = str(target_node.get("type", ""))
    prompt = str(target_node.get("prompt", "") or target_node.get("text", ""))
    if not prompt:
        raise HTTPException(status_code=400, detail=f"Node {node_id} has no prompt")

    # Use the canvas image task API (simpler, no project-specific routing)
    payload = {
        "prompt": prompt,
        "provider_id": "comfly",
        "model": "",
        "size": "1024x1024",
        "quality": "auto",
        "n": 1,
        "reference_images": [],
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


# =============================================================================
# Viewport tools
# =============================================================================


@viewport_router.post("/focus")
async def focus_node(project_id: str, request: Request, x_agent_project: str = Header(default="")):
    """Scroll canvas to focus on a node."""
    _validate_project(project_id)
    body = await request.json()
    node_id = body.get("node_id")
    if not node_id:
        raise HTTPException(status_code=400, detail="node_id is required")

    # Get the node's position
    store = _app_main.FIREFLY_PROJECT_STORE
    opened = store.open_project(project_id)
    canvas = opened.get("canvas", {})
    nodes = canvas.get("nodes", [])
    target_node = None
    for node in nodes:
        if isinstance(node, dict) and str(node.get("id", "")) == node_id:
            target_node = node
            break
    if target_node is None:
        raise HTTPException(status_code=404, detail=f"Node not found: {node_id}")

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
