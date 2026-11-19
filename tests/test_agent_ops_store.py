from contextlib import closing
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services import agent_ops_store as store
from firefly_opsdb import OpsDb


def record(op_id, turn_id=None):
    return {
        "op_id": op_id, "turn_id": turn_id, "agent_node_id": "agent-1",
        "tool": "node.update", "args": {"nested": {"missing-is-not-null": None}},
        "before": {"x": 1}, "after": {"x": 2}, "touched_node_ids": ["node-1"],
        "created_at": "2026-01-01T00:00:00Z", "irreversible_effects": [], "undone": False,
    }


class AgentOpsStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = OpsDb(self.root)
        (self.root / "p").mkdir()
        (self.root / "q").mkdir()

    def append(self, op_id, turn_id=None, project_id="p"):
        store.append_operation(self.db, project_id, record(op_id, turn_id))

    def test_missing_db_never_created_by_reads_or_undo(self):
        self.assertEqual(store.list_operations(self.db, "p"), [])
        self.assertIsNone(store.get_operation(self.db, "p", "none"))
        with self.assertRaises(KeyError):
            store.mark_undone(self.db, "p", ["none"])
        self.assertFalse(self.db.db_path("p").exists())
        self.assertEqual(store.list_operations(self.db, "missing"), [])
        with self.assertRaises(KeyError):
            self.append("x", project_id="missing")
        self.assertFalse((self.root / "missing").exists())

    def test_open_does_not_recreate_disappearing_project_or_db(self):
        connect = self.db.connect

        def remove_project_then_connect(*args, **kwargs):
            (self.root / "p").rmdir()
            return connect(*args, **kwargs)

        with patch.object(self.db, "connect", side_effect=remove_project_then_connect):
            with self.assertRaises(sqlite3.OperationalError):
                self.append("x")
        self.assertFalse((self.root / "p").exists())
        (self.root / "p").mkdir()
        with patch.object(self.db, "has_db", return_value=True):
            with self.assertRaises(sqlite3.OperationalError):
                store.list_operations(self.db, "p")
        self.assertFalse(self.db.db_path("p").exists())

    def test_forward_migration_and_other_tenant_unchanged(self):
        path = self.db.db_path("p")
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.execute("CREATE TABLE ops_migrations (key TEXT PRIMARY KEY, done_at INTEGER NOT NULL)")
            conn.execute("INSERT INTO ops_migrations VALUES ('versions_001', 1)")
            conn.execute("CREATE TABLE versions_snapshots (sentinel TEXT)")
            conn.execute("INSERT INTO versions_snapshots VALUES ('preserved')")
        self.assertEqual(store.list_operations(self.db, "p"), [])
        with closing(sqlite3.connect(path)) as conn:
            self.assertEqual(conn.execute("SELECT sentinel FROM versions_snapshots").fetchone()[0], "preserved")
            self.assertEqual(conn.execute("SELECT count(*) FROM ops_migrations WHERE key = 'agent_ops_001'").fetchone()[0], 1)
            self.assertIsNotNone(conn.execute("SELECT name FROM sqlite_master WHERE name = 'agent_ops_operations'").fetchone())
            self.assertIsNotNone(conn.execute("SELECT name FROM sqlite_master WHERE name = 'agent_ops_project_turn'").fetchone())
        self.assertEqual(store.list_operations(self.db, "p"), [])

    def test_existing_db_migration_failure_propagates_from_reads_and_writes(self):
        path = self.db.db_path("p")
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.execute("CREATE TABLE sentinel (value TEXT)")
            conn.execute("INSERT INTO sentinel VALUES ('preserved')")
        with patch.object(self.db, "_migrate", side_effect=sqlite3.OperationalError("migration failed")):
            with self.assertRaisesRegex(sqlite3.OperationalError, "migration failed"):
                store.list_operations(self.db, "p")
            with self.assertRaisesRegex(sqlite3.OperationalError, "migration failed"):
                self.append("x")
        with closing(sqlite3.connect(path)) as conn:
            self.assertEqual(conn.execute("SELECT value FROM sentinel").fetchone()[0], "preserved")
        self.assertEqual(store.list_operations(self.db, "p"), [])

    def test_roundtrip_order_filter_and_project_isolation(self):
        first = record("same", "turn")
        first["before"] = {"null": None, "deep": {"absent": {}}}
        first["after"] = None
        first["irreversible_effects"] = [{"type": "external", "detail": None}]
        store.append_operation(self.db, "p", first)
        self.append("second", "other")
        self.append("same", "turn", "q")
        reopened = OpsDb(self.root)
        self.assertEqual(store.get_operation(reopened, "p", "same"), first)
        self.assertEqual([r["op_id"] for r in store.list_operations(reopened, "p")], ["second", "same"])
        self.assertEqual([r["op_id"] for r in store.list_operations(reopened, "p", turn_id="turn")], ["same"])
        self.assertEqual([r["op_id"] for r in store.list_operations(reopened, "p", limit=1)], ["second"])
        self.assertEqual(store.get_operation(reopened, "q", "same"), record("same", "turn"))
        self.assertIsNone(store.get_operation(reopened, "q", "second"))

    def test_parent_roundtrip_tree_validation_and_legacy_record(self):
        legacy = record('root', 'turn')
        store.append_operation(self.db, 'p', legacy)
        for op, parent in [('child', 'root'), ('sibling', 'root'), ('grandchild', 'child')]:
            item = record(op, 'turn')
            item['parent_op_id'] = parent
            store.append_operation(self.db, 'p', item)
        self.assertIsNone(store.get_operation(self.db, 'p', 'root').get('parent_op_id'))
        self.assertEqual(store.get_operation(self.db, 'p', 'grandchild')['parent_op_id'], 'child')
        self.assertEqual([r['op_id'] for r in store.list_operation_tree(self.db, 'p', 'root')],
                         ['grandchild', 'sibling', 'child', 'root'])
        self.assertEqual([r['op_id'] for r in store.list_operation_tree(self.db, 'p', 'child')],
                         ['grandchild', 'child'])
        self.assertEqual(store.list_operation_tree(self.db, 'q', 'root'), [])
        for project, parent, turn in [('p', 'missing', 'turn'), ('q', 'root', 'turn'), ('p', 'root', 'other')]:
            item = record('invalid', turn)
            item['parent_op_id'] = parent
            with self.assertRaises(ValueError):
                store.append_operation(self.db, project, item)
        store.mark_undone(self.db, 'p', ['child'])
        item = record('late', 'turn')
        item['parent_op_id'] = 'child'
        with self.assertRaises(ValueError):
            store.append_operation(self.db, 'p', item)

    def test_empty_delta_effects_and_noop_validation(self):
        for effect in ('', '   ', None, {}, {'detail': '  '}):
            item = record('invalid')
            item.update(before=[], after=[], irreversible_effects=[effect])
            with self.subTest(effect=effect), self.assertRaises(ValueError):
                store.append_operation(self.db, 'p', item)
        item = record('noop')
        item.update(before=[], after=[])
        with self.assertRaises(ValueError):
            store.append_operation(self.db, 'p', item)
        item.update(op_id='effect', irreversible_effects=['deleted media'])
        store.append_operation(self.db, 'p', item)
        self.assertEqual(store.get_operation(self.db, 'p', 'effect'), item)

    def test_ungrouped_family_retention_is_atomic(self):
        with patch.object(store, 'MAX_OPERATIONS', 3):
            self.append('root')
            item = record('child')
            item['parent_op_id'] = 'root'
            store.append_operation(self.db, 'p', item)
            self.append('other')
            item = record('grandchild')
            item['parent_op_id'] = 'child'
            store.append_operation(self.db, 'p', item)
            self.assertEqual([r['op_id'] for r in store.list_operations(self.db, 'p')],
                             ['grandchild', 'child', 'root'])
            self.append('new')
            self.assertEqual([r['op_id'] for r in store.list_operations(self.db, 'p')], ['new'])

    def test_pruning_turn_with_children_counts_cascades_once(self):
        with patch.object(store, 'MAX_OPERATIONS', 3):
            store.begin_turn(self.db, 'p', 'old', 'session', external=True)
            self.append('root', 'old')
            child = record('child', 'old')
            child['parent_op_id'] = 'root'
            store.append_operation(self.db, 'p', child)
            store.begin_turn(self.db, 'p', 'other', 'session', external=True)
            self.append('keep', 'other')
            store.begin_turn(self.db, 'p', 'new', 'session', external=True)
            self.append('new', 'new')
            self.assertEqual([r['op_id'] for r in store.list_operations(self.db, 'p')], ['new', 'keep'])

    def test_real_cap_prunes_whole_oldest_turn_and_ungrouped_individually(self):
        store.begin_turn(self.db, 'p', 'old', 'session', external=True)
        self.append("a", "old")
        self.append("b", "old")
        store.begin_turn(self.db, 'p', 'next', 'session', external=True)
        self.append("c")
        # Populate efficiently without thousands of connection/migration cycles.
        with self.db.project_lock("p"), self.db.connect("p") as conn, conn:
            template = conn.execute("SELECT * FROM agent_ops_operations WHERE op_id = 'c'").fetchone()
            columns = [key for key in template.keys() if key != "id"]
            sql = "INSERT INTO agent_ops_operations (" + ",".join(columns) + ") VALUES (" + ",".join("?" for _ in columns) + ")"
            for i in range(997):
                row = dict(template)
                row["op_id"] = f"bulk-{i}"
                conn.execute(sql, [row[key] for key in columns])
        self.append("new")
        rows = store.list_operations(self.db, "p", limit=2000)
        self.assertEqual(len(rows), 999)
        self.assertNotIn("a", [r["op_id"] for r in rows])
        self.assertNotIn("b", [r["op_id"] for r in rows])
        with self.assertRaises(ValueError):
            self.append("resurrect", "old")
        self.append("newer")
        self.append("latest")
        rows = store.list_operations(self.db, "p", limit=2000)
        self.assertEqual(len(rows), 1000)
        self.assertIsNone(store.get_operation(self.db, "p", "c"))
        self.assertIsNotNone(store.get_operation(self.db, "p", "bulk-0"))
        with self.db.project_lock("p"), self.db.connect("p") as conn, conn:
            conn.execute("UPDATE agent_ops_operations SET turn_id = 'big' WHERE project_id = 'p'")
        with self.assertLogs(store.logger, level='WARNING'):
            self.append("over-cap", "big")
        self.assertEqual(len(store.list_operations(self.db, "p", limit=2000)), 1001)

    def test_oversized_and_interleaved_turns_remain_whole(self):
        with patch.object(store, "MAX_OPERATIONS", 3):
            for i in range(3):
                self.append(str(i), "turn")
            with self.assertLogs(store.logger, level='WARNING'):
                self.append("overflow", "turn")
            self.append("other", "other")
            self.assertEqual(len(store.list_operations(self.db, 'p')), 5)

    def test_retention_prunes_interleaved_oldest_turn_as_a_whole(self):
        with patch.object(store, "MAX_OPERATIONS", 4):
            store.begin_turn(self.db, 'p', 'old', 'one', external=True)
            store.begin_turn(self.db, 'p', 'other', 'two', external=True)
            self.append("a", "old")
            self.append("b", "other")
            self.append("c", "old")
            self.append("d", "other")
            store.begin_turn(self.db, 'p', 'new', 'one', external=True)
            self.append("e", "new")
            self.assertEqual([r["op_id"] for r in store.list_operations(self.db, "p")],
                             ["e", "d", "b"])
            with self.assertRaises(ValueError):
                self.append("f", "old")

    def test_prune_and_insert_rollback_on_delete_failure_and_duplicate(self):
        with patch.object(store, "MAX_OPERATIONS", 2):
            store.begin_turn(self.db, 'p', 'old', 'session', external=True)
            self.append("a", "old")
            self.append("b", "old")
            store.begin_turn(self.db, 'p', 'next', 'session', external=True)
            with self.db.project_lock("p"), self.db.connect("p") as conn, conn:
                conn.execute("CREATE TRIGGER agent_ops_fail_tombstone BEFORE INSERT ON agent_ops_pruned_turns BEGIN SELECT RAISE(ABORT, 'test'); END")
            before = store.list_operations(self.db, "p")
            with self.assertLogs(store.logger, level='WARNING'):
                self.append("c")
            self.assertEqual(store.list_operations(self.db, "p")[1:], before)
            before = store.list_operations(self.db, "p")
            with self.assertRaises(sqlite3.IntegrityError):
                self.append("a")
            self.assertEqual(store.list_operations(self.db, "p"), before)

    def test_reject_invalid_records_before_creating_db(self):
        bad = []
        for field, value in (("op_id", ""), ("turn_id", ""), ("undone", 1),
                             ("touched_node_ids", [None]), ("irreversible_effects", None),
                             ("args", {"bad": float("nan")}), ("before", {1: "bad"}),
                             ("after", {"format": "firefly-canvas-v1", "nodes": []})):
            item = record("bad")
            item[field] = value
            bad.append(item)
        missing = record("missing")
        del missing["before"]
        bad.append(missing)
        extra = record("extra")
        extra["unexpected"] = None
        bad.append(extra)
        for item in bad:
            with self.subTest(item=item), self.assertRaises(ValueError):
                store.append_operation(self.db, "p", item)
        self.assertFalse(self.db.db_path("p").exists())
        for invalid in ("../p", "", "/p", "p\\q"):
            with self.subTest(project=invalid), self.assertRaises(ValueError):
                store.list_operations(self.db, invalid)
        with self.assertRaises(ValueError):
            store.list_operations(self.db, "p", limit=0)

    def test_mark_undone_atomic_and_project_scoped(self):
        self.append("a", "turn")
        self.append("b", "turn")
        self.append("a", "turn", "q")
        with self.assertRaises(KeyError):
            store.mark_undone(self.db, "p", ["a", "missing"])
        self.assertFalse(store.get_operation(self.db, "p", "a")["undone"])
        store.mark_undone(self.db, "p", ["a", "b", "a"])
        store.mark_undone(self.db, "p", ["a"])
        self.assertTrue(store.get_operation(self.db, "p", "a")["undone"])
        self.assertTrue(store.get_operation(self.db, "p", "b")["undone"])
        self.assertFalse(store.get_operation(self.db, "q", "a")["undone"])


if __name__ == "__main__":
    unittest.main()
