"""ADR 0021 ops.db 租户层测试（issue #257）。

- ops.db 懒创建（每工程独立 SQLite 文件，WAL + FK）
- additive-only 迁移：key 幂等、跨实例只跑一次
- 短连接模型：每次操作 open→迁移→close，操作后不残留打开句柄
  （Windows 回归保护：长连接缓存导致临时工程目录无法清理，WinError 32）
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

    def tearDown(self):
        # 短连接模型下无需显式 close——这里直接 rmtree 即是对"无残留句柄"的回归检查
        self.temp_dir.cleanup()

    def make_ops(self):
        return OpsDb(self.root)

    def test_lazy_create_and_migrations(self):
        ops = self.make_ops()
        self.assertFalse(ops.has_db("p1"))
        with ops.connect("p1") as conn:
            self.assertTrue((self.root / "p1" / "ops.db").is_file())
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertIn("ops_migrations", tables)
            self.assertIn("versions_snapshots", tables)
            self.assertIn("usage_turns", tables)
            self.assertIn("undo_snapshots", tables)   # #251 undo 栈租户
            self.assertIn("anno_annotations", tables)  # #272 画布批注租户
            applied = [row[0] for row in conn.execute("SELECT key FROM ops_migrations ORDER BY rowid")]
            self.assertEqual(applied, ["versions_001", "usage_001", "undo_001", "anno_001"])
        # 幂等：第二次短连接迁移不重跑（done-row 标记）
        with ops.connect("p1") as conn:
            self.assertEqual(len(list(conn.execute("SELECT key FROM ops_migrations"))), 4)

    def test_migration_runs_once_across_instances(self):
        with self.make_ops().connect("p1"):
            pass
        with self.make_ops().connect("p1") as conn:
            self.assertEqual(len(list(conn.execute("SELECT key FROM ops_migrations"))), 4)

    def test_wal_and_fk_pragmas(self):
        with self.make_ops().connect("p1") as conn:
            self.assertEqual(str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower(), "wal")
            self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)

    def test_versions_table_schema(self):
        with self.make_ops().connect("p1") as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(versions_snapshots)")}
            self.assertEqual(
                columns,
                {"id", "project_id", "label", "canvas_json", "canvas_sha256", "node_count", "created_at"},
            )

    def test_migration_failure_is_visible_and_not_recorded(self):
        firefly_opsdb.OPS_MIGRATIONS.append(("bad_test", ["CREATE TABLE bad (id INTEGER PRIMARY KEY,"]))
        try:
            with self.assertRaises(sqlite3.OperationalError):
                with self.make_ops().connect("p1"):
                    pass
        finally:
            firefly_opsdb.OPS_MIGRATIONS.pop()
        # 迁移失败不得静默：重连时仍会再次尝试该迁移（未记入 ops_migrations）
        with self.make_ops().connect("p1") as conn:
            applied = {row[0] for row in conn.execute("SELECT key FROM ops_migrations")}
            self.assertNotIn("bad_test", applied)
            self.assertIn("versions_001", applied)

    def test_short_lived_connection_releases_handle(self):
        """Windows 回归：操作完成后文件句柄必须释放（否则 rmtree 报 WinError 32）。"""
        ops = self.make_ops()
        with ops.connect("p1"):
            pass
        path = self.root / "p1" / "ops.db"
        self.assertTrue(path.is_file())
        path.unlink()  # 有打开句柄时 Windows 上此操作抛 PermissionError(WinError 32)
        # WAL 附属文件随最后一次 close 清理，不残留
        self.assertFalse((self.root / "p1" / "ops.db-wal").exists())
        self.assertFalse((self.root / "p1" / "ops.db-shm").exists())
        self.assertFalse(ops.has_db("p1"))


if __name__ == "__main__":
    unittest.main()
