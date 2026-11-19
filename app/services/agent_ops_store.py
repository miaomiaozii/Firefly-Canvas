"""Nonauthoritative per-project Agent operation diffs; never a canvas backup.

Callers must supply only the parts changed by an operation, not canvas snapshots.
Results are in reverse insertion order; timestamps do not determine retention order.
"""
from __future__ import annotations

import json
import logging
import math
import re
import sqlite3
import threading
import time
import weakref
from typing import Any

from firefly_opsdb import OpsDb

MAX_OPERATIONS = 1000
# External clients have no reliable end event; inactivity is the fallback boundary.
EXTERNAL_TURN_INACTIVITY_SECONDS = 30 * 60
logger = logging.getLogger(__name__)
_FIELDS = frozenset({
    "op_id", "turn_id", "agent_node_id", "tool", "args", "before", "after",
    "touched_node_ids", "created_at", "irreversible_effects", "undone",
})
_JSON_FIELDS = ("args", "before", "after", "touched_node_ids", "irreversible_effects")

# Reservations are shared across OpsDb instances. Never call into a journal or
# ProjectStore while holding this lock: listeners acquire store -> journal -> here.
_reservation_lock = threading.RLock()
_reservations = weakref.WeakKeyDictionary()
_active_families = weakref.WeakKeyDictionary()
# A failed sidecar append makes its entire turn unsafe to undo until restart.
_failed_turns = {}
_failed_operations = set()
_APPEND_FAILURE = "sidecar append failed; this turn cannot be safely undone"
_TURN_LOOKUP_FAILURE = "sidecar turn lookup failed; this turn is incomplete and cannot be safely undone"
_inflight_turns = {}
_warned_oversized_turns = set()


class InvalidOperationParent(ValueError):
    pass


def release_turn(opsdb, project_id, turn_id):
    with _reservation_lock:
        key = (str(opsdb.root.resolve()), project_id, turn_id)
        remaining = _inflight_turns.get(key, 0) - 1
        if remaining > 0:
            _inflight_turns[key] = remaining
        else:
            _inflight_turns.pop(key, None)


def mark_append_failed(opsdb, project_id, record):
    with _reservation_lock:
        key = (str(opsdb.root.resolve()), project_id)
        if record["turn_id"] is None:
            _failed_operations.add((*key, record["op_id"]))
            if (parent_id := record.get("parent_op_id")) is not None:
                _failed_operations.add((*key, parent_id))
        else:
            _failed_turns.setdefault((*key, record["turn_id"]), _APPEND_FAILURE)


def mark_turn_lookup_failed(opsdb, project_id, turn_id):
    with _reservation_lock:
        _failed_turns.setdefault((str(opsdb.root.resolve()), project_id, turn_id), _TURN_LOOKUP_FAILURE)


def undo_failure(opsdb, project_id, record):
    with _reservation_lock:
        key = (str(opsdb.root.resolve()), project_id)
        if ((*key, record["op_id"]) in _failed_operations or
                (*key, record.get("parent_op_id")) in _failed_operations):
            return _APPEND_FAILURE
        return _failed_turns.get((*key, record["turn_id"]))


def set_active_operation(store, project_id, op_id, task_id, running):
    """Synchronize retention protection with append and reservation checks."""
    with _reservation_lock:
        entries = _active_families.setdefault(store, {})
        key = (str(store.root.resolve()), project_id, op_id)
        if running:
            entries.setdefault(key, set()).add(task_id)
        else:
            tasks = entries.get(key)
            if tasks is not None:
                tasks.discard(task_id)
                if not tasks:
                    entries.pop(key, None)


def _active(opsdb, project_id):
    root = str(opsdb.root.resolve())
    return {key[2] for entries in _active_families.values() for key in entries
            if key[:2] == (root, project_id)}


