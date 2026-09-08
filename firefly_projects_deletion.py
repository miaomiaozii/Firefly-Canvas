"""Deletion and recovery domain of the project store.

Owns delete-current-original / restore / project-wide deletion plus the
deleted-media trash zone.  Module-level helpers, error types and constants
live in ``firefly_projects`` and are reached through ``_store`` so that tests
patching ``firefly_projects.<name>`` keep intercepting these call sites.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List

from firefly_diagnostics import emit as _diag_emit

import firefly_projects as _store


class ProjectDeletionMixin:
    """Delete/restore generated outputs and the deleted-media trash zone."""

    @classmethod
    def _valid_output_id(cls, value: Any) -> str:
        output_id = str(value or "").strip().lower()
        return output_id if cls._OUTPUT_ID_PATTERN.fullmatch(output_id) else ""

    @staticmethod
    def _valid_output_order(value: Any) -> int | None:
        try:
            order = int(value)
        except (TypeError, ValueError):
            return None
        return order if order >= 0 else None

    @classmethod
    def _stable_legacy_output_id(
        cls,
        owner_id: str,
        output_index: int,
        asset_id: str,
        discriminator: int = 0,
    ) -> str:
        seed = f"{owner_id}\0{output_index}\0{asset_id}\0{discriminator}".encode("utf-8")
        return f"output-{hashlib.sha256(seed).hexdigest()[:32]}"

    @classmethod
    def _ensure_output_occurrence_ids(
        cls,
        canvas: Dict[str, Any],
        history: List[Dict[str, Any]],
    ) -> bool:
        """Give every generated output a stable identity owned by the Store.

        History records are the canonical owners.  Their producer node mirrors
        the same IDs by position and asset, while unrelated records that happen
        to share content-addressed media receive different IDs.  Existing IDs
        are preserved so deleting an earlier output never renumbers survivors.
        """
        changed = False
        used_history_ids: set[str] = set()
        producer_outputs: Dict[str, List[Dict[str, str]]] = {}
        for record_index, record in enumerate(history):
            if not isinstance(record, dict):
                continue
            outputs = record.get("outputs")
            if not isinstance(outputs, list):
                continue
            owner_id = str(record.get("id") or f"legacy-history-{record_index}")
            canonical: List[Dict[str, Any]] = []
            reserved_ids = {
                cls._valid_output_id(item.get("deleted_output_id"))
                for item in record.get("deletedOutputTombstones") or []
                if isinstance(item, dict)
            }
            used_history_ids.update(output_id for output_id in reserved_ids if output_id)
            used_orders = {
                order
                for item in record.get("deletedOutputTombstones") or []
                if isinstance(item, dict)
                for order in [cls._valid_output_order(item.get("output_order"))]
                if order is not None
            }
            next_order = max(used_orders, default=-1) + 1
            for output_index, item in enumerate(outputs):
                if not isinstance(item, dict):
                    canonical.append({"asset_id": "", "output_id": ""})
                    continue
                asset_id = str(item.get("asset_id") or "")
                output_id = cls._valid_output_id(item.get("output_id"))
                if not output_id or output_id in used_history_ids:
                    discriminator = 0
                    output_id = cls._stable_legacy_output_id(
                        owner_id, output_index, asset_id, discriminator,
                    )
                    while output_id in used_history_ids:
                        discriminator += 1
                        output_id = cls._stable_legacy_output_id(
                            owner_id, output_index, asset_id, discriminator,
                        )
                    item["output_id"] = output_id
                    changed = True
                elif item.get("output_id") != output_id:
                    item["output_id"] = output_id
                    changed = True
                used_history_ids.add(output_id)
                output_order = cls._valid_output_order(item.get("output_order"))
                if output_order is None or output_order in used_orders:
                    while next_order in used_orders:
                        next_order += 1
                    output_order = next_order
                    next_order += 1
                    item["output_order"] = output_order
                    changed = True
                elif item.get("output_order") != output_order:
                    item["output_order"] = output_order
                    changed = True
                used_orders.add(output_order)
                canonical.append({
                    "asset_id": asset_id,
                    "output_id": output_id,
                    "output_order": output_order,
                })
            result_node_id = str(
                record.get("result_node_id")
                or record.get("resultNodeId")
                or record.get("nodeId")
                or record.get("node_id")
                or ""
            ).strip()
            if result_node_id:
                producer_outputs.setdefault(result_node_id, []).extend(canonical)

        for node_index, node in enumerate(canvas.get("nodes") or []):
            if not isinstance(node, dict):
                continue
            images = node.get("images")
            if not isinstance(images, list):
                continue
            node_id = str(node.get("id") or f"legacy-node-{node_index}")
            canonical = producer_outputs.get(node_id, [])
            if not canonical:
                continue
            assigned_node_ids: set[str] = set()
            assigned_node_orders: set[int] = set()
            for image_index, item in enumerate(images):
                if not isinstance(item, dict):
                    continue
                asset_id = str(item.get("asset_id") or "")
                existing_output_id = cls._valid_output_id(item.get("output_id"))
                aligned = next(
                    (
                        candidate for candidate in canonical
                        if candidate["asset_id"] == asset_id
                        and candidate["output_id"] not in assigned_node_ids
                        and (
                            not existing_output_id
                            or candidate["output_id"] == existing_output_id
                        )
                    ),
                    None,
                )
                if aligned is None and not existing_output_id:
                    aligned = next(
                        (
                            candidate for candidate in canonical
                            if candidate["asset_id"] == asset_id
                            and candidate["output_id"] not in assigned_node_ids
                        ),
                        None,
                    )
                if aligned is None:
                    continue
                output_id = aligned["output_id"]
                existing_order = cls._valid_output_order(item.get("output_order"))
                aligned_order = cls._valid_output_order(aligned.get("output_order"))
                if existing_order is not None and existing_order not in assigned_node_orders:
                    output_order = existing_order
                elif aligned_order is not None and aligned_order not in assigned_node_orders:
                    output_order = aligned_order
                else:
                    output_order = max(0, image_index)
                    while output_order in assigned_node_orders:
                        output_order += 1
                if item.get("output_id") != output_id:
                    item["output_id"] = output_id
                    changed = True
                if item.get("output_order") != output_order:
                    item["output_order"] = output_order
                    changed = True
                assigned_node_ids.add(output_id)
                assigned_node_orders.add(output_order)
        return changed

    @classmethod
    def _append_output_occurrence_tombstone(
        cls,
        owner: Dict[str, Any],
        *,
        asset_id: str,
        output_id: str,
        deletion_id: str,
        log_id: str,
        output_index: int,
        record: Dict[str, Any],
    ) -> None:
        items = [item for item in owner.get("deletedOutputTombstones") or [] if isinstance(item, dict)]
        items = [item for item in items if str(item.get("deletion_id") or "") != deletion_id]
        tombstone = cls._deleted_asset_tombstone(asset_id, record)
        tombstone.update({
            "deleted_output_id": output_id,
            "deletion_id": deletion_id,
            "log_id": log_id,
            "output_index": output_index,
        })
        items.append(tombstone)
        owner["deletedOutputTombstones"] = items

    @staticmethod
    def _remove_output_occurrence_tombstone(owner: Dict[str, Any], deletion_id: str) -> None:
        remaining = [
            item for item in owner.get("deletedOutputTombstones") or []
            if not (isinstance(item, dict) and str(item.get("deletion_id") or "") == deletion_id)
        ]
        if remaining:
            owner["deletedOutputTombstones"] = remaining
        else:
            owner.pop("deletedOutputTombstones", None)

    @classmethod
    def _pop_output_occurrence(
        cls,
        owner: Dict[str, Any],
        key: str,
        *,
        asset_id: str,
        output_id: str,
        expected_index: int | None,
    ) -> Dict[str, Any]:
        values = owner.get(key)
        if not isinstance(values, list):
            raise _store.InvalidMutation("生成输出身份与项目记录不一致")
        matches = [
            index for index, item in enumerate(values)
            if isinstance(item, dict)
            and cls._valid_output_id(item.get("output_id")) == output_id
        ]
        if expected_index is None:
            if len(matches) != 1:
                raise _store.InvalidMutation("生成输出身份已变化，请刷新后重试")
            selected_index = matches[0]
        else:
            if matches != [expected_index]:
                raise _store.InvalidMutation("生成输出身份已变化，请刷新后重试")
            selected_index = expected_index
        selected = values[selected_index]
        if str(selected.get("asset_id") or "") != asset_id:
            raise _store.InvalidMutation("生成输出身份与媒体资源不一致")
        return values.pop(selected_index)

    @classmethod
    def _insert_output_occurrence(
        cls,
        owner: Dict[str, Any],
        key: str,
        item: Dict[str, Any],
        *,
        output_id: str,
        output_index: int,
    ) -> None:
        values = owner.get(key)
        if not isinstance(values, list):
            values = []
            owner[key] = values
        if any(
            isinstance(value, dict)
            and cls._valid_output_id(value.get("output_id")) == output_id
            for value in values
        ):
            raise _store.InvalidMutation("生成输出已恢复")
        restored = copy.deepcopy(item)
        restored_order = cls._valid_output_order(restored.get("output_order"))
        if restored_order is None:
            restored["output_order"] = max(0, int(output_index))
        values.append(restored)
        decorated = list(enumerate(values))
        decorated.sort(key=lambda pair: (
            cls._valid_output_order(pair[1].get("output_order"))
            if isinstance(pair[1], dict) and cls._valid_output_order(pair[1].get("output_order")) is not None
            else 1_000_000_000 + pair[0]
        ))
        values[:] = [value for _index, value in decorated]

    def _delete_generated_output_occurrence(
        self,
        project_id: str,
        asset_id: str,
        *,
        base_revision: int,
        log_id: str,
        output_id: str,
        output_index: int,
    ) -> Dict[str, Any]:
        project_id = _store._clean_project_id(project_id)
        log_id = str(log_id or "").strip()
        output_id = self._valid_output_id(output_id)
        if not log_id or not output_id:
            raise _store.InvalidMutation("删除当前原图需要稳定的生成输出身份")
        try:
            output_index = int(output_index)
        except (TypeError, ValueError) as exc:
            raise _store.InvalidMutation("生成输出位置无效") from exc
        if output_index < 0:
            raise _store.InvalidMutation("生成输出位置无效")

        with self._project_operation_lock(project_id):
            with self._lock:
                opened = self.open_project(project_id)
                current_revision = int(opened["canvas"].get("revision") or 0)
                if int(base_revision) != current_revision:
                    raise _store.RevisionConflict(opened["canvas"])
                self._cleanup_expired_deleted_media_locked(project_id)
                media_index = self._load_media_index(project_id)
                original = media_index["items"].get(asset_id)
                if not isinstance(original, dict):
                    raise _store.ProjectNotFound("项目媒体不存在")
                if not self._is_generated_original(original):
                    raise _store.ProjectStoreError("只能删除项目生成原图")
                blocking_task_ids = self._active_generation_references_asset(opened["canvas"], asset_id)
                if blocking_task_ids:
                    raise _store.ProjectStoreError(
                        "该原图正在被生成任务引用，请等待任务完成后再删除："
                        + "、".join(blocking_task_ids)
                    )

                history = copy.deepcopy(self._histories.get(project_id, []))
                record_matches = [
                    record for record in history
                    if isinstance(record, dict) and str(record.get("id") or "") == log_id
                ]
                if len(record_matches) != 1:
                    raise _store.InvalidMutation("生成记录身份已变化，请刷新后重试")
                target_record = record_matches[0]
                result_node_id = str(
                    target_record.get("result_node_id")
                    or target_record.get("resultNodeId")
                    or target_record.get("nodeId")
                    or target_record.get("node_id")
                    or ""
                ).strip()
                nodes = copy.deepcopy(opened["canvas"].get("nodes") or [])
                node_matches = [
                    node for node in nodes
                    if isinstance(node, dict) and str(node.get("id") or "") == result_node_id
                ] if result_node_id else []
                if result_node_id and len(node_matches) != 1:
                    raise _store.InvalidMutation("生成结果节点身份已变化，请刷新后重试")
                target_node = node_matches[0] if node_matches else None

                history_owner_state = {
                    key: copy.deepcopy(target_record[key])
                    for key in ("status", "originalDeleted")
                    if key in target_record
                }
                node_state_keys = (
                    "needsRegeneration", "generationState", "resultDeleted", "pending",
                    "running", "queued", "pendingTasks", "runFinishedAt", "runElapsedMs",
                )
                node_owner_state = {
                    key: copy.deepcopy(target_node[key])
                    for key in node_state_keys
                    if target_node is not None and key in target_node
                }
                selected_output = self._pop_output_occurrence(
                    target_record,
                    "outputs",
                    asset_id=asset_id,
                    output_id=output_id,
                    expected_index=output_index,
                )
                selected_image = None
                if target_node is not None:
                    try:
                        selected_image = self._pop_output_occurrence(
                            target_node,
                            "images",
                            asset_id=asset_id,
                            output_id=output_id,
                            expected_index=None,
                        )
                    except _store.InvalidMutation:
                        # target_node.images 可能没有该 output_id，忽略
                        pass

                deletion_id = f"deletion-{uuid.uuid4().hex}"
                self._append_output_occurrence_tombstone(
                    target_record,
                    asset_id=asset_id,
                    output_id=output_id,
                    deletion_id=deletion_id,
                    log_id=log_id,
                    output_index=output_index,
                    record=original,
                )
                remaining_outputs = target_record.get("outputs") or []
                if remaining_outputs:
                    target_record.pop("originalDeleted", None)
                    if target_record.get("status") == "original_deleted":
                        target_record["status"] = "succeeded"
                else:
                    target_record["status"] = "original_deleted"
                    target_record["originalDeleted"] = True
                if target_node is not None:
                    self._append_output_occurrence_tombstone(
                        target_node,
                        asset_id=asset_id,
                        output_id=output_id,
                        deletion_id=deletion_id,
                        log_id=log_id,
                        output_index=output_index,
                        record=original,
                    )
                    if not target_node.get("images"):
                        self._reset_deleted_original_owner_state(target_node)

                canvas = copy.deepcopy(opened["canvas"])
                canvas.pop("logs", None)
                canvas["nodes"] = nodes
                canvas["revision"] = current_revision + 1
                canvas["updated_at"] = max(_store._now_ms(), int(canvas.get("updated_at") or 0) + 1)
                referenced_after = asset_id in self._collect_asset_ids({
                    "nodes": nodes,
                    "history": history,
                })

                project_dir = self._project_directory(project_id)
                self._safe_project_path(project_dir, "trash")
                self._safe_project_path(project_dir, "trash/undo")
                self._safe_project_path(project_dir, "trash/media")
                self._safe_project_path(project_dir, "trash/previews")
                media_path = self._safe_project_path(
                    project_dir,
                    (Path("media") / str(original.get("filename") or "")).as_posix(),
                )
                if not media_path.is_file():
                    raise _store.ProjectNotFound("项目媒体不存在")
                preview_root = self._safe_project_path(project_dir, "previews")
                preview_files = [
                    self._safe_project_path(project_dir, path.relative_to(project_dir).as_posix())
                    for path in sorted(preview_root.glob(f"{asset_id}-*"))
                    if path.is_file()
                ]
                move_plan: List[tuple[Path, Path]] = []
                old_media_index = copy.deepcopy(media_index)
                if not referenced_after:
                    move_plan = [
                        (
                            media_path,
                            self._safe_project_path(
                                project_dir,
                                (Path("trash") / "media" / media_path.name).as_posix(),
                            ),
                        )
                    ] + [
                        (
                            path,
                            self._safe_project_path(
                                project_dir,
                                (Path("trash") / "previews" / path.name).as_posix(),
                            ),
                        )
                        for path in preview_files
                    ]
                    media_index["items"].pop(asset_id, None)

                old_trash_index = self._load_media_trash_index(project_id)
                trash_index = copy.deepcopy(old_trash_index)
                deleted_at = _store._now_ms()
                snapshot_filename = f"{deletion_id}.json"
                trash_item = copy.deepcopy(original)
                trash_item.update({
                    "deletion_id": deletion_id,
                    "output_id": output_id,
                    "log_id": log_id,
                    "result_node_id": result_node_id,
                    "output_index": output_index,
                    "asset_id": asset_id,
                    "deleted_at": deleted_at,
                    "expires_at": deleted_at + _store.MEDIA_TRASH_RETENTION_MS,
                    "media_retained": referenced_after,
                    "media_filename": "" if referenced_after else media_path.name,
                    "preview_filenames": [] if referenced_after else [path.name for path in preview_files],
                    "reason": "delete-generated-output",
                    "undo": {
                        "operation": "restore-generated-output",
                        "asset_id": asset_id,
                        "deletion_id": deletion_id,
                        "output_id": output_id,
                        "log_id": log_id,
                        "deleted_revision": current_revision + 1,
                        "snapshot_filename": snapshot_filename,
                    },
                })
                trash_index["items"] = [
                    item for item in trash_index.get("items", [])
                    if not (
                        isinstance(item, dict)
                        and str(item.get("deletion_id") or "") == deletion_id
                    )
                ]
                trash_index["items"].append(trash_item)
                node_owner_state_after_delete = {
                    key: copy.deepcopy(target_node[key])
                    for key in node_state_keys
                    if target_node is not None and key in target_node
                }
                undo_snapshot = {
                    "deletion_id": deletion_id,
                    "output_id": output_id,
                    "asset_id": asset_id,
                    "log_id": log_id,
                    "result_node_id": result_node_id,
                    "output_index": output_index,
                    "history_output": copy.deepcopy(selected_output),
                    "node_image": copy.deepcopy(selected_image),
                    "history_owner_state": history_owner_state,
                    "node_owner_state": node_owner_state,
                    "node_owner_state_after_delete": node_owner_state_after_delete,
                }

                before_canvas = copy.deepcopy(opened["canvas"])
                before_canvas.pop("logs", None)
                metadata = _store._read_json(self._safe_project_path(project_dir, "project.json"))
                before_metadata = self._metadata_for_canvas(metadata, before_canvas)
                after_metadata = self._metadata_for_canvas(metadata, canvas)
                files = [
                    {
                        "live": source.relative_to(project_dir).as_posix(),
                        "trash": destination.relative_to(project_dir).as_posix(),
                        "required": True,
                    }
                    for source, destination in move_plan
                ]
                self._commit_original_media_transaction(
                    project_id,
                    "delete-output",
                    asset_id,
                    files,
                    snapshot_filename,
                    {
                        "file_location": "live",
                        "canvas": before_canvas,
                        "history": self._histories.get(project_id, []),
                        "media_index": old_media_index,
                        "trash_index": old_trash_index,
                        "project": before_metadata,
                        "undo_snapshot": None,
                    },
                    {
                        "file_location": "live" if referenced_after else "trash",
                        "canvas": canvas,
                        "history": history,
                        "media_index": media_index,
                        "trash_index": trash_index,
                        "project": after_metadata,
                        "undo_snapshot": undo_snapshot,
                    },
                )
                self._canvases[project_id] = canvas
                self._histories[project_id] = history
                self._saved_revisions[project_id] = int(canvas["revision"])
                result = copy.deepcopy(canvas)
                result["logs"] = copy.deepcopy(history)
                return {"canvas": result, "deleted": copy.deepcopy(trash_item)}

    def delete_generated_original(
        self,
        project_id: str,
        asset_id: str,
        *,
        base_revision: int,
        output_index: int | None = None,
        log_id: str | None = None,
        output_id: str | None = None,
    ) -> Dict[str, Any]:
        """Remove exactly one generated output occurrence.

        The operation is fail-closed: callers must provide the complete stable
        occurrence identity.  Project-wide asset deletion is a separate,
        explicitly named destructive operation.
        """
        project_id = _store._clean_project_id(project_id)
        asset_id = str(asset_id or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,200}", asset_id):
            raise _store.ProjectStoreError("媒体资源标识无效")
        identity_fields = (log_id, output_id, output_index)
        if not all(value is not None for value in identity_fields):
            raise _store.InvalidMutation("删除当前原图需要完整的生成输出身份")
        return self._delete_generated_output_occurrence(
            project_id,
            asset_id,
            base_revision=base_revision,
            log_id=str(log_id or ""),
            output_id=str(output_id or ""),
            output_index=int(output_index),
        )

    def delete_generated_original_project_wide(
        self,
        project_id: str,
        asset_id: str,
        *,
        base_revision: int,
    ) -> Dict[str, Any]:
        """Delete one generated original and every direct project reference."""
        project_id = _store._clean_project_id(project_id)
        asset_id = str(asset_id or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,200}", asset_id):
            raise _store.ProjectStoreError("媒体资源标识无效")
        with self._project_operation_lock(project_id):
            with self._lock:
                opened = self.open_project(project_id)
                current_revision = int(opened["canvas"].get("revision") or 0)
                if int(base_revision) != current_revision:
                    raise _store.RevisionConflict(opened["canvas"])
                self._cleanup_expired_deleted_media_locked(project_id)
                media_index = self._load_media_index(project_id)
                original = media_index["items"].get(asset_id)
                if not isinstance(original, dict):
                    raise _store.ProjectNotFound("项目媒体不存在")
                if not self._is_generated_original(original):
                    raise _store.ProjectStoreError("只能删除项目生成原图")
                blocking_task_ids = self._active_generation_references_asset(opened["canvas"], asset_id)
                if blocking_task_ids:
                    raise _store.ProjectStoreError(
                        "该原图正在被生成任务引用，请等待任务完成后再删除："
                        + "、".join(blocking_task_ids)
                    )

                project_dir = self._project_directory(project_id)
                preview_root = self._safe_project_path(project_dir, "previews")
                self._safe_project_path(project_dir, "media")
                self._safe_project_path(project_dir, "trash")
                self._safe_project_path(project_dir, "trash/media")
                self._safe_project_path(project_dir, "trash/previews")
                # Validate the eventual undo target before the transaction journal
                # or any media file is changed.
                self._safe_project_path(project_dir, "trash/undo")

                media_path = self._safe_project_path(
                    project_dir,
                    (Path("media") / str(original.get("filename") or "")).as_posix(),
                )
                if not media_path.is_file():
                    raise _store.ProjectNotFound("项目媒体不存在")
                preview_files = []
                for path in sorted(preview_root.glob(f"{asset_id}-*")):
                    if not path.is_file():
                        continue
                    preview_files.append(
                        self._safe_project_path(
                            project_dir,
                            path.relative_to(project_dir).as_posix(),
                        )
                    )
                move_plan = [
                    (
                        media_path,
                        self._safe_project_path(
                            project_dir,
                            (Path("trash") / "media" / media_path.name).as_posix(),
                        ),
                    )
                ] + [
                    (
                        path,
                        self._safe_project_path(
                            project_dir,
                            (Path("trash") / "previews" / path.name).as_posix(),
                        ),
                    )
                    for path in preview_files
                ]
                old_media_index = copy.deepcopy(media_index)
                old_trash_index = self._load_media_trash_index(project_id)
                trash_index = copy.deepcopy(old_trash_index)
                history = copy.deepcopy(self._histories.get(project_id, []))
                original_nodes = copy.deepcopy(opened["canvas"].get("nodes") or [])
                undo_snapshot = {
                    "nodes": [
                        node for node in original_nodes
                        if asset_id in self._collect_asset_ids(node)
                    ],
                    "history": [
                        record for record in history
                        if asset_id in self._collect_asset_ids(record)
                    ],
                }
                deleted_at = _store._now_ms()
                snapshot_filename = f"{asset_id}-{deleted_at}.json"
                trash_item = copy.deepcopy(original)
                trash_item.update(
                    {
                        "asset_id": asset_id,
                        "deleted_at": deleted_at,
                        "expires_at": deleted_at + _store.MEDIA_TRASH_RETENTION_MS,
                        "media_filename": media_path.name,
                        "preview_filenames": [path.name for path in preview_files],
                        "reason": "delete-generated-original",
                        "undo": {
                            "operation": "restore-generated-original",
                            "asset_id": asset_id,
                            "deleted_revision": current_revision + 1,
                            "snapshot_filename": snapshot_filename,
                        },
                    }
                )
                trash_index["items"] = [
                    item for item in trash_index.get("items", [])
                    if not (isinstance(item, dict) and str(item.get("asset_id") or "") == asset_id)
                ]
                trash_index["items"].append(trash_item)
                media_index["items"].pop(asset_id, None)

                history_producer_ids = set()
                cleaned_history: List[Dict[str, Any]] = []
                for record in history:
                    if asset_id not in self._collect_asset_ids(record):
                        cleaned_history.append(record)
                        continue
                    owns_original = asset_id in self._collect_asset_ids(record.get("outputs") or [])
                    if owns_original:
                        result_node_id = str(record.get("result_node_id") or "").strip()
                        if result_node_id:
                            history_producer_ids.add(result_node_id)
                    cleaned = self._remove_asset_references(record, asset_id) or {}
                    if owns_original:
                        self._append_deleted_asset_tombstone(
                            cleaned, "deletedOutputTombstones", asset_id, original,
                        )
                        remaining_outputs = self._collect_asset_ids(cleaned.get("outputs") or [])
                        if remaining_outputs:
                            cleaned.pop("originalDeleted", None)
                            if cleaned.get("status") == "original_deleted":
                                prior_status = str(record.get("status") or "").strip()
                                cleaned["status"] = (
                                    prior_status if prior_status and prior_status != "original_deleted"
                                    else "succeeded"
                                )
                        else:
                            cleaned["status"] = "original_deleted"
                            cleaned["originalDeleted"] = True
                    else:
                        self._append_deleted_asset_tombstone(
                            cleaned, "deletedReferenceTombstones", asset_id, original,
                        )
                    cleaned_history.append(cleaned)

                nodes = []
                for node in copy.deepcopy(opened["canvas"].get("nodes") or []):
                    # #63: 统一归属判定（run 字段 + output_id 身份，避免拖入图节点误判）
                    owns_original = self._node_owns_generated_output(node, asset_id, history_producer_ids)
                    directly_references_original = asset_id in self._collect_asset_ids(node)
                    cleaned_node = self._remove_asset_references(node, asset_id) or {}
                    if owns_original:
                        self._append_deleted_asset_tombstone(
                            cleaned_node, "deletedOutputTombstones", asset_id, original,
                        )
                        if not self._collect_asset_ids(cleaned_node.get("images") or []):
                            self._reset_deleted_original_owner_state(cleaned_node)
                    elif directly_references_original:
                        self._append_deleted_asset_tombstone(
                            cleaned_node, "deletedInputTombstones", asset_id, original,
                        )
                    nodes.append(cleaned_node)

                canvas = copy.deepcopy(opened["canvas"])
                canvas.pop("logs", None)
                canvas["nodes"] = nodes
                canvas["revision"] = current_revision + 1
                canvas["updated_at"] = max(_store._now_ms(), int(canvas.get("updated_at") or 0) + 1)
                before_canvas = copy.deepcopy(opened["canvas"])
                before_canvas.pop("logs", None)
                metadata = _store._read_json(self._safe_project_path(project_dir, "project.json"))
                before_metadata = self._metadata_for_canvas(metadata, before_canvas)
                after_metadata = self._metadata_for_canvas(metadata, canvas)
                files = [
                    {
                        "live": source.relative_to(project_dir).as_posix(),
                        "trash": destination.relative_to(project_dir).as_posix(),
                        "required": True,
                    }
                    for source, destination in move_plan
                ]
                self._commit_original_media_transaction(
                    project_id,
                    "delete",
                    asset_id,
                    files,
                    snapshot_filename,
                    {
                        "file_location": "live",
                        "canvas": before_canvas,
                        "history": history,
                        "media_index": old_media_index,
                        "trash_index": old_trash_index,
                        "project": before_metadata,
                        "undo_snapshot": None,
                    },
                    {
                        "file_location": "trash",
                        "canvas": canvas,
                        "history": cleaned_history,
                        "media_index": media_index,
                        "trash_index": trash_index,
                        "project": after_metadata,
                        "undo_snapshot": undo_snapshot,
                    },
                )
                self._canvases[project_id] = canvas
                self._histories[project_id] = cleaned_history
                self._saved_revisions[project_id] = int(canvas["revision"])
                result = copy.deepcopy(canvas)
                result["logs"] = copy.deepcopy(cleaned_history)
                return {"canvas": result, "deleted": copy.deepcopy(trash_item)}

    def delete_project_media_asset(
        self,
        project_id: str,
        asset_id: str,
        *,
        base_revision: int,
    ) -> Dict[str, Any]:
        """Permanently delete one library media asset and every direct project reference.

        User-initiated deletion from the asset library (see ADR 0015): the media
        file is removed directly (it does not enter the deleted-media trash zone),
        all direct references inside the project (node images/asset fields, history
        inputs/outputs) are removed, and affected nodes/history records receive the
        matching tombstones so the UI can show the "upstream asset deleted" state.
        """
        project_id = _store._clean_project_id(project_id)
        asset_id = str(asset_id or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,200}", asset_id):
            raise _store.ProjectStoreError("媒体资源标识无效")
        with self._project_operation_lock(project_id):
            with self._lock:
                opened = self.open_project(project_id)
                source_canvas = opened["canvas"]
                current_revision = int(source_canvas.get("revision") or 0)
                if int(base_revision) != current_revision:
                    raise _store.RevisionConflict(source_canvas)
                blocking_task_ids = self._active_generation_references_asset(source_canvas, asset_id)
                if blocking_task_ids:
                    raise _store.ProjectStoreError(
                        "该媒体正在被生成任务引用，请等待任务完成后再删除："
                        + "、".join(blocking_task_ids)
                    )
                self._cleanup_expired_deleted_media_locked(project_id)
                media_index = self._load_media_index(project_id)
                original = media_index["items"].get(asset_id)
                if not isinstance(original, dict):
                    raise _store.ProjectNotFound("项目媒体不存在")

                project_dir = self._project_directory(project_id)
                media_path = self._safe_project_path(
                    project_dir,
                    (Path("media") / str(original.get("filename") or "")).as_posix(),
                )
                if media_path.is_file():
                    media_path.unlink()
                preview_root = self._safe_project_path(project_dir, "previews")
                for path in sorted(preview_root.glob(f"{asset_id}-*")):
                    if path.is_file():
                        self._safe_project_path(
                            project_dir, path.relative_to(project_dir).as_posix()
                        ).unlink(missing_ok=True)

                history = copy.deepcopy(self._histories.get(project_id, []))
                history_producer_ids = set()
                for record in history:
                    if asset_id in self._collect_asset_ids(record.get("outputs") or []):
                        result_node_id = str(record.get("result_node_id") or "").strip()
                        if result_node_id:
                            history_producer_ids.add(result_node_id)

                cleaned_history: List[Dict[str, Any]] = []
                for record in history:
                    if asset_id not in self._collect_asset_ids(record):
                        cleaned_history.append(record)
                        continue
                    owns_output = asset_id in self._collect_asset_ids(record.get("outputs") or [])
                    cleaned = self._remove_asset_references(record, asset_id) or {}
                    if owns_output:
                        self._append_deleted_asset_tombstone(
                            cleaned, "deletedOutputTombstones", asset_id, original,
                        )
                        if not self._collect_asset_ids(cleaned.get("outputs") or []):
                            cleaned["status"] = "original_deleted"
                            cleaned["originalDeleted"] = True
                    else:
                        self._append_deleted_asset_tombstone(
                            cleaned, "deletedReferenceTombstones", asset_id, original,
                        )
                    cleaned_history.append(cleaned)

                nodes = []
                for node in copy.deepcopy(source_canvas.get("nodes") or []):
                    if asset_id not in self._collect_asset_ids(node):
                        nodes.append(node)
                        continue
                    # #63: 统一归属判定——普通引用节点（前端 run 状态机会给所有 smart-image 节点盖
                    # runStartedAt 戳，单凭 run 字段会把「拖入图片的节点」误判为生成节点）。
                    # 生成输出归属需同时满足：节点展示该资产，且 ① 历史记录 result_node_id 指向该
                    # 节点，或 ② 有 run 快照且 images 条目带生成输出身份 output_id（归档时固化）。
                    owns_output = self._node_owns_generated_output(node, asset_id, history_producer_ids)
                    cleaned_node = self._remove_asset_references(node, asset_id) or {}
                    if owns_output:
                        self._append_deleted_asset_tombstone(
                            cleaned_node, "deletedOutputTombstones", asset_id, original,
                        )
                        if not self._collect_asset_ids(cleaned_node.get("images") or []):
                            self._reset_deleted_original_owner_state(cleaned_node)
                    else:
                        self._append_deleted_asset_tombstone(
                            cleaned_node, "deletedInputTombstones", asset_id, original,
                        )
                    nodes.append(cleaned_node)

                canvas = copy.deepcopy(source_canvas)
                canvas.pop("logs", None)
                canvas["nodes"] = nodes
                canvas["revision"] = current_revision + 1
                canvas["updated_at"] = max(_store._now_ms(), int(source_canvas.get("updated_at") or 0) + 1)

                media_index["items"].pop(asset_id, None)
                _store._write_json_atomic(
                    self._safe_project_path(project_dir, "media/index.json"),
                    media_index,
                    keep_backup=False,
                )
                _store._write_json_atomic(
                    self._safe_project_path(project_dir, "canvas.json"),
                    self._project_canvas_for_persistence(canvas),
                    keep_backup=True,
                )
                self._histories[project_id] = cleaned_history
                self._persist_history(project_id)
                metadata = _store._read_json(self._safe_project_path(project_dir, "project.json"))
                after_metadata = self._metadata_for_canvas(metadata, canvas)
                _store._write_json_atomic(self._safe_project_path(project_dir, "project.json"), after_metadata, keep_backup=False)

                self._canvases[project_id] = canvas
                self._saved_revisions[project_id] = int(canvas["revision"])
                result = copy.deepcopy(canvas)
                result["logs"] = copy.deepcopy(cleaned_history)
                return {"canvas": result, "deleted": asset_id}

    def _restore_deleted_output_occurrence(
        self,
        project_id: str,
        asset_id: str,
        *,
        base_revision: int,
        deletion_id: str,
    ) -> Dict[str, Any]:
        project_id = _store._clean_project_id(project_id)
        deletion_id = str(deletion_id or "").strip().lower()
        if not self._DELETION_ID_PATTERN.fullmatch(deletion_id):
            raise _store.InvalidMutation("生成输出撤销身份无效")
        with self._project_operation_lock(project_id):
            with self._lock:
                opened = self.open_project(project_id)
                current_revision = int(opened["canvas"].get("revision") or 0)
                if int(base_revision) != current_revision:
                    raise _store.RevisionConflict(opened["canvas"])
                self._cleanup_expired_deleted_media_locked(project_id)
                trash_index = self._load_media_trash_index(project_id)
                matches = [
                    item for item in trash_index.get("items", [])
                    if isinstance(item, dict)
                    and str(item.get("deletion_id") or "") == deletion_id
                ]
                if len(matches) != 1:
                    raise _store.ProjectNotFound("项目回收区中不存在该生成输出")
                item = matches[0]
                if str(item.get("asset_id") or "") != asset_id:
                    raise _store.InvalidMutation("生成输出撤销身份与媒体资源不一致")
                undo = item.get("undo")
                if not isinstance(undo, dict) or str(undo.get("deletion_id") or "") != deletion_id:
                    raise _store.ProjectStoreError("该项目回收记录不可撤销")
                snapshot_filename = str(undo.get("snapshot_filename") or "")
                project_dir = self._project_directory(project_id)
                undo_path = self._safe_project_path(
                    project_dir,
                    (Path("trash") / "undo" / snapshot_filename).as_posix(),
                )
                try:
                    snapshot = _store._read_json(undo_path)
                except (OSError, ValueError, json.JSONDecodeError) as exc:
                    raise _store.ProjectStoreError("该项目回收记录不可撤销") from exc
                if (
                    str(snapshot.get("deletion_id") or "") != deletion_id
                    or str(snapshot.get("asset_id") or "") != asset_id
                    or self._valid_output_id(snapshot.get("output_id"))
                    != self._valid_output_id(item.get("output_id"))
                ):
                    raise _store.ProjectStoreError("该项目回收记录身份不一致")

                log_id = str(snapshot.get("log_id") or "")
                result_node_id = str(snapshot.get("result_node_id") or "")
                output_id = self._valid_output_id(snapshot.get("output_id"))
                try:
                    output_index = int(snapshot.get("output_index"))
                except (TypeError, ValueError) as exc:
                    raise _store.ProjectStoreError("该项目回收记录位置无效") from exc
                history = copy.deepcopy(self._histories.get(project_id, []))
                history_matches = [
                    record for record in history
                    if isinstance(record, dict) and str(record.get("id") or "") == log_id
                ]
                if len(history_matches) != 1:
                    raise _store.InvalidMutation("生成记录身份已变化，无法安全恢复")
                target_record = history_matches[0]
                tombstones = [
                    tombstone for tombstone in target_record.get("deletedOutputTombstones") or []
                    if isinstance(tombstone, dict)
                    and str(tombstone.get("deletion_id") or "") == deletion_id
                ]
                if len(tombstones) != 1:
                    raise _store.InvalidMutation("生成输出撤销状态已变化")
                history_output = snapshot.get("history_output")
                if not isinstance(history_output, dict):
                    raise _store.ProjectStoreError("该项目回收记录缺少生成输出")
                self._insert_output_occurrence(
                    target_record,
                    "outputs",
                    history_output,
                    output_id=output_id,
                    output_index=output_index,
                )
                self._remove_output_occurrence_tombstone(target_record, deletion_id)
                history_owner_state = snapshot.get("history_owner_state")
                if isinstance(history_owner_state, dict):
                    for key in ("status", "originalDeleted"):
                        if key in history_owner_state:
                            target_record[key] = copy.deepcopy(history_owner_state[key])
                        else:
                            target_record.pop(key, None)

                canvas = copy.deepcopy(opened["canvas"])
                canvas.pop("logs", None)
                nodes = canvas.get("nodes") or []
                if result_node_id:
                    node_matches = [
                        node for node in nodes
                        if isinstance(node, dict) and str(node.get("id") or "") == result_node_id
                    ]
                    if len(node_matches) != 1:
                        raise _store.InvalidMutation("生成结果节点身份已变化，无法安全恢复")
                    target_node = node_matches[0]
                    node_tombstones = [
                        tombstone for tombstone in target_node.get("deletedOutputTombstones") or []
                        if isinstance(tombstone, dict)
                        and str(tombstone.get("deletion_id") or "") == deletion_id
                    ]
                    if len(node_tombstones) != 1:
                        raise _store.InvalidMutation("生成结果节点撤销状态已变化")
                    node_image = snapshot.get("node_image")
                    if isinstance(node_image, dict):
                        self._insert_output_occurrence(
                            target_node,
                            "images",
                            node_image,
                            output_id=output_id,
                            output_index=output_index,
                        )
                    self._remove_output_occurrence_tombstone(target_node, deletion_id)
                    node_owner_state = snapshot.get("node_owner_state")
                    node_owner_state_after_delete = snapshot.get("node_owner_state_after_delete")
                    deletion_marker_keys = (
                        "needsRegeneration", "generationState", "resultDeleted",
                    )
                    task_state_keys = (
                        "pending", "running", "queued", "pendingTasks",
                        "runFinishedAt", "runElapsedMs",
                    )
                    node_state_keys = deletion_marker_keys + task_state_keys
                    if isinstance(node_owner_state, dict):
                        missing = object()

                        def state_value(state: Dict[str, Any], key: str) -> Any:
                            return state[key] if key in state else missing

                        keys_to_restore = node_state_keys
                        if isinstance(node_owner_state_after_delete, dict):
                            task_state_changed = any(
                                state_value(target_node, key)
                                != state_value(node_owner_state_after_delete, key)
                                for key in task_state_keys
                            )
                            unchanged_markers = tuple(
                                key for key in deletion_marker_keys
                                if state_value(target_node, key)
                                == state_value(node_owner_state_after_delete, key)
                            )
                            keys_to_restore = unchanged_markers + (
                                () if task_state_changed else task_state_keys
                            )
                        elif current_revision != int(undo.get("deleted_revision") or -1):
                            # Older snapshots cannot support a three-way merge. Preserve
                            # all owner state after any intervening project mutation.
                            keys_to_restore = ()

                        for key in keys_to_restore:
                            if key in node_owner_state:
                                target_node[key] = copy.deepcopy(node_owner_state[key])
                            else:
                                target_node.pop(key, None)

                media_retained = bool(item.get("media_retained"))
                media_index = self._load_media_index(project_id)
                old_media_index = copy.deepcopy(media_index)
                old_trash_index = copy.deepcopy(trash_index)
                media_was_live = asset_id in media_index.get("items", {})
                move_plan: List[tuple[Path, Path]] = []
                restored_record = {
                    key: copy.deepcopy(value)
                    for key, value in item.items()
                    if key not in {
                        "deletion_id", "output_id", "log_id", "result_node_id", "output_index",
                        "deleted_at", "expires_at", "media_retained", "media_filename",
                        "preview_filenames", "reason", "undo",
                    }
                }
                restored_record["asset_id"] = asset_id
                if media_retained and media_was_live:
                    pass
                else:
                    if media_was_live:
                        raise _store.ProjectStoreError("项目媒体已恢复")
                    physical_owner = item if not media_retained else next(
                        (
                            candidate for candidate in trash_index.get("items", [])
                            if isinstance(candidate, dict)
                            and str(candidate.get("asset_id") or "") == asset_id
                            and not bool(candidate.get("media_retained"))
                            and str(candidate.get("media_filename") or "").strip()
                        ),
                        None,
                    )
                    if not isinstance(physical_owner, dict):
                        raise _store.ProjectStoreError("共享项目媒体已丢失，无法安全恢复")
                    media_filename = str(physical_owner.get("media_filename") or "")
                    trash_media = self._safe_project_path(
                        project_dir, (Path("trash") / "media" / media_filename).as_posix(),
                    )
                    live_media = self._safe_project_path(
                        project_dir, (Path("media") / str(restored_record.get("filename") or "")).as_posix(),
                    )
                    if not trash_media.is_file():
                        raise _store.ProjectNotFound("项目回收区媒体不存在")
                    move_plan.append((trash_media, live_media))
                    for name in physical_owner.get("preview_filenames") or []:
                        if not isinstance(name, str):
                            continue
                        move_plan.append((
                            self._safe_project_path(project_dir, (Path("trash") / "previews" / name).as_posix()),
                            self._safe_project_path(project_dir, (Path("previews") / name).as_posix()),
                        ))
                    physical_record = {
                        key: copy.deepcopy(value)
                        for key, value in physical_owner.items()
                        if key not in {
                            "deletion_id", "output_id", "log_id", "result_node_id", "output_index",
                            "deleted_at", "expires_at", "media_retained", "media_filename",
                            "preview_filenames", "reason", "undo",
                        }
                    }
                    physical_record["asset_id"] = asset_id
                    media_index["items"][asset_id] = physical_record
                    restored_record = copy.deepcopy(physical_record)
                    if physical_owner is not item:
                        physical_owner["media_retained"] = True
                        physical_owner["media_filename"] = ""
                        physical_owner["preview_filenames"] = []

                trash_index["items"] = [
                    candidate for candidate in trash_index.get("items", [])
                    if not (
                        isinstance(candidate, dict)
                        and str(candidate.get("deletion_id") or "") == deletion_id
                    )
                ]
                canvas["revision"] = current_revision + 1
                canvas["updated_at"] = max(_store._now_ms(), int(canvas.get("updated_at") or 0) + 1)
                before_canvas = copy.deepcopy(opened["canvas"])
                before_canvas.pop("logs", None)
                before_history = copy.deepcopy(self._histories.get(project_id, []))
                metadata = _store._read_json(self._safe_project_path(project_dir, "project.json"))
                before_metadata = self._metadata_for_canvas(metadata, before_canvas)
                after_metadata = self._metadata_for_canvas(metadata, canvas)
                files = [
                    {
                        "live": live.relative_to(project_dir).as_posix(),
                        "trash": trash.relative_to(project_dir).as_posix(),
                        "required": index == 0,
                    }
                    for index, (trash, live) in enumerate(move_plan)
                ]
                self._commit_original_media_transaction(
                    project_id,
                    "restore-output",
                    asset_id,
                    files,
                    snapshot_filename,
                    {
                        "file_location": "live" if media_was_live else "trash",
                        "canvas": before_canvas,
                        "history": before_history,
                        "media_index": old_media_index,
                        "trash_index": old_trash_index,
                        "project": before_metadata,
                        "undo_snapshot": snapshot,
                    },
                    {
                        "file_location": "live",
                        "canvas": canvas,
                        "history": history,
                        "media_index": media_index,
                        "trash_index": trash_index,
                        "project": after_metadata,
                        "undo_snapshot": None,
                    },
                )
                self._canvases[project_id] = canvas
                self._histories[project_id] = history
                self._saved_revisions[project_id] = int(canvas["revision"])
                result = copy.deepcopy(canvas)
                result["logs"] = copy.deepcopy(history)
                return {"canvas": result, "restored": restored_record, "deletion_id": deletion_id}

    def restore_deleted_original(
        self,
        project_id: str,
        asset_id: str,
        *,
        base_revision: int,
        deletion_id: str | None = None,
    ) -> Dict[str, Any]:
        """Restore exactly one generated output occurrence."""
        project_id = _store._clean_project_id(project_id)
        asset_id = str(asset_id or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,200}", asset_id):
            raise _store.ProjectStoreError("媒体资源标识无效")
        if deletion_id is None:
            raise _store.InvalidMutation("恢复当前原图需要生成输出撤销身份")
        return self._restore_deleted_output_occurrence(
            project_id,
            asset_id,
            base_revision=base_revision,
            deletion_id=deletion_id,
        )

    def restore_deleted_original_project_wide(
        self,
        project_id: str,
        asset_id: str,
        *,
        base_revision: int,
    ) -> Dict[str, Any]:
        """Restore one explicitly project-wide generated-original deletion."""
        project_id = _store._clean_project_id(project_id)
        asset_id = str(asset_id or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,200}", asset_id):
            raise _store.ProjectStoreError("媒体资源标识无效")
        with self._project_operation_lock(project_id):
            with self._lock:
                opened = self.open_project(project_id)
                current_revision = int(opened["canvas"].get("revision") or 0)
                if int(base_revision) != current_revision:
                    raise _store.RevisionConflict(opened["canvas"])
                self._cleanup_expired_deleted_media_locked(project_id)
                trash_index = self._load_media_trash_index(project_id)
                item = next(
                    (
                        candidate
                        for candidate in trash_index.get("items", [])
                        if isinstance(candidate, dict) and str(candidate.get("asset_id") or "") == asset_id
                    ),
                    None,
                )
                if not isinstance(item, dict):
                    raise _store.ProjectNotFound("项目回收区中不存在该媒体")
                undo = item.get("undo")
                if not isinstance(undo, dict) or not isinstance(undo.get("snapshot_filename"), str):
                    raise _store.ProjectStoreError("该项目回收记录不可撤销")
                project_dir = self._project_directory(project_id)
                undo_path = self._safe_project_path(
                    project_dir,
                    (Path("trash") / "undo" / undo["snapshot_filename"]).as_posix(),
                )
                try:
                    undo_snapshot = _store._read_json(undo_path)
                except (OSError, ValueError, json.JSONDecodeError) as exc:
                    raise _store.ProjectStoreError("该项目回收记录不可撤销") from exc
                if not isinstance(undo_snapshot, dict) or not isinstance(undo_snapshot.get("nodes"), list) or not isinstance(undo_snapshot.get("history"), list):
                    raise _store.ProjectStoreError("该项目回收记录不可撤销")

                media_index = self._load_media_index(project_id)
                if asset_id in media_index["items"]:
                    raise _store.ProjectStoreError("项目媒体已恢复")
                self._safe_project_path(project_dir, "media")
                self._safe_project_path(project_dir, "previews")
                self._safe_project_path(project_dir, "trash")
                self._safe_project_path(project_dir, "trash/media")
                self._safe_project_path(project_dir, "trash/previews")
                restored_record = {
                    key: copy.deepcopy(value)
                    for key, value in item.items()
                    if key not in {"deleted_at", "expires_at", "media_filename", "preview_filenames", "reason", "undo"}
                }
                restored_record["asset_id"] = asset_id
                media_path = self._safe_project_path(
                    project_dir,
                    (Path("media") / str(restored_record.get("filename") or "")).as_posix(),
                )
                trash_media_path = self._safe_project_path(
                    project_dir,
                    (Path("trash") / "media" / str(item.get("media_filename") or "")).as_posix(),
                )
                if not trash_media_path.is_file():
                    raise _store.ProjectNotFound("项目回收区媒体不存在")
                preview_filenames = item.get("preview_filenames")
                if not isinstance(preview_filenames, list):
                    preview_filenames = []
                move_plan = [(trash_media_path, media_path)]
                for name in preview_filenames:
                    if not isinstance(name, str):
                        continue
                    move_plan.append(
                        (
                            self._safe_project_path(
                                project_dir,
                                (Path("trash") / "previews" / name).as_posix(),
                            ),
                            self._safe_project_path(
                                project_dir,
                                (Path("previews") / name).as_posix(),
                            ),
                        )
                    )
                old_media_index = copy.deepcopy(media_index)
                old_trash_index = copy.deepcopy(trash_index)
                media_index["items"][asset_id] = restored_record
                trash_index["items"] = [
                    candidate
                    for candidate in trash_index.get("items", [])
                    if not (isinstance(candidate, dict) and str(candidate.get("asset_id") or "") == asset_id)
                ]
                canvas = copy.deepcopy(opened["canvas"])
                canvas.pop("logs", None)
                current_nodes = self._restore_deleted_nodes(
                    copy.deepcopy(opened["canvas"].get("nodes") or []),
                    undo_snapshot["nodes"],
                    asset_id,
                )
                canvas["nodes"] = current_nodes
                canvas["revision"] = current_revision + 1
                canvas["updated_at"] = max(_store._now_ms(), int(canvas.get("updated_at") or 0) + 1)
                history = self._restore_deleted_history_records(
                    copy.deepcopy(self._histories.get(project_id, [])),
                    undo_snapshot["history"],
                    asset_id,
                )
                before_canvas = copy.deepcopy(opened["canvas"])
                before_canvas.pop("logs", None)
                before_history = copy.deepcopy(self._histories.get(project_id, []))
                metadata = _store._read_json(self._safe_project_path(project_dir, "project.json"))
                before_metadata = self._metadata_for_canvas(metadata, before_canvas)
                after_metadata = self._metadata_for_canvas(metadata, canvas)
                files = [
                    {
                        "live": live.relative_to(project_dir).as_posix(),
                        "trash": trash.relative_to(project_dir).as_posix(),
                        "required": index == 0,
                    }
                    for index, (trash, live) in enumerate(move_plan)
                ]
                self._commit_original_media_transaction(
                    project_id,
                    "restore",
                    asset_id,
                    files,
                    undo["snapshot_filename"],
                    {
                        "file_location": "trash",
                        "canvas": before_canvas,
                        "history": before_history,
                        "media_index": old_media_index,
                        "trash_index": old_trash_index,
                        "project": before_metadata,
                        "undo_snapshot": undo_snapshot,
                    },
                    {
                        "file_location": "live",
                        "canvas": canvas,
                        "history": history,
                        "media_index": media_index,
                        "trash_index": trash_index,
                        "project": after_metadata,
                        "undo_snapshot": None,
                    },
                )
                self._canvases[project_id] = canvas
                self._histories[project_id] = history
                self._saved_revisions[project_id] = int(canvas["revision"])
                result = copy.deepcopy(canvas)
                result["logs"] = copy.deepcopy(history)
                _diag_emit(
                    "recovery.restored",
                    project_id=project_id,
                    asset_id=asset_id,
                    revision=canvas["revision"],
                    state="restored",
                )
                return {"canvas": result, "restored": copy.deepcopy(restored_record)}

    def purge_deleted_media(self, project_id: str, asset_id: str | None = None) -> int:
        """Permanently remove project media trash entries after explicit purge."""
        project_id = _store._clean_project_id(project_id)
        target_asset = str(asset_id or "").strip() if asset_id is not None else None
        if target_asset is not None and not re.fullmatch(r"[A-Za-z0-9._-]{1,200}", target_asset):
            raise _store.ProjectStoreError("媒体资源标识无效")
        with self._project_operation_lock(project_id):
            with self._lock:
                self.open_project(project_id)
                self._cleanup_expired_deleted_media_locked(project_id)
                project_dir = self._project_directory(project_id)
                self._safe_project_path(project_dir, "trash")
                index = self._load_media_trash_index(project_id)
                remaining = []
                removed = 0
                for item in index.get("items", []):
                    if not isinstance(item, dict):
                        remaining.append(item)
                        continue
                    if target_asset is not None and str(item.get("asset_id") or "") != target_asset:
                        remaining.append(item)
                        continue
                    media_filename = str(item.get("media_filename") or "").strip()
                    relative_names = [f"media/{media_filename}"] if media_filename else []
                    preview_filenames = item.get("preview_filenames") if isinstance(item.get("preview_filenames"), list) else []
                    relative_names.extend(f"previews/{name}" for name in preview_filenames if isinstance(name, str))
                    undo = item.get("undo")
                    if isinstance(undo, dict) and isinstance(undo.get("snapshot_filename"), str):
                        relative_names.append(f"undo/{undo['snapshot_filename']}")
                    for relative_name in relative_names:
                        self._safe_project_path(
                            project_dir,
                            (Path("trash") / relative_name).as_posix(),
                        ).unlink(missing_ok=True)
                    removed += 1
                if removed:
                    _store._write_json_atomic(
                        self._safe_project_path(project_dir, "trash/index.json"),
                        {"items": remaining},
                        keep_backup=False,
                    )
                return removed

    def cleanup_expired_deleted_media(self, project_id: str | None = None) -> int:
        with self._lock:
            if project_id is not None:
                project_id = _store._clean_project_id(project_id)
                return self._cleanup_expired_deleted_media_locked(project_id)
            if not self.root.is_dir():
                return 0
            removed = 0
            for directory in self.root.iterdir():
                if self._is_path_redirect(directory):
                    continue
                if directory.is_dir() and (directory / "project.json").is_file():
                    removed += self._cleanup_expired_deleted_media_locked(directory.name)
            return removed

    def list_deleted_media(self, project_id: str) -> List[Dict[str, Any]]:
        project_id = _store._clean_project_id(project_id)
        with self._lock:
            self.open_project(project_id)
            self._cleanup_expired_deleted_media_locked(project_id)
            return copy.deepcopy(self._load_media_trash_index(project_id).get("items", []))

    def _cleanup_expired_deleted_media_locked(self, project_id: str, now: int | None = None) -> int:
        try:
            project_dir = self._project_directory(project_id)
            metadata_path = self._safe_project_path(project_dir, "project.json")
        except _store.ProjectNotFound:
            return 0
        if not metadata_path.is_file():
            return 0
        index = self._load_media_trash_index(project_id)
        timestamp = int(now if now is not None else _store._now_ms())
        remaining = []
        removed = 0
        for item in index.get("items", []):
            if not isinstance(item, dict):
                remaining.append(item)
                continue
            try:
                expires_at = int(item.get("expires_at") or 0)
            except (TypeError, ValueError):
                remaining.append(item)
                continue
            if not expires_at or expires_at > timestamp:
                remaining.append(item)
                continue
            preview_filenames = item.get("preview_filenames")
            if not isinstance(preview_filenames, list):
                preview_filenames = []
            media_filename = str(item.get("media_filename") or "").strip()
            relative_names = [f"media/{media_filename}"] if media_filename else []
            relative_names.extend(f"previews/{name}" for name in preview_filenames if isinstance(name, str))
            undo = item.get("undo")
            if isinstance(undo, dict) and isinstance(undo.get("snapshot_filename"), str):
                relative_names.append(f"undo/{undo['snapshot_filename']}")
            for relative_name in relative_names:
                path = self._safe_project_path(
                    project_dir,
                    (Path("trash") / relative_name).as_posix(),
                )
                path.unlink(missing_ok=True)
            removed += 1
        if removed:
            _store._write_json_atomic(
                self._safe_project_path(project_dir, "trash/index.json"),
                {"items": remaining},
                keep_backup=False,
            )
        return removed

    def _load_media_trash_index(self, project_id: str) -> Dict[str, Any]:
        try:
            project_dir = self._project_directory(project_id)
            payload = _store._read_json(self._safe_project_path(project_dir, "trash/index.json"))
            if not isinstance(payload, dict):
                return {"items": []}
            items = payload.get("items")
            return {"items": copy.deepcopy(items) if isinstance(items, list) else []}
        except (OSError, ValueError, json.JSONDecodeError):
            return {"items": []}


    @classmethod
    def _merge_deleted_asset_references(cls, current: Any, snapshot: Any, asset_id: str) -> Any:
        """Merge only references removed by one original-delete operation.

        The current object remains authoritative for prompts, positions, labels and
        all later edits.  Snapshot values are consulted only along branches that
        contained the deleted project media ID.
        """
        if isinstance(snapshot, dict) and str(snapshot.get("asset_id") or "") == asset_id:
            current_asset_id = str(current.get("asset_id") or "") if isinstance(current, dict) else ""
            if current_asset_id not in ("", asset_id):
                return copy.deepcopy(current)
            restored = copy.deepcopy(snapshot)
            if isinstance(current, dict):
                restored.update(copy.deepcopy(current))
            restored["asset_id"] = asset_id
            return restored
        if isinstance(snapshot, str) and snapshot == asset_id:
            return snapshot if current in (None, "", asset_id) else copy.deepcopy(current)
        if isinstance(snapshot, list):
            result = copy.deepcopy(current) if isinstance(current, list) else []
            for snapshot_index, snapshot_item in enumerate(snapshot):
                if isinstance(snapshot_item, str) and snapshot_item == asset_id:
                    required_occurrences = sum(
                        1 for item in snapshot[:snapshot_index + 1] if item == snapshot_item
                    )
                    if sum(1 for item in result if item == snapshot_item) < required_occurrences:
                        result.insert(min(snapshot_index, len(result)), snapshot_item)
                    continue
                if asset_id not in cls._collect_asset_ids(snapshot_item):
                    continue
                if isinstance(snapshot_item, dict) and str(snapshot_item.get("asset_id") or "") == asset_id:
                    direct_identity = cls._stable_list_item_identity(snapshot_item)
                    direct_matches = [
                        index for index, item in enumerate(result)
                        if direct_identity is not None
                        and cls._stable_list_item_identity(item) == direct_identity
                    ]
                    if len(direct_matches) == 1:
                        match_index = direct_matches[0]
                        current_item = result[match_index]
                        current_asset_id = str(current_item.get("asset_id") or "") if isinstance(current_item, dict) else ""
                        if current_asset_id not in ("", asset_id):
                            continue
                        restored_item = copy.deepcopy(snapshot_item)
                        if isinstance(current_item, dict):
                            restored_item.update(copy.deepcopy(current_item))
                        restored_item["asset_id"] = asset_id
                        result[match_index] = restored_item
                        continue
                    required_occurrences = sum(
                        1 for item in snapshot[:snapshot_index + 1] if item == snapshot_item
                    )
                    if sum(1 for item in result if item == snapshot_item) >= required_occurrences:
                        continue
                    result.insert(min(snapshot_index, len(result)), copy.deepcopy(snapshot_item))
                    continue
                stable_identity = cls._stable_list_item_identity(snapshot_item)
                stable_matches = [
                    index for index, item in enumerate(result)
                    if stable_identity is not None
                    and cls._stable_list_item_identity(item) == stable_identity
                ]
                match_index = stable_matches[0] if len(stable_matches) == 1 else None
                if match_index is None:
                    snapshot_without_asset = cls._remove_asset_references(snapshot_item, asset_id)
                    structural_matches = [
                        index for index, item in enumerate(result)
                        if item == snapshot_without_asset
                    ]
                    if structural_matches:
                        match_index = structural_matches[0]
                if match_index is None:
                    result.append(copy.deepcopy(snapshot_item))
                else:
                    result[match_index] = cls._merge_deleted_asset_references(
                        result[match_index], snapshot_item, asset_id,
                    )
            return result
        if isinstance(snapshot, dict):
            result = copy.deepcopy(current) if isinstance(current, dict) else {}
            for key, snapshot_value in snapshot.items():
                if asset_id not in cls._collect_asset_ids({key: snapshot_value}):
                    continue
                result[key] = cls._merge_deleted_asset_references(result.get(key), snapshot_value, asset_id)
            return result
        return copy.deepcopy(current)

    @staticmethod
    def _deleted_asset_tombstone(asset_id: str, record: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "deleted_asset_id": asset_id,
            "kind": str(record.get("kind") or "image"),
            "name": str(record.get("name") or ""),
            "reason": "upstream_media_deleted",
        }

    @classmethod
    def _append_deleted_asset_tombstone(
        cls,
        owner: Dict[str, Any],
        key: str,
        asset_id: str,
        record: Dict[str, Any],
    ) -> None:
        items = [item for item in owner.get(key) or [] if isinstance(item, dict)]
        if not any(str(item.get("deleted_asset_id") or "") == asset_id for item in items):
            items.append(cls._deleted_asset_tombstone(asset_id, record))
        owner[key] = items

    @staticmethod
    def _has_deleted_asset_tombstone(owner: Dict[str, Any], key: str, asset_id: str) -> bool:
        return any(
            isinstance(item, dict) and str(item.get("deleted_asset_id") or "") == asset_id
            for item in owner.get(key) or []
        )

    @classmethod
    def _remove_deleted_asset_tombstone(cls, owner: Dict[str, Any], key: str, asset_id: str) -> None:
        remaining = [
            item for item in owner.get(key) or []
            if not (isinstance(item, dict) and str(item.get("deleted_asset_id") or "") == asset_id)
        ]
        if remaining:
            owner[key] = remaining
        else:
            owner.pop(key, None)

    @classmethod
    def _remove_asset_references(cls, value: Any, asset_id: str) -> Any:
        if isinstance(value, list):
            cleaned = []
            for item in value:
                if isinstance(item, dict) and str(item.get("asset_id") or "") == asset_id:
                    continue
                result = cls._remove_asset_references(item, asset_id)
                if result is not None:
                    cleaned.append(result)
            return cleaned
        if not isinstance(value, dict):
            return value
        if str(value.get("asset_id") or "") == asset_id:
            return None
        cleaned = {}
        for key, child in value.items():
            if key == "asset_id" and str(child or "") == asset_id:
                continue
            if key in cls._MEDIA_ASSET_ID_FIELDS and isinstance(child, str) and child == asset_id:
                continue
            if key in cls._MEDIA_ASSET_ID_LIST_FIELDS and isinstance(child, list):
                child = [item for item in child if not (isinstance(item, str) and item == asset_id)]
            result = cls._remove_asset_references(child, asset_id) if isinstance(child, (dict, list)) else child
            if result is not None:
                cleaned[key] = result
        return cleaned
