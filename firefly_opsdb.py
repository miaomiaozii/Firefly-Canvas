"""ops.db 工程操作租户（ADR 0021；issue #257 首个租户：画布版本树）。

每个工程在自己的工程目录内拥有独立的 ``ops.db``（SQLite，WAL + 外键开启）。
租户表通过 additive-only 迁移机制管理：迁移 key 记录在 ``ops_migrations`` 表，
已应用的 key 永不重跑；新增租户 = 追加新的 (key, SQL) 条目，不动既有表。

失败模型（ADR 0021）：
- 无 ``ops.db`` 文件 = 该工程空状态（调用方用 ``has_db`` 判定，返回空结果）。
- 迁移失败 = 可见报错（异常向上传播给调用方；不记入 ``ops_migrations``，
  下次连接会重试，不会静默跳过）。
"""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Tuple

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
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class _ProjectOps:
    """某工程的 ops.db 连接 + 串行锁。

    所有租户读写必须持有 ``lock`` 后再访问 ``conn``（单连接多线程安全约定）。
    """

    __slots__ = ("conn", "lock")

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.lock = threading.Lock()


class OpsDb:
    """管理各工程 ops.db 连接与迁移（进程级单例，线程安全）。"""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self._registry_lock = threading.Lock()
        self._projects: dict[str, _ProjectOps] = {}

    def db_path(self, project_id: str) -> Path:
        return self.root / project_id / "ops.db"

    def has_db(self, project_id: str) -> bool:
        """无 ops.db 文件 = 该工程空状态（ADR 0021 失败模型）。"""
        return self.db_path(project_id).is_file()

    def connection_for(self, project_id: str) -> _ProjectOps:
        """返回指定工程的 ops.db 连接视图；首次访问时建文件并跑迁移。

        迁移失败会抛出 sqlite3.Error（可见报错），连接不缓存、迁移 key 不记录。
        """
        with self._registry_lock:
            ops = self._projects.get(project_id)
            if ops is not None:
                return ops
            path = self.db_path(project_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            try:
                self._migrate(conn)
            except sqlite3.Error:
                conn.close()
                raise
            ops = _ProjectOps(conn)
            self._projects[project_id] = ops
            return ops

    def _migrate(self, conn: sqlite3.Connection) -> None:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS ops_migrations ("
            " key TEXT PRIMARY KEY, "
            " applied_at TEXT NOT NULL"
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
                    "INSERT INTO ops_migrations (key, applied_at) VALUES (?, ?)",
                    (key, _now_iso()),
                )
                conn.commit()
            except sqlite3.Error:
                conn.rollback()
                # 不记录失败的 key：下次连接重试；异常上抛 = 可见报错（ADR 0021）
                raise

    def close(self, project_id: str) -> None:
        with self._registry_lock:
            ops = self._projects.pop(project_id, None)
        if ops is not None:
            ops.conn.close()

    def close_all(self) -> None:
        with self._registry_lock:
            pending = list(self._projects.values())
            self._projects.clear()
        for ops in pending:
            ops.conn.close()
