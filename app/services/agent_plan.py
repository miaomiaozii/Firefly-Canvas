"""Session-scoped workbook previews and verified conversation references (#399)."""
from collections import Counter, OrderedDict
from copy import deepcopy
from dataclasses import dataclass, replace
from hashlib import sha256
import os
from pathlib import Path
import re
from threading import Lock
from time import monotonic
from uuid import uuid4
from weakref import WeakKeyDictionary
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile
from zlib import error as ZlibError

from fastapi import HTTPException

from app.constants import NODE_TYPE_AGENT, NODE_TYPE_GROUP, NODE_TYPE_IMAGE
from app.services.agent_attachments import AGENT_ATTACHMENT_MAX_BYTES, attachment_root, resolve_attachments
from app.services.agent_errors import agent_error
from app.services.agent_node_state import ownership_fields
from app.services.agent_refs import invalid, require_object
from app.services.excel_plan_mutation import build_mutations
from excel_importer import XlsxParser
from layout_engine import DeterministicLayout
from plan_ir.agent_preview import MAX_TASKS, project_workbook
from plan_ir.text_utils import build_display_no, join_distinct_lines

ATTACHMENT_ID = re.compile(r"[a-f0-9]{32}")
ARCHIVE_TYPE = "confirmed_agent_attachment"
_TTL = 1800
_CACHE = WeakKeyDictionary()
_LOCK = Lock()


def file_not_allowed():
    return agent_error(400, "FILE_NOT_ALLOWED", "只接受当前 Agent 会话内已发送的附件 ID，不接受路径。",
                       "请将文件作为附件发送到当前会话，再使用该附件 ID。")


def stale():
    return agent_error(409, "PLAN_STALE", "方案预览、画布、原附件或参考图已变化；未应用任何任务。",
                       "请检查最新预览和参考图，再请求确认。")


def ref_not_allowed():
    return agent_error(400, "REF_NOT_ALLOWED", "参考图不是该会话中已明确归档的项目图片。",
                       "请单独确认归档该会话附件，或从方案中移除此引用。")


def attachment_id(value):
    if not isinstance(value, str) or not ATTACHMENT_ID.fullmatch(value):
        raise file_not_allowed()
    return value


def text(value, field, length=200):
    if not isinstance(value, str) or not value.strip() or len(value) > length:
        raise invalid(f"{field} must be a nonblank string of at most {length} characters")
    return value


def ids(values, field, limit):
    if not isinstance(values, list) or len(values) > limit:
        raise invalid(f"{field} must be a list of at most {limit} IDs")
    result = [text(value, field, 128) for value in values]
    if len(set(result)) != len(result):
        raise invalid(f"{field} must not contain duplicates")
    return result


def conversation(store, manager, project, actor, canvas=None):
    session = manager.conversation_session(project, actor)
    if not session:
        raise file_not_allowed()
    node = next((n for n in (canvas if canvas is not None else store.canvas(project))["nodes"]
                 if n.get("id") == actor and n.get("type") in (NODE_TYPE_AGENT, "agent")), None)
    if not node or (node.get("agentCli") or "pi") != session.agent_cli:
        raise file_not_allowed()
    # Codex's runtime generation changes each turn; the resumed thread does not.
    return session.input_epoch, session.agent_cli, session.input_branch


def submitted(manager, project, actor, host, branch):
    session = manager.conversation_session(project, actor)
    if not session:
        raise file_not_allowed()
    events = [record for record in manager.load_events(project, actor, host=host)["events"]
              if record.get("project_id") == project and record.get("node_id") == actor]
    bindings = {(record.get("input_epoch"), record["generation"]): record["input_branch"] for record in events
                if record.get("input_binding") is True}
    found = {}
    for record in events:
        if record.get("accepted_input") is not True:
            continue
        identity = record.get("input_epoch"), record["generation"]
        record_branch = record.get("input_branch") or bindings.get(identity, "")
        if record_branch != branch or (not branch and (session.input_epoch, session.generation) != identity):
            continue
        for item in record["event"].get("attachments", []):
            if isinstance(item, dict) and ATTACHMENT_ID.fullmatch(str(item.get("id", ""))):
                found[item["id"]] = {key: item.get(key) for key in ("id", "name", "size", "type")}
    return found


def attachment(store, project, actor, submitted_records, identifier):
    expected = submitted_records.get(identifier)
    if not expected:
        raise file_not_allowed()
    try:
        root = attachment_root(store.root, project, actor)
        # Never use the path printed to the host, supplied in a tool call, or in replay.
        record, = resolve_attachments(root, [identifier])
        if not all(record.get(key) == value for key, value in expected.items()):
            raise file_not_allowed()
        return record
    except (HTTPException, OSError, ValueError, KeyError, TypeError):
        raise file_not_allowed() from None


