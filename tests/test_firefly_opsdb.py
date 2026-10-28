"""ADR 0021 ops.db 租户层测试（issue #257）。

- ops.db 懒创建（每工程独立 SQLite 文件，WAL + FK）
- additive-only 迁移：key 幂等、跨实例只跑一次
- 失败模型：无 ops.db 文件 = 空状态；迁移失败 = 可见报错且不误记为已应用
"""
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import firefly_opsdb
from firefly_opsdb import OpsDb


class OpsDbTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name) / "projects"
        self.root.mkdir(parents=True)
        self.ops_instances = []

    def tearDown(self):
        for ops in self.ops_instances:
            ops.close_all()
        self.temp_dir.cleanup()

    def make_ops(self):
        ops = OpsDb(self.root)
        self.ops_instances.append(ops)
        return ops

    def test_lazy_create_and_migrations(self):
        ops = self.make_ops()
        self.assertFalse(ops.has_db("p1"))
        ops_view = ops.connection_for("p1")
        conn = ops_view.conn
        self.assertTrue((self.root / "p1" / "ops.db").is_file())
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn("ops_migrations", tables)
        self.assertIn("versions_snapshots", tables)
        applied = [row[0] for row in conn.execute("SELECT key FROM ops_migrations ORDER BY rowid")]
        self.assertEqual(applied, ["versions_001"])
        # 幂等：同一实例第二次拿到同一连接对象，迁移不重跑
        self.assertIs(ops.connection_for("p1"), ops_view)
        self.assertEqual(len(list(conn.execute("SELECT key FROM ops_migrations"))), 1)

    def test_migration_runs_once_across_instances(self):
        self.make_ops().connection_for("p1")
        conn = self.make_ops().connection_for("p1").conn
        self.assertEqual(len(list(conn.execute("SELECT key FROM ops_migrations"))), 1)

    def test_wal_and_fk_pragmas(self):
        conn = self.make_ops().connection_for("p1").conn
        self.assertEqual(str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower(), "wal")
        self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)

    def test_versions_table_schema(self):
        conn = self.make_ops().connection_for("p1").conn
        columns = {row[1] for row in conn.execute("PRAGMA table_info(versions_snapshots)")}
        self.assertEqual(
            columns,
            {"id", "project_id", "label", "canvas_json", "canvas_sha256", "node_count", "created_at"},
        )

    def test_migration_failure_is_visible_and_not_recorded(self):
        firefly_opsdb.OPS_MIGRATIONS.append(("bad_test", ["CREATE TABLE bad (id INTEGER PRIMARY KEY,"]))
        try:
            with self.assertRaises(sqlite3.OperationalError):
                self.make_ops().connection_for("p1")
        finally:
            firefly_opsdb.OPS_MIGRATIONS.pop()
        # 迁移失败不得静默：重连时仍会再次尝试该迁移（未记入 ops_migrations）
        conn = self.make_ops().connection_for("p1").conn
        applied = {row[0] for row in conn.execute("SELECT key FROM ops_migrations")}
        self.assertNotIn("bad_test", applied)
        self.assertIn("versions_001", applied)

    def test_close_all_disconnects(self):
        ops = self.make_ops()
        conn = ops.connection_for("p1").conn
        ops.close_all()
        with self.assertRaises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")


if __name__ == "__main__":
    unittest.main()
