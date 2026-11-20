"""Agent-managed references: edge identity, upload order and numbered preview."""
import re
import uuid
from collections.abc import Callable

from app.constants import NODE_TYPE_IMAGE
from app.services.agent_errors import agent_error
from firefly_projects import ProjectNotFound

ROLES = {
    "product": "产品主体", "detail": "细节", "layout": "布局",
    "scene_style": "场景/风格", "general": "普通",
}
_ROLE_ORDER = {name: i for i, name in enumerate(ROLES)}
_MASK = re.compile(r"_遮罩_(?:Cryptomatte|ID)", re.IGNORECASE)
_ASSET_ID = re.compile(r"[A-Za-z0-9._-]{1,200}")
IMAGE_TYPES = frozenset({NODE_TYPE_IMAGE, "image"})


def require_object(value, name, *, required=(), optional=()):
    if not isinstance(value, dict) or set(required) - value.keys() or value.keys() - set((*required, *optional)):
        raise invalid(f"Invalid {name} object")
    return value


def invalid(message):
    return agent_error(400, "INVALID_ARGUMENT", message, "检查工具参数定义后重试。")


def identifier(value, name):
    if not isinstance(value, str) or not value.strip():
        raise invalid(f"{name} must be a nonblank string")
    return value


def validate_asset_id(value):
    if not isinstance(value, str) or not _ASSET_ID.fullmatch(value):
        raise invalid("asset_id must be a project asset ID")
    return value


def media_image(store, project_id, asset_id, *, reference):
    code, status = ("REF_NOT_IN_PROJECT", 400) if reference else ("ASSET_NOT_FOUND", 404)
    try:
        record = store.media_record(project_id, asset_id)
    except ProjectNotFound as exc:
        raise agent_error(status, code, f"Project asset not found: {asset_id}",
                          "先调用 media.list；如需要，可用 media.upload 或 canvas.place_media。") from exc
    if _MASK.search(record.get("name") or ""):
        raise agent_error(400, "ASSET_IS_MASK", "Mask files cannot be used as canvas reference images",
                          "选用同名的 beauty 图，不要使用 Cryptomatte / ID 遮罩。")
    if reference and record.get("kind") != "image":
        raise invalid("Only image assets can be attached as image references")
    return {"asset_id": asset_id, "kind": record["kind"], "name": record["name"]}


def managed_edge(edge, target):
    return (isinstance(edge, dict) and edge.get("kind") == "input"
            and edge.get("agentRef") is True and edge.get("to") == target)


def managed_order(edge):
    order = edge.get("order")
    return order if type(order) is int and 0 < order < 2**53 else float("inf")


def managed_edges(canvas, target):
    edges = [edge for edge in canvas.get("connections", []) if managed_edge(edge, target)]
    return sorted(edges, key=managed_order)


def numbered_refs(edges):
    count = 0
    refs = []
    for edge in edges:
        asset, source, role = edge.get("asset_id"), edge.get("from"), edge.get("role")
        if not isinstance(asset, str) or not _ASSET_ID.fullmatch(asset) or not isinstance(source, str):
            continue
        if not isinstance(role, str) or role not in ROLES:
            role = "general"
        enabled = edge.get("enabled") is True
        count += int(enabled)
        refs.append({"n": count if enabled else None, "role": role, "enabled": enabled,
                     "asset_id": asset, "from_node": source})
    return refs


def role_prefix(roles):
    labels = [f"图{n}：{ROLES.get(role, ROLES['general'])}" for n, role in enumerate(roles, 1) if role is not None]
    return "参考图说明：" + "；".join(labels) + "。" if labels else ""


def with_role_prefix(prompt, roles):
    prefix = role_prefix(roles)
    return f"{prefix}\n\n{prompt}" if prefix else prompt