def read_attachment(record, *, content=False):
    try:
        path = Path(record["path"])
        if path.resolve() != path:
            raise ValueError
        with path.open("rb") as file:
            size = record["size"]
            stat = os.fstat(file.fileno())
            if not 0 < size <= AGENT_ATTACHMENT_MAX_BYTES or stat.st_size != size:
                raise ValueError
            digest = sha256()
            data = file.read(size + 1) if content else None
            if content:
                if len(data) != size:
                    raise ValueError
                digest.update(data)
            else:
                remaining = size
                while remaining:
                    chunk = file.read(min(remaining, 1024**2))
                    if not chunk:
                        raise ValueError
                    digest.update(chunk)
                    remaining -= len(chunk)
            current = path.stat()
            if (current.st_size, current.st_mtime_ns) != (size, stat.st_mtime_ns) or not os.path.samestat(stat, current):
                raise ValueError
            return (digest.hexdigest(), data) if content else digest.hexdigest()
    except (OSError, ValueError, TypeError):
        raise file_not_allowed() from None


def archive_source(actor, record):
    return {"type": ARCHIVE_TYPE, "locator": f"agent-attachment:{actor}:{record['id']}", "name": record["name"]}


def _key(*parts):
    return sha256(repr(parts).encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True)
class Preview:
    plan_id: str
    scope: tuple
    attachment_id: str
    digest: str
    sheet: str | None
    name: str
    generation: int
    expires: float
    data: dict

    def response(self, records):
        return {"plan_id": self.plan_id, "generation": self.generation, "preview": {
            **deepcopy(self.data), "sections": dict(Counter(task["section"] for task in self.data["tasks"])),
            "suggested_refs": [], "source": {"attachment_id": self.attachment_id, "name": self.name,
                                                      "sheet": self.sheet, "sha256": self.digest},
        }}


def parse_workbook(content, sheet):
    try:
        parsed = XlsxParser(content, literal_only=True).parse()
        data = project_workbook(parsed, sheet)
    except (BadZipFile, ParseError, RuntimeError, ValueError, OverflowError, NotImplementedError, EOFError, ZlibError) as exc:
        raise agent_error(400, "NOT_XLSX", "附件不是支持的 XLSX 方案工作簿，或超出解析限制。") from exc
    if not data["tasks"]:
        sheets = list(parsed["sheets"])
        raise agent_error(422, "NO_PLAN_SHEET", "找不到可用的方案任务。",
                          "可选工作表：" + ("、".join(sheets) or "无"),
                          details={"sheets": sheets, "excluded": data["excluded"]})
    return data


def remember(store, scope, record, digest, sheet, data):
    with _LOCK:
        pending = _CACHE.setdefault(store, OrderedDict())
        now = monotonic()
        for expired in [identity for identity, preview in pending.items() if preview.expires <= now]:
            del pending[expired]
        for old in pending.values():
            if (old.scope, old.attachment_id, old.digest, old.sheet) == (scope, record["id"], digest, sheet):
                return old
        key = uuid4().hex
        while len(pending) >= 64:
            pending.popitem(last=False)
        ordinals = Counter()
        for task in data["tasks"]:
            row = task["source_row"]
            ordinals[row] += 1
            task["task_id"] = "task-" + _key(digest, row, ordinals[row])
        _link_main(data["tasks"])
        preview = Preview(key, scope, record["id"], digest, sheet, record["name"], 1, now + _TTL, data)
        pending[key] = preview
        return preview


def get(store, scope, plan_id, args):
    with _LOCK:
        preview = _CACHE.get(store, {}).get(plan_id)
        if (not preview or preview.scope != scope or preview.expires <= monotonic()
                or ("generation" in args and (type(args["generation"]) is not int or args["generation"] != preview.generation))):
            raise stale()
        return preview


def _link_main(tasks):
    main = [task for task in tasks if "主图" in task["section"]]
    for task in tasks:
        index = task["linked_main_index"]
        task["linked_task_id"] = main[index - 1]["task_id"] if task["same_as_main"] and 0 < index <= len(main) else None


def _task(tasks, task_id):
    found = next((task for task in tasks if task["task_id"] == task_id), None)
    if found is None:
        raise agent_error(404, "TASK_NOT_FOUND", "方案任务不存在。", "请使用当前预览返回的 task_id。")
    return found


