"""In-process settlement of Agent operations against canvas replacement events.

Use ``with journal.mutation(project_id) as operation`` around the *Agent's*
mutation and operation.register(record, delta), after opening the project. Do not
flush inside that block before registration. The store lock prevents user writes and timer flushes from
interleaving with the captured delta. Flush normally afterwards; its exceptions
remain the caller's, while operation.result() reports settlement independently.
The record's before and after must be the lists returned by record_sides(delta),
and its touched_node_ids must equal the delta's touched IDs. op_id must be globally
unique per project, including across journal instances. Pending state is in memory:
a process crash can lose a log entry.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import logging
import sqlite3
import threading
import weakref

from app.services import agent_ops_delta as delta_ops
from app.services import agent_ops_store


_journals_lock = threading.RLock()
_journals = weakref.WeakSet()
_appending = threading.local()
logger = logging.getLogger(__name__)


def before_append_operation(opsdb, project_id, record):
    """Queue direct effects behind canvas entries. Return True if handled.

    The store calls this outside its database/reservation locks, after validation.
    Journal-originated appends bypass this hook; queued records are memory-only.
    """
    if (getattr(_appending, "active", False) or record["before"] != [] or
            record["after"] != [] or not record["irreversible_effects"]):
        return False
    with _journals_lock:
        journals = [journal for journal in _journals
                    if journal._opsdb.root.resolve() == opsdb.root.resolve()]
    for journal in journals:
        with journal._store._lock, journal._lock:
            if journal._closed:
                continue
            key = (project_id, record["op_id"])
            if key in journal._entries:
                raise ValueError("operation already registered")
            agent_ops_store._validate_record(record)
            journal._entries[key] = {
                "project_id": project_id, "op_id": record["op_id"],
                "status": "pending", "conflicts": [], "error": None,
                "record": deepcopy(record), "delta": [], "attempted": False,
                "observed": True,
            }
            entries = [entry for (pid, _), entry in journal._entries.items() if pid == project_id]
            if all(entry.get("observed", False) for entry in entries):
                journal._on_persisted(project_id, journal._last_canvas.get(project_id, {}))
            if journal._entries[key]["status"] != "settled":
                agent_ops_store.mark_append_failed(opsdb, project_id, record)
            return True
    return False


def record_sides(delta):
    """Encode only changed fields for the operation record's before/after."""
    return tuple([{key: deepcopy(value) for key, value in change.items() if key != other}
                  for change in delta] for other in ("after", "before"))


def _conflicts(canvas, delta):
    indexes = {kind: delta_ops._index(canvas, kind) for kind in ("node", "connection")}
    conflicts = []
    for change in delta:
        entity = indexes[change["entity"]].get(change["id"], delta_ops._MISSING)
        path = change["path"]
        if path and entity is not delta_ops._MISSING:
            valid, current = delta_ops._at_path(entity, path)
        else:
            valid, current = not path, entity
        if not valid or not delta_ops._equal(current, delta_ops._value(change["after"])):
            conflicts.append(delta_ops._conflict(change, "persisted value differs from after"))
    return conflicts


def _blocked_conflicts(delta, blocked):
    conflicts = []
    for change in delta:
        for later in blocked:
            if change["entity"] != later["entity"] or change["id"] != later["id"]:
                continue
            path, other = change["path"], later["path"]
            if path[:len(other)] == other or other[:len(path)] == path:
                conflicts.append(delta_ops._conflict(change, "later change could not be reversed"))
                break
    return conflicts


class PendingOperation:
    def __init__(self, journal, project_id, before):
        self._journal = journal
        self._project_id = project_id
        self._before = before
        self._key = None
        self._active = True
        self._owner = threading.get_ident()

    def register(self, record, delta):
        """Register the exact Agent mutation while the mutation context is held."""
        if not self._active or self._owner != threading.get_ident() or self._key is not None:
            raise RuntimeError("register exactly once inside the mutation context")
        agent_ops_store._validate_record(record)
        agent_ops_store._json_value(delta)
        after = self._journal._store._project_canvas_for_persistence(
            self._journal._store._canvases[self._project_id]
        )
        actual = delta_ops.build_delta(self._before, after)
        if ((not actual and not record["irreversible_effects"])
                or not delta_ops._equal(actual, delta)):
            raise ValueError("delta must exactly describe the Agent mutation")
        before, after = record_sides(actual)
        if (not delta_ops._equal(record["before"], before)
                or not delta_ops._equal(record["after"], after)
                or record["touched_node_ids"] != delta_ops.touched_node_ids(actual)):
            raise ValueError("record must contain the exact delta and touched node IDs")
        key = (self._project_id, record["op_id"])
        with self._journal._lock:
            if key in self._journal._entries:
                raise ValueError("operation already registered")
            agent_ops_store._reserve(self._journal, self._journal._opsdb, self._project_id, record)
            self._journal._entries[key] = {
                "project_id": self._project_id, "op_id": record["op_id"],
                "status": "pending", "conflicts": [], "error": None,
                "record": deepcopy(record), "delta": deepcopy(delta), "attempted": False,
            }
            self._key = key
        return self.result()

    def result(self):
        if self._key is None:
            raise RuntimeError("operation not registered")
        return self._journal.result(*self._key)


