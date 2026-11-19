import copy
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import firefly_json_io
from firefly_opsdb import OpsDb
from firefly_projects import ProjectStore
from app.services.agent_ops_delta import build_delta, touched_node_ids
from app.services import agent_ops_store
from app.services.agent_ops_journal import AgentOpsJournal, record_sides
from app.services.agent_ops_undo import undo_ops


def record(op_id, delta=None):
    if delta is None:
        delta = build_delta({"nodes": [{"id": "n", "type": "text", "value": 1}]},
                            {"nodes": [{"id": "n", "type": "text", "value": 2}]})
    before, after = record_sides(delta)
    return {"op_id": op_id, "turn_id": None, "agent_node_id": "agent", "tool": "node.update",
            "args": {}, "before": before, "after": after,
            "touched_node_ids": ["n"], "created_at": "2026-01-01T00:00:00Z",
            "irreversible_effects": [], "undone": False}


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = ProjectStore(Path(self.tmp.name) / "projects", snapshot_delay=0)
        self.addCleanup(self.store.close)
        self.pid = self.store.create_project("test")["id"]
        self.db = OpsDb(self.store.root)
        self.update(1)
        self.store.flush(self.pid)
        self.journal = AgentOpsJournal(self.store, self.db)
        self.addCleanup(self.journal.close)
        self.path = self.store.root / self.pid / "canvas.json"
        self.records = {}

    def update(self, value, pid=None):
        pid = pid or self.pid
        canvas = self.store.open_project(pid)["canvas"]
        self.store.apply_mutations(pid, [{"op": "upsert_node", "node": {"id": "n", "type": "text", "value": value}}],
                                   base_revision=canvas["revision"])

    def register(self, op_id="op", value=2, *, pid=None, turn=None):
        pid = pid or self.pid
        with self.journal.mutation(pid) as operation:
            before = self.store.open_project(pid)["canvas"]
            self.update(value, pid)
            after = self.store.open_project(pid)["canvas"]
            delta = build_delta(before, after)
            self.records[op_id] = record(op_id, delta)
            self.records[op_id]["turn_id"] = turn
            state = operation.register(self.records[op_id], delta)
        self.assertEqual(state["status"], "pending")
        return operation

    def persisted(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def mutate(self, *mutations):
        canvas = self.store.open_project(self.pid)["canvas"]
        self.store.apply_mutations(self.pid, list(mutations), base_revision=canvas["revision"])

    def register_mutations(self, op_id, *mutations):
        with self.journal.mutation(self.pid) as op:
            before = self.store.open_project(self.pid)["canvas"]
            self.mutate(*mutations)
            delta = build_delta(before, self.store.open_project(self.pid)["canvas"])
            rec = record(op_id, delta)
            rec["touched_node_ids"] = touched_node_ids(delta)
            self.records[op_id] = rec
            op.register(rec, delta)
        return op

    def node(self, **fields):
        return {"op": "upsert_node", "node": {"id": "n", "type": "text", **fields}}

    def test_success_exact_record_no_user_attribution_and_idempotence(self):
        self.update(9)
        self.store.flush(self.pid)
        self.assertEqual(agent_ops_store.list_operations(self.db, self.pid), [])
        op = self.register()
        before_flush = self.persisted()
        self.assertEqual(op.result()["status"], "pending")
        self.assertEqual(self.persisted(), before_flush)
        self.store.flush(self.pid)
        self.assertEqual(op.result()["status"], "settled")
        self.assertEqual(agent_ops_store.get_operation(self.db, self.pid, "op"), self.records["op"])
        self.update(8)
        self.store.flush(self.pid)
        self.assertEqual(len(agent_ops_store.list_operations(self.db, self.pid)), 1)
        self.assertEqual(op.result()["status"], "settled")

    def test_pre_replace_failure_then_retry(self):
        op = self.register()
        old = self.persisted()
        original = firefly_json_io._replace_staged
        def fail(staging, target):
            if target == self.path:
                raise OSError("before canvas")
            return original(staging, target)
        with patch.object(firefly_json_io, "_replace_staged", side_effect=fail):
            with self.assertRaisesRegex(OSError, "before canvas"):
                self.store.flush(self.pid)
        self.assertEqual(self.persisted(), old)
        self.assertEqual(op.result()["status"], "pending")
        self.assertEqual(agent_ops_store.list_operations(self.db, self.pid), [])
        self.store.flush(self.pid)
        self.assertEqual(op.result()["status"], "settled")
        self.assertEqual(agent_ops_store.get_operation(self.db, self.pid, "op"), record("op"))

    def test_pre_replace_failure_then_overwrite_including_revert_is_conflict(self):
        for overwritten in (1, 3):
            with self.subTest(overwritten=overwritten):
                op = self.register(f"op-{overwritten}", value=2)
                original = firefly_json_io._replace_staged
                def fail(staging, target):
                    if target == self.path:
                        raise OSError("before")
                    return original(staging, target)
                with patch.object(firefly_json_io, "_replace_staged", side_effect=fail):
                    with self.assertRaises(OSError):
                        self.store.flush(self.pid)
                self.update(overwritten)
                self.store.flush(self.pid)
                self.assertEqual(op.result()["status"], "conflict")
                self.assertTrue(op.result()["conflicts"])
                self.assertIsNone(agent_ops_store.get_operation(self.db, self.pid, f"op-{overwritten}"))
                self.update(1)
                self.store.flush(self.pid)
                self.assertEqual(op.result()["status"], "conflict")

    def test_history_and_cleanup_errors_propagate_after_settlement(self):
        for failure in ("history", "cleanup"):
            with self.subTest(failure=failure):
                op = self.register(failure)
                original_replace = firefly_json_io._replace_staged
                original_staging = firefly_json_io._staging_path
                original_unlink = Path.unlink
                staging = self.path.parent / ".canvas-test.tmp"
                def replace(source, target):
                    if failure == "history" and target.name == "history.json":
                        raise OSError("history failed")
                    return original_replace(source, target)
                def staging_path(target):
                    return staging if failure == "cleanup" and target == self.path else original_staging(target)
                def unlink(path, *args, **kwargs):
                    if failure == "cleanup" and path == staging:
                        raise OSError("cleanup failed")
                    return original_unlink(path, *args, **kwargs)
                with patch.object(firefly_json_io, "_replace_staged", side_effect=replace), \
                     patch.object(firefly_json_io, "_staging_path", side_effect=staging_path), \
                     patch.object(Path, "unlink", unlink):
                    with self.assertRaisesRegex(OSError, failure + " failed"):
                        self.store.flush(self.pid)
                self.assertEqual(op.result()["status"], "settled")
                self.assertEqual(agent_ops_store.get_operation(self.db, self.pid, failure), record(failure))
                self.store.flush(self.pid)
                self.update(1)
                self.store.flush(self.pid)
        self.assertEqual(len(agent_ops_store.list_operations(self.db, self.pid)), 2)

    def test_sidecar_failure_is_reported_without_replay_or_overwriting_later_edits(self):
        op = self.register("retry")
        with patch.object(agent_ops_store, "append_operation", side_effect=OSError("db failure")):
            self.store.flush(self.pid)
        self.assertEqual(op.result()["status"], "append_failed")
        self.assertIn("db failure", op.result()["error"])
        self.assertIsNone(agent_ops_store.get_operation(self.db, self.pid, "retry"))
        self.update(3)
        self.store.flush(self.pid)
        self.assertEqual(op.result()["status"], "append_failed")
        self.assertIsNone(agent_ops_store.get_operation(self.db, self.pid, "retry"))
        result = undo_ops(self.store, self.pid, op_id="retry")
        self.assertEqual(result["status"], "conflict")
        self.assertIn("sidecar append failed", result["error"])
        self.assertEqual(self.persisted()["nodes"][0]["value"], 3)

    def test_commit_then_append_error_is_idempotent(self):
        op = self.register()
        original = agent_ops_store.append_operation
        def append_then_fail(*args):
            original(*args)
            raise OSError("post commit")
        with patch.object(agent_ops_store, "append_operation", side_effect=append_then_fail):
            self.store.flush(self.pid)
        self.assertEqual(op.result()["status"], "append_failed")
        self.update(3)
        self.store.flush(self.pid)
        self.assertEqual(op.result()["status"], "append_failed")
        self.assertEqual(agent_ops_store.list_operations(self.db, self.pid), [record("op")])

    def test_exact_delta_is_required_and_snapshots_are_isolated(self):
        with self.journal.mutation(self.pid) as op:
            before = self.store.open_project(self.pid)["canvas"]
            self.update(2)
            delta = build_delta(before, self.store.open_project(self.pid)["canvas"])
            with self.assertRaises(ValueError):
                op.register(record("bad"), [])
            rec = record("op", delta)
            wrong = copy.deepcopy(rec)
            wrong["after"][0]["after"]["value"] = 7
            with self.assertRaises(ValueError):
                op.register(wrong, delta)
            op.register(rec, delta)
            rec["args"]["changed"] = True
            delta[0]["after"]["value"] = 99
        self.store.flush(self.pid)
        snapshot = op.result()
        snapshot["status"] = "bad"
        self.assertEqual(op.result()["status"], "settled")
        self.assertEqual(agent_ops_store.get_operation(self.db, self.pid, "op"), record("op"))
        with self.assertRaises(RuntimeError):
            op.register(record("later"), delta)

    def test_every_changed_field_must_match_and_unrelated_fields_are_ignored(self):
        with self.journal.mutation(self.pid) as op:
            before = self.store.open_project(self.pid)["canvas"]
            self.store.apply_mutations(self.pid, [{"op": "upsert_node", "node": {
                "id": "n", "type": "text", "value": 2, "label": "agent"}}],
                base_revision=before["revision"])
            delta = build_delta(before, self.store.open_project(self.pid)["canvas"])
            op.register(record("partial", delta), delta)
        canvas = self.store.open_project(self.pid)["canvas"]
        self.store.apply_mutations(self.pid, [{"op": "upsert_node", "node": {
            "id": "n", "type": "text", "value": 2, "label": "user"}}],
            base_revision=canvas["revision"])
        self.store.flush(self.pid)
        self.assertEqual(op.result()["status"], "conflict")
        self.assertIsNone(agent_ops_store.get_operation(self.db, self.pid, "partial"))
        with self.journal.mutation(self.pid) as op2:
            before = self.store.open_project(self.pid)["canvas"]
            self.update(4)
            delta = build_delta(before, self.store.open_project(self.pid)["canvas"])
            op2.register(record("unrelated", delta), delta)
        canvas = self.store.open_project(self.pid)["canvas"]
        self.store.apply_mutations(self.pid, [{"op": "upsert_node", "node": {
            "id": "n", "type": "text", "value": 4, "user_field": "preserved"}}],
            base_revision=canvas["revision"])
        self.store.flush(self.pid)
        self.assertEqual(op2.result()["status"], "settled")
        self.assertEqual(agent_ops_store.get_operation(self.db, self.pid, "unrelated"), record("unrelated", delta))

    def test_existing_op_id_is_not_reported_as_a_new_success(self):
        agent_ops_store.append_operation(self.db, self.pid, record("op"))
        op = self.register()
        self.store.flush(self.pid)
        self.assertEqual(op.result()["status"], "error")
        self.assertIn("existing record", op.result()["error"])
        self.assertEqual(agent_ops_store.list_operations(self.db, self.pid), [record("op")])

    def test_bool_is_not_zero_and_missing_is_not_null(self):
        op = self.register(value=False)
        self.update(0)
        self.store.flush(self.pid)
        self.assertEqual(op.result()["status"], "conflict")
        self.assertIsNone(agent_ops_store.get_operation(self.db, self.pid, "op"))
        self.update(1)
        self.store.flush(self.pid)
        op = self.register("null", value=None)
        canvas = self.store.open_project(self.pid)["canvas"]
        self.store.apply_mutations(self.pid, [{"op": "upsert_node", "node": {"id": "n", "type": "text"}}],
                                   base_revision=canvas["revision"])
        self.store.flush(self.pid)
        self.assertEqual(op.result()["status"], "conflict")
        self.assertIsNone(agent_ops_store.get_operation(self.db, self.pid, "null"))

    def test_create_then_edit_and_successive_edits_are_recorded_in_order(self):
        create = self.register_mutations("create", self.node(value=1),
            {"op": "upsert_node", "node": {"id": "x", "type": "text", "value": 1}})
        edit = self.register_mutations("edit", {"op": "upsert_node", "node": {
            "id": "x", "type": "text", "value": 2}})
        edit2 = self.register_mutations("edit2", {"op": "upsert_node", "node": {
            "id": "x", "type": "text", "value": 3}})
        self.store.flush(self.pid)
        self.assertEqual([op.result()["status"] for op in (create, edit, edit2)], ["settled"] * 3)
        self.assertEqual(agent_ops_store.list_operations(self.db, self.pid),
                         [self.records[key] for key in ("edit2", "edit", "create")])
        self.assertEqual(next(node for node in self.persisted()["nodes"] if node["id"] == "x")["value"], 3)

    def test_disjoint_user_edit_survives_reverse_proof(self):
        first = self.register("first", 2)
        second = self.register("second", 3)
        self.mutate(self.node(value=3, user_field="keep"))
        self.store.flush(self.pid)
        self.assertEqual([first.result()["status"], second.result()["status"]], ["settled", "settled"])
        self.assertEqual(self.persisted()["nodes"][0]["user_field"], "keep")

    def test_user_same_field_revert_does_not_fabricate_older_success(self):
        first = self.register("first", 2)
        second = self.register("second", 3)
        self.update(2)
        self.store.flush(self.pid)
        self.assertEqual([first.result()["status"], second.result()["status"]], ["conflict", "conflict"])
        self.assertEqual(agent_ops_store.list_operations(self.db, self.pid), [])
        self.assertEqual(self.persisted()["nodes"][0]["value"], 2)

    def test_dirty_before_between_operations_is_not_attributed(self):
        first = self.register("first", 2)
        self.update(7)
        second = self.register("second", 3)
        self.store.flush(self.pid)
        self.assertEqual(first.result()["status"], "conflict")
        self.assertEqual(second.result()["status"], "settled")
        self.assertEqual(agent_ops_store.list_operations(self.db, self.pid), [self.records["second"]])

    def test_create_with_dirty_before_or_later_user_field_conflicts(self):
        create = self.register_mutations("create", {"op": "upsert_node", "node": {
            "id": "x", "type": "text", "value": 1}})
        self.mutate({"op": "upsert_node", "node": {"id": "x", "type": "text", "value": 1, "user": 7}})
        edit = self.register_mutations("edit", {"op": "upsert_node", "node": {
            "id": "x", "type": "text", "value": 2, "user": 7}})
        self.store.flush(self.pid)
        self.assertEqual(create.result()["status"], "conflict")
        self.assertEqual(edit.result()["status"], "settled")
        self.assertEqual(next(node for node in self.persisted()["nodes"] if node["id"] == "x")["user"], 7)

    def test_insertion_and_connection_dependency(self):
        create = self.register_mutations("create", {"op": "upsert_node", "node": {
            "id": "x", "type": "text"}})
        edge = self.register_mutations("edge", {"op": "upsert_edge", "edge": {
            "id": "edge", "from": "n", "to": "x"}})
        self.store.flush(self.pid)
        self.assertEqual([create.result()["status"], edge.result()["status"]], ["settled", "settled"])
        self.assertEqual(len(self.persisted()["connections"]), 1)

    def test_user_connection_preserved_and_creation_recorded(self):
        create = self.register_mutations("create", {"op": "upsert_node", "node": {
            "id": "x", "type": "text"}})
        self.mutate({"op": "upsert_edge", "edge": {"id": "edge", "from": "n", "to": "x"}})
        self.store.flush(self.pid)
        self.assertEqual(create.result()["status"], "settled")
        self.assertEqual(len(self.persisted()["connections"]), 1)

    def check_later_records_continue(self, commit_first):
        first = self.register("first", 2, turn="turn")
        second = self.register("second", 3, turn="turn")
        other = self.store.create_project("other")['id']
        self.update(1, other)
        self.store.flush(other)
        unrelated = self.register("unrelated", 2, pid=other)
        original = agent_ops_store.append_operation
        calls = []

        def fail_first(db, pid, rec):
            calls.append((pid, rec["op_id"]))
            if pid == self.pid and rec["op_id"] == "first":
                if commit_first:
                    original(db, pid, rec)
                raise OSError("db failure")
            return original(db, pid, rec)

        with patch.object(agent_ops_store, "append_operation", side_effect=fail_first):
            self.store.flush(self.pid)
            self.store.flush(other)
        self.assertEqual([first.result()["status"], second.result()["status"]], ["append_failed", "settled"])
        self.assertEqual(unrelated.result()["status"], "settled")
        self.assertEqual(calls, [(self.pid, "first"), (self.pid, "second"), (other, "unrelated")])
        self.assertEqual(agent_ops_store.list_operations(self.db, other), [self.records["unrelated"]])

        self.store.flush(self.pid)
        self.assertEqual(agent_ops_store.list_operations(self.db, self.pid),
                         [self.records["second"]] + ([self.records["first"]] if commit_first else []))
        for target in ({"turn_id": "turn"}, {"op_id": "second"}):
            result = undo_ops(self.store, self.pid, **target)
            self.assertEqual(result["status"], "conflict")
            self.assertIn("sidecar append failed", result["error"])
            self.assertEqual(result["reverted"], [])
        self.assertEqual(self.persisted()["nodes"][0]["value"], 3)

    def test_failed_append_does_not_block_later_records(self):
        self.check_later_records_continue(commit_first=False)

    def test_post_commit_failure_does_not_block_later_records(self):
        self.check_later_records_continue(commit_first=True)

    def test_sidecar_failure_does_not_retry_over_a_later_user_edit(self):
        first = self.register("first", 2, turn="turn")
        second = self.register("second", 3, turn="turn")
        original = agent_ops_store.append_operation
        def fail_first(db, pid, rec):
            if rec["op_id"] == "first":
                raise OSError("db failure")
            return original(db, pid, rec)
        with patch.object(agent_ops_store, "append_operation", side_effect=fail_first):
            self.store.flush(self.pid)
        self.update(2)
        self.store.flush(self.pid)
        self.assertEqual(first.result()["status"], "append_failed")
        self.assertEqual(second.result()["status"], "settled")
        self.assertEqual(agent_ops_store.list_operations(self.db, self.pid), [self.records["second"]])
        self.assertEqual(undo_ops(self.store, self.pid, op_id="second")["status"], "conflict")
        self.assertEqual(self.persisted()["nodes"][0]["value"], 2)

    def test_unreversible_later_dependency_cannot_prove_earlier_creation(self):
        create = self.register_mutations("create", {"op": "upsert_node", "node": {
            "id": "x", "type": "text", "value": 1}})
        delete = self.register_mutations("delete", {"op": "remove_node", "node_id": "x"})
        recreate = self.register_mutations("recreate", {"op": "upsert_node", "node": {
            "id": "x", "type": "text", "value": 1}})
        self.mutate({"op": "upsert_edge", "edge": {"id": "edge", "from": "n", "to": "x"}})
        self.store.flush(self.pid)
        self.assertEqual([op.result()["status"] for op in (create, delete, recreate)],
                         ["conflict", "conflict", "settled"])
        self.assertEqual(len(self.persisted()["connections"]), 1)

    def test_concurrent_agent_mutations_are_serialized_and_settled(self):
        started, finished = threading.Event(), threading.Event()
        results, errors = [], []
        with self.journal.mutation(self.pid) as first:
            before = self.store.open_project(self.pid)["canvas"]
            self.update(2)
            def worker():
                started.set()
                try:
                    results.append(self.register("second", 3))
                except Exception as exc:
                    errors.append(exc)
                finally:
                    finished.set()
            thread = threading.Thread(target=worker)
            thread.start()
            self.assertTrue(started.wait(2))
            self.assertFalse(finished.wait(0.05))
            delta = build_delta(before, self.store.open_project(self.pid)["canvas"])
            first.register(record("first", delta), delta)
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.store.flush(self.pid)
        self.assertEqual([first.result()["status"], results[0].result()["status"]], ["settled", "settled"])

    def test_other_project_and_concurrent_user_write(self):
        other = self.store.create_project("other")["id"]
        self.update(5, other)
        with self.journal.mutation(self.pid) as op:
            before = self.store.open_project(self.pid)["canvas"]
            self.update(2)
            started, finished = threading.Event(), threading.Event()
            def user_write():
                started.set()
                self.update(3)
                finished.set()
            thread = threading.Thread(target=user_write)
            thread.start()
            self.assertTrue(started.wait(2))
            self.assertFalse(finished.wait(0.05))
            op.register(record("op"), build_delta(before, self.store.open_project(self.pid)["canvas"]))
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.store.flush(other)
        self.assertEqual(op.result()["status"], "pending")
        self.store.flush(self.pid)
        self.assertEqual(op.result()["status"], "conflict")
        self.assertEqual(agent_ops_store.list_operations(self.db, self.pid), [])
        self.assertEqual(agent_ops_store.list_operations(self.db, other), [])


if __name__ == "__main__":
    unittest.main()