def edit(store, project, actor, preview, ops, records):
    if not isinstance(ops, list) or not 1 <= len(ops) <= MAX_TASKS:
        raise invalid(f"ops must contain 1..{MAX_TASKS} edits")
    data = deepcopy(preview.data)
    tasks = data["tasks"]
    fields = ("select", "rename", "merge_into", "set_section")
    for op in ops:
        require_object(op, "plan edit", required=("task_id",), optional=fields)
        task = _task(tasks, text(op["task_id"], "task_id", 128))
        edits = op.keys() - {"task_id"}
        if len(edits) != 1:
            raise invalid("Each edit must specify exactly one operation")
        action, = edits
        value = op[action]
        if action == "select":
            if type(value) is not bool:
                raise invalid("select must be boolean")
            task["selected"] = value
        elif action in ("rename", "set_section"):
            task["name" if action == "rename" else "section"] = " ".join(text(value, action).split())
        else:
            target = _task(tasks, text(value, action, 128))
            if target is task:
                raise invalid("Cannot merge a task into itself")
            for field in ("base_prompt", "overlay_plan"):
                target[field] = join_distinct_lines(target[field], task[field])
            target["source_rows"] = list(dict.fromkeys(target["source_rows"] + task["source_rows"]))
            for suggestion in task["suggested_refs"]:
                if suggestion not in target["suggested_refs"]:
                    target["suggested_refs"].append(suggestion)
            target["selected"] |= task["selected"]
            tasks.remove(task)
    numbers = Counter()
    for task in tasks:
        numbers[task["section"]] += 1
        task["display_no"] = build_display_no(task["section"], numbers[task["section"]])
    _link_main(tasks)
    edited = replace(preview, generation=preview.generation + 1, data=data)
    with _LOCK:
        if _CACHE.get(store, {}).get(preview.plan_id) is not preview or preview.expires <= monotonic():
            raise stale()
        _CACHE[store][preview.plan_id] = edited
    return edited


def selected(preview, args):
    tasks = preview.data["tasks"]
    if "task_ids" in args:
        task_ids = ids(args["task_ids"], "task_ids", MAX_TASKS)
        return [_task(tasks, task_id) for task_id in task_ids]
    return [task for task in tasks if task["selected"]]


def prepare_mutations(canvas, actor, preview, tasks):
    """Never upsert a surviving task or grow a user-edited frame on a repeated apply."""
    ids_in_canvas = {node["id"] for node in canvas["nodes"]}
    existing = {node.get("excel_task_id") for node in canvas["nodes"]
                if isinstance(node.get("planLineage"), dict) and node["planLineage"].get("source_sha256") == preview.digest}
    missing, skipped, layouts, metadata, results = [], [], [], {}, []
    for index, task in enumerate(tasks):
        task_id = task["task_id"]
        if task_id in existing:
            skipped.append(task_id)
            continue
        missing.append(task)
        layouts.append({key: task[key] for key in ("task_id", "section", "display_no", "name", "base_prompt",
                                                   "overlay_plan", "target_aspect_ratio")}
                       | {"generation_references": []})
        metadata[task_id] = {"overlay_plan": task["overlay_plan"], "planOrder": index, "planLineage": {
            "plan_id": preview.plan_id, "source_attachment_id": preview.attachment_id, "source_sha256": preview.digest,
            "source_row": task["source_row"], "source_rows": task["source_rows"],
            "linked_task_id": task["linked_task_id"],
        }}
        results.append({"task_id": task_id, "prompt_node": f"{task_id}-prompt", "gen_node": f"{task_id}-image",
                        "ref_nodes": []})
    operations = DeterministicLayout().generate_canvas_operations({"tasks": layouts}, canvas)
    groups = []
    by_section = {}
    owner = ownership_fields(actor)
    for op in operations:
        if op["type"] != "create_node":
            continue
        if op["node_id"] in ids_in_canvas:
            raise stale()
        op["extra_data"].update(metadata[op["excel_task_id"]], **owner)
        if op["node_type"] == NODE_TYPE_IMAGE:
            # The connected prompt is authoritative; import only an empty generation slot (#399).
            op["extra_data"]["runSettings"].update(prompt="", count=1)
        by_section.setdefault(op["excel_section"], []).append(op)
    for section, members in by_section.items():
        group_id = "plan-frame-" + _key(preview.plan_id, section, canvas["revision"])
        if group_id in ids_in_canvas:
            raise stale()
        groups.append({"group_id": group_id, "section": section})
        x, y = min(op["x"] for op in members) - 24, min(op["y"] for op in members) - 38
        operations.append({"type": "create_node", "node_id": group_id, "node_type": NODE_TYPE_GROUP,
                           "label": section, "x": x, "y": y, "excel_section": section,
                           "width": max(op["x"] + op["width"] for op in members) - x + 24,
                           "height": max(op["y"] + op["height"] for op in members) - y + 24,
                           "extra_data": {**owner, "layoutFrame": True, "planSection": section,
                                          "items": [op["node_id"] for op in members]}})
    mutations = build_mutations(operations)
    for mutation in mutations:
        if mutation["op"] == "upsert_edge":
            mutation["edge"]["kind"] = "input"
    edges = {edge["id"] for edge in canvas.get("connections", [])}
    if any(mutation["op"] == "upsert_edge" and mutation["edge"]["id"] in edges for mutation in mutations):
        raise stale()
    return mutations, missing, {"groups": groups, "tasks": results, "already_applied": skipped, "op_id": None}