def _reserve(journal, opsdb, project_id, record):
    with _reservation_lock:
        parent_id = record.get("parent_op_id")
        if parent_id is not None:
            local = journal._entries.get((project_id, parent_id))
            pending_parent = any(op_id == parent_id and turn == record["turn_id"]
                                 for op_id, turn, _ in _reserved(opsdb, project_id))
            failed_parent = (local is not None and local["status"] == "append_failed"
                             and local["record"]["turn_id"] == record["turn_id"])
            if not (pending_parent or failed_parent):
                try:
                    parent = get_operation(opsdb, project_id, parent_id)
                except (OSError, sqlite3.Error):
                    if (local is None or local["status"] != "settled" or
                            local["record"]["turn_id"] != record["turn_id"] or
                            parent_id not in _active(opsdb, project_id)):
                        raise
                    parent = local["record"]
                if parent is None or parent["turn_id"] != record["turn_id"] or parent["undone"]:
                    raise ValueError("pending operation parent is missing, pruned, or undone")
        key = (str(opsdb.root.resolve()), project_id, record["op_id"])
        _reservations.setdefault(journal, {})[key] = (record.get("turn_id"), parent_id)


def _release(journal, opsdb, project_id, op_id):
    with _reservation_lock:
        entries = _reservations.get(journal, {})
        entries.pop((str(opsdb.root.resolve()), project_id, op_id), None)


def _reserved(opsdb, project_id):
    root = str(opsdb.root.resolve())
    return [(key[2], *value) for entries in _reservations.values()
            for key, value in entries.items() if key[:2] == (root, project_id)]


def _project_id(project_id: str) -> None:
    if not isinstance(project_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]+", project_id):
        raise ValueError("invalid project_id")


def _string(value: Any, name: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a nonempty string")


def _json_value(value: Any) -> None:
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) is list:
        for item in value:
            _json_value(item)
        return
    if type(value) is dict:
        if value.get("format") == "firefly-canvas-v1":
            raise ValueError("canvas snapshots cannot be stored in agent_ops")
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("JSON object keys must be strings")
            _json_value(item)
        return
    raise ValueError("value must be valid JSON")


def _validate_record(record: dict) -> dict[str, str]:
    if not isinstance(record, dict) or record.keys() - {"parent_op_id"} != _FIELDS:
        raise ValueError("record must contain exactly the operation fields")
    for name in ("op_id", "agent_node_id", "tool", "created_at"):
        _string(record[name], name)
    if record["turn_id"] is not None:
        _string(record["turn_id"], "turn_id")
    if record.get("parent_op_id") is not None:
        _string(record["parent_op_id"], "parent_op_id")
    if type(record["undone"]) is not bool:
        raise ValueError("undone must be a boolean")
    if not isinstance(record["touched_node_ids"], list):
        raise ValueError("touched_node_ids must be a list")
    for node_id in record["touched_node_ids"]:
        _string(node_id, "touched node id")
    if not isinstance(record["irreversible_effects"], list):
        raise ValueError("irreversible_effects must be a list")
    for effect in record["irreversible_effects"]:
        if not ((isinstance(effect, str) and effect.strip()) or
                (isinstance(effect, dict) and effect and any(
                    isinstance(value, str) and value.strip() for value in effect.values()))):
            raise ValueError("irreversible effects must describe an effect")
    encoded = {}
    for name in _JSON_FIELDS:
        _json_value(record[name])
        encoded[name] = json.dumps(record[name], ensure_ascii=False, allow_nan=False)
    same = (json.dumps(record["before"], sort_keys=True, ensure_ascii=False) ==
            json.dumps(record["after"], sort_keys=True, ensure_ascii=False))
    before, after = record["before"], record["after"]
    if (isinstance(before, list) and isinstance(after, list) and len(before) == len(after)
            and before and all(isinstance(a, dict) and isinstance(b, dict) and
                               "before" in a and "after" in b for a, b in zip(before, after))):
        same = all(json.dumps(a["before"], sort_keys=True, ensure_ascii=False) ==
                   json.dumps(b["after"], sort_keys=True, ensure_ascii=False)
                   for a, b in zip(before, after))
    if same and not record["irreversible_effects"]:
        raise ValueError("no-op operations cannot be recorded")
    return encoded


