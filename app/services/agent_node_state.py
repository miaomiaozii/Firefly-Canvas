"""Pure ownership defaults and canvas.list_nodes v2 projections.

This module reads an already-open canvas; it never repairs legacy data, writes a
store, resolves media, or invokes generation/routing. ``run`` is saved config,
not the actual execution request. ``refs`` prefers live Agent input edges.
"""
from __future__ import annotations

import math
import re
from collections import defaultdict
from typing import Any

from app.constants import NODE_TYPE_GROUP, NODE_TYPE_LOOP, NODE_TYPE_PROMPT
from app.services.agent_errors import agent_error
from app.services.agent_refs import ROLES, managed_edge, managed_order, numbered_refs


_FIELDS = frozenset({"summary", "prompt", "refs", "run"})
# Same project-media ID syntax as ProjectStore._invalid_project_media_value.
_ASSET_ID = re.compile(r"[A-Za-z0-9._-]{1,200}")
_RATIO = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*:\s*([0-9]+(?:\.[0-9]+)?)")
# Saved browser presets: static/js/canvas-generation-tasks.js aspectRatioForRun.
_RATIO_PRESETS = {
    "square": "1:1", "portrait": "2:3", "landscape": "3:2",
    "portrait43": "3:4", "landscape43": "4:3", "story": "9:16",
    "wide": "16:9", "ultrawide": "21:9", "ultratall": "9:21",
    "portrait45": "4:5", "landscape45": "5:4", "portrait14": "1:4",
    "landscape14": "4:1", "portrait18": "1:8", "landscape18": "8:1",
}


def _nonempty_string(value: Any) -> str | None:
    # Identifiers are opaque: check for blank values without changing identity.
    return value if isinstance(value, str) and value.strip() else None


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _finite_number(value: Any) -> int | float | None:
    # bool is an int in Python, but not a stored coordinate/timestamp/count.
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    return None


def ownership_fields(agent_node_id: str = "") -> dict:
    """Return fresh creation metadata from explicit caller context only.

    An empty context denotes ordinary user creation. Non-string or whitespace-
    only contexts are invalid, rather than silently creating an Agent owner.
    """
    if not isinstance(agent_node_id, str) or (agent_node_id and not agent_node_id.strip()):
        raise agent_error(
            400, "INVALID_ARGUMENT", "agent_node_id must be a nonblank string or omitted.",
            "Pass the initiating Agent node ID, or omit it for user creation.",
        )
    return {
        "createdBy": "agent" if agent_node_id else "user",
        "agentNodeId": agent_node_id or None,
        "userTouchedAt": None,
        "promptEditedByUser": False,
    }


def _ownership(node: dict) -> dict:
    agent_id = _nonempty_string(node.get("agentNodeId"))
    is_agent = node.get("createdBy") == "agent" and agent_id is not None
    touched = _finite_number(node.get("userTouchedAt"))
    return {
        "createdBy": "agent" if is_agent else "user",
        "agentNodeId": agent_id if is_agent else None,
        # Legal protection values survive even when creation metadata is corrupt.
        "userTouchedAt": touched if touched is not None and touched >= 0 else None,
        "promptEditedByUser": node.get("promptEditedByUser") is True,
    }


def _validate_fields(fields: Any) -> set[str]:
    # None is the internal omitted sentinel. Transport adapters must reject an
    # explicitly supplied JSON/query null before calling this pure helper.
    if fields is None:
        return set(_FIELDS)
    if not isinstance(fields, list) or any(
        not isinstance(field, str) or field not in _FIELDS for field in fields
    ):
        raise agent_error(
            400, "INVALID_ARGUMENT", "fields must be an array of summary, prompt, refs, or run.",
            "Omit fields for all blocks, or pass [] for summary only.",
        )
    return set(fields) | {"summary"}


def _last_failure(value: Any) -> dict | None:
    if not isinstance(value, dict):
        return None
    if not _nonempty_string(value.get("code")) or not _nonempty_string(value.get("message")):
        return None
    if not isinstance(value.get("hint"), str):
        return None
    return {key: value[key] for key in ("code", "message", "hint")}


def _title(node: dict) -> str:
    return _text(node.get("title")) or _text(node.get("text"))[:50]


