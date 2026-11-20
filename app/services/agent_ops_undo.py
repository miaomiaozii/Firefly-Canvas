"""Synchronous, conflict-checked Agent operation listing and canvas undo.

The store lock serializes this service with ProjectStore and journal mutations.
A successful canvas replacement is observed separately from flush's later errors;
only then are sidecar records marked undone. There is no cross-file transaction.
"""
from __future__ import annotations

from copy import deepcopy
import sqlite3
import json

from firefly_opsdb import OpsDb
from firefly_projects import InvalidMutation
from app.services import agent_ops_delta as delta_ops, agent_ops_store


def list_ops(store, project_id, *, turn_id=None, limit=100):
    """Return retained records, newest first; this never creates an ops.db."""
    return agent_ops_store.list_operations(OpsDb(store.root), project_id, turn_id=turn_id, limit=limit)


def _delta(record):
    before, after = record["before"], record["after"]
    if not isinstance(before, list) or not isinstance(after, list) or len(before) != len(after):
        raise ValueError("invalid operation delta")
    result = []
    for old, new in zip(before, after):
        if not isinstance(old, dict) or not isinstance(new, dict):
            raise ValueError("invalid operation delta entry")
        common = {key: value for key, value in old.items() if key != "before"}
        if not delta_ops._equal(common, {key: value for key, value in new.items() if key != "after"}):
            raise ValueError("operation delta sides disagree")
        kind, entity_id, path = common.get("entity"), common.get("id"), common.get("path")
        keys = {"entity", "id", "path"} | ({"node_ids"} if kind == "connection" else set())
        if (common.keys() != keys or kind not in ("node", "connection")
                or not isinstance(entity_id, str) or not entity_id
                or not isinstance(path, list) or any(not isinstance(k, str) for k in path)):
            raise ValueError("invalid operation delta identity")
        if kind == "connection" and (not isinstance(common["node_ids"], list)
                                     or any(not isinstance(n, str) for n in common["node_ids"])):
            raise ValueError("invalid connection node IDs")
        for state in (old.get("before"), new.get("after")):
            if not isinstance(state, dict) or type(state.get("present")) is not bool:
                raise ValueError("invalid operation delta state")
            if state.keys() != ({"present", "value"} if state["present"] else {"present"}):
                raise ValueError("invalid operation delta state")
        result.append({**deepcopy(common), "before": deepcopy(old["before"]), "after": deepcopy(new["after"])})
    if not result and not record["irreversible_effects"]:
        raise ValueError("empty operation delta without irreversible effects")
    return result


def _mutations(before, after):
    old = {kind: delta_ops._index(before, kind) for kind in ("node", "connection")}
    new = {kind: delta_ops._index(after, kind) for kind in ("node", "connection")}
    mutations = []
    for kind, op, key in (("connection", "remove_edge", "edge_id"), ("node", "remove_node", "node_id")):
        mutations.extend({"op": op, key: entity_id} for entity_id in old[kind] if entity_id not in new[kind])
    for kind, op, key in (("node", "upsert_node", "node"), ("connection", "upsert_edge", "edge")):
        mutations.extend({"op": op, key: deepcopy(entity)} for entity_id, entity in new[kind].items()
                         if entity_id not in old[kind] or not delta_ops._equal(old[kind][entity_id], entity))
    return mutations


def _same_delta_values(left, right, delta):
    indexes = [{kind: delta_ops._index(canvas, kind) for kind in ("node", "connection")}
               for canvas in (left, right)]

    def value(index, change):
        entity = index[change["entity"]].get(change["id"], delta_ops._MISSING)
        for key in change["path"]:
            if not isinstance(entity, dict):
                return delta_ops._MISSING
            entity = entity.get(key, delta_ops._MISSING)
        return entity

    return all(delta_ops._equal(value(indexes[0], change), value(indexes[1], change)) for change in delta)


def _broadcast_ids(baseline, expected, deltas):
    """Identify inverses evidenced by replay from the actual persisted canvas."""
    replay = deepcopy(baseline)
    applied = []
    for op_id, delta in deltas:
        replayed, conflicts, already = delta_ops.apply_inverse(replay, delta)
        if not conflicts:
            replay = replayed
            if not already:
                applied.append((op_id, delta))
    return [op_id for op_id, delta in applied
            if _same_delta_values(replay, expected, delta)
            and not _same_delta_values(baseline, expected, delta)]