def _decode(row: Any) -> dict:
    result = {name: row[name] for name in (
        "op_id", "turn_id", "agent_node_id", "tool", "created_at",
    )}
    # Preserve the legacy record shape; a missing parent is equivalent to None.
    if row["parent_op_id"] is not None:
        result["parent_op_id"] = row["parent_op_id"]
    for name in _JSON_FIELDS:
        result[name] = json.loads(row[name + "_json"])
    result["undone"] = bool(row["undone"])
    return result


def begin_turn(opsdb: OpsDb, project_id: str, turn_id: str, owner: str, *, external: bool, reserve: bool = False) -> str | None:
    """End the owner's preceding turn and classify reused external IDs atomically."""
    _project_id(project_id)
    _string(turn_id, "turn_id")
    _string(owner, "owner")
    now = time.time()
    with _reservation_lock, opsdb.project_lock(project_id):
        if not (opsdb.root / project_id).is_dir():
            raise KeyError(project_id)
        with opsdb.connect(project_id, create_project_dir=False) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            _expire_external_turns(conn, project_id, now)
            pruned = conn.execute("SELECT 1 FROM agent_ops_pruned_turns WHERE project_id=? AND turn_id=?",
                                  (project_id, turn_id)).fetchone()
            if external and pruned:
                return 'pruned'
            conn.execute("UPDATE agent_ops_turns SET ended=1 WHERE project_id=? AND owner=? AND kind=? AND turn_id<>?",
                         (project_id, owner, 'external' if external else 'hosted', turn_id))
            conn.execute("INSERT OR IGNORE INTO agent_ops_turns VALUES (?, ?, ?, ?, ?, 0)",
                         (project_id, turn_id, owner, 'external' if external else 'hosted', now))
        if reserve:
            key = (str(opsdb.root.resolve()), project_id, turn_id)
            _inflight_turns[key] = _inflight_turns.get(key, 0) + 1
    return None


def _expire_external_turns(conn, project_id, now):
    conn.execute("UPDATE agent_ops_turns SET ended=1 WHERE project_id=? AND kind='external' "
                 "AND ended=0 AND last_write<=?", (project_id, now - EXTERNAL_TURN_INACTIVITY_SECONDS))