def _geometry(node: dict) -> dict:
    return {
        "x": _finite_number(node.get("x")) or 0,
        "y": _finite_number(node.get("y")) or 0,
        "w": _finite_number(node.get("w")),
        "h": _finite_number(node.get("h")),
    }


def _project_prompt(node: dict) -> str:
    if node.get("type") == NODE_TYPE_PROMPT and isinstance(node.get("text"), str):
        return node["text"][:80]
    if node.get("type") == NODE_TYPE_LOOP:
        # smartLoopPromptFieldValues/setSmartLoopPromptFieldValues store a list
        # of strings in variablePrompts, with variablePrompt as legacy fallback;
        # there is no stored loopPromptFields object/HTML structure to stringify.
        fields = node.get("variablePrompts")
        if isinstance(fields, list) and fields:
            # Bound intermediate text as well as the result. Never expand tokens,
            # read upstream nodes, inject defaults, or resurrect an old runPrompt.
            preview = ""
            first = True
            for value in fields:
                if not isinstance(value, str):
                    continue
                if not first:
                    preview += "\n"
                preview += value[:80 - len(preview)]
                first = False
                if len(preview) >= 80:
                    break
            return preview
        if isinstance(node.get("variablePrompt"), str):
            return node["variablePrompt"][:80]
    for key in ("promptDraftText", "prompt", "runPrompt", "text"):
        value = node.get(key)
        if isinstance(value, str):
            return value[:80]  # Explicit blank is the current editable value.
    return ""


