"""Cleanup domain of the project store.

Owns preview / execute cleanup plus unreferenced-media deletion.  Module-level
helpers, error types and constants live in ``firefly_projects`` and are reached
through ``_store`` so that tests patching ``firefly_projects.<name>`` keep
intercepting these call sites.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List

import firefly_projects as _store


class ProjectCleanupMixin:
    def preview_project_cleanup(self, project_id: str) -> Dict[str, Any]:
        """Describe proven transient media orphans without changing project state."""
        project_id = _store._clean_project_id(project_id)
        with self._project_operation_lock(project_id):
            with self._lock:
                project_dir = self._project_directory(project_id)
                transaction_path = self._safe_project_path(
                    project_dir,
                    f"trash/{_store.ORIGINAL_MEDIA_TRANSACTION_NAME}",
                )
                if transaction_path.exists():
                    raise _store.ProjectStoreError("项目清理预览发现媒体事务尚未完成")
                try:
                    project = _store._read_json(self._safe_project_path(project_dir, "project.json"))
                    persisted_canvas = _store._read_json(self._safe_project_path(project_dir, "canvas.json"))
                    persisted_history_payload = _store._read_json(
                        self._safe_project_path(project_dir, "history.json")
                    )
                    media_index = _store._read_json(self._safe_project_path(project_dir, "media/index.json"))
                    trash_index = _store._read_json(self._safe_project_path(project_dir, "trash/index.json"))
                except (OSError, ValueError) as exc:
                    raise _store.ProjectStoreError("项目清理预览无法读取项目数据") from exc
                persisted_history = persisted_history_payload.get("records")
                canvas = copy.deepcopy(persisted_canvas)
                history = copy.deepcopy(persisted_history)
                loaded_canvas = self._canvases.get(project_id)
                loaded_history = self._histories.get(project_id)
                runtime_canvas = (
                    copy.deepcopy(loaded_canvas) if isinstance(loaded_canvas, dict) else None
                )
                runtime_history = (
                    copy.deepcopy(loaded_history) if isinstance(loaded_history, list) else None
                )
                if runtime_canvas is not None:
                    canvas = runtime_canvas
                if runtime_history is not None:
                    history = runtime_history
                media_items = media_index.get("items")
                trash_items = trash_index.get("items")
                if (
                    project.get("format") != "firefly-project-v1"
                    or project.get("id") != project_id
                    or persisted_canvas.get("format") != "firefly-canvas-v1"
                    or persisted_canvas.get("project_id") != project_id
                    or persisted_canvas.get("id") != project_id
                    or canvas.get("format") != "firefly-canvas-v1"
                    or canvas.get("project_id") != project_id
                    or canvas.get("id") != project_id
                    or not isinstance(persisted_history, list)
                    or not isinstance(history, list)
                    or not isinstance(media_items, dict)
                    or not isinstance(trash_items, list)
                ):
                    raise _store.ProjectStoreError("项目清理预览无法验证项目数据")

                ownership_documents: Dict[str, Any] = {
                    "project.json": project,
                    "canvas.json": persisted_canvas,
                    "history.json": persisted_history_payload,
                    "media/index.json": media_index,
                    "trash/index.json": trash_index,
                }
                if runtime_canvas is not None:
                    ownership_documents["runtime/canvas.json"] = runtime_canvas
                if runtime_history is not None:
                    ownership_documents["runtime/history.json"] = {"records": runtime_history}
                backup_path = self._safe_project_path(project_dir, "canvas.json.bak")
                undo_dir = self._safe_project_path(project_dir, "trash/undo")
                try:
                    if backup_path.exists():
                        backup = _store._read_json(backup_path)
                        if (
                            not isinstance(backup, dict)
                            or backup.get("format") != "firefly-canvas-v1"
                            or backup.get("id") != project_id
                            or backup.get("project_id") != project_id
                        ):
                            raise _store.ProjectStoreError("项目清理预览发现恢复数据所有权冲突")
                        ownership_documents["canvas.json.bak"] = backup
                    if undo_dir.exists():
                        if not undo_dir.is_dir():
                            raise _store.ProjectStoreError("项目清理预览无法验证撤销记录")
                        for undo_path in sorted(undo_dir.iterdir(), key=lambda item: item.name):
                            relative_name = undo_path.relative_to(project_dir).as_posix()
                            safe_undo_path = self._safe_project_path(project_dir, relative_name)
                            if safe_undo_path.suffix.lower() != ".json" or not safe_undo_path.is_file():
                                raise _store.ProjectStoreError("项目清理预览无法验证撤销记录")
                            ownership_documents[relative_name] = _store._read_json(safe_undo_path)
                except (OSError, ValueError) as exc:
                    raise _store.ProjectStoreError("项目清理预览无法读取恢复数据") from exc

                declared_trash_files = {
                    "media": set(),
                    "previews": set(),
                    "undo": set(),
                }
                for item in trash_items:
                    if not isinstance(item, dict):
                        raise _store.ProjectStoreError("项目清理预览无法验证恢复索引")
                    media_filename = item.get("media_filename")
                    if media_filename not in (None, ""):
                        if not isinstance(media_filename, str) or Path(media_filename).name != media_filename:
                            raise _store.ProjectStoreError("项目清理预览无法验证恢复媒体路径")
                        declared_trash_files["media"].add(media_filename)
                    preview_filenames = item.get("preview_filenames")
                    if preview_filenames is not None:
                        if (
                            not isinstance(preview_filenames, list)
                            or any(
                                not isinstance(name, str) or Path(name).name != name
                                for name in preview_filenames
                            )
                        ):
                            raise _store.ProjectStoreError("项目清理预览无法验证恢复预览路径")
                        declared_trash_files["previews"].update(preview_filenames)
                    undo = item.get("undo")
                    if undo is not None:
                        snapshot_filename = (
                            undo.get("snapshot_filename") if isinstance(undo, dict) else None
                        )
                        if (
                            not isinstance(snapshot_filename, str)
                            or Path(snapshot_filename).name != snapshot_filename
                            or not snapshot_filename.endswith(".json")
                        ):
                            raise _store.ProjectStoreError("项目清理预览无法验证撤销路径")
                        declared_trash_files["undo"].add(snapshot_filename)
                for directory_name, expected_names in declared_trash_files.items():
                    directory = self._safe_project_path(
                        project_dir,
                        f"trash/{directory_name}",
                    )
                    if not directory.exists():
                        if expected_names:
                            raise _store.ProjectStoreError("项目清理预览恢复数据读取不完整")
                        continue
                    if not directory.is_dir():
                        raise _store.ProjectStoreError("项目清理预览无法验证恢复数据目录")
                    try:
                        entries = list(directory.iterdir())
                    except OSError as exc:
                        raise _store.ProjectStoreError("项目清理预览无法读取恢复数据目录") from exc
                    actual_names = set()
                    for entry in entries:
                        safe_entry = self._safe_project_path(
                            project_dir,
                            entry.relative_to(project_dir).as_posix(),
                        )
                        if not safe_entry.is_file():
                            raise _store.ProjectStoreError("项目清理预览无法验证恢复数据文件")
                        actual_names.add(entry.name)
                    if actual_names != expected_names:
                        label = "恢复媒体" if directory_name == "media" else "恢复数据"
                        raise _store.ProjectStoreError(f"项目清理预览发现未索引{label}或恢复文件缺失")

                owned_asset_ids = self._collect_asset_ids(canvas)
                owned_asset_ids.update(self._collect_asset_ids(history))
                owned_asset_ids.update(self._collect_asset_ids(trash_index))
                ownership_json = json.dumps(
                    {
                        key: value
                        for key, value in ownership_documents.items()
                        if key != "media/index.json"
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )

                candidates: List[Dict[str, Any]] = []
                proof_candidates: List[Dict[str, str]] = []
                for asset_id, record in media_items.items():
                    if not isinstance(asset_id, str) or not isinstance(record, dict):
                        raise _store.ProjectStoreError("项目清理预览无法验证媒体索引")
                    filename = str(record.get("filename") or "")
                    sha256 = str(record.get("sha256") or "").lower()
                    if (
                        record.get("asset_id") != asset_id
                        or not re.fullmatch(r"[0-9a-f]{64}", asset_id)
                        or sha256 != asset_id
                        or Path(filename).stem != asset_id
                    ):
                        raise _store.ProjectStoreError("项目清理预览无法验证媒体身份")
                    media_path = self._safe_project_path(
                        project_dir,
                        (Path("media") / filename).as_posix(),
                    )
                    if not media_path.is_file() or _store._file_sha256(media_path) != sha256:
                        raise _store.ProjectStoreError("项目清理预览媒体读取不完整")
                    try:
                        size = int(record.get("size"))
                    except (TypeError, ValueError) as exc:
                        raise _store.ProjectStoreError("项目清理预览无法验证媒体大小") from exc
                    if size != media_path.stat().st_size:
                        raise _store.ProjectStoreError("项目清理预览无法验证媒体大小")
                    known_media_fields = {
                        "asset_id", "filename", "name", "mime_type", "size",
                        "sha256", "kind", "created_at", "source", "sources",
                    }
                    if set(record) - known_media_fields:
                        raise _store.ProjectStoreError("项目清理预览无法验证媒体 schema")
                    raw_source = record.get("source")
                    raw_sources = record.get("sources")
                    if (
                        raw_source is not None and not isinstance(raw_source, dict)
                        or raw_sources is not None and (
                            not isinstance(raw_sources, list)
                            or any(not isinstance(item, dict) for item in raw_sources)
                        )
                    ):
                        raise _store.ProjectStoreError("项目清理预览无法验证媒体来源")
                    sources = []
                    if isinstance(raw_source, dict):
                        sources.append(raw_source)
                    if isinstance(raw_sources, list):
                        sources.extend(raw_sources)
                    if (
                        any(self._media_source(source) != source for source in sources)
                        or isinstance(raw_sources, list)
                        and isinstance(raw_source, dict)
                        and (not raw_sources or raw_sources[0] != raw_source)
                    ):
                        raise _store.ProjectStoreError("项目清理预览无法验证媒体来源")
                    source_types = {
                        str(source.get("type") or "")
                        for source in sources
                        if str(source.get("type") or "")
                    }
                    cleanup_lifecycles = {
                        str(source.get("lifecycle") or "")
                        for source in sources
                        if str(source.get("type") or "") == "online_generation"
                    }
                    cleanup_lifecycle = (
                        next(iter(cleanup_lifecycles))
                        if len(cleanup_lifecycles) == 1
                        and cleanup_lifecycles <= {"failed", "transient"}
                        else ""
                    )
                    if (
                        not cleanup_lifecycle
                        or source_types != {"online_generation"}
                        or asset_id in owned_asset_ids
                        or asset_id in ownership_json
                    ):
                        continue
                    candidates.append({
                        "asset_id": asset_id,
                        "name": str(record.get("name") or filename),
                        "kind": str(record.get("kind") or self._media_kind(
                            str(record.get("mime_type") or ""), filename,
                        )),
                        "mime_type": str(record.get("mime_type") or "application/octet-stream"),
                        "size": size,
                        "source_category": f"{cleanup_lifecycle}_generation",
                    })
                    proof_candidates.append({
                        "asset_id": asset_id,
                        "filename": filename,
                        "sha256": sha256,
                    })

                media_root = self._safe_project_path(project_dir, "media")
                indexed_filenames = {
                    str(record.get("filename") or "")
                    for record in media_items.values()
                    if isinstance(record, dict)
                }
                try:
                    media_entries = list(media_root.iterdir())
                    if any(
                        path.name != "index.json" and not path.is_file()
                        for path in media_entries
                    ):
                        raise _store.ProjectStoreError("项目清理预览发现无法验证的媒体目录条目")
                    physical_filenames = {
                        path.name
                        for path in media_entries
                        if path.name != "index.json"
                    }
                except OSError as exc:
                    raise _store.ProjectStoreError("项目清理预览无法读取媒体目录") from exc
                if physical_filenames != indexed_filenames:
                    raise _store.ProjectStoreError("项目清理预览发现未索引媒体或缺失索引文件")

                candidates.sort(key=lambda item: item["asset_id"])
                proof_candidates.sort(key=lambda item: item["asset_id"])
                proof_payload = {
                    "format": "firefly-project-cleanup-preview-v1",
                    "project_id": project_id,
                    "revision": int(canvas.get("revision") or 0),
                    "ownership": ownership_documents,
                    "candidates": proof_candidates,
                }
                ownership_proof = hashlib.sha256(json.dumps(
                    proof_payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")).hexdigest()
                return {
                    "project_id": project_id,
                    "revision": proof_payload["revision"],
                    "ownership_proof": ownership_proof,
                    "candidate_count": len(candidates),
                    "total_bytes": sum(int(item["size"]) for item in candidates),
                    "candidates": candidates,
                }

    def execute_project_cleanup(
        self,
        project_id: str,
        *,
        revision: int,
        ownership_proof: str,
        candidate_asset_ids: List[str],
    ) -> Dict[str, Any]:
        """Permanently delete exactly the previewed cleanup candidates.

        The confirmation must bind the preview's revision, ownership proof and
        exact candidate identities.  Current state is re-analysed read-only and
        compared against the confirmation before any deletion; any drift aborts
        without deleting and demands a fresh preview.  Deletion writes no
        recovery transaction, trash record or undo entry: successful deletions
        are permanent and unrecoverable.
        """
        project_id = _store._clean_project_id(project_id)
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            raise _store.ProjectStoreError("永久清理确认包含无效版本")
        proof = str(ownership_proof or "")
        if not re.fullmatch(r"[0-9a-f]{64}", proof):
            raise _store.ProjectStoreError("永久清理确认包含无效所有权证明")
        submitted = candidate_asset_ids
        if (
            not isinstance(submitted, list)
            or any(
                not isinstance(item, str) or not re.fullmatch(r"[0-9a-f]{64}", item)
                for item in submitted
            )
            or len({item for item in submitted if isinstance(item, str)}) != len(submitted)
        ):
            raise _store.ProjectStoreError("永久清理确认包含无效候选")
        with self._project_operation_lock(project_id):
            with self._lock:
                preview = self.preview_project_cleanup(project_id)
                if int(preview.get("revision") or 0) != revision:
                    raise _store.CleanupDrift("项目版本已变化，请重新预览后再确认")
                if preview.get("ownership_proof") != proof:
                    raise _store.CleanupDrift("项目所有权证明已变化，请重新预览后再确认")
                current_candidates = {
                    str(item["asset_id"])
                    for item in preview.get("candidates") or []
                    if isinstance(item, dict) and item.get("asset_id")
                }
                if current_candidates != set(submitted):
                    raise _store.CleanupDrift("清理候选已变化，请重新预览后再确认")
                previewed_by_id = {
                    str(item["asset_id"]): item
                    for item in preview.get("candidates") or []
                    if isinstance(item, dict) and item.get("asset_id")
                }
                project_dir = self._project_directory(project_id)
                index = self._load_media_index(project_id)
                deleted: List[Dict[str, Any]] = []
                failed: List[Dict[str, Any]] = []
                for asset_id in sorted(set(submitted)):
                    record = index["items"].get(asset_id)
                    if not isinstance(record, dict):
                        raise _store.CleanupDrift("清理执行时媒体索引已变化，请重新预览后再确认")
                    filename = str(record.get("filename") or "")
                    media_path = self._safe_project_path(
                        project_dir,
                        (Path("media") / filename).as_posix(),
                    )
                    try:
                        media_path.unlink(missing_ok=True)
                    except OSError as exc:
                        # The candidate stays fully indexed so a later preview
                        # can prove it again; report the exact failure instead
                        # of claiming the deletion.
                        failed.append({
                            "asset_id": asset_id,
                            "reason": str(exc) or "文件删除失败",
                        })
                        continue
                    index["items"].pop(asset_id, None)
                    previewed = previewed_by_id.get(asset_id) or {}
                    deleted.append({
                        "asset_id": asset_id,
                        "filename": filename,
                        "name": str(record.get("name") or filename),
                        "kind": str(record.get("kind") or self._media_kind(
                            str(record.get("mime_type") or ""), filename,
                        )),
                        "mime_type": str(record.get("mime_type") or "application/octet-stream"),
                        "size": int(record.get("size") or 0),
                        "source_category": str(previewed.get("source_category") or ""),
                    })
                if deleted:
                    _store._write_json_atomic(
                        self._safe_project_path(project_dir, "media/index.json"),
                        index,
                        keep_backup=False,
                    )
                _store._diag_emit(
                    "cleanup.executed",
                    project_id=project_id,
                    revision=revision,
                    deleted_count=len(deleted),
                    failed_count=len(failed),
                    deleted_bytes=sum(int(item.get("size") or 0) for item in deleted),
                    state="completed" if not failed else "partial",
                )
                return {
                    "project_id": project_id,
                    "revision": revision,
                    "deleted_count": len(deleted),
                    "deleted_bytes": sum(int(item["size"]) for item in deleted),
                    "deleted": sorted(deleted, key=lambda item: item["asset_id"]),
                    "failed": sorted(failed, key=lambda item: item["asset_id"]),
                    "complete": not failed,
                }

    def delete_media_if_unreferenced(self, project_id: str, asset_id: str) -> bool:
        project_id = _store._clean_project_id(project_id)
        asset_id = str(asset_id or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,200}", asset_id):
            return False
        with self._lock:
            opened = self.open_project(project_id)["canvas"]
            referenced = self._collect_asset_ids(opened.get("nodes") or [])
            referenced.update(self._collect_asset_ids(self._histories.get(project_id, [])))
            if asset_id in referenced:
                return False
            index = self._load_media_index(project_id)
            record = index["items"].pop(asset_id, None)
            if not isinstance(record, dict):
                return False
            media_root = (self.root / project_id / "media").resolve()
            path = (media_root / str(record.get("filename") or "")).resolve()
            if os.path.commonpath([str(media_root), str(path)]) == str(media_root):
                path.unlink(missing_ok=True)
            _store._write_json_atomic(media_root / "index.json", index, keep_backup=False)
            return True

    # 回收区条目中仅属于「删除时附加」的字段；恢复时剥离后剩余部分就是原媒体索引记录。
    _TRASH_ONLY_FIELDS = frozenset({
        "deleted_at", "expires_at", "media_filename", "preview_filenames",
        "reason", "undo", "deletion_id", "deleted_output_id", "name",
    })

    def trash_unreferenced_generation_media(self, project_id: str, asset_id: str, reason: str = "generation-orphan") -> bool:
        """系统回收（#79/D3）：生成孤儿媒体移入回收区（7 天可恢复），替代静默硬删。

        引用检查与 delete_media_if_unreferenced 一致：被画布节点或历史记录引用时不处理。
        回收区条目可被回收区面板列出/恢复/彻底删除（恢复走 restore_trash_media）。
        """
        project_id = _store._clean_project_id(project_id)
        asset_id = str(asset_id or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,200}", asset_id):
            return False
        with self._lock:
            opened = self.open_project(project_id)["canvas"]
            referenced = self._collect_asset_ids(opened.get("nodes") or [])
            referenced.update(self._collect_asset_ids(self._histories.get(project_id, [])))
            if asset_id in referenced:
                return False
            index = self._load_media_index(project_id)
            record = index["items"].get(asset_id)
            if not isinstance(record, dict):
                return False
            project_dir = self._project_directory(project_id)
            media_root = (project_dir / "media").resolve()
            path = (media_root / str(record.get("filename") or "")).resolve()
            if os.path.commonpath([str(media_root), str(path)]) != str(media_root):
                return False
            if not path.is_file():
                index["items"].pop(asset_id, None)
                _store._write_json_atomic(media_root / "index.json", index, keep_backup=False)
                return True
            preview_root = (project_dir / "previews").resolve()
            preview_files = sorted(
                (p for p in preview_root.glob(f"{asset_id}-*") if p.is_file()),
                key=lambda p: p.name,
            ) if preview_root.is_dir() else []
            deleted_at = _store._now_ms()
            item = copy.deepcopy(record)
            item.update({
                "asset_id": asset_id,
                "deleted_at": deleted_at,
                "expires_at": deleted_at + _store.MEDIA_TRASH_RETENTION_MS,
                "media_filename": path.name,
                "preview_filenames": [p.name for p in preview_files],
                "reason": reason,
            })
            trash_media_root = self._safe_project_path(project_dir, (Path("trash") / "media").as_posix())
            trash_preview_root = self._safe_project_path(project_dir, (Path("trash") / "previews").as_posix())
            trash_media_root.mkdir(parents=True, exist_ok=True)
            trash_preview_root.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(trash_media_root / path.name))
            for preview in preview_files:
                shutil.move(str(preview), str(trash_preview_root / preview.name))
            trash_index = self._load_media_trash_index(project_id)
            trash_index["items"] = [
                candidate for candidate in trash_index.get("items", [])
                if not (isinstance(candidate, dict) and str(candidate.get("asset_id") or "") == asset_id)
            ]
            trash_index["items"].append(item)
            index["items"].pop(asset_id, None)
            _store._write_json_atomic(media_root / "index.json", index, keep_backup=False)
            _store._write_json_atomic(
                self._safe_project_path(project_dir, "trash/index.json"),
                {"items": trash_index["items"]},
                keep_backup=False,
            )
            return True

    def restore_trash_media(self, project_id: str, asset_id: str, *, base_revision: int) -> Dict[str, Any]:
        """通用回收区恢复：把回收区内的媒体移回 media/ 并重新注册到资源库。

        与 restore_deleted_original（生成输出 occurrence 恢复，需 deletion_id + 撤销快照）
        互补：不带撤销身份的回收条目（如生成孤儿回收）只恢复文件与索引，不恢复画布引用。
        """
        project_id = _store._clean_project_id(project_id)
        asset_id = str(asset_id or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,200}", asset_id):
            raise _store.ProjectStoreError("媒体资源标识无效")
        with self._project_operation_lock(project_id):
            with self._lock:
                opened = self.open_project(project_id)["canvas"]
                current_revision = int(opened.get("revision") or 0)
                if int(base_revision) != current_revision:
                    raise _store.RevisionConflict(opened)
                self._cleanup_expired_deleted_media_locked(project_id)
                trash_index = self._load_media_trash_index(project_id)
                item = next(
                    (
                        candidate for candidate in trash_index.get("items", [])
                        if isinstance(candidate, dict) and str(candidate.get("asset_id") or "") == asset_id
                    ),
                    None,
                )
                if item is None:
                    raise _store.ProjectNotFound("项目回收区中不存在该媒体")
                referenced = self._collect_asset_ids(opened.get("nodes") or [])
                referenced.update(self._collect_asset_ids(self._histories.get(project_id, [])))
                if asset_id in referenced:
                    raise _store.ProjectStoreError("该媒体已被画布引用，无需恢复")
                index = self._load_media_index(project_id)
                if asset_id in index["items"]:
                    raise _store.ProjectStoreError("该媒体已存在，无需恢复")
                media_filename = str(item.get("media_filename") or "").strip()
                if not media_filename:
                    raise _store.ProjectStoreError("该项目回收记录缺少媒体文件")
                project_dir = self._project_directory(project_id)
                trash_media_path = self._safe_project_path(
                    project_dir,
                    (Path("trash") / "media" / media_filename).as_posix(),
                )
                if not trash_media_path.is_file():
                    raise _store.ProjectNotFound("回收区媒体文件不存在")
                media_root = self._safe_project_path(project_dir, "media")
                media_root.mkdir(parents=True, exist_ok=True)
                preview_names = item.get("preview_filenames") if isinstance(item.get("preview_filenames"), list) else []
                preview_root = project_dir / "previews"
                restore_record = {
                    key: value for key, value in item.items() if key not in self._TRASH_ONLY_FIELDS
                }
                restore_record["asset_id"] = asset_id
                shutil.move(str(trash_media_path), str(media_root / media_filename))
                for name in preview_names:
                    if not isinstance(name, str) or not name:
                        continue
                    source = self._safe_project_path(
                        project_dir,
                        (Path("trash") / "previews" / name).as_posix(),
                    )
                    if source.is_file():
                        preview_root.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(source), str(preview_root / name))
                index["items"][asset_id] = restore_record
                trash_index["items"] = [
                    candidate for candidate in trash_index.get("items", [])
                    if not (isinstance(candidate, dict) and str(candidate.get("asset_id") or "") == asset_id)
                ]
                _store._write_json_atomic(media_root / "index.json", index, keep_backup=False)
                _store._write_json_atomic(
                    self._safe_project_path(project_dir, "trash/index.json"),
                    {"items": trash_index["items"]},
                    keep_backup=False,
                )
                canvas = copy.deepcopy(opened)
                canvas.pop("logs", None)
                canvas["revision"] = current_revision + 1
                canvas["updated_at"] = max(_store._now_ms(), int(canvas.get("updated_at") or 0) + 1)
                self._canvases[project_id] = canvas
                self._schedule_snapshot(project_id)
                result = copy.deepcopy(canvas)
                result["logs"] = copy.deepcopy(self._histories.get(project_id, []))
                return {"canvas": result, "restored": asset_id}