def append_operation(opsdb: OpsDb, project_id: str, record: dict) -> None:
    """Append and prune only unprotected, ended units in one transaction."""
    _project_id(project_id)
    encoded = _validate_record(record)
    from app.services.agent_ops_journal import before_append_operation
    if before_append_operation(opsdb, project_id, record):
        return
    with _reservation_lock, opsdb.project_lock(project_id):
        if not (opsdb.root / project_id).is_dir():
            raise KeyError(project_id)
        key = (str(opsdb.root.resolve()), project_id)
        pruned_turns = set()
        pruned_operations = set()
        oversized_turns = set()
        with opsdb.connect(project_id, create_project_dir=False) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            turn_id = record["turn_id"]
            parent_op_id = record.get("parent_op_id")
            if parent_op_id is not None:
                parent = conn.execute(
                    "SELECT turn_id, undone FROM agent_ops_operations WHERE project_id = ? AND op_id = ?",
                    (project_id, parent_op_id),
                ).fetchone()
                if parent is None or parent["turn_id"] != turn_id or parent["undone"]:
                    raise InvalidOperationParent("parent must exist in the same project and turn and not be undone")
            if turn_id is not None:
                pruned = conn.execute(
                    "SELECT 1 FROM agent_ops_pruned_turns WHERE project_id = ? AND turn_id = ?",
                    (project_id, turn_id),
                ).fetchone()
                if pruned is not None:
                    raise ValueError("cannot append to a pruned turn")
                conn.execute("UPDATE agent_ops_turns SET last_write=? WHERE project_id=? AND turn_id=?",
                             (time.time(), project_id, turn_id))
            conn.execute(
                "INSERT INTO agent_ops_operations "
                "(project_id, op_id, turn_id, parent_op_id, agent_node_id, tool, args_json, before_json, "
                "after_json, touched_node_ids_json, created_at, irreversible_effects_json, undone) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (project_id, record["op_id"], turn_id, parent_op_id, record["agent_node_id"], record["tool"],
                 encoded["args"], encoded["before"], encoded["after"], encoded["touched_node_ids"],
                 record["created_at"], encoded["irreversible_effects"], int(record["undone"])),
            )
            count = conn.execute(
                "SELECT COUNT(*) FROM agent_ops_operations WHERE project_id = ?", (project_id,)
            ).fetchone()[0]
            _expire_external_turns(conn, project_id, time.time())
            reserved = _reserved(opsdb, project_id)
            active = _active(opsdb, project_id)
            candidates = conn.execute(
                "SELECT id, op_id, turn_id FROM agent_ops_operations WHERE project_id=? "
                "AND (turn_id IS NOT NULL OR parent_op_id IS NULL) ORDER BY id", (project_id,)
            ).fetchall()
            seen = set()
            for candidate in candidates:
                if count <= MAX_OPERATIONS:
                    break
                tid = candidate['turn_id']
                if tid is not None:
                    if tid in seen:
                        continue
                    seen.add(tid)
                    state = conn.execute("SELECT ended FROM agent_ops_turns WHERE project_id=? AND turn_id=?",
                                         (project_id, tid)).fetchone()
                    rows = conn.execute("SELECT op_id FROM agent_ops_operations WHERE project_id=? AND turn_id=?",
                                        (project_id, tid)).fetchall()
                    ids = {row['op_id'] for row in rows}
                    if not state or not state['ended']:
                        if len(ids) > MAX_OPERATIONS and (*key, tid) not in _warned_oversized_turns:
                            oversized_turns.add(tid)
                        continue
                    if tid == turn_id or _inflight_turns.get((*key, tid)):
                        continue
                    if ids & active or any(t == tid or parent in ids for _, t, parent in reserved):
                        continue
                    conn.execute('SAVEPOINT retention_unit')
                    try:
                        conn.execute("DELETE FROM agent_ops_operations WHERE project_id=? AND turn_id=?", (project_id, tid))
                        conn.execute("INSERT OR IGNORE INTO agent_ops_pruned_turns VALUES (?, ?)", (project_id, tid))
                    except sqlite3.Error:
                        conn.execute('ROLLBACK TO retention_unit')
                        conn.execute('RELEASE retention_unit')
                        logger.warning('Agent operation retention failed; keeping the inserted operation', exc_info=True)
                        break
                    conn.execute('RELEASE retention_unit')
                    pruned_turns.add(tid)
                else:
                    rows = conn.execute(
                        "WITH RECURSIVE family(op_id) AS (SELECT op_id FROM agent_ops_operations WHERE project_id=? AND id=? "
                        "UNION ALL SELECT child.op_id FROM agent_ops_operations child JOIN family "
                        "ON child.parent_op_id=family.op_id WHERE child.project_id=?) SELECT op_id FROM family",
                        (project_id, candidate['id'], project_id)).fetchall()
                    ids = {row['op_id'] for row in rows}
                    if (record['op_id'] in ids or ids & active or
                            any(op in ids or parent in ids for op, _, parent in reserved)):
                        continue
                    try:
                        conn.execute("DELETE FROM agent_ops_operations WHERE project_id=? AND id=?", (project_id, candidate['id']))
                    except sqlite3.Error:
                        logger.warning('Agent operation retention failed; keeping the inserted operation', exc_info=True)
                        break
                count -= len(ids)
                pruned_operations.update(ids)
        # A rolled-back retention transaction must not re-enable undo.
        for tid in pruned_turns:
            turn_key = (*key, tid)
            _failed_turns.pop(turn_key, None)
            _warned_oversized_turns.discard(turn_key)
        _failed_operations.difference_update((*key, op_id) for op_id in pruned_operations)
        for tid in oversized_turns:
            _warned_oversized_turns.add((*key, tid))
            logger.warning('Agent operation turn %s exceeds retention cap; retained whole', tid)


