"""Bound, single-use previews for Agent batch prompt edits (#398)."""
from collections import OrderedDict
from dataclasses import dataclass
from html import escape
import secrets
import threading
import time
import weakref

from app.services.agent_errors import agent_error
from app.services.agent_refs import identifier, invalid, require_object

MAX_EDITS = 50
MAX_TEXT = 16_384
PREVIEW_TTL = 300
MAX_PENDING = 64
_PROMPT_FIELDS = {"smart-prompt": "text", "smart-image": "prompt", "smart-video": "prompt"}
_DRAFT_FIELDS = ("promptDraftText", "promptDraftHtml")


def stale():
    return agent_error(409, "DIFF_STALE", "提示词预览已使用、失效或与当前提示词不符。",
                       "请用 canvas.batch_prompt_edit 重新预览，再请求确认。")


def parse(args):
    args = require_object(args, "batch_prompt_edit", required=("apply",), optional=("edits", "diff_id"))
    if args["apply"] is True and set(args) == {"apply", "diff_id"}:
        return identifier(args["diff_id"], "diff_id")
    if args["apply"] is not False or set(args) != {"apply", "edits"}:
        raise invalid("Preview requires apply:false and edits; application requires apply:true and diff_id")
    edits = args["edits"]
    if not isinstance(edits, list) or not 1 <= len(edits) <= MAX_EDITS:
        raise invalid(f"edits must contain 1..{MAX_EDITS} items")
    seen = set()
    for edit in edits:
        require_object(edit, "edit", required=("node_id",), optional=("prompt", "find", "replace", "append"))
        node_id = identifier(edit["node_id"], "node_id")
        fields = edit.keys() - {"node_id"}
        if fields not in ({"prompt"}, {"find", "replace"}, {"append"}):
            raise invalid("Each edit needs exactly one of prompt, find and replace, or append")
        if any(not isinstance(edit[key], str) or len(edit[key]) > MAX_TEXT for key in fields):
            raise invalid(f"Prompt values must be strings of at most {MAX_TEXT} characters")
        if edit.get("find") == "" or node_id in seen:
            raise invalid("find cannot be empty and node_id must be unique in the batch")
        seen.add(node_id)
    return edits


def _signature(node):
    field = _PROMPT_FIELDS.get(node.get("type"))
    return (node.get("type"), node.get("created_at"), node.get("promptEditedByUser") is True,
            node.get(field), node.get("prompt"), *(node.get(key) for key in _DRAFT_FIELDS))


@dataclass(frozen=True)
class Item:
    node_id: str
    signature: tuple
    before: str
    after: str

    @property
    def protected(self):
        return self.signature[2]

    def public(self):
        return {"node_id": self.node_id, "before": self.before, "after": self.after,
                "protected": self.protected}


def _nodes(canvas):
    return {node["id"]: node for node in canvas.get("nodes", [])}


def preview(canvas, edits):
    nodes = _nodes(canvas)
    items = []
    for edit in edits:
        node_id = edit["node_id"]
        node = nodes.get(node_id)
        if node is None:
            raise agent_error(404, "NODE_NOT_FOUND", f"Node {node_id} not found",
                              "先调用 canvas.list_nodes 确认节点 ID。")
        field = _PROMPT_FIELDS.get(node.get("type"))
        if field is None:
            raise invalid(f"Node {node_id} does not have an editable scalar prompt")
        signature = _signature(node)
        before = node.get("promptDraftText") if field == "prompt" else None
        if not isinstance(before, str):
            before = node.get(field, node.get("prompt")) or ""
        if not isinstance(before, str) or len(before) > MAX_TEXT:
            raise invalid(f"Node {node_id} does not have a bounded text prompt")
        if "prompt" in edit:
            after = edit["prompt"]
        elif "append" in edit:
            after = before + edit["append"]
        else:
            old, new = edit["find"], edit["replace"]
            if len(before) + before.count(old) * (len(new) - len(old)) > MAX_TEXT:
                raise invalid(f"Resulting prompt exceeds {MAX_TEXT} characters")
            after = before.replace(old, new)
        if len(after) > MAX_TEXT:
            raise invalid(f"Resulting prompt exceeds {MAX_TEXT} characters")
        items.append(Item(node_id, signature, before, after))
    return tuple(items)


def changed(items):
    return tuple(item for item in items if item.before != item.after)


def prompt_update(node, prompt, *, legacy=False):
    field = _PROMPT_FIELDS.get(node.get("type"), "prompt")
    update = {field: prompt}
    if legacy or "prompt" in node:
        update["prompt"] = prompt  # Generation also reads this legacy field on text nodes.
    if field == "prompt" and (not legacy or any(key in node for key in _DRAFT_FIELDS)):
        # Composer drafts override the stored prompt in the browser. Keep the
        # newly approved text visible without reusing stale HTML.
        update.update(promptDraftText=prompt, promptDraftHtml=escape(prompt).replace("\n", "<br>"))
    return update


def build_mutations(canvas, items, now):
    nodes = _nodes(canvas)
    for item in items:
        if item.node_id not in nodes or _signature(nodes[item.node_id]) != item.signature:
            raise stale()
    mutations = []
    for item in changed(items):
        node = nodes[item.node_id]
        update = prompt_update(node, item.after)
        mutations.append({"op": "upsert_node", "node": {**node, **update, "updated_at": now}})
    return mutations


_PENDING = weakref.WeakKeyDictionary()
_LOCK = threading.Lock()


def remember(store, project, binding, items):
    diff_id = secrets.token_urlsafe(24)
    now = time.monotonic()
    with _LOCK:
        pending = _PENDING.setdefault(store, OrderedDict())
        while pending and (len(pending) >= MAX_PENDING or next(iter(pending.values()))[0] <= now):
            pending.popitem(last=False)
        pending[diff_id] = (now + PREVIEW_TTL, project, binding, items)
    return diff_id


def take(store, project, binding, diff_id):
    with _LOCK:
        pending = _PENDING.get(store, {})
        record = pending.get(diff_id)
        if record is None or record[0] <= time.monotonic() or record[1:3] != (project, binding):
            raise stale()
        del pending[diff_id]
        return record[3]
