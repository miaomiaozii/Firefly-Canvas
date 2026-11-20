"""A7 layout planning. No canvas writes or confirmation authority live here."""
from dataclasses import dataclass, field
from math import ceil, sqrt
import re

from app.constants import NODE_TYPE_AGENT, NODE_TYPE_GROUP
from app.services import agent_frame
from app.services.agent_errors import agent_error
from app.services.agent_node_state import ownership_fields
from app.services.agent_tools_manifest import LAYOUT_LIMIT as _LIMIT, LAYOUT_SCHEMAS
_FRAME_KEYS = ("x", "y", "w", "h")
_PAD = agent_frame.FRAME_PAD
_HEADER = agent_frame.FRAME_HEADER
_GAP = agent_frame.CELL_GAP


def invalid(message, *, details=None):
    return agent_error(400, "INVALID_ARGUMENT", message, "检查参数，使用 canvas.list_nodes 返回的节点 ID。", details=details)


def _valid(value, schema):
    if "oneOf" in schema:
        return sum(_valid(value, variant) for variant in schema["oneOf"]) == 1
    kind = schema["type"]
    if kind == "object":
        props = schema["properties"]
        return (isinstance(value, dict) and set(schema["required"]) <= value.keys() <= props.keys()
                and all(_valid(v, props[k]) for k, v in value.items()))
    if kind == "string":
        return (isinstance(value, str) and bool(value.strip()) and len(value) <= schema.get("maxLength", 500)
                and ("enum" not in schema or value in schema["enum"]))
    if kind == "array":
        return (isinstance(value, list) and len(value) <= schema["maxItems"]
                and all(_valid(v, schema["items"]) for v in value) and len(value) == len(set(value)))
    if kind == "boolean":
        return type(value) is bool
    return type(value) in (int, float) and schema["minimum"] <= value <= schema["maximum"]


def validate_args(tool, args):
    if tool not in LAYOUT_SCHEMAS or not _valid(args, LAYOUT_SCHEMAS[tool]):
        raise invalid(f"Invalid arguments for {tool}")


def _number(value, default=0):
    try:
        n = float(value) if isinstance(value, str) else value
        return n if type(n) in (int, float) and -_LIMIT <= n <= _LIMIT else default
    except ValueError:
        return default


