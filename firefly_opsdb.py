"""ops.db 工程操作租户（ADR 0021；issue #257 首个租户：画布版本树）。

每个工程在自己的工程目录内拥有独立的 ``ops.db``（SQLite，WAL + 外键开启）。
租户表通过 additive-only 迁移机制管理：迁移 key 记录在 ``ops_migrations`` 表，
已应用的 key 永不重跑；新增租户 = 追加新的 (key, SQL) 条目，不动既有表。

连接模型（ADR 0021 连接不变量的 Windows 适配，见 .pipeline/batches/issue-257-result.md）：
- **短生命周期连接**：每次读写操作 open → 执行 ``PRAGMA journal_mode=WAL`` /
  ``PRAGMA foreign_keys=ON`` → 幂等迁移 → 使用 → close。操作完成后不残留任何
  打开句柄——Windows 上打开中的 SQLite 文件无法删除，进程级缓存的长连接会
  阻止测试/质量门清理临时工程目录（WinError 32）。
- 跨线程串行化：每项目一把锁（ADR 0021「每项目锁」不变量），连接只允许在
  ``project_lock`` 作用域内使用。
- 懒创建：``ops.db`` 不随项目目录布局生成，首次有租户写入时才建文件
  （ADR 0021 决策 3）。

失败模型（ADR 0021）：
- 无 ``ops.db`` 文件 = 该工程空状态（调用方用 ``has_db`` 判定，返回空结果）。
- 迁移失败 = 可见报错（异常向上传播给调用方；不记入 ``ops_migrations``，
  下次连接会重试，不会静默跳过）。
"""
from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, List, Tuple

# additive-only 迁移表：(key, [SQL 语句])。追加即可新增租户，禁止修改已发布条目。
OPS_MIGRATIONS: List[Tuple[str, List[str]]] = [
    (
        "versions_001",
        [
            """
            CREATE TABLE IF NOT EXISTS versions_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id TEXT NOT NULL,
                label TEXT NOT NULL DEFAULT 'auto',
                canvas_json TEXT NOT NULL,
                canvas_sha256 TEXT NOT NULL,
                node_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
            """
        ],
    ),
    (
        # LLM 用量/成本遥测租户（issue #258；每 turn 一行；表前缀 usage_* 符合 ADR 0021 决策 5）
        "usage_001",
        [
            """
            CREATE TABLE IF NOT EXISTS usage_turns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                model TEXT NOT NULL DEFAULT '',
                node_id TEXT NOT NULL DEFAULT '',
                input_tokens INTEGER NOT NULL DEFAULT 0,
                output_tokens INTEGER NOT NULL DEFAULT 0,
                cache_read_input_tokens INTEGER NOT NULL DEFAULT 0,
                cache_creation_input_tokens INTEGER NOT NULL DEFAULT 0,
                total_tokens INTEGER NOT NULL DEFAULT 0,
                cost_usd REAL,
                duration_seconds REAL,
                created_at TEXT NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_usage_turns_project_time ON usage_turns (project_id, created_at)",
        ],
    ),
    (
        # 画布级 undo/redo 栈租户（issue #251；每项目单行整栈 upsert；表前缀 undo_* 符合 ADR 0021 决策 5）。
        # 与 versions_snapshots 是不同租户：版本树（flush 触发、供回滚）vs 会话级 undo 连续性。
        "undo_001",
        [
            """
            CREATE TABLE IF NOT EXISTS undo_snapshots (
                project_id TEXT PRIMARY KEY,
                undo_json TEXT NOT NULL DEFAULT '[]',
                redo_json TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL
            )
            """
        ],
    ),
]


def _now_ms() -> int:
    """epoch 毫秒（ADR 0021 约定：ops_migrations.done_at 为 INTEGER）。"""
    return int(time.time() * 1000)


class OpsDb:
    """管理各工程 ops.db 短连接与迁移（线程安全）。"""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self._registry_lock = threading.Lock()
        self._project_locks: dict[str, threading.Lock] = {}

    def db_path(self, project_id: str) -> Path:
        return self.root / project_id / "ops.db"

    def has_db(self, project_id: str) -> bool:
        """无 ops.db 文件 = 该工程空状态（ADR 0021 失败模型）。"""
        return self.db_path(project_id).is_file()

    def project_lock(self, project_id: str) -> threading.Lock:
        """同工程读写串行化（ADR 0021 每项目锁不变量）。"""
        with self._registry_lock:
            lock = self._project_locks.get(project_id)
            if lock is None:
                lock = threading.Lock()
                self._project_locks[project_id] = lock
            return lock

    @contextmanager
    def connect(self, project_id: str) -> Iterator[sqlite3.Connection]:
        """一条短连接：WAL + 外键 PRAGMA → 幂等迁移 → yield → close（释放文件句柄）。

        迁移失败抛出 sqlite3.Error（可见报错），连接即关闭、迁移 key 不记录。
        调用方必须已在 ``project_lock(project_id)`` 作用域内（或上层业务锁内）。
        """
        path = self.db_path(project_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._migrate(conn)
            yield conn
        finally:
            conn.close()

    def _migrate(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS ops_migrations ("
            " key TEXT PRIMARY KEY, "
            " done_at INTEGER NOT NULL"
            ")"
        )
        applied = {row[0] for row in conn.execute("SELECT key FROM ops_migrations")}
        for key, statements in OPS_MIGRATIONS:
            if key in applied:
                continue
            try:
                for statement in statements:
                    conn.execute(statement)
                conn.execute(
                    "INSERT INTO ops_migrations (key, done_at) VALUES (?, ?)",
                    (key, _now_ms()),
                )
                conn.commit()
            except sqlite3.Error:
                conn.rollback()
                # 不记录失败的 key：下次连接重试；异常上抛 = 可见报错（ADR 0021）
                raise
