"""画布版本树（issue #257；ADR 0021 ops.db 租户 + ADR 0018 恢复语义）。

- 每次 flush（手动保存 / 自动保存 / 崩溃安全网）自动把画布快照进工程
  ops.db 的 ``versions_snapshots``；sha256 去重，相同内容不产生新快照。
- 回滚到历史版本前，必须先存当前状态为 ``pre-restore`` 快照（防止回滚毁数据），
  再恢复目标版本并 flush（canvas.json .bak 轮转），任何时刻都可从"回滚前"再回滚回去。
- 保留上限 ``KEEP_VERSIONS = 30``（对照上游 ComfyUI KEEP_VERSIONS），写入后裁剪最旧快照。

失败模型：
- 回滚流程（strict）：ops.db 出错直接上抛 = 可见报错，且发生在修改画布之前。
- flush 自动快照（non-strict）：出错仅记日志，不阻塞画布保存（canvas.json/.bak
  仍是 ADR 0018 主安全网）；版本 API 会原样暴露 ops.db 错误。
"""
from __future__ import annotations

import copy
import hashlib
import json
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from firefly_opsdb import OpsDb
from firefly_projects import _clean_project_id

# 节点级运行时字段：不参与版本 diff 对比（与 duplicate_project 的剥离口径一致）
RUNTIME_NODE_KEYS = {"prompt", "modelId", "provider", "engine", "pendingTasks", "pending"}

# 版本树保留上限（对照上游 ComfyUI KEEP_VERSIONS=30）
KEEP_VERSIONS = 30


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _node_id(node: Dict[str, Any]) -> str:
    return str(node.get("id") or "")


def _node_label(node: Dict[str, Any]) -> str:
    return str(node.get("label") or node.get("type") or _node_id(node))


def _normalize_node(node: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in node.items() if key not in RUNTIME_NODE_KEYS}