def _completed_inverse_ids(canvas, deltas):
    # Reconstruct the operation chain to recognize inverses hidden by older ones.
    forward = deepcopy(canvas)
    for _, delta in reversed(deltas):
        swapped = [{**change, "before": change["after"], "after": change["before"]} for change in delta]
        forward, conflicts, _ = delta_ops.apply_inverse(forward, swapped)
        if conflicts:
            return set()
    replay = deepcopy(forward)
    for _, delta in deltas:
        replay, conflicts, _ = delta_ops.apply_inverse(replay, delta)
        if conflicts:
            return set()
    if not all(_same_delta_values(replay, canvas, delta) for _, delta in deltas):
        return set()
    return {op_id for op_id, _ in deltas}


def undo_ops(store, project_id, *, op_id=None, turn_id=None, is_running=None):
    """Undo an op and descendants or all retained operations in a turn.

    is_running(project_id, op_id), when supplied, must report active generation;
    any running operation rejects the entire undo without cancelling generation.

    Result keys: status, reverted, already_reverted, conflicts,
    irreversible_effects (items contain op_id and effect), error. A pending result
    means an original operation has not settled, or the inverse is only in memory.
    A post-replacement error is reported even if the undo was marked successfully.
    broadcast_op_ids, when present, identifies newly persisted canvas inverses.
    """
    agent_ops_store._project_id(project_id)
    if (op_id is None) == (turn_id is None):
        raise ValueError("provide exactly one of op_id or turn_id")
    agent_ops_store._string(op_id if op_id is not None else turn_id, "op_id" if op_id is not None else "turn_id")
    db = OpsDb(store.root)
    result = {"status": "not_found", "reverted": [], "already_reverted": [],
              "conflicts": [], "irreversible_effects": [], "error": None}
    with store._lock:
        target = {"op_id": op_id, "turn_id": turn_id}
        if reason := agent_ops_store.undo_failure(db, project_id, target):
            result.update(status="conflict", error=reason,
                          conflicts=[{"op_id": op_id, "node_id": None, "reason": reason}])
            return result
        records = (agent_ops_store.list_operation_tree(db, project_id, op_id) if op_id is not None else
                   agent_ops_store.list_turn_snapshot(db, project_id, turn_id))
        failed = [(record, reason) for record in records
                  if (reason := agent_ops_store.undo_failure(db, project_id, record))]
        if failed:
            result.update(status="conflict", error=failed[0][1],
                          conflicts=[{"op_id": record["op_id"], "node_id": None, "reason": reason}
                                     for record, reason in failed])
            return result
        entries = []
        for journal in store._agent_ops_journals:
            with journal._lock:
                entries.extend(deepcopy(entry) for (pid, _), entry in journal._entries.items()
                               if pid == project_id and entry["status"] != "settled")
        if turn_id is not None:
            affected = [entry for entry in entries if entry["record"]["turn_id"] == turn_id]
        else:
            family = {op_id} | {record["op_id"] for record in records}
            while True:
                expanded = family | {entry["op_id"] for entry in entries
                                     if entry["record"].get("parent_op_id") in family}
                if expanded == family:
                    break
                family = expanded
            affected = [entry for entry in entries if entry["op_id"] in family]
        if affected:
            failed = [(entry, reason) for entry in affected
                      if (reason := agent_ops_store.undo_failure(db, project_id, entry["record"]))]
            if failed:
                result.update(status="conflict", error=failed[0][1],
                              conflicts=[{"op_id": entry["op_id"], "node_id": None, "reason": reason}
                                         for entry, reason in failed])
                return result
            conflicted = any(entry["status"] == "conflict" for entry in affected)
            result.update(status="conflict" if conflicted else "pending",
                          conflicts=[{"op_id": entry["op_id"], "node_id": None,
                                      "reason": f"original operation is {entry['status']}"}
                                     for entry in affected])
            return result
        if not records:
            return result
        result["irreversible_effects"] = [{"op_id": record["op_id"], "effect": deepcopy(effect)}
                                          for record in records for effect in record["irreversible_effects"]]
        active = [record for record in records if not record["undone"]]
        result["already_reverted"] = [record["op_id"] for record in records if record["undone"]]
        if not active:
            result["status"] = "already_reverted"
            return result
        canvas = store.open_project(project_id)["canvas"]
        # A process crash drops the in-memory tokens, not the admitted task. Do
        # not undo a family (or just one of its children) before reconciliation.
        pending = {task['op_id'] for node in canvas.get('nodes', [])
                   for task in node.get('pendingTasks', []) if isinstance(task, dict)
                   and str(task.get('taskId', '')).startswith('agent_img_')
                   and isinstance(task.get('op_id'), str)}
        running = [record['op_id'] for record in active
                   if record['op_id'] in pending or record.get('parent_op_id') in pending
                   or (is_running is not None and is_running(project_id, record['op_id']))]
        if running:
            result.update(status="generating", running=running, error="生成仍在进行")
            return result
        deltas = [(record["op_id"], _delta(record)) for record in active]
        deltas = [(op, delta) for op, delta in deltas if delta]
        # Saved revisions can lag a successful replacement when a later flush step fails.
        try:
            with (store.root / project_id / "canvas.json").open(encoding="utf-8") as source:
                baseline = json.load(source)
            superseded = set(_broadcast_ids(baseline, canvas, deltas))
        except (OSError, UnicodeError, ValueError, TypeError, KeyError, AttributeError):
            baseline = None
            superseded = set()
        superseded.update(_completed_inverse_ids(canvas, deltas))
        candidate = deepcopy(canvas)
        already = []
        for current_op, delta in deltas:
            updated, conflicts, is_already = delta_ops.apply_inverse(candidate, delta)
            if conflicts:
                # An older inverse may already have removed a newer inverse's target.
                if current_op in superseded and _same_delta_values(canvas, candidate, delta):
                    already.append(current_op)
                    continue
                result.update(status="conflict", conflicts=[{**item, "op_id": current_op} for item in conflicts])
                return result
            candidate = updated
            if is_already:
                already.append(current_op)
        mutations = _mutations(canvas, candidate)
        if mutations:
            # Preflight the real mutation rules, including server-owned task fields,
            # before changing the live canvas; do not silently accept normalization.
            preview = deepcopy(canvas)
            try:
                for mutation in mutations:
                    store._apply_mutation(preview, mutation, trusted_agent_bindings=True)
            except InvalidMutation as exc:
                result.update(status="conflict", conflicts=[{"node_id": None, "reason": f"store rejected inverse: {exc}"}])
                return result
            store._ensure_output_occurrence_ids(preview, deepcopy(store._histories.get(project_id, [])))
            if (not delta_ops._equal(preview.get("nodes", []), candidate.get("nodes", [])) or
                    not delta_ops._equal(preview.get("connections", []), candidate.get("connections", []))):
                result.update(status="conflict", conflicts=[{"node_id": None, "reason": "store mutation changes additional canvas data"}])
                return result
            store.apply_agent_host_mutations(project_id, mutations, base_revision=canvas["revision"],
                                             force_empty=bool(canvas.get("nodes")) and not candidate.get("nodes"))
        if not deltas:
            try:
                agent_ops_store.mark_undone(db, project_id, [record["op_id"] for record in active])
            except (OSError, sqlite3.Error, KeyError) as exc:
                result.update(status="error", error=f"{type(exc).__name__}: {exc}")
                return result
            result.update(status="reverted", reverted=[record["op_id"] for record in active])
            return result
        expected = store._project_canvas_for_persistence(store._canvases[project_id])
        try:
            broadcast_ids = _broadcast_ids(baseline, expected, deltas) if baseline is not None else []
        except (ValueError, TypeError, KeyError, AttributeError):
            broadcast_ids = []
        observed = False

        def on_persisted(pid, persisted):
            nonlocal observed
            if pid == project_id and delta_ops._equal(persisted, expected):
                observed = True

        store.add_persisted_canvas_listener(on_persisted)
        flush_error = None
        try:
            try:
                store.flush(project_id)
            except Exception as exc:
                # A listener can fail after replacement; the receipt, not the exception, proves undo.
                flush_error = f"{type(exc).__name__}: {exc}"
        finally:
            store.remove_persisted_canvas_listener(on_persisted)
        if not observed:
            result.update(status="pending", error=flush_error)
            return result
        if broadcast_ids:
            result["broadcast_op_ids"] = broadcast_ids
        try:
            agent_ops_store.mark_undone(db, project_id, [record["op_id"] for record in active])
        except (OSError, sqlite3.Error, KeyError) as exc:
            result.update(status="error", error=f"{type(exc).__name__}: {exc}")
            if flush_error:
                result["error"] += f"; flush: {flush_error}"
            return result
        result["already_reverted"].extend(already)
        result["reverted"] = [record["op_id"] for record in active if record["op_id"] not in already]
        result.update(status="reverted" if result["reverted"] else "already_reverted", error=flush_error)
        return result