def _size(node, in_frame=False):
    """Reserve the independently rendered card, not the legacy member thumbnail."""
    kind = node.get("type")
    w, h = _number(node.get("w")), _number(node.get("h"))
    media = kind in ("smart-image", "image", "smart-video", "video", None)
    if in_frame and w > 0 and h > 0 and (media or kind in ("smart-prompt", "smart-loop")):
        if media and not node.get("images") and not node.get("pending") and not node.get("queued"):
            h = max(h, 178)  # .image-node.empty-node overrides the saved box's height.
        return w, h
    if kind == "smart-prompt":
        split = (34 + max(40, min(220, _number(node.get("promptSplitPreviewHeight"), 70)))
                 if node.get("promptSplitEnabled") is True else 0)
        # LLM cards can render input and upstream previews even without saved height.
        base_h = (420 if node.get("llmSystemEnabled") else 360) if node.get("llmEnabled") else 240
        if node.get("llmEnabled"):
            base_h += 176 + max(0, min(400, _number(node.get("llmInstructionHeight"), 58)) - 58)
        return ceil(w if w > 0 and w != 360 else 316), ceil(max(h, base_h + split))
    if kind in ("smart-conversation", "conversation"):
        return ceil(max(280, w or 390)), 96 if node.get("conversation_collapsed") is True else ceil(max(240, h or 430))
    if kind == "smart-loop":
        # Loop body height can depend on connected inputs; use a conservative
        # envelope instead of writing fake w/h to the existing card.
        return ceil(w if w > 0 else 340), ceil(max(h, 168 + 72 + 94 + 3 * 58 + 176))
    if not media:
        return agent_frame.node_size(node)

    images = node.get("images") if isinstance(node.get("images"), list) else []
    count = len(images)
    aspect = node.get("_generationAspect")
    fixed = isinstance(aspect, dict) and _number(aspect.get("w")) > 0 and _number(aspect.get("h")) > 0
    pending = _number(node.get("pending")) > 0 or bool(node.get("queued"))
    batch = int(max(0, min(8, _number(node.get("_batchTotal")))))
    total = batch or int(_number(node.get("pending"))) or count
    if ((pending or not count) and total > 1) or (count > 1 and (batch > 1 or fixed)):
        c = max(2, min(8, total if pending or not count else batch if batch > 1 else count))
        cols = next((k for k in (4, 3, 2) if c % k == 0 and 2 <= c // k <= 4), ceil(sqrt(c)))
        rows = ceil(c / cols)
        return cols * 136 + (cols - 1) * 9 + 32, rows * 102 + (rows - 1) * 9 + 64 + (48 if pending or not count else 0)
    if pending or not count:
        if not fixed:
            aspect = next((img for img in images if isinstance(img, dict) and _number(img.get("width")) > 0
                           and _number(img.get("height")) > 0), None)
            tasks = node.get("pendingTasks")
            task = tasks[0] if isinstance(tasks, list) and tasks and isinstance(tasks[0], dict) else {}
            payload = task.get("requestPayload") or task.get("request_payload") or {}
            size = task.get("size") or (payload.get("size") if isinstance(payload, dict) else "")
            dims = re.search(r"([0-9]{1,7})\s*[x×:：]\s*([0-9]{1,7})", str(size))
            aw, ah = ((_number(aspect["width"]), _number(aspect["height"])) if aspect else
                      tuple(max(1, int(n)) for n in dims.groups()) if dims else (1, 1))
        else:
            aw, ah = max(1, _number(aspect["w"])), max(1, _number(aspect["h"]))
        return ceil(32 + min(260, 380 * aw / ah)), ceil(32 + min(380, 260 * ah / aw))
    if w > (24 if count == 1 else 40) and h > (24 if count == 1 else 40):
        return ceil(w), ceil(h)
    scale = _number(node.get("scale")) or (2 if kind in ("smart-image", "image", None) and count == 1 else 1)
    scale = max(1, scale)
    if count == 1:
        return ceil(288 * scale), ceil(220 * scale)
    # Without explicit dimensions a gallery depends on hydrated image metadata.
    # Reserve full thumbnail columns and use the renderer's maximum cell height.
    grid = next((img["grid"] for img in images if isinstance(img, dict) and isinstance(img.get("grid"), dict)
                 and img["grid"].get("type") == "grid-split"), {})
    cols = max(1, int(_number(grid.get("cols"), min(4, ceil(sqrt(count))))))
    rows = max(1, int(_number(grid.get("rows"), ceil(count / cols))))
    return ceil(cols * (224 * scale + 8) + 32), ceil(rows * (224 * scale + 8) + 32)


def _pos(node):
    return _number(node.get("x")), _number(node.get("y"))


def _rect(node, in_frame=False):
    x, y = _pos(node)
    w, h = _size(node, in_frame)
    return x, y, x + w, y + h


def _geometry(node):
    return {key: node[key] for key in _FRAME_KEYS}


def is_layout_frame(node):
    return (node.get("type") == NODE_TYPE_GROUP and node.get("layoutFrame") is True
            and not node.get("chatFrame") and not node.get("refFrame"))


def _valid_bounds(group):
    return (all(type(group.get(k)) in (int, float) and -_LIMIT <= group[k] <= _LIMIT for k in _FRAME_KEYS)
            and group["w"] > 0 and group["h"] > 0)


def owned(node, owner):
    return (node.get("createdBy") == "agent" and node.get("agentNodeId") == owner
            and node.get("userTouchedAt") is None
            and (node.get("promptEditedByUser") is None or node["promptEditedByUser"] is False)
            and not node.get("locked"))


@dataclass
class LayoutPlan:
    result: dict = field(default_factory=lambda: {"moved": [], "skipped": []})
    mutations: list = field(default_factory=list)
    confirm: list = field(default_factory=list)

    def skip(self, identity, reason):
        self.result["skipped"].append({"id": identity, "reason": reason})

    def upsert(self, before, after):
        if before != after:
            if any(k in after and type(after[k]) in (int, float) and not -_LIMIT <= after[k] <= _LIMIT for k in _FRAME_KEYS):
                raise invalid("Layout would exceed the supported canvas bounds")
            self.mutations.append({"op": "upsert_node", "node": after})

    def move(self, node, x, y, *, confirm=False):
        # Preserve exact anchored endpoints and suppress floating point noise on repeats.
        bx, by = _pos(node)
        if abs(bx - x) < 1e-7 and abs(by - y) < 1e-7:
            return
        self.upsert(node, {**node, "x": x, "y": y})
        self.result["moved"].append(node["id"])
        if confirm:
            self.require_confirmation(node, x, y)

    def require_confirmation(self, node, x, y):
        self.confirm.append({"id": node["id"], "title": str(node.get("title") or node["id"])[:80],
                             "before": dict(zip(("x", "y"), _pos(node))), "after": {"x": x, "y": y}})


def _group_error(group_id, message, hint="请选择 canvas.group_create 新建的排版框；旧分组和会话框不会转换。"):
    return agent_error(400, "INVALID_ARGUMENT", message, hint, details={"group_id": group_id})


def _grid(cards, layout, gap, x, y, fixed=()):
    if not cards:
        return []
    cols = len(cards) if layout == "row" else 1 if layout == "column" else min(3, len(cards))
    # Keep the session-frame slot ordering/padding, but replace its nominal
    # cell pitch with measured column widths and row heights for A7.
    virtual = {"w": 2 * _PAD + cols * agent_frame.CELL_W + (cols - 1) * _GAP}
    sizes = [_size(node, True) for node in cards]
    widths = [max(w for w, _ in sizes[c::cols]) for c in range(cols)]
    heights = [max(h for _, h in sizes[i:i + cols]) for i in range(0, len(cards), cols)]
    placements = []
    for i, (w, h) in enumerate(sizes):
        sx, sy, _ = agent_frame.compute_frame_slot(virtual, i)
        col = (sx - _PAD) // (agent_frame.CELL_W + _GAP)
        row = (sy - _HEADER - _PAD) // (agent_frame.CELL_H + _GAP)
        placements.append((x + _PAD + sum(widths[:col]) + col * gap,
                           y + _HEADER + _PAD + sum(heights[:row]) + row * gap, w, h))
    right = max(px + w for px, _, w, _ in placements)
    bottom = max(py + h for _, py, _, h in placements)
    left, top = placements[0][:2]
    shift = 0
    for l, t, r, b in sorted(fixed, key=lambda r: r[1]):
        if left < r + gap and right + gap > l and top + shift < b + gap and bottom + shift + gap > t:
            shift = b + gap - top
    return [(px, py + shift, px + w, py + shift + h) for px, py, w, h in placements]


class LayoutPlanner:
    def __init__(self, canvas, owner):
        self.nodes = {n["id"]: n for n in canvas.get("nodes", [])}
        self.owner = owner
        self.memberships = {}
        for group in self.nodes.values():
            if group.get("type") == NODE_TYPE_GROUP and isinstance(group.get("items"), list):
                for member in group["items"]:
                    if isinstance(member, str):
                        self.memberships.setdefault(member, set()).add(group["id"])
        self.plan = LayoutPlan()

    def node(self, identity, *, group=False):
        node = self.nodes.get(identity)
        if not node or (group and node.get("type") != NODE_TYPE_GROUP):
            kind = "group_id" if group else "node_id"
            raise agent_error(404, "NODE_NOT_FOUND", f"{'Group' if group else 'Node'} not found: {identity}",
                              "先调用 canvas.list_nodes 查看真实 ID。", details={kind: identity})
        return node

    def eligible(self, node, named=False):
        if owned(node, self.owner):
            return True
        identity = node["id"]
        if node.get("createdBy") == "agent" and node.get("agentNodeId") not in (None, "", self.owner):
            self.plan.skip(identity, "OTHER_AGENT")
        elif named and not node.get("locked"):
            return True
        else:
            self.plan.skip(identity, "USER_NODE")
        return False

    def members(self, group):
        identity = group["id"]
        ids = group.get("items")
        if (not _valid_bounds(group) or identity in self.memberships or not isinstance(ids, list) or
                any(not isinstance(i, str) or i not in self.nodes or
                    self.nodes[i].get("type") == NODE_TYPE_GROUP or self.memberships[i] != {identity} for i in ids) or
                len(ids) != len(set(ids))):
            return None
        return [self.nodes[i] for i in ids]

    def create(self, args, identity, now):
        title = args["title"].strip()
        ids = args.get("node_ids", [])
        for group in self.nodes.values():
            if (is_layout_frame(group) and group.get("createdBy") == "agent" and
                    group.get("agentNodeId") == self.owner and group.get("title") == title):
                if not owned(group, self.owner) or self.members(group) is None:
                    raise _group_error(group["id"], "Matching layout frame is protected or has invalid membership",
                                       "保留该框不变；如需另建排版框，请使用不同标题。")
                for i in ids:
                    if i not in group["items"]:
                        self.plan.skip(i, "NOT_IN_REUSED_GROUP")
                self.plan.result.update(group_id=group["id"], reused=True, **_geometry(group))
                return
        selected = [self.node(i) for i in ids]
        cards = []
        for node in selected:
            i = node["id"]
            if node.get("type") == NODE_TYPE_GROUP:
                raise invalid("Nested layout groups are not supported", details={"group_id": i})
            if i in self.memberships or node.get("smartGroupMember"):
                self.plan.skip(i, "ALREADY_GROUPED")
            elif self.eligible(node, args.get("include_user_nodes", False)):
                cards.append(node)
        if not cards and self.plan.result["skipped"]:
            raise agent_error(403, "NOT_ALLOWED_ON_USER_NODE", "None of the requested nodes can join this layout frame",
                              "请选择尚未入组的本 Agent 卡片，或对用户明确点名的卡片使用 include_user_nodes；不会从旧组或会话框移出卡片。",
                              details={"skipped": self.plan.result["skipped"]})
        anchor = args.get("anchor", "auto")
        if isinstance(anchor, dict):
            x, y = anchor["x"], anchor["y"]
        else:
            reference = selected if anchor == "right_of_selection" and selected else self.nodes.values()
            x = max(0, max((_rect(n)[2] for n in reference), default=0)) + _HEADER
            y = min((_pos(n)[1] for n in selected), default=0)
        group = {"id": identity, "type": NODE_TYPE_GROUP, "title": title, "layoutFrame": True, "items": [n["id"] for n in cards],
                 "x": x, "y": y, "w": 280, "h": 220, "created_at": now, **ownership_fields(self.owner)}
        if "plan_section" in args:
            group["planSection"] = args["plan_section"].strip()
        boxes = _grid(cards, "grid", _GAP, x, y)
        for node, (px, py, _, _) in zip(cards, boxes):
            self.plan.move(node, px, py)
            if not owned(node, self.owner):
                self.plan.require_confirmation(node, px, py)
        self.fit(group, boxes)
        self.plan.upsert(None, group)
        self.plan.result.update(group_id=identity, reused=False, **_geometry(group))

    @staticmethod
    def fit(group, boxes):
        x, y = _pos(group)
        if any(l < x or t < y for l, t, _, _ in boxes):
            raise _group_error(group["id"], "Protected member lies above or left of the frame",
                               "请用户把该成员移回框内后再排列。")
        if boxes:
            group["w"] = max(80, ceil(max(r for _, _, r, _ in boxes) - x + _PAD))
            group["h"] = max(80, ceil(max(b for _, _, _, b in boxes) - y + _PAD))

    def arrange(self, args):
        group = self.node(args["group_id"], group=True)
        identity = group["id"]
        if not is_layout_frame(group):
            raise _group_error(identity, "This group does not use saved layout bounds")
        members = self.members(group)
        if members is None:
            raise _group_error(identity, "Layout frame has invalid bounds or membership",
                               "请检查组框位置和组员；当前仅支持无嵌套且不共享成员的排版框。")
        self.plan.result.update(group_id=identity, **_geometry(group))
        if not self.eligible(group):
            return
        cards = [n for n in members if self.eligible(n)]
        if not cards:
            return
        fixed = [_rect(n, True) for n in members if n not in cards]
        ordering = args.get("sort_by", "current")
        if ordering == "current":
            cards.sort(key=lambda n: _pos(n)[::-1])
        elif ordering == "title":
            cards.sort(key=lambda n: str(n.get("title") or "").casefold())
        elif ordering == "plan_order":
            cards.sort(key=lambda n: next((n[k] for k in ("planOrder", "source_row", "index")
                                           if type(n.get(k)) in (int, float) and -_LIMIT <= n[k] <= _LIMIT), float("inf")))
        boxes = _grid(cards, args["layout"], args.get("gap", _GAP), *_pos(group), fixed)
        for node, (x, y, _, _) in zip(cards, boxes):
            self.plan.move(node, x, y)
        updated = dict(group)
        self.fit(updated, boxes + fixed)
        self.plan.upsert(group, updated)
        self.plan.result.update(_geometry(updated))

    def arrange_groups(self, args):
        pairs = []
        for i in args["group_ids"]:
            group = self.node(i, group=True)
            if not is_layout_frame(group):
                self.plan.skip(i, "LEGACY_GROUP_USE_GROUP_CREATE")
                continue
            if not self.eligible(group):
                continue
            members = self.members(group)
            if members is None:
                self.plan.skip(i, "INVALID_MEMBERSHIP")
                continue
            if any(not owned(member, self.owner) for member in members):
                self.plan.skip(i, "PROTECTED_MEMBER")
                continue
            pairs.append((group, members))
        self.plan.result["groups"] = []
        if not pairs:
            return
        x, y = _pos(pairs[0][0])
        gap = args.get("gap", _GAP)
        for group, members in pairs:
            gx, gy = _pos(group)
            self.plan.move(group, x, y)
            for member in members:
                mx, my = _pos(member)
                self.plan.move(member, mx + (x - gx), my + (y - gy))
            self.plan.result["groups"].append({"group_id": group["id"], "x": x, "y": y, "w": group["w"], "h": group["h"]})
            if args["direction"] == "row":
                x += group["w"] + gap
            else:
                y += group["h"] + gap

    def align_or_distribute(self, tool, args):
        cards = []
        for identity in args["node_ids"]:
            node = self.node(identity)
            parents = self.memberships.get(identity, ())
            if node.get("type") == NODE_TYPE_GROUP:
                self.plan.skip(identity, "USE_ARRANGE_GROUPS")
            elif (node.get("smartGroupMember") or len(parents) > 1 or
                  any(not is_layout_frame(self.nodes[p]) or self.members(self.nodes[p]) is None for p in parents)):
                self.plan.skip(identity, "LEGACY_OR_SHARED_MEMBERSHIP")
            elif self.eligible(node, args.get("include_user_nodes", False)):
                cards.append(node)
        minimum = 3 if tool == "canvas.distribute" and "gap" not in args else 2
        if len(args["node_ids"]) < minimum:
            raise agent_error(400, "TOO_FEW_NODES", f"At least {minimum} distinct nodes are required",
                              "增加目标节点，或为 distribute 指定 gap。")
        if len(cards) < minimum:
            return
        def visible_size(node):
            return _size(node, bool(self.memberships.get(node["id"])))

        if tool == "canvas.align":
            edge = args["edge"]
            axis = 0 if edge in ("left", "center_x", "right") else 1
            factor = 0.5 if edge.startswith("center") else 1 if edge in ("right", "bottom") else 0
            target = _pos(cards[0])[axis] + visible_size(cards[0])[axis] * factor
            positions = [target - visible_size(n)[axis] * factor for n in cards]
        else:
            axis = 0 if args["axis"] == "x" else 1
            cards.sort(key=lambda n: _pos(n)[axis])
            sizes = [visible_size(n)[axis] for n in cards]
            start = _pos(cards[0])[axis]
            gap = args.get("gap")
            if gap is None:
                gap = (_pos(cards[-1])[axis] - start - sum(sizes[:-1])) / (len(cards) - 1)
                if gap < -1e-7:
                    raise invalid("Not enough space for non-overlapping equal gaps; specify gap to extend the span")
                gap = max(0, gap)
            positions = [start + sum(sizes[:i]) + i * gap for i in range(len(cards))]
        for node, coordinate in zip(cards, positions):
            pos = list(_pos(node))
            if abs(pos[axis] - coordinate) < 1e-7:
                continue
            pos[axis] = coordinate
            for group_id in self.memberships.get(node["id"], ()):
                group = self.nodes[group_id]
                x, y = _pos(group)
                w, h = visible_size(node)
                if not (x <= pos[0] <= x + group["w"] - w and y + _HEADER <= pos[1] <= y + group["h"] - h):
                    raise _group_error(group_id, "Requested move would leave saved frame bounds",
                                       "改用 canvas.group_arrange 在框内排布，或减少/调整目标。")
            self.plan.move(node, *pos, confirm=not owned(node, self.owner))


def make_plan(canvas, tool, args, owner, identity, now):
    planner = LayoutPlanner(canvas, owner)
    agent = planner.nodes.get(owner)
    if not agent or agent.get("type") not in (NODE_TYPE_AGENT, "agent"):
        raise invalid("Layout tools require the initiating Agent node of this canvas")
    if identity in planner.nodes:
        raise agent_error(409, "CANVAS_CONFLICT", "Generated group ID already exists", "检查画布并重试。")
    if tool == "canvas.group_create":
        planner.create(args, identity, now)
    elif tool == "canvas.group_arrange":
        planner.arrange(args)
    elif tool == "canvas.arrange_groups":
        planner.arrange_groups(args)
    else:
        planner.align_or_distribute(tool, args)
    return planner.plan
