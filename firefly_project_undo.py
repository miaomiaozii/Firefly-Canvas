"""画布级 undo/redo 栈持久化（issue #251：undo 栈刷新即失忆 → ops.db ADR 0021 租户之二）。

前端画布 undo 快照（static/js/canvas-undo.js：UNDO_LIMIT=40、pushUndo/commitPendingUndo、
悬空媒体守卫 #62/#146）原先只在内存里，页面刷新即失忆（buildSavePayload 不含 undoStack，
canvas 加载恢复也不重建）。现在整栈（undo + redo）按项目持久化进工程 ops.db 的
``undo_snapshots``（每项目单行 upsert），页面加载时前端拉回，刷新/重启后可跨会话撤销。

与 #257 ``versions_snapshots`` 的关系：不同概念、不同表，勿混——
版本树是 flush 触发的历史版本（供回滚 diff/restore）；undo_snapshots 是会话级
undo 连续性（前端撤销栈快照），两者只共享 ops.db 的租户机制。

失败模型（ADR 0021）：
- 无 ``ops.db`` 文件 = 该工程空状态（load 返回空栈，不报错）。
- 项目不存在 = KeyError（路由映射 404）。
- 快照内容对后端是不透明 JSON（前端 schema 为准），后端只校验形状（list）与
  JSON 可序列化，并裁剪到 ``UNDO_STACK_MAX_ENTRIES``（与前端 UNDO_LIMIT=40 同口径）。
- save 出错（strict=True，API 显式调用）上抛 = 可见报错；strict=False 记日志返回 None，
  不阻塞画布操作（前端自动保存路径）。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List

# 快照栈保留上限：与 static/js/canvas-undo.js 的 UNDO_LIMIT=40 同口径。
UNDO_STACK_MAX_ENTRIES = 40


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sanitize_entries(entries: Any) -> List[Any]:
    """校验快照列表：必须是 list、每项 JSON 可序列化，并裁剪到最新 N 条。

    裁剪保留**最新**的 UNDO_STACK_MAX_ENTRIES 条——与前端 pushUndo 的
    ``push → if(>LIMIT) shift`` FIFO 语义一致（最旧先淘汰）。
    """
    if not isinstance(entries, list):
        raise ValueError("undo/redo 必须是列表")
    try:
        # JSON 往返 = 可序列化校验 + 深拷贝（不信任传入对象的后续变动）。
        cleaned = [json.loads(json.dumps(entry, ensure_ascii=False)) for entry in entries]
    except TypeError as exc:
        raise ValueError(f"快照项必须可 JSON 序列化: {exc}") from exc
    if len(cleaned) > UNDO_STACK_MAX_ENTRIES:
        cleaned = cleaned[-UNDO_STACK_MAX_ENTRIES:]
    return cleaned


class ProjectUndoStackMixin:
    """undo/redo 栈持久化。依赖 ProjectVersionsMixin 提供 _opsdb / _require_project_id（同一 ops.db 租户机制）。"""

    UNDO_STACK_MAX_ENTRIES = UNDO_STACK_MAX_ENTRIES

    def save_undo_stack(
        self,
        project_id: str,
        undo_entries: Any,
        redo_entries: Any,
        strict: bool = False,
    ) -> Dict[str, Any] | None:
        """整栈 upsert 进 ops.db undo_snapshots（每项目单行）。

        undo/redo 形状非法（非 list / 不可序列化）抛 ValueError（路由映射 400）；
        存储错误 strict=True 上抛（可见报错）、strict=False 记日志返回 None。
        """
        project_id = self._require_project_id(project_id)
        undo = _sanitize_entries(undo_entries)
        redo = _sanitize_entries(redo_entries)
        timestamp = _now_iso()
        undo_json = json.dumps(undo, ensure_ascii=False, separators=(",", ":"))
        redo_json = json.dumps(redo, ensure_ascii=False, separators=(",", ":"))
        try:
            db = self._opsdb()
            with db.project_lock(project_id), db.connect(project_id) as conn:
                conn.execute(
                    "INSERT INTO undo_snapshots (project_id, undo_json, redo_json, updated_at) "
                    "VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(project_id) DO UPDATE SET "
                    "undo_json = excluded.undo_json, redo_json = excluded.redo_json, "
                    "updated_at = excluded.updated_at",
                    (project_id, undo_json, redo_json, timestamp),
                )
                conn.commit()
        except Exception:
            if strict:
                raise
            print(
                f"[firefly-projects] 项目 {project_id} undo 栈持久化失败（不影响画布操作）",
                file=sys.stderr,
            )
            return None
        return {
            "project_id": project_id,
            "undo_count": len(undo),
            "redo_count": len(redo),
            "updated_at": timestamp,
        }

    def load_undo_stack(self, project_id: str) -> Dict[str, Any]:
        """读取整栈（页面加载恢复用）。无 ops.db / 无行 = 空栈（ADR 0021 失败模型）。"""
        project_id = self._require_project_id(project_id)
        empty = {"project_id": project_id, "undo": [], "redo": [], "updated_at": None}
        db = self._opsdb()
        if not db.has_db(project_id):
            return empty
        with db.project_lock(project_id), db.connect(project_id) as conn:
            row = conn.execute(
                "SELECT undo_json, redo_json, updated_at FROM undo_snapshots WHERE project_id = ?",
                (project_id,),
            ).fetchone()
        if row is None:
            return empty
        return {
            "project_id": project_id,
            "undo": json.loads(row["undo_json"]),
            "redo": json.loads(row["redo_json"]),
            "updated_at": row["updated_at"],
        }
