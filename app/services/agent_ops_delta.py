"""Pure, nonauthoritative canvas entity deltas; these are not canvas backups.

Each entry has entity ("node" or "connection"), id, path, before and after.
A state is {"present": false} or {"present": true, "value": ...}; an empty
path denotes the entire entity. Connection entries additionally have node_ids,
the sorted union of their endpoint IDs. Mapping paths recurse; lists are atomic.
"""
from __future__ import annotations

from copy import deepcopy

_ENDPOINTS = ("from", "to", "source", "target", "fromNode", "toNode", "sourceNodeId", "targetNodeId")
_MISSING = object()


def _id(entity: dict, kind: str) -> str:
    explicit = str(entity.get("id") or "")
    if explicit or kind == "node":
        return explicit
    source = entity.get("from") or entity.get("source") or entity.get("fromNode") or entity.get("sourceNodeId")
    target = entity.get("to") or entity.get("target") or entity.get("toNode") or entity.get("targetNodeId")
    if source and target:
        return f"{source}->{target}:{entity.get('fromPort') or entity.get('sourcePort') or ''}:{entity.get('toPort') or entity.get('targetPort') or ''}"
    return ""


def _endpoints(edge: dict) -> set[str]:
    return {str(edge[key]) for key in _ENDPOINTS if edge.get(key) is not None and str(edge[key])}


def _index(canvas: dict, kind: str) -> dict[str, dict]:
    name = "nodes" if kind == "node" else "connections"
    result = {}
    for entity in canvas.get(name, []):
        entity_id = _id(entity, kind)
        if not entity_id or entity_id in result:
            raise ValueError(f"missing or duplicate {kind} identity: {entity_id!r}")
        result[entity_id] = entity
    return result


def _equal(left, right) -> bool:
    if left is _MISSING or right is _MISSING:
        return left is right
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(_equal(left[k], right[k]) for k in left)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(_equal(a, b) for a, b in zip(left, right))
    return type(left) is type(right) and left == right


def _state(value) -> dict:
    if value is _MISSING:
        return {"present": False}
    return {"present": True, "value": deepcopy(value)}


def _value(state: dict):
    return state["value"] if state["present"] else _MISSING


def _fields(before: dict, after: dict, path: list[str]):
    for key in sorted(before.keys() | after.keys()):
        old, new = before.get(key, _MISSING), after.get(key, _MISSING)
        if _equal(old, new):
            continue
        if isinstance(old, dict) and isinstance(new, dict):
            yield from _fields(old, new, path + [key])
        else:
            yield path + [key], old, new


def build_delta(before: dict, after: dict) -> list[dict]:
    """Return only changed nodes, edges and fields, in stable identity/path order.

    Node and edge IDs must be unique and nonempty in both canvases. Other canvas
    properties are intentionally outside this delta's scope.
    """
    result = []
    for kind in ("node", "connection"):
        old_entities, new_entities = _index(before, kind), _index(after, kind)
        for entity_id in sorted(old_entities.keys() | new_entities.keys()):
            old = old_entities.get(entity_id, _MISSING)
            new = new_entities.get(entity_id, _MISSING)
            common = {"entity": kind, "id": entity_id}
            if kind == "connection":
                endpoints = set()
                for edge in (old, new):
                    if edge is not _MISSING:
                        endpoints.update(_endpoints(edge))
                common["node_ids"] = sorted(endpoints)
            if old is _MISSING or new is _MISSING:
                changes = [([], old, new)]
            else:
                changes = _fields(old, new, [])
            for path, old_value, new_value in changes:
                result.append({**common, "path": path, "before": _state(old_value), "after": _state(new_value)})
    return result


def _at_path(entity, path: list[str]):
    for key in path[:-1]:
        if not isinstance(entity, dict) or key not in entity:
            return False, _MISSING
        entity = entity[key]
    if not isinstance(entity, dict):
        return False, _MISSING
    return True, entity.get(path[-1], _MISSING)


def _conflict(change: dict, reason: str) -> dict:
    return {"node_id": change["id"] if change["entity"] == "node" else None,
            "entity": change["entity"], "id": change["id"],
            "path": list(change["path"]), "reason": reason}


def apply_inverse(canvas: dict, delta: list[dict]) -> tuple[dict, list[dict], bool]:
    """Apply an inverse atomically, or return an unchanged copy and conflicts.

    Changes already at before are skipped. A missing intermediate mapping is a
    conflict rather than permission to recreate a concurrently removed parent.
    """
    indexes = {kind: _index(canvas, kind) for kind in ("node", "connection")}
    pending, conflicts = [], []
    for change in delta:
        entity = indexes[change["entity"]].get(change["id"], _MISSING)
        path = change["path"]
        if path:
            if entity is _MISSING:
                conflicts.append(_conflict(change, "entity missing"))
                continue
            valid, current = _at_path(entity, path)
            if not valid:
                conflicts.append(_conflict(change, "parent mapping missing or changed"))
                continue
        else:
            current = entity
        if _equal(current, _value(change["before"])):
            continue
        if not _equal(current, _value(change["after"])):
            conflicts.append(_conflict(change, "current value differs from before and after"))
        else:
            pending.append(change)
    if conflicts:
        return deepcopy(canvas), conflicts, False

    result = deepcopy(canvas)
    result_indexes = {kind: _index(result, kind) for kind in ("node", "connection")}
    removed_nodes = []
    for change in pending:
        kind, entity_id, path = change["entity"], change["id"], change["path"]
        name = "nodes" if kind == "node" else "connections"
        before = _value(change["before"])
        entity = result_indexes[kind].get(entity_id, _MISSING)
        if not path:
            if before is _MISSING:
                result[name].remove(entity)
                del result_indexes[kind][entity_id]
                if kind == "node":
                    removed_nodes.append(change)
            else:
                restored = deepcopy(before)
                result.setdefault(name, []).append(restored)
                result_indexes[kind][entity_id] = restored
        else:
            parent = entity
            for key in path[:-1]:
                parent = parent[key]
            if before is _MISSING:
                del parent[path[-1]]
            else:
                parent[path[-1]] = deepcopy(before)

    # Only references also removed by this inverse are safe to discard.
    for change in removed_nodes:
        node_id = change["id"]
        if any(node_id in _endpoints(edge) for edge in result.get("connections", [])):
            conflicts.append(_conflict(change, "remaining incident connection"))
        if any(node.get("type") == "smart-group" and isinstance(node.get("items"), list)
               and any(item is not None and str(item) == node_id for item in node["items"])
               for node in result.get("nodes", [])):
            conflicts.append(_conflict(change, "remaining smart-group membership"))
        if any(isinstance(node.get("inputNodeIds"), list)
               and node_id in node["inputNodeIds"] for node in result.get("nodes", [])):
            conflicts.append(_conflict(change, "remaining inputNodeIds reference"))
        if any(node.get("historyFor") == node_id for node in result.get("nodes", [])):
            conflicts.append(_conflict(change, "remaining historyFor reference"))
    if conflicts:
        return deepcopy(canvas), conflicts, False
    return result, [], not pending


def touched_node_ids(delta: list[dict]) -> list[str]:
    """Return sorted node IDs directly changed or incident to changed edges."""
    ids = set()
    for change in delta:
        if change["entity"] == "node":
            ids.add(change["id"])
        else:
            ids.update(change["node_ids"])
    return sorted(ids)