class AgentOpsJournal:
    """Observe one ProjectStore; sidecar errors never escape its listener."""

    def __init__(self, store, opsdb):
        self._store = store
        self._opsdb = opsdb
        self._lock = threading.RLock()
        self._entries = {}
        self._last_canvas = {}
        self._closed = False
        with _journals_lock:
            _journals.add(self)
        with store._lock:
            store._agent_ops_journals.append(self)
            store.add_persisted_canvas_listener(self._on_persisted)

    @contextmanager
    def mutation(self, project_id):
        """Serialize one Agent mutation and its registration with all store writes."""
        agent_ops_store._project_id(project_id)
        with self._store._lock:
            with self._lock:
                if self._closed:
                    raise RuntimeError("journal closed")
            before = self._store._project_canvas_for_persistence(self._store._canvases[project_id])
            operation = PendingOperation(self, project_id, before)
            try:
                yield operation
            finally:
                operation._active = False
                operation._before = None

    def result(self, project_id, op_id):
        """Return an independent status snapshot; does not read or write canvas."""
        with self._lock:
            entry = self._entries[(project_id, op_id)]
            return deepcopy({key: entry[key] for key in ("project_id", "op_id", "status", "conflicts", "error")})

    def close(self):
        # Lock order must match the synchronous listener: store, then journal.
        with self._store._lock:
            with self._lock:
                if not self._closed:
                    self._store.remove_persisted_canvas_listener(self._on_persisted)
                    self._closed = True
                    # Unsettled entries must still block undo and retention after close.
                    if all(entry["status"] == "settled" for entry in self._entries.values()):
                        self._store._agent_ops_journals.remove(self)

    def _on_persisted(self, project_id, canvas):
        with self._lock:
            self._last_canvas[project_id] = deepcopy(canvas)
            entries = [entry for (pid, _), entry in self._entries.items() if pid == project_id]
            for entry in entries:
                entry["observed"] = True
            unresolved = [i for i, entry in enumerate(entries) if entry["status"] in ("pending", "error")]
            if not unresolved:
                return
            entries = entries[min(unresolved):]
            virtual = canvas
            blocked = []
            proofs = {}
            for entry in reversed(entries):
                if entry["status"] == "conflict":
                    blocked.extend(entry["delta"])
                    continue
                try:
                    conflicts = _conflicts(virtual, entry["delta"])
                    conflicts += _blocked_conflicts(entry["delta"], blocked)
                    if conflicts:
                        blocked.extend(entry["delta"])
                    else:
                        reversed_canvas, inverse_conflicts, _ = delta_ops.apply_inverse(virtual, entry["delta"])
                        if inverse_conflicts:
                            # The operation is represented, but reversing it could
                            # discard a later structural dependency.
                            blocked.extend(entry["delta"])
                        else:
                            virtual = reversed_canvas
                    proofs[entry["op_id"]] = (conflicts, None)
                except ValueError as exc:
                    blocked.extend(entry["delta"])
                    proofs[entry["op_id"]] = ([], exc)

            # Sidecar insertion order is operation order, not proof order.
            for entry in entries:
                if entry["status"] in ("settled", "conflict", "append_failed"):
                    continue
                proven = False
                try:
                    # Unexpected errors still require checking for a prior commit.
                    existing = (agent_ops_store.get_operation(self._opsdb, project_id, entry["op_id"])
                                if entry["attempted"] else None)
                    if existing is not None:
                        expected = deepcopy(entry["record"])
                        if expected.get("parent_op_id") is None:
                            expected.pop("parent_op_id", None)
                        if not delta_ops._equal(existing, expected):
                            raise ValueError("op_id already belongs to an existing record")
                    else:
                        conflicts, error = proofs[entry["op_id"]]
                        if error is not None:
                            raise error
                        if conflicts:
                            entry.update(status="conflict", conflicts=conflicts)
                            continue
                        proven = True
                        if not entry["attempted"] and agent_ops_store.get_operation(self._opsdb, project_id, entry["op_id"]) is not None:
                            raise ValueError("op_id already belongs to an existing record")
                        entry["attempted"] = True
                        _appending.active = True
                        try:
                            agent_ops_store.append_operation(self._opsdb, project_id, entry["record"])
                        finally:
                            _appending.active = False
                    entry.update(status="settled", error=None)
                    agent_ops_store._release(self, self._opsdb, project_id, entry["op_id"])
                except Exception as exc:
                    # Fail closed for an unproven write; only known sidecar loss may settle.
                    record = entry["record"]
                    parent = self._entries.get((project_id, record.get("parent_op_id")))
                    lost_parent = (isinstance(exc, agent_ops_store.InvalidOperationParent) and
                                   parent is not None and parent["status"] == "append_failed")
                    if proven and (isinstance(exc, (OSError, sqlite3.Error)) or lost_parent):
                        # #393: do not replay a lost log write or hold up subsequent writes.
                        agent_ops_store.mark_append_failed(self._opsdb, project_id, record)
                        logger.warning("Agent operation %s could not be logged: %s", entry["op_id"], exc)
                        entry.update(status="append_failed", error=f"{type(exc).__name__}: {exc}")
                        agent_ops_store._release(self, self._opsdb, project_id, entry["op_id"])
                        continue
                    if entry["attempted"] or not isinstance(exc, ValueError):
                        agent_ops_store.mark_append_failed(self._opsdb, project_id, record)
                    entry.update(status="error", error=f"{type(exc).__name__}: {exc}")
                    break