def list_operations(opsdb: OpsDb, project_id: str, *, turn_id: str | None = None,
                    limit: int = 100) -> list[dict]:
    """List newest first; a null turn_id means no turn filter."""
    _project_id(project_id)
    if turn_id is not None:
        _string(turn_id, "turn_id")
    if type(limit) is not int or limit <= 0:
        raise ValueError("limit must be a positive integer")
    with opsdb.project_lock(project_id):
        if not opsdb.has_db(project_id):
            return []
        with opsdb.connect(project_id, create_project_dir=False, create_db=False) as conn:
            if turn_id is None:
                rows = conn.execute(
                    "SELECT * FROM agent_ops_operations WHERE project_id = ? ORDER BY id DESC LIMIT ?",
                    (project_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM agent_ops_operations WHERE project_id = ? AND turn_id = ? ORDER BY id DESC LIMIT ?",
                    (project_id, turn_id, limit),
                ).fetchall()
    return [_decode(row) for row in rows]


def list_turn_snapshot(opsdb: OpsDb, project_id: str, turn_id: str) -> list[dict]:
    """Read every retained record in a turn from one database snapshot."""
    _project_id(project_id)
    _string(turn_id, "turn_id")
    with opsdb.project_lock(project_id):
        if not opsdb.has_db(project_id):
            return []
        with opsdb.connect(project_id, create_project_dir=False, create_db=False) as conn:
            rows = conn.execute(
                "SELECT * FROM agent_ops_operations WHERE project_id = ? AND turn_id = ? ORDER BY id DESC",
                (project_id, turn_id),
            ).fetchall()
    return [_decode(row) for row in rows]


def list_operation_tree(opsdb: OpsDb, project_id: str, op_id: str) -> list[dict]:
    """Return a retained operation and all its descendants in reverse insertion order."""
    _project_id(project_id)
    _string(op_id, "op_id")
    with opsdb.project_lock(project_id):
        if not opsdb.has_db(project_id):
            return []
        with opsdb.connect(project_id, create_project_dir=False, create_db=False) as conn:
            rows = conn.execute(
                "WITH RECURSIVE family(op_id) AS ("
                "SELECT op_id FROM agent_ops_operations WHERE project_id = ? AND op_id = ? "
                "UNION ALL SELECT child.op_id FROM agent_ops_operations child JOIN family "
                "ON child.parent_op_id = family.op_id WHERE child.project_id = ?) "
                "SELECT o.* FROM agent_ops_operations o JOIN family ON o.op_id = family.op_id "
                "WHERE o.project_id = ? ORDER BY o.id DESC",
                (project_id, op_id, project_id, project_id),
            ).fetchall()
    return [_decode(row) for row in rows]


def get_operation(opsdb: OpsDb, project_id: str, op_id: str) -> dict | None:
    _project_id(project_id)
    _string(op_id, "op_id")
    with opsdb.project_lock(project_id):
        if not opsdb.has_db(project_id):
            return None
        with opsdb.connect(project_id, create_project_dir=False, create_db=False) as conn:
            row = conn.execute(
                "SELECT * FROM agent_ops_operations WHERE project_id = ? AND op_id = ?",
                (project_id, op_id),
            ).fetchone()
    return _decode(row) if row is not None else None


def mark_undone(opsdb: OpsDb, project_id: str, op_ids: list[str]) -> None:
    """Mark existing IDs undone atomically; unknown IDs are rejected."""
    _project_id(project_id)
    if not isinstance(op_ids, list):
        raise ValueError("op_ids must be a list")
    for op_id in op_ids:
        _string(op_id, "op_id")
    if not op_ids:
        return
    with opsdb.project_lock(project_id):
        if not opsdb.has_db(project_id):
            raise KeyError(op_ids[0])
        with opsdb.connect(project_id, create_project_dir=False, create_db=False) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            for op_id in dict.fromkeys(op_ids):
                cursor = conn.execute(
                    "UPDATE agent_ops_operations SET undone = 1 WHERE project_id = ? AND op_id = ?",
                    (project_id, op_id),
                )
                if cursor.rowcount != 1:
                    raise KeyError(op_id)