def ref_result(edges):
    refs = numbered_refs(edges)
    return {"refs": refs, "prefix_preview": role_prefix(ref["role"] for ref in refs if ref["enabled"]),
            "warnings": ["REFS_MANY"] if sum(ref["enabled"] for ref in refs) > 8 else []}


def _role(value):
    if not isinstance(value, str) or value not in ROLES:
        raise invalid("Unknown reference role")
    return value


def parse_attachments(args):
    require_object(args, "attach_refs", required=("node_id", "refs"), optional=("mode",))
    mode = args.get("mode", "append")
    if mode not in ("append", "replace") or not isinstance(args["refs"], list):
        raise invalid("Expected refs array and append/replace mode")
    for ref in args["refs"]:
        require_object(ref, "reference", required=("source",), optional=("role", "enabled"))
        source = require_object(ref["source"], "source", optional=("asset_id", "node_id", "image_index"))
        if set(source) == {"asset_id"}:
            validate_asset_id(source["asset_id"])
        elif set(source) in ({"node_id"}, {"node_id", "image_index"}):
            identifier(source["node_id"], "source.node_id")
            index = source.get("image_index", 0)
            if type(index) is not int or index < 0:
                raise invalid("image_index must be a nonnegative integer")
        else:
            raise invalid("source must name one project asset or one node image")
        _role(ref.get("role", "general"))
        if type(ref.get("enabled", True)) is not bool:
            raise invalid("enabled must be a boolean")
    return mode, args["refs"]


def parse_reorder(args):
    require_object(args, "set_ref_order", required=("node_id",), optional=("order", "role_overrides"))
    order, overrides = args.get("order", []), args.get("role_overrides", {})
    if not {"order", "role_overrides"}.intersection(args) or not isinstance(order, list) or not isinstance(overrides, dict):
        raise invalid("Expected order array and/or role_overrides object")
    for asset in [*order, *overrides]:
        validate_asset_id(asset)
    if len(set(order)) != len(order):
        raise invalid("order contains duplicate asset IDs")
    for role in overrides.values():
        _role(role)
    return order, overrides


def attach(edges, refs, mode, target, resolve: Callable):
    by_asset = {edge["asset_id"]: edge for edge in edges}
    selected = dict(by_asset) if mode == "append" else {}
    for ref in refs:
        asset, source = resolve(ref["source"], by_asset)
        existing = by_asset.get(asset)
        selected[asset] = {"id": existing["id"] if existing else f"edge-{uuid.uuid4().hex[:12]}",
                           "from": source, "to": target, "kind": "input", "agentRef": True,
                           "asset_id": asset, "role": ref.get("role", "general"), "enabled": ref.get("enabled", True)}
    return _ordered(selected.values())


def reorder(edges, order, overrides):
    by_asset = {edge["asset_id"]: edge for edge in edges}
    unknown = (set(order) | set(overrides)) - by_asset.keys()
    if unknown:
        raise agent_error(400, "REF_NOT_ATTACHED", "Reference is not attached to this node",
                          "只传当前节点已挂的 Agent 参考图资源 ID。", details={"asset_ids": sorted(unknown)})
    ids = dict.fromkeys([*order, *by_asset])
    return _ordered({**by_asset[asset], "role": overrides.get(asset, by_asset[asset]["role"])} for asset in ids)


def _ordered(edges):
    # Explicit reorder is a tie-break within each role, not a bypass of the
    # upload rule. Both clients read the persisted order, not a second role table.
    ranked = sorted(edges, key=lambda edge: _ROLE_ORDER[edge["role"]])
    return [{**edge, "order": i} for i, edge in enumerate(ranked, 1)]


def edge_changes(before, after):
    old = {edge["id"]: edge for edge in before}
    new = {edge["id"]: edge for edge in after}
    return ([{"op": "remove_edge", "edge_id": key} for key in old.keys() - new.keys()]
            + [{"op": "upsert_edge", "edge": edge} for key, edge in new.items() if edge != old.get(key)])
