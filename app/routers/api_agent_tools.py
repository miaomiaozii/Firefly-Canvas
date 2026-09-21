"""Agent canvas tools API endpoints.

These endpoints are called by the Pi agent's canvas-tools extension via localhost HTTP.
They execute canvas mutations through the existing project store.
"""
from fastapi import APIRouter, Header, HTTPException, Query, Request
import json
import uuid

import main

router = APIRouter(prefix="/api/projects/{project_id}/canvas", tags=["agent-tools"])


def _validate_project(project_id: str):
    """Validate that the project exists and return the store."""
    store = main.FIREFLY_PROJECT_STORE
    if store is None:
        raise HTTPException(status_code=500, detail="Project store not initialized")
    try:
        store.open_project(project_id)
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")


def _apply_mutations(project_id: str, mutations: list):
    """Apply canvas mutations with retry on revision conflict."""
    store = main.FIREFLY_PROJECT_STORE
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
    store = main.FIREFLY_PROJECT_STORE
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
    store = main.FIREFLY_PROJECT_STORE
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
    node["created_at"] = main.now_ms()

    _apply_mutations(project_id, [{"op": "upsert_node", "node": node}])
    return {"success": True, "node_id": node["id"]}


@router.patch("/nodes/{node_id}")
async def update_node(project_id: str, node_id: str, request: Request, x_agent_project: str = Header(default="")):
    """Update an existing node's properties."""
    _validate_project(project_id)
    body = await request.json()

    # Get current node
    store = main.FIREFLY_PROJECT_STORE
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
    target_node["updated_at"] = main.now_ms()

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
    store = main.FIREFLY_PROJECT_STORE
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
