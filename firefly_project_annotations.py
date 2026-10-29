"""画布批注对象持久化（issue #272：#259 产品决策——批注=画布可持久对象）。

#259 评论区维护者已批口径：①批注=画布可持久对象（存 ops.db 新租户 anno_*，
additive 迁移；可撤销、刷新恢复、随画板走）；②批注→改图经 #242 确认门由
runAll 逐条转化（下一票，本票只预留 status 字段）；③批注不进 #262 任务中心首版。

批注对象模型（锚定语义）：
- 锚定节点：``node_id``（画布节点 id，随画板走——节点在画布上移动/缩放，
  批注跟随，不存绝对屏幕坐标）；
- 节点内相对位置：``x`` / ``y`` ∈ [0,1]（归一化到节点宽高，画布缩放不失真；
  区域级圈注锚定留给 runAll 票）；
- 文本：``text``（≤ ``ANNOTATION_MAX_TEXT_LEN`` 字符）；
- 状态机：``status`` ∈ {``pending``, ``converted``}——pending=待转化，
  converted=已被 runAll 转为改图任务（本票只写入 pending，converted 通路
  在 update 上开放，供下一票使用）。

与 #257 versions_snapshots / #251 undo_snapshots 的关系：不同租户、不同表，
只共享 ops.db 的 ADR 0021 additive-only 迁移机制（迁移 key ``anno_001``）。

失败模型（ADR 0021）：
- 无 ``ops.db`` 文件 = 该工程空状态（list 返回 []，不报错）。
- 项目不存在 = KeyError（路由映射 404）；批注 id 不存在 = KeyError（路由映射 404）。
- 字段非法（空文本/空 node_id/越界 x,y/非法 status/超长文本）= ValueError（路由映射 400）。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# 批注文本上限（防滥用撑爆气泡列表；与 ADR 0021「ops.db 只存画布对象级内容」同口径）。
ANNOTATION_MAX_TEXT_LEN = 500

# 状态机取值（#259 产品决策：pending→converted；converted 由 runAll 票写入）。
ANNOTATION_STATUSES = ("pending", "converted")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _validate_annotation_fields(
    node_id: Any = None,
    text: Any = None,
    x: Any = None,
    y: Any = None,
    status: Any = None,
) -> Dict[str, Any]:
    """校验批注字段（create 全量、update 增量——只校验调用方显式传了的字段）。

    非法抛 ValueError（路由映射 400）：空 node_id / 空文本 / 文本超长 /
    x 或 y 非数值或不在 [0,1] / status 不在 {pending, converted}。
    """
    cleaned: Dict[str, Any] = {}
    if node_id is not None:
        node_id = str(node_id).strip()
        if not node_id:
            raise ValueError("node_id 不能为空")
        cleaned["node_id"] = node_id
    if text is not None:
        text = str(text).strip()
        if not text:
            raise ValueError("批注文本不能为空")
        if len(text) > ANNOTATION_MAX_TEXT_LEN:
            raise ValueError(f"批注文本不能超过 {ANNOTATION_MAX_TEXT_LEN} 字符")
        cleaned["text"] = text
    for axis in ("x", "y"):
        value = locals()[axis]
        if value is not None:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{axis} 必须是数值")
            value = float(value)
            if value < 0.0 or value > 1.0:
                raise ValueError(f"{axis} 必须在 [0,1] 内（节点内归一化坐标）")
            cleaned[axis] = value
    if status is not None:
        status = str(status).strip()
        if status not in ANNOTATION_STATUSES:
            raise ValueError(f"status 必须是 {'/'.join(ANNOTATION_STATUSES)} 之一")
        cleaned["status"] = status
    return cleaned


def _annotation_row(row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "project_id": row["project_id"],
        "node_id": row["node_id"],
        "text": row["text"],
        "x": row["x"],
        "y": row["y"],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


class ProjectAnnotationsMixin:
    """画布批注 CRUD。依赖 ProjectVersionsMixin 提供 _opsdb / _require_project_id（同一 ops.db 租户机制）。"""

    ANNOTATION_MAX_TEXT_LEN = ANNOTATION_MAX_TEXT_LEN

    def list_project_annotations(self, project_id: str) -> List[Dict[str, Any]]:
        """批注列表（创建序，id 升序）。无 ops.db 文件 = 空状态（ADR 0021 失败模型）。"""
        project_id = self._require_project_id(project_id)
        db = self._opsdb()
        if not db.has_db(project_id):
            return []
        with db.project_lock(project_id), db.connect(project_id) as conn:
            rows = conn.execute(
                "SELECT id, project_id, node_id, text, x, y, status, created_at, updated_at "
                "FROM anno_annotations WHERE project_id = ? ORDER BY id ASC",
                (project_id,),
            ).fetchall()
        return [_annotation_row(row) for row in rows]

    def create_project_annotation(
        self,
        project_id: str,
        node_id: str,
        text: str,
        x: float = 0.5,
        y: float = 0.2,
        status: str = "pending",
    ) -> Dict[str, Any]:
        """新建批注（默认 pending），返回落库后的完整行（含服务端分配的 id/时间戳）。

        字段非法抛 ValueError（路由映射 400）；项目不存在抛 KeyError（路由映射 404）。
        """
        project_id = self._require_project_id(project_id)
        cleaned = _validate_annotation_fields(node_id=node_id, text=text, x=x, y=y, status=status)
        timestamp = _now_iso()
        db = self._opsdb()
        with db.project_lock(project_id), db.connect(project_id) as conn:
            cursor = conn.execute(
                "INSERT INTO anno_annotations (project_id, node_id, text, x, y, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    project_id,
                    cleaned["node_id"],
                    cleaned["text"],
                    cleaned["x"],
                    cleaned["y"],
                    cleaned["status"],
                    timestamp,
                    timestamp,
                ),
            )
            anno_id = cursor.lastrowid
            conn.commit()
            row = conn.execute(
                "SELECT id, project_id, node_id, text, x, y, status, created_at, updated_at "
                "FROM anno_annotations WHERE project_id = ? AND id = ?",
                (project_id, anno_id),
            ).fetchone()
        return _annotation_row(row)

    def update_project_annotation(
        self,
        project_id: str,
        anno_id: int,
        text: Optional[str] = None,
        x: Optional[float] = None,
        y: Optional[float] = None,
        status: Optional[str] = None,
    ) -> Dict[str, Any]:
        """编辑批注（增量：只改显式传入的字段，created_at 不变）。

        字段非法抛 ValueError（400）；批注 id 不存在抛 KeyError（404）。
        """
        project_id = self._require_project_id(project_id)
        cleaned = _validate_annotation_fields(text=text, x=x, y=y, status=status)
        if not cleaned:
            raise ValueError("没有要更新的字段")
        db = self._opsdb()
        with db.project_lock(project_id), db.connect(project_id) as conn:
            row = conn.execute(
                "SELECT id FROM anno_annotations WHERE project_id = ? AND id = ?",
                (project_id, int(anno_id)),
            ).fetchone()
            if row is None:
                raise KeyError(anno_id)
            assignments = ", ".join(f"{key} = ?" for key in cleaned)
            conn.execute(
                f"UPDATE anno_annotations SET {assignments}, updated_at = ? WHERE project_id = ? AND id = ?",
                (*cleaned.values(), _now_iso(), project_id, int(anno_id)),
            )
            conn.commit()
            row = conn.execute(
                "SELECT id, project_id, node_id, text, x, y, status, created_at, updated_at "
                "FROM anno_annotations WHERE project_id = ? AND id = ?",
                (project_id, int(anno_id)),
            ).fetchone()
        return _annotation_row(row)

    def delete_project_annotation(self, project_id: str, anno_id: int) -> Dict[str, Any]:
        """删除批注。批注 id 不存在抛 KeyError（路由映射 404）。"""
        project_id = self._require_project_id(project_id)
        db = self._opsdb()
        with db.project_lock(project_id), db.connect(project_id) as conn:
            cursor = conn.execute(
                "DELETE FROM anno_annotations WHERE project_id = ? AND id = ?",
                (project_id, int(anno_id)),
            )
            conn.commit()
            if cursor.rowcount == 0:
                raise KeyError(anno_id)
        return {"project_id": project_id, "id": int(anno_id)}