class ProjectVersionsMixin:
    """版本树操作。依赖 ProjectStore 提供 _lock / _canvases / root / _project_operation_lock / flush。"""

    KEEP_VERSIONS = KEEP_VERSIONS

    # ── 基础设施 ─────────────────────────────────────────────────────

    def _opsdb(self) -> OpsDb:
        db = getattr(self, "_opsdb_instance", None)
        if db is None:
            db = OpsDb(self.root)
            self._opsdb_instance = db
        return db

    def _ops(self, project_id: str):
        return self._opsdb().connection_for(project_id)

    def _require_project_id(self, project_id: str) -> str:
        """规范化项目 id；不存在时抛 KeyError（路由映射 404）。"""
        project_id = _clean_project_id(project_id)
        if not (self.root / project_id).is_dir():
            raise KeyError(project_id)
        return project_id

    # ── 快照 ─────────────────────────────────────────────────────────

    def capture_project_version(
        self,
        project_id: str,
        canvas: Dict[str, Any],
        label: str = "auto",
        strict: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """把画布快照写入版本树；与最新快照内容相同（sha256）时返回 None（去重）。

        strict=True（回滚"先存当前"）：ops.db 出错上抛（可见报错，且发生在改画布前）。
        strict=False（flush 自动快照）：出错记日志并返回 None，不阻塞画布保存。
        """
        project_id = self._require_project_id(project_id)
        canvas_json = json.dumps(canvas, ensure_ascii=False, separators=(",", ":"))
        digest = _sha256(canvas_json)
        node_count = len(canvas.get("nodes") or [])
        version_id: Optional[int] = None
        try:
            ops = self._ops(project_id)
            with ops.lock:
                last = ops.conn.execute(
                    "SELECT id, canvas_sha256 FROM versions_snapshots "
                    "WHERE project_id = ? ORDER BY id DESC LIMIT 1",
                    (project_id,),
                ).fetchone()
                if last is not None and last["canvas_sha256"] == digest:
                    return None
                cursor = ops.conn.execute(
                    "INSERT INTO versions_snapshots "
                    "(project_id, label, canvas_json, canvas_sha256, node_count, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (project_id, label, canvas_json, digest, node_count, _now_iso()),
                )
                version_id = cursor.lastrowid
                # 保留上限：只留最新 KEEP_VERSIONS 条，裁剪最旧（对照上游 KEEP_VERSIONS=30）
                ops.conn.execute(
                    "DELETE FROM versions_snapshots WHERE id NOT IN ("
                    "SELECT id FROM versions_snapshots WHERE project_id = ? ORDER BY id DESC LIMIT ?)",
                    (project_id, self.KEEP_VERSIONS),
                )
                ops.conn.commit()
        except Exception:
            if strict:
                raise
            print(
                f"[firefly-projects] 项目 {project_id} 版本快照写入失败（不影响画布保存）",
                file=sys.stderr,
            )
            return None
        return self._get_version_row(project_id, version_id)

    def _get_version_row(self, project_id: str, version_id: int) -> Optional[Dict[str, Any]]:
        ops = self._ops(project_id)
        with ops.lock:
            row = ops.conn.execute(
                "SELECT id, label, node_count, created_at, canvas_sha256, canvas_json "
                "FROM versions_snapshots WHERE project_id = ? AND id = ?",
                (project_id, version_id),
            ).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "label": row["label"],
            "node_count": row["node_count"],
            "created_at": row["created_at"],
            "sha256": row["canvas_sha256"],
            "canvas": json.loads(row["canvas_json"]),
        }

    def list_project_versions(self, project_id: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """版本列表（新→旧）。无 ops.db 文件 = 该工程空状态（ADR 0021）。"""
        project_id = _clean_project_id(project_id)
        if not self._opsdb().has_db(project_id):
            return []
        ops = self._ops(project_id)
        sql = (
            "SELECT id, label, node_count, created_at, canvas_sha256 "
            "FROM versions_snapshots WHERE project_id = ? ORDER BY id DESC"
        )
        params: List[Any] = [project_id]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
        with ops.lock:
            rows = ops.conn.execute(sql, params).fetchall()
        return [
            {
                "id": row["id"],
                "label": row["label"],
                "node_count": row["node_count"],
                "created_at": row["created_at"],
                "sha256": row["canvas_sha256"],
            }
            for row in rows
        ]

    def get_project_version(self, project_id: str, version_id: int) -> Dict[str, Any]:
        record = self._get_version_row(project_id, version_id)
        if record is None:
            raise KeyError(version_id)
        return record

    # ── diff ─────────────────────────────────────────────────────────

    def _resolve_canvas(self, project_id: str, ref: str) -> Dict[str, Any]:
        """ref = 版本 id 或 'current'（内存画布优先；不在内存则取最新快照；再没有则读盘）。"""
        ref = str(ref or "current")
        if ref != "current":
            return self.get_project_version(project_id, int(ref))["canvas"]
        canvas = self._canvases.get(project_id)
        if canvas is None:
            versions = self.list_project_versions(project_id, limit=1)
            if versions:
                return self.get_project_version(project_id, versions[0]["id"])["canvas"]
            canvas = self.open_project(project_id)["canvas"]
        return copy.deepcopy(canvas)

    def diff_project_versions(self, project_id: str, base: Any, target: Any) -> Dict[str, Any]:
        """对比两个画布状态 base → target（回滚前确认用）。

        base/target 可为版本 id 或 'current'（target 不能是 current）。
        运行时字段（prompt/modelId/…）不参与对比。
        """
        project_id = self._require_project_id(project_id)
        base_ref = str(base or "current")
        target_ref = str(target)
        if target_ref == "current":
            raise ValueError("target 不能是 current")
        base_canvas = self._resolve_canvas(project_id, base_ref)
        target_record = self.get_project_version(project_id, int(target_ref))
        target_canvas = target_record["canvas"]

        base_nodes = {_node_id(n): _normalize_node(n) for n in base_canvas.get("nodes") or [] if _node_id(n)}
        target_nodes = {_node_id(n): _normalize_node(n) for n in target_canvas.get("nodes") or [] if _node_id(n)}
        added = [
            {"id": nid, "label": _node_label(n)}
            for nid, n in target_nodes.items()
            if nid not in base_nodes
        ]
        removed = [
            {"id": nid, "label": _node_label(n)}
            for nid, n in base_nodes.items()
            if nid not in target_nodes
        ]
        changed = [
            {"id": nid, "label": _node_label(target_nodes[nid])}
            for nid in base_nodes
            if nid in target_nodes and base_nodes[nid] != target_nodes[nid]
        ]
        return {
            "project_id": project_id,
            "base": {"ref": base_ref, "node_count": len(base_canvas.get("nodes") or [])},
            "target": {
                "id": target_record["id"],
                "label": target_record["label"],
                "created_at": target_record["created_at"],
                "node_count": len(target_canvas.get("nodes") or []),
            },
            "title_from": str(base_canvas.get("title") or ""),
            "title_to": str(target_canvas.get("title") or ""),
            "added": added,
            "removed": removed,
            "changed": changed,
            "stats": {
                "nodes_from": len(base_canvas.get("nodes") or []),
                "nodes_to": len(target_canvas.get("nodes") or []),
                "connections_from": len(base_canvas.get("connections") or []),
                "connections_to": len(target_canvas.get("connections") or []),
            },
        }

    # ── 回滚 ─────────────────────────────────────────────────────────

    def restore_project_version(self, project_id: str, version_id: int) -> Dict[str, Any]:
        """回滚到历史版本（ADR 0018 恢复语义）：

        1. 先存当前：把内存中未保存内容与当前状态存为 ``pre-restore`` 快照（strict，
           失败则中止回滚且不动画布）——回滚毁不了数据，"回滚前"永远在版本树里；
        2. 恢复目标版本进内存画布并 flush：canvas.json 原子写 + .bak 轮转，
           磁盘上同时保留 ADR 0018 的 .bak 副本，"回滚前"也可从 .bak 恢复。
        """
        with self._project_operation_lock(project_id):
            with self._lock:
                project_id = self._require_project_id(project_id)
                self.flush(project_id)  # 未保存内容先落盘，保证"当前"完整
                target = self.get_project_version(project_id, version_id)

                # ① 先存当前（防回滚毁数据）
                latest_before = self.list_project_versions(project_id, limit=1)
                latest_before_id = latest_before[0]["id"] if latest_before else None
                pre_restore_id = latest_before_id
                current = self._canvases.get(project_id)
                if current is not None:
                    snapshot = self.capture_project_version(
                        project_id,
                        self._project_canvas_for_persistence(current),
                        label="pre-restore",
                        strict=True,
                    )
                    if snapshot is not None:
                        pre_restore_id = snapshot["id"]

                # ② 回滚目标版本
                canvas = copy.deepcopy(target["canvas"])
                canvas.pop("_recovery", None)
                canvas["id"] = project_id
                canvas["project_id"] = project_id
                canvas["project"] = project_id
                canvas["revision"] = int(canvas.get("revision") or 0) + 1
                self._canvases[project_id] = canvas
                result = self.flush(project_id)  # flush 内自动快照会因去重跳过（内容 = 目标版本）
            return {
                "project_id": project_id,
                "version_id": version_id,
                "pre_restore_version_id": pre_restore_id,
                "revision": result["revision"],
                "saved_at": result["saved_at"],
            }

    # ── flush 自动快照钩子 ────────────────────────────────────────────

    def _capture_version_snapshot_if_enabled(self, project_id: str, canvas: Dict[str, Any]) -> None:
        """flush 自动快照（non-strict）：失败仅记日志，不阻塞画布保存。"""
        try:
            self.capture_project_version(project_id, canvas, label="auto", strict=False)
        except Exception:
            # 理论上 capture_project_version(strict=False) 已内部吞掉；
            # 这里兜底任何意外异常，保证 flush 路径健壮。
            print(f"[firefly-projects] 项目 {project_id} 版本快照钩子异常（不影响画布保存）", file=sys.stderr)