def _asset_id(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value if _ASSET_ID.fullmatch(value) else None


def _project_refs(node: dict, edges) -> list[dict]:
    if edges:
        return [
            {"n": ref["n"], "role": ref["role"], "asset_id": ref["asset_id"]}
            for ref in numbered_refs(sorted(edges, key=managed_order))
            if ref["enabled"]
        ]
    # Legacy nodes may still carry the saved reference projection.
    refs = []
    if "reference_asset_ids" in node:
        # Presence is authoritative even for [] or malformed legacy values.
        for value in _list(node["reference_asset_ids"]):
            asset_id = _asset_id(value)
            if asset_id:
                refs.append({"n": len(refs) + 1, "role": "general", "asset_id": asset_id})
    else:
        for value in _list(node.get("runInputRefs")):
            if not isinstance(value, dict):
                continue
            asset_id = _asset_id(value.get("asset_id"))
            if not asset_id:
                continue
            role = value.get("role")
            role = role if isinstance(role, str) and role in ROLES else "general"
            refs.append({"n": len(refs) + 1, "role": role, "asset_id": asset_id})
    return refs


def _saved_string(value: Any) -> str | None:
    return value.strip() if _nonempty_string(value) else None


def _count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _saved_ratio(value: Any) -> str | None:
    value = _saved_string(value)
    match = _RATIO.fullmatch(value) if value is not None else None
    if match and all(math.isfinite(float(part)) and float(part) > 0 for part in match.groups()):
        return value
    return None


def _project_run(node: dict) -> dict:
    settings = node.get("runSettings")
    settings = settings if isinstance(settings, dict) else {}
    aspect_ratio = _saved_ratio(settings.get("aspect_ratio"))
    if aspect_ratio is None:
        ratio = settings.get("ratio")
        if isinstance(ratio, str):
            if ratio in ("custom", "source"):
                aspect_ratio = _saved_ratio(settings.get("customRatio"))
            else:
                aspect_ratio = _RATIO_PRESETS.get(ratio)
    return {
        "model": _saved_string(settings.get("model")) or _saved_string(node.get("model")),
        "aspect_ratio": aspect_ratio or _saved_ratio(node.get("aspect_ratio")),
        "count": _count(settings.get("count")) or _count(node.get("count")),
    }


def project_nodes(canvas: dict, *, group_id=None, fields=None, created_by=None) -> dict:
    """Project node summaries plus requested blocks, without modifying ``canvas``.

    ``fields=None`` returns all blocks, [] only summary; summary is always
    present. Optional None values mean omitted (explicit null is a transport
    validation concern). Filters use immutable normalized creation source and
    the first valid containing group in canvas order, never geometry/recursion.
    """
    selected = _validate_fields(fields)
    if group_id is not None and _nonempty_string(group_id) is None:
        raise agent_error(
            400, "INVALID_ARGUMENT", "group_id must be a nonblank string.",
            "Use a group ID from canvas.list_nodes, or omit group_id.",
        )
    if created_by is not None and (
        not isinstance(created_by, str) or created_by not in ("agent", "user")
    ):
        raise agent_error(
            400, "INVALID_ARGUMENT", "created_by must be agent or user.",
            "Pass agent or user, or omit created_by.",
        )

    canvas = canvas if isinstance(canvas, dict) else {}
    nodes = []
    by_id = {}
    group_nodes = {}
    # Index identity/group order once. The first occurrence of a duplicate ID
    # defines lookup identity; malformed entries cannot become group members.
    for node in _list(canvas.get("nodes")):
        if not isinstance(node, dict):
            continue
        nodes.append(node)
        node_id = _nonempty_string(node.get("id"))
        if node_id is not None and node_id not in by_id:
            by_id[node_id] = node
            if node.get("type") == NODE_TYPE_GROUP:
                group_nodes[node_id] = node

    if group_id is not None:
        if group_id not in by_id:
            raise agent_error(
                404, "NODE_NOT_FOUND", f"Group node not found: {group_id}",
                "Call canvas.list_nodes to find an existing group ID.",
                details={"group_id": group_id},
            )
        if group_id not in group_nodes:
            raise agent_error(
                400, "INVALID_ARGUMENT", "group_id does not identify a group node.",
                "Use the ID of a group from canvas.list_nodes.",
                details={"group_id": group_id},
            )

    parent = {}
    members = {}
    for current_id, group in group_nodes.items():
        items = []
        seen = set()
        for item in _list(group.get("items")):
            item_id = _nonempty_string(item)
            if item_id is None or item_id == current_id or item_id not in by_id or item_id in seen:
                continue
            seen.add(item_id)
            items.append(item_id)
            parent.setdefault(item_id, current_id)
        # Retain valid stored direct members in the group directory, even when
        # another group is the canonical parent of a multiply-contained node.
        members[current_id] = items

    incoming = defaultdict(list)
    outgoing = defaultdict(list)
    reference_edges = defaultdict(list)
    for edge in _list(canvas.get("connections")):
        if not isinstance(edge, dict):
            continue
        source = _nonempty_string(edge.get("from"))
        target = _nonempty_string(edge.get("to"))
        if source is not None and target is not None:
            outgoing[source].append(target)
            incoming[target].append(source)
            if managed_edge(edge, target):
                reference_edges[target].append(edge)

    summaries = []
    for node in nodes:
        node_id = _text(node.get("id"))
        parent_id = parent.get(node_id)
        if group_id is not None and parent_id != group_id:
            continue
        ownership = _ownership(node)
        if created_by is not None and ownership["createdBy"] != created_by:
            continue
        summary = {
            "id": node_id,
            "type": _text(node.get("type")),
            "title": _title(node),
            **_geometry(node),
            "group_id": parent_id,
            **ownership,
            "agentStatus": node.get("agentStatus") if isinstance(node.get("agentStatus"), str) else None,
            "lastFailure": _last_failure(node.get("lastFailure")),
            "incoming": list(incoming.get(node_id, ())),
            "outgoing": list(outgoing.get(node_id, ())),
            "status": _text(node.get("status")) or _text(node.get("generationState")),
        }
        if "prompt" in selected:
            summary["prompt_preview"] = _project_prompt(node)
        if "refs" in selected:
            summary["refs"] = _project_refs(node, reference_edges.get(node_id))
        if "run" in selected:
            summary["run"] = _project_run(node)
        summaries.append(summary)

    groups = []
    for current_id, group in group_nodes.items():
        if group_id is not None and current_id != group_id:
            continue
        ownership = _ownership(group)
        groups.append({
            "id": current_id,
            "type": NODE_TYPE_GROUP,
            "title": _title(group),
            **_geometry(group),
            "items": list(members[current_id]),
            "createdBy": ownership["createdBy"],
            "agentNodeId": ownership["agentNodeId"],
            "refFrame": group.get("refFrame") is True,
            "chatFrame": group.get("chatFrame") is True,
        })
    return {"nodes": summaries, "count": len(summaries), "groups": groups}
