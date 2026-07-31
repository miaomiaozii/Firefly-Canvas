"""Project-owned persistence for Firefly Canvas.

The store intentionally has no default project.  Each project directory owns
exactly one ``canvas.json`` and callers submit small mutations; full JSON
snapshots are written by a throttled background timer.
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
import mimetypes
import os
import re
import shutil
import stat
import threading
import time
import uuid
import zipfile
import zlib
import urllib.parse
from pathlib import Path
from typing import Any, BinaryIO, Dict, Iterable, List


MEDIA_TRASH_RETENTION_MS = 7 * 24 * 60 * 60 * 1000
ORIGINAL_MEDIA_TRANSACTION_FORMAT = "firefly-original-media-transaction-v1"
ORIGINAL_MEDIA_TRANSACTION_NAME = "original-media-transaction.json"


class ProjectStoreError(RuntimeError):
    pass


class ProjectNotFound(ProjectStoreError):
    pass


class InvalidMutation(ProjectStoreError):
    pass


class RevisionConflict(ProjectStoreError):
    def __init__(self, canvas: Dict[str, Any]):
        super().__init__("画布已在其他窗口更新")
        self.canvas = canvas


def _validated_projects_root(value: Path | str) -> Path:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        raise ProjectStoreError("项目库路径必须是绝对路径")
    resolved = candidate.resolve()
    repository = Path(__file__).resolve().parent
    try:
        if os.path.commonpath([os.path.normcase(str(repository)), os.path.normcase(str(resolved))]) == os.path.normcase(str(repository)):
            raise ProjectStoreError("项目库不能位于 Git 代码仓库内")
    except ValueError:
        pass
    return resolved


def default_projects_root() -> Path:
    configured = os.environ.get("FIREFLY_PROJECTS_ROOT", "").strip()
    return _validated_projects_root(configured or r"D:\Firefly-Canvas-Projects")


def _now_ms() -> int:
    return int(time.time() * 1000)


def _clean_project_id(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]", "", str(value or ""))
    if not cleaned or cleaned != value:
        raise ProjectNotFound("项目不存在")
    return cleaned


def _read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError("JSON root must be an object")
    return value


def _write_json_atomic(path: Path, value: Dict[str, Any], *, keep_backup: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        if keep_backup and path.exists():
            backup = path.with_name(path.name + ".bak")
            backup_temp = path.with_name(f".{backup.name}.{uuid.uuid4().hex}.tmp")
            try:
                shutil.copyfile(path, backup_temp)
                os.replace(backup_temp, backup)
            finally:
                backup_temp.unlink(missing_ok=True)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _library_manifest(root: Path) -> Dict[str, Any]:
    if not root.exists():
        return {"directories": [], "files": {}}
    directories: List[str] = []
    files: Dict[str, Dict[str, Any]] = {}
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix().casefold()):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise ProjectStoreError("项目库不能包含符号链接")
        if path.is_dir():
            directories.append(relative)
        elif path.is_file():
            files[relative] = {"size": path.stat().st_size, "sha256": _file_sha256(path)}
        else:
            raise ProjectStoreError(f"项目库包含不支持的文件类型: {relative}")
    return {"directories": directories, "files": files}


class ProjectStore:
    def __init__(self, root: Path | str | None = None, *, snapshot_delay: float = 1.2):
        self.root = _validated_projects_root(root) if root is not None else default_projects_root()
        self.snapshot_delay = max(0.0, float(snapshot_delay))
        self._lock = threading.RLock()
        self._canvases: Dict[str, Dict[str, Any]] = {}
        self._histories: Dict[str, List[Dict[str, Any]]] = {}
        self._timers: Dict[str, threading.Timer] = {}
        self._saved_revisions: Dict[str, int] = {}
        self._project_operations: Dict[str, threading.RLock] = {}
        self._fly_import_lock = threading.Lock()
        self._recover_original_media_transactions()
        self._cleanup_orphan_media_imports()
        self.cleanup_expired_deleted_media()

    def _project_operation_lock(self, project_id: str) -> threading.RLock:
        with self._lock:
            return self._project_operations.setdefault(project_id, threading.RLock())

    @staticmethod
    def _project_canvas_for_persistence(canvas: Dict[str, Any]) -> Dict[str, Any]:
        """Return the project-owned canvas shape without application UI state.

        Viewport/pan/zoom and the composer defaults are local application state,
        not creative content.  Strip them at the storage seam as a second line
        of defence so an older client or a recovery transaction cannot put them
        back into ``canvas.json``.
        """
        persisted = copy.deepcopy(canvas or {})
        persisted.pop("viewport", None)
        persisted.pop("settings", None)
        persisted.pop("logs", None)
        return persisted

    @staticmethod
    def _is_path_redirect(path: Path) -> bool:
        """Return whether an existing component can redirect filesystem access."""
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise ProjectStoreError("无法验证项目路径") from exc
        if stat.S_ISLNK(info.st_mode):
            return True
        attributes = int(getattr(info, "st_file_attributes", 0) or 0)
        reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400) or 0x400)
        if not attributes & reparse_flag:
            return False
        # Name-surrogate reparse points include Windows junctions and symlinks.
        # Other tags (for example cloud placeholders) do not redirect traversal
        # and remain valid project storage.
        tag = int(getattr(info, "st_reparse_tag", 0) or 0)
        return not tag or bool(tag & 0x20000000)

    def _project_directory(self, project_id: str) -> Path:
        project_id = _clean_project_id(project_id)
        lexical = self.root / project_id
        if self._is_path_redirect(lexical):
            raise ProjectStoreError("项目目录不能是符号链接或重解析路径")
        try:
            root = self.root.resolve(strict=True)
            resolved = lexical.resolve(strict=True)
            contained = os.path.commonpath(
                [os.path.normcase(str(root)), os.path.normcase(str(resolved))]
            ) == os.path.normcase(str(root))
        except FileNotFoundError as exc:
            raise ProjectNotFound("项目不存在") from exc
        except (OSError, ValueError) as exc:
            raise ProjectStoreError("项目目录路径无效") from exc
        if not contained or not resolved.is_dir():
            raise ProjectStoreError("项目目录路径越界")
        return resolved

    def _original_transaction_checkpoint(self, _checkpoint: str) -> None:
        """Crash-test seam at durable transaction boundaries.

        Production leaves this as a no-op.  Tests may replace it with a callback
        that raises ``BaseException`` to model abrupt process termination; normal
        operation errors still take the in-process rollback path.
        """

    def _original_transaction_path(self, project_id: str) -> Path:
        project_dir = self._project_directory(project_id)
        return self._safe_project_path(
            project_dir,
            f"trash/{ORIGINAL_MEDIA_TRANSACTION_NAME}",
        )

    def _recover_original_media_transactions(self) -> None:
        if not self.root.is_dir():
            return
        for project_dir in self.root.iterdir():
            if self._is_path_redirect(project_dir):
                continue
            if not project_dir.is_dir() or not (project_dir / "project.json").is_file():
                continue
            try:
                transaction_path = self._safe_project_path(
                    project_dir,
                    f"trash/{ORIGINAL_MEDIA_TRANSACTION_NAME}",
                )
            except ProjectStoreError:
                continue
            if transaction_path.is_file():
                self._recover_original_media_transaction(project_dir.name)

    def _recover_original_media_transaction(self, project_id: str) -> None:
        transaction_path = self._original_transaction_path(project_id)
        try:
            transaction = _read_json(transaction_path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise ProjectStoreError("项目原图事务日志损坏，无法安全恢复") from exc
        if (
            transaction.get("format") != ORIGINAL_MEDIA_TRANSACTION_FORMAT
            or transaction.get("project_id") != project_id
            or transaction.get("phase") not in {"prepared", "applying", "committed"}
        ):
            raise ProjectStoreError("项目原图事务日志无效，无法安全恢复")
        state_name = "after" if transaction["phase"] == "committed" else "before"
        self._apply_original_media_transaction_state(
            project_id,
            transaction,
            state_name,
            checkpoints=False,
            strict_moves=False,
        )
        transaction_path.unlink(missing_ok=True)

    def _commit_original_media_transaction(
        self,
        project_id: str,
        operation: str,
        asset_id: str,
        files: List[Dict[str, Any]],
        snapshot_filename: str,
        before: Dict[str, Any],
        after: Dict[str, Any],
    ) -> None:
        transaction_path = self._original_transaction_path(project_id)
        if transaction_path.exists():
            self._recover_original_media_transaction(project_id)
        transaction = {
            "format": ORIGINAL_MEDIA_TRANSACTION_FORMAT,
            "project_id": project_id,
            "operation": operation,
            "asset_id": asset_id,
            "phase": "prepared",
            "files": copy.deepcopy(files),
            "snapshot_filename": snapshot_filename,
            "before": copy.deepcopy(before),
            "after": copy.deepcopy(after),
        }
        _write_json_atomic(transaction_path, transaction, keep_backup=False)
        self._original_transaction_checkpoint("after_journal")
        transaction["phase"] = "applying"
        _write_json_atomic(transaction_path, transaction, keep_backup=False)
        try:
            self._apply_original_media_transaction_state(
                project_id,
                transaction,
                "after",
                checkpoints=True,
                strict_moves=True,
            )
            transaction["phase"] = "committed"
            _write_json_atomic(transaction_path, transaction, keep_backup=False)
            self._original_transaction_checkpoint("after_commit")
        except Exception:
            try:
                self._apply_original_media_transaction_state(
                    project_id,
                    transaction,
                    "before",
                    checkpoints=False,
                    strict_moves=False,
                )
            except Exception:
                # Keep the prepared/applying journal for deterministic recovery on
                # the next store open if the immediate rollback itself is blocked.
                raise
            else:
                transaction_path.unlink(missing_ok=True)
            raise
        transaction_path.unlink(missing_ok=True)

    def _apply_original_media_transaction_state(
        self,
        project_id: str,
        transaction: Dict[str, Any],
        state_name: str,
        *,
        checkpoints: bool,
        strict_moves: bool,
    ) -> None:
        state = transaction.get(state_name)
        if not isinstance(state, dict):
            raise ProjectStoreError("项目原图事务缺少状态快照")
        project_dir = self._project_directory(project_id)
        desired_location = str(state.get("file_location") or "")
        if desired_location not in {"live", "trash"}:
            raise ProjectStoreError("项目原图事务文件状态无效")
        snapshot_filename = str(transaction.get("snapshot_filename") or "")
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,240}", snapshot_filename) or not snapshot_filename.endswith(".json"):
            raise ProjectStoreError("项目原图事务撤销路径无效")
        media_index_path = self._safe_project_path(project_dir, "media/index.json")
        trash_index_path = self._safe_project_path(project_dir, "trash/index.json")
        canvas_path = self._safe_project_path(project_dir, "canvas.json")
        canvas_backup_path = self._safe_project_path(project_dir, "canvas.json.bak")
        history_path = self._safe_project_path(project_dir, "history.json")
        metadata_path = self._safe_project_path(project_dir, "project.json")
        undo_path = self._safe_project_path(
            project_dir,
            (Path("trash") / "undo" / snapshot_filename).as_posix(),
        )
        resolved_files = []
        for mapping in transaction.get("files") or []:
            if not isinstance(mapping, dict):
                raise ProjectStoreError("项目原图事务文件清单无效")
            live = self._safe_project_path(project_dir, mapping.get("live"))
            trash = self._safe_project_path(project_dir, mapping.get("trash"))
            resolved_files.append((mapping, live, trash))
        for mapping, live, trash in resolved_files:
            desired, other = (live, trash) if desired_location == "live" else (trash, live)
            if desired.exists() and other.exists():
                if strict_moves or _file_sha256(desired) != _file_sha256(other):
                    raise ProjectStoreError("项目原图事务发现冲突文件")
                other.unlink()
            elif not desired.exists() and other.exists():
                desired.parent.mkdir(parents=True, exist_ok=True)
                os.replace(other, desired)
            elif not desired.exists() and bool(mapping.get("required", True)):
                raise ProjectStoreError("项目原图事务缺少媒体文件")
        if checkpoints:
            self._original_transaction_checkpoint("after_media_move")

        media_index = state.get("media_index")
        trash_index = state.get("trash_index")
        canvas = state.get("canvas")
        history = state.get("history")
        metadata = state.get("project")
        if not all(isinstance(item, dict) for item in (media_index, trash_index, canvas, metadata)) or not isinstance(history, list):
            raise ProjectStoreError("项目原图事务 JSON 状态无效")
        _write_json_atomic(media_index_path, media_index, keep_backup=False)
        if checkpoints:
            self._original_transaction_checkpoint("after_media_index")
        _write_json_atomic(trash_index_path, trash_index, keep_backup=False)
        if checkpoints:
            self._original_transaction_checkpoint("after_trash_index")
        persisted_canvas = self._project_canvas_for_persistence(canvas)
        _write_json_atomic(
            canvas_path,
            persisted_canvas,
            keep_backup=state_name == "after",
        )
        if checkpoints:
            self._original_transaction_checkpoint("after_canvas")
        _write_json_atomic(history_path, {"records": history}, keep_backup=False)
        if checkpoints:
            self._original_transaction_checkpoint("after_history")
        _write_json_atomic(metadata_path, metadata, keep_backup=False)
        if checkpoints:
            self._original_transaction_checkpoint("after_project")

        undo_snapshot = state.get("undo_snapshot")
        if undo_snapshot is None:
            undo_path.unlink(missing_ok=True)
        elif isinstance(undo_snapshot, dict):
            _write_json_atomic(undo_path, undo_snapshot, keep_backup=False)
        else:
            raise ProjectStoreError("项目原图事务撤销快照无效")
        if checkpoints:
            self._original_transaction_checkpoint("after_undo_snapshot")
        # ``canvas.json`` is only one member of this project-wide transaction.
        # Once every companion file has reached the selected state, make the
        # single ADR-0010 backup represent that same committed state.  Leaving
        # the pre-transaction canvas in ``.bak`` would resurrect references to
        # media that the post-transaction indexes and history have deleted.
        _write_json_atomic(
            canvas_backup_path,
            persisted_canvas,
            keep_backup=False,
        )

    def _cleanup_orphan_media_imports(self) -> None:
        if not self.root.exists():
            return
        for project_dir in self.root.iterdir():
            if self._is_path_redirect(project_dir) or not project_dir.is_dir():
                continue
            try:
                media_root = self._safe_project_path(project_dir, "media")
            except ProjectStoreError:
                continue
            if not media_root.is_dir():
                continue
            for temporary in media_root.glob(".incoming.*.tmp"):
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def list_projects(self) -> List[Dict[str, Any]]:
        if not self.root.exists():
            return []
        projects: List[Dict[str, Any]] = []
        for directory in self.root.iterdir():
            if not directory.is_dir():
                continue
            try:
                metadata = _read_json(directory / "project.json")
                if metadata.get("id") != directory.name:
                    continue
                projects.append(self._project_record(metadata))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
        return sorted(projects, key=lambda item: (int(item.get("updated_at") or 0) * -1, item["id"]))

    def create_project(self, name: str) -> Dict[str, Any]:
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            project_id = uuid.uuid4().hex
            timestamp = _now_ms()
            metadata = {
                "format": "firefly-project-v1",
                "id": project_id,
                "name": (str(name or "").strip() or "新项目")[:60],
                "canvas_id": project_id,
                "created_at": timestamp,
                "updated_at": timestamp,
            }
            canvas = {
                "format": "firefly-canvas-v1",
                "id": project_id,
                "project_id": project_id,
                "project": project_id,
                "title": metadata["name"],
                "icon": "sparkles",
                "kind": "core",
                "created_at": timestamp,
                "updated_at": timestamp,
                "revision": 0,
                "nodes": [],
                "connections": [],
            }
            directory = self.root / project_id
            directory.mkdir(parents=False, exist_ok=False)
            for child in ("media", "previews", "trash"):
                (directory / child).mkdir()
            _write_json_atomic(directory / "media" / "index.json", {"items": {}}, keep_backup=False)
            _write_json_atomic(directory / "trash" / "index.json", {"items": []}, keep_backup=False)
            _write_json_atomic(directory / "project.json", metadata, keep_backup=False)
            _write_json_atomic(directory / "canvas.json", canvas, keep_backup=False)
            _write_json_atomic(directory / "history.json", {"records": []}, keep_backup=False)
            self._canvases[project_id] = canvas
            self._histories[project_id] = []
            self._saved_revisions[project_id] = 0
            return self._project_record(metadata)

    def export_fly_to(self, project_id: str, target: Path | str) -> Dict[str, Any]:
        project_id = _clean_project_id(project_id)
        destination = Path(target).expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.parent / f".{destination.name}.{uuid.uuid4().hex}.tmp"
        operation_lock = self._project_operation_lock(project_id)
        with operation_lock:
            with self._lock:
                self.flush(project_id)
                directory = self.root / project_id
                project_payload = _read_json(directory / "project.json")
                canvas_payload = _read_json(directory / "canvas.json")
                history_payload = _read_json(directory / "history.json")
                media_index = {"items": {}}
                media_files: List[tuple[str, Path]] = []
                media_root = (directory / "media").resolve()
                packaged_paths: set[str] = set()
                for asset_id in self._load_media_index(project_id)["items"]:
                    record = self.media_record(project_id, asset_id)
                    source_path = Path(record.pop("path"))
                    relative_name = source_path.relative_to(media_root).as_posix()
                    archive_path = f"media/{relative_name}"
                    if Path(relative_name).stem != asset_id or archive_path.casefold() in packaged_paths:
                        raise ProjectStoreError("项目媒体索引无法安全导出")
                    packaged_paths.add(archive_path.casefold())
                    record["filename"] = relative_name
                    record.pop("source", None)
                    record.pop("sources", None)
                    media_index["items"][asset_id] = record
                    media_files.append((archive_path, source_path))

                json_files = {
                    "project.json": json.dumps(project_payload, ensure_ascii=False, indent=2).encode("utf-8"),
                    "canvas.json": json.dumps(canvas_payload, ensure_ascii=False, indent=2).encode("utf-8"),
                    "history.json": json.dumps(history_payload, ensure_ascii=False, indent=2).encode("utf-8"),
                    "media/index.json": json.dumps(media_index, ensure_ascii=False, indent=2).encode("utf-8"),
                }
            declared = [
                {"path": path, "size": len(content), "sha256": hashlib.sha256(content).hexdigest()}
                for path, content in json_files.items()
            ]
            declared.extend(
                {"path": path, "size": source.stat().st_size, "sha256": _file_sha256(source)}
                for path, source in media_files
            )
            manifest = {
                "format": "firefly-package-v1",
                "version": 1,
                "project_id": project_id,
                "files": sorted(declared, key=lambda item: item["path"]),
            }
            try:
                with zipfile.ZipFile(temporary, "w", allowZip64=True) as archive:
                    for path, content in json_files.items():
                        archive.writestr(path, content, compress_type=zipfile.ZIP_DEFLATED)
                    for path, source in media_files:
                        archive.write(source, path, compress_type=zipfile.ZIP_STORED)
                    archive.writestr(
                        "manifest.json",
                        json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
                        compress_type=zipfile.ZIP_DEFLATED,
                    )
                os.replace(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)
        return {
            "project_id": project_id,
            "project_name": str(project_payload.get("name") or "firefly-project"),
            "path": str(destination),
            "file_count": len(declared),
            "size": destination.stat().st_size,
        }

    @property
    def project_trash_root(self) -> Path:
        return self.root / ".project-trash"

    def list_trashed_projects(self) -> List[Dict[str, Any]]:
        with self._lock:
            if not self.project_trash_root.is_dir():
                return []
            items = []
            for directory in self.project_trash_root.iterdir():
                if not directory.is_dir():
                    continue
                try:
                    metadata = _read_json(directory / "project.json")
                    trash = _read_json(directory / "trash.json")
                    if (
                        metadata.get("id") != directory.name
                        or metadata.get("canvas_id") != directory.name
                        or trash.get("project_id") != directory.name
                    ):
                        continue
                    item = self._project_record(metadata)
                    item["deleted_at"] = int(trash.get("deleted_at") or 0)
                    items.append(item)
                except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
                    continue
            return sorted(items, key=lambda item: (-int(item.get("deleted_at") or 0), item["id"]))

    def trash_project(self, project_id: str) -> Dict[str, Any]:
        project_id = _clean_project_id(project_id)
        with self._project_operation_lock(project_id):
            with self._lock:
                directory = self.root / project_id
                if not (directory / "project.json").is_file():
                    raise ProjectNotFound("项目不存在")
                self.flush(project_id)
                timer = self._timers.pop(project_id, None)
                if timer is not None and timer is not threading.current_thread():
                    timer.cancel()
                self.project_trash_root.mkdir(parents=True, exist_ok=True)
                target = self.project_trash_root / project_id
                if target.exists():
                    raise ProjectStoreError("项目回收站中已存在同名项目")
                deleted_at = _now_ms()
                trash_metadata = directory / "trash.json"
                _write_json_atomic(
                    trash_metadata,
                    {"project_id": project_id, "deleted_at": deleted_at},
                    keep_backup=False,
                )
                try:
                    os.replace(directory, target)
                except Exception:
                    trash_metadata.unlink(missing_ok=True)
                    raise
                self._canvases.pop(project_id, None)
                self._histories.pop(project_id, None)
                self._saved_revisions.pop(project_id, None)
                return {"id": project_id, "deleted_at": deleted_at, "path": str(target)}

    def restore_project(self, project_id: str) -> Dict[str, Any]:
        project_id = _clean_project_id(project_id)
        with self._project_operation_lock(project_id):
            with self._lock:
                source = self.project_trash_root / project_id
                target = self.root / project_id
                if not (source / "project.json").is_file():
                    raise ProjectStoreError("项目回收站中不存在该项目")
                if target.exists():
                    raise ProjectStoreError("项目库中已存在同名项目，无法恢复")
                metadata = _read_json(source / "project.json")
                trash_metadata = _read_json(source / "trash.json")
                if (
                    metadata.get("id") != project_id
                    or metadata.get("canvas_id") != project_id
                    or trash_metadata.get("project_id") != project_id
                ):
                    raise ProjectStoreError("项目回收站元数据与项目标识不一致")
                os.replace(source, target)
                try:
                    (target / "trash.json").unlink()
                except Exception:
                    os.replace(target, source)
                    if not (source / "trash.json").is_file():
                        _write_json_atomic(source / "trash.json", trash_metadata, keep_backup=False)
                    raise
                return self._project_record(metadata)

    def purge_trashed_project(self, project_id: str) -> Dict[str, Any]:
        project_id = _clean_project_id(project_id)
        with self._project_operation_lock(project_id):
            with self._lock:
                target = self.project_trash_root / project_id
                if not (target / "project.json").is_file():
                    raise ProjectStoreError("项目回收站中不存在该项目")
                shutil.rmtree(target)
                try:
                    self.project_trash_root.rmdir()
                except OSError:
                    pass
                return {"id": project_id, "permanently_deleted": True}

    def import_fly(self, package: bytes | Path | str | BinaryIO) -> Dict[str, Any]:
        with self._fly_import_lock:
            try:
                return self._import_fly(package)
            except ProjectStoreError:
                raise
            except (
                EOFError,
                NotImplementedError,
                OSError,
                RuntimeError,
                ValueError,
                zipfile.BadZipFile,
                zipfile.LargeZipFile,
                zlib.error,
            ) as exc:
                raise ProjectStoreError("Fly 项目包损坏或无法读取") from exc

    def _import_fly(self, package: bytes | Path | str | BinaryIO) -> Dict[str, Any]:
        if isinstance(package, (str, Path)):
            source: Any = Path(package)
            if not source.is_file():
                raise ProjectStoreError("Fly 项目包为空")
        elif hasattr(package, "read") and hasattr(package, "seek"):
            source = package
            source.seek(0)
        else:
            if not package:
                raise ProjectStoreError("Fly 项目包为空")
            source = io.BytesIO(package)
        try:
            archive = zipfile.ZipFile(source)
        except zipfile.BadZipFile as exc:
            raise ProjectStoreError("不是有效的 Fly 项目包") from exc
        with archive:
            members = archive.infolist()
            if len(members) > 10000 or sum(item.file_size for item in members) > 20 * 1024 * 1024 * 1024:
                raise ProjectStoreError("Fly 项目包超过安全限制")
            safe_members: Dict[str, zipfile.ZipInfo] = {}
            windows_targets: Dict[str, str] = {}
            for member in members:
                if member.filename.startswith(("/", "\\")):
                    raise ProjectStoreError("Fly 项目包含不安全路径")
                raw_name = member.filename.replace("\\", "/")
                name = raw_name.rstrip("/")
                parts = [part for part in name.split("/") if part]
                if not parts or any(part in {".", ".."} for part in parts) or any(":" in part or "\x00" in part for part in parts) or name != "/".join(parts):
                    raise ProjectStoreError("Fly 项目包含不安全路径")
                if (member.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ProjectStoreError("Fly 项目不能包含符号链接")
                windows_parts = []
                for part in parts:
                    normalized_part = part.rstrip(" .")
                    reserved_stem = normalized_part.split(".", 1)[0].upper()
                    if normalized_part != part or not normalized_part or reserved_stem in {
                        "CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4", "COM5",
                        "COM6", "COM7", "COM8", "COM9", "LPT1", "LPT2", "LPT3", "LPT4", "LPT5",
                        "LPT6", "LPT7", "LPT8", "LPT9",
                    }:
                        raise ProjectStoreError("Fly 项目包含 Windows 不安全路径")
                    windows_parts.append(normalized_part.casefold())
                windows_key = "/".join(windows_parts)
                previous_name = windows_targets.get(windows_key)
                if previous_name is not None and previous_name != name:
                    raise ProjectStoreError("Fly 项目包含 Windows 路径冲突")
                windows_targets[windows_key] = name
                safe_name = name + ("/" if member.is_dir() else "")
                if safe_name in safe_members:
                    raise ProjectStoreError("Fly 项目包含重复路径")
                safe_members[safe_name] = member
            windows_file_keys = {
                "/".join(part.rstrip(" .").casefold() for part in name.rstrip("/").split("/"))
                for name, member in safe_members.items() if not member.is_dir()
            }
            windows_all_keys = {
                "/".join(part.rstrip(" .").casefold() for part in name.rstrip("/").split("/"))
                for name in safe_members
            }
            if any(
                other != file_key and other.startswith(file_key + "/")
                for file_key in windows_file_keys for other in windows_all_keys
            ):
                raise ProjectStoreError("Fly 项目包含文件与目录路径冲突")
            if any(required not in safe_members for required in ("manifest.json", "project.json", "canvas.json")):
                raise ProjectStoreError("Fly 项目缺少 manifest.json、project.json 或 canvas.json")
            if any(safe_members[name].file_size > 16 * 1024 * 1024 for name in ("manifest.json", "project.json", "canvas.json")):
                raise ProjectStoreError("Fly 项目元数据超过安全限制")
            try:
                manifest = json.loads(archive.read(safe_members["manifest.json"]).decode("utf-8"))
                source_project = json.loads(archive.read(safe_members["project.json"]).decode("utf-8"))
                source_canvas = json.loads(archive.read(safe_members["canvas.json"]).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ProjectStoreError("Fly 项目元数据不可读取") from exc
            if not isinstance(manifest, dict) or manifest.get("format") != "firefly-package-v1":
                raise ProjectStoreError("Fly 项目包格式版本不受支持")
            try:
                package_version = int(manifest.get("version"))
            except (TypeError, ValueError) as exc:
                raise ProjectStoreError("Fly 项目包格式版本不受支持") from exc
            if package_version != 1:
                raise ProjectStoreError("Fly 项目包格式版本不受支持")
            if not isinstance(source_project, dict) or source_project.get("format") != "firefly-project-v1":
                raise ProjectStoreError("Fly 项目元数据格式版本不受支持")
            if not isinstance(source_canvas, dict) or source_canvas.get("format") != "firefly-canvas-v1":
                raise ProjectStoreError("Fly 画布格式版本不受支持")
            declared_files = manifest.get("files")
            if not isinstance(declared_files, list):
                raise ProjectStoreError("Fly 项目清单无效")
            declared: Dict[str, Dict[str, Any]] = {}
            for item in declared_files:
                if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                    raise ProjectStoreError("Fly 项目清单条目无效")
                path = item["path"]
                if path in declared:
                    raise ProjectStoreError("Fly 项目清单包含重复路径")
                declared[path] = item
            actual_files = {name for name, member in safe_members.items() if not member.is_dir() and name != "manifest.json"}
            if set(declared) != actual_files:
                raise ProjectStoreError("Fly 项目清单与实际文件不一致")
            fixed_files = {"project.json", "canvas.json", "history.json", "media/index.json"}
            if any(path not in fixed_files and not path.startswith("media/") for path in actual_files):
                raise ProjectStoreError("Fly 项目包含格式未声明的文件")
            media_members = [
                (name, safe_members[name]) for name in actual_files
                if name.startswith("media/") and name != "media/index.json"
            ]
            if any(
                member.flag_bits & 0x1
                or member.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
                or (member.compress_type == zipfile.ZIP_STORED and member.compress_size != member.file_size)
                or member.file_size > 1024 * 1024 * 1024
                for _name, member in media_members
            ):
                raise ProjectStoreError("Fly 项目媒体压缩格式或文件大小不受支持")
            if any(
                member.compress_type == zipfile.ZIP_DEFLATED
                and member.file_size > max(1024 * 1024, member.compress_size * 20)
                for _name, member in media_members
            ):
                raise ProjectStoreError("Fly 项目媒体压缩比超过安全限制")
            media_bytes = sum(member.file_size for _name, member in media_members)
            try:
                space_probe = self.root
                while not space_probe.exists() and space_probe.parent != space_probe:
                    space_probe = space_probe.parent
                free_bytes = shutil.disk_usage(space_probe).free
            except OSError as exc:
                raise ProjectStoreError("无法检查项目库可用空间") from exc
            reserve = max(256 * 1024 * 1024, media_bytes // 20)
            if media_bytes + reserve > free_bytes:
                raise ProjectStoreError("项目库可用空间不足，无法导入 Fly 项目")
            for path, item in declared.items():
                try:
                    declared_size = int(item.get("size", -1))
                except (TypeError, ValueError) as exc:
                    raise ProjectStoreError(f"Fly 文件大小清单无效: {path}") from exc
                declared_hash = str(item.get("sha256") or "").lower()
                member = safe_members[path]
                if declared_size != member.file_size:
                    raise ProjectStoreError(f"Fly 文件大小校验失败: {path}")
                digest = hashlib.sha256()
                with archive.open(member) as source_handle:
                    for chunk in iter(lambda: source_handle.read(1024 * 1024), b""):
                        digest.update(chunk)
                if not re.fullmatch(r"[0-9a-f]{64}", declared_hash) or declared_hash != digest.hexdigest():
                    raise ProjectStoreError(f"Fly 文件校验失败: {path}")
            if not isinstance(source_project, dict) or not isinstance(source_canvas, dict):
                raise ProjectStoreError("Fly 项目元数据格式无效")
            source_nodes = source_canvas.get("nodes") or []
            source_connections = source_canvas.get("connections") or []
            if not isinstance(source_nodes, list) or not isinstance(source_connections, list):
                raise ProjectStoreError("Fly 画布节点或连线格式无效")
            for node in source_nodes:
                if not isinstance(node, dict):
                    raise ProjectStoreError("Fly 画布包含无效节点")
                if self._contains_external_media_reference(node):
                    raise ProjectStoreError("Fly 画布包含外部媒体引用")
            for edge in source_connections:
                if not isinstance(edge, dict) or not self._entity_id(edge):
                    raise ProjectStoreError("Fly 画布包含无效连线")
            source_history: List[Dict[str, Any]] = []
            if "history.json" in safe_members:
                if safe_members["history.json"].file_size > 64 * 1024 * 1024:
                    raise ProjectStoreError("Fly 生成历史超过安全限制")
                try:
                    history_payload = json.loads(archive.read(safe_members["history.json"]).decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ProjectStoreError("Fly 生成历史不可读取") from exc
                if not isinstance(history_payload, dict) or not isinstance(history_payload.get("records"), list):
                    raise ProjectStoreError("Fly 生成历史格式无效")
                source_history = copy.deepcopy(history_payload["records"][-5000:])
            if self._contains_external_media_reference(source_history):
                raise ProjectStoreError("Fly 生成历史包含外部媒体引用")
            packaged_media_index: Dict[str, Dict[str, Any]] = {}
            if "media/index.json" in safe_members:
                index_member = safe_members["media/index.json"]
                if index_member.file_size > 64 * 1024 * 1024:
                    raise ProjectStoreError("Fly 项目媒体索引超过安全限制")
                try:
                    index_payload = json.loads(archive.read(index_member).decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ProjectStoreError("Fly 项目媒体索引不可读取") from exc
                index_items = index_payload.get("items") if isinstance(index_payload, dict) else None
                if not isinstance(index_items, dict):
                    raise ProjectStoreError("Fly 项目媒体索引格式无效")
                for asset_id, record in index_items.items():
                    if (
                        not isinstance(asset_id, str)
                        or not re.fullmatch(r"[A-Za-z0-9._-]{1,200}", asset_id)
                        or not isinstance(record, dict)
                        or str(record.get("asset_id") or asset_id) != asset_id
                    ):
                        raise ProjectStoreError("Fly 项目媒体索引条目无效")
                    packaged_media_index[asset_id] = record
            referenced_assets = self._collect_asset_ids(source_nodes) | self._collect_asset_ids(source_history)
            packaged_assets: set[str] = set()
            for name, _member in media_members:
                asset_id = Path(name).stem
                if asset_id in packaged_assets:
                    raise ProjectStoreError(f"Fly 项目媒体 ID 冲突: {asset_id}")
                packaged_assets.add(asset_id)
                indexed = packaged_media_index.get(asset_id)
                if indexed is not None:
                    indexed_filename = str(indexed.get("filename") or "").replace("\\", "/")
                    if indexed_filename != name.removeprefix("media/"):
                        raise ProjectStoreError(f"Fly 项目媒体索引路径不一致: {asset_id}")
            if set(packaged_media_index) - packaged_assets:
                raise ProjectStoreError("Fly 项目媒体索引包含不存在的原件")
            missing_assets = referenced_assets - packaged_assets
            if missing_assets:
                raise ProjectStoreError(f"Fly 项目缺少媒体原件: {sorted(missing_assets)[0]}")

            project = self.create_project(str(source_project.get("name") or source_canvas.get("title") or "导入项目"))
            project_id = project["id"]
            directory = self.root / project_id
            try:
                canvas = {
                    "format": "firefly-canvas-v1",
                    "id": project_id,
                    "project_id": project_id,
                    "project": project_id,
                    "title": str(source_canvas.get("title") or project["name"])[:80],
                    "icon": str(source_canvas.get("icon") or "sparkles")[:32],
                    "kind": "core",
                    "created_at": project["created_at"],
                    "updated_at": _now_ms(),
                    "revision": 0,
                    "nodes": copy.deepcopy(source_nodes),
                    "connections": copy.deepcopy(source_connections),
                }
                history = copy.deepcopy(source_history)
                for name, member in safe_members.items():
                    if member.is_dir() or not name.startswith("media/") or name == "media/index.json":
                        continue
                    relative = Path(*name.split("/")[1:])
                    media_root = (directory / "media").resolve()
                    target = (media_root / relative).resolve()
                    if os.path.commonpath([str(media_root), str(target)]) != str(media_root):
                        raise ProjectStoreError("Fly 项目包含不安全媒体路径")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(member) as source, target.open("wb") as destination:
                        shutil.copyfileobj(source, destination)
                media_index = {"items": {}}
                for name, member in safe_members.items():
                    if member.is_dir() or not name.startswith("media/") or name == "media/index.json":
                        continue
                    asset_id = Path(name).stem
                    relative_name = str(Path(*name.split("/")[1:]))
                    exported_record = packaged_media_index.get(asset_id) or {}
                    original_name = Path(str(exported_record.get("name") or Path(name).name)).name
                    mime_type = str(exported_record.get("mime_type") or mimetypes.guess_type(name)[0] or "application/octet-stream")[:256]
                    try:
                        created_at = max(0, int(exported_record.get("created_at") or 0))
                    except (TypeError, ValueError):
                        created_at = 0
                    media_index["items"][asset_id] = {
                        "asset_id": asset_id,
                        "filename": relative_name,
                        "name": original_name,
                        "mime_type": mime_type,
                        "size": member.file_size,
                        "sha256": str(declared[name]["sha256"]).lower(),
                        "kind": self._media_kind(mime_type, relative_name),
                        "created_at": created_at,
                    }
                _write_json_atomic(directory / "media" / "index.json", media_index, keep_backup=False)
                self._canvases[project_id] = canvas
                self._histories[project_id] = history
                self.flush(project_id)
                return self._project_record(_read_json(directory / "project.json"))
            except Exception:
                self._canvases.pop(project_id, None)
                self._histories.pop(project_id, None)
                self._saved_revisions.pop(project_id, None)
                shutil.rmtree(directory, ignore_errors=True)
                raise

    def open_project(self, project_id: str) -> Dict[str, Any]:
        project_id = _clean_project_id(project_id)
        with self._lock:
            project_dir = self._project_directory(project_id)
            metadata_path = self._safe_project_path(project_dir, "project.json")
            if not metadata_path.is_file():
                raise ProjectNotFound("项目不存在")
            metadata = _read_json(metadata_path)
            if metadata.get("id") != project_id or metadata.get("canvas_id") != project_id:
                raise ProjectStoreError("项目与唯一画布标识不一致")
            canvas = self._canvases.get(project_id)
            if canvas is None:
                canvas = self._load_canvas_with_recovery(project_id)
                self._canvases[project_id] = canvas
                self._saved_revisions[project_id] = int(canvas.get("revision") or 0)
            history = self._histories.get(project_id)
            if history is None:
                history = self._load_history(project_id)
                self._histories[project_id] = history
            opened_canvas = copy.deepcopy(canvas)
            opened_canvas["logs"] = copy.deepcopy(history)
            return {"project": self._project_record(metadata), "canvas": opened_canvas}

    def canvas(self, canvas_id: str) -> Dict[str, Any]:
        return self.open_project(canvas_id)["canvas"]

    def import_media(
        self,
        project_id: str,
        content: bytes,
        filename: str,
        mime_type: str = "",
        source_info: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        project_id = _clean_project_id(project_id)
        if not content:
            raise ProjectStoreError("媒体文件为空")
        if len(content) > 1024 * 1024 * 1024:
            raise ProjectStoreError("媒体文件超过 1GB 限制")
        with self._lock:
            self.open_project(project_id)
            digest = hashlib.sha256(content).hexdigest()
            index = self._load_media_index(project_id)
            existing = self._existing_media_record(project_id, index, digest, source_info)
            if existing is not None:
                return existing
            suffix = Path(str(filename or "")).suffix.lower()
            if not re.fullmatch(r"\.[a-z0-9]{1,10}", suffix):
                suffix = mimetypes.guess_extension(str(mime_type or "").split(";", 1)[0].strip()) or ".bin"
            stored_name = f"{digest}{suffix}"
            media_root = self.root / project_id / "media"
            media_path = media_root / stored_name
            if not media_path.exists():
                temporary = media_root / f".{stored_name}.{uuid.uuid4().hex}.tmp"
                try:
                    with temporary.open("wb") as handle:
                        handle.write(content)
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(temporary, media_path)
                finally:
                    temporary.unlink(missing_ok=True)
            record = self._new_media_record(digest, stored_name, filename, mime_type, len(content), source_info)
            index["items"][digest] = record
            index_path = media_root / "index.json"
            previous_index_bytes = index_path.read_bytes() if index_path.is_file() else None
            try:
                _write_json_atomic(index_path, index, keep_backup=False)
            except Exception:
                # Do not leave a content-addressed file behind when the index
                # commit fails.  A pre-existing file belongs to another valid
                # record and must remain untouched.
                index["items"].pop(digest, None)
                if not existing and media_path.exists():
                    media_path.unlink(missing_ok=True)
                if previous_index_bytes is None:
                    index_path.unlink(missing_ok=True)
                else:
                    rollback_index = index_path.with_name(f".{index_path.name}.{uuid.uuid4().hex}.rollback")
                    try:
                        rollback_index.write_bytes(previous_index_bytes)
                        os.replace(rollback_index, index_path)
                    finally:
                        rollback_index.unlink(missing_ok=True)
                raise
            return copy.deepcopy(record)

    def import_media_file(
        self,
        project_id: str,
        source_path: Path | str,
        filename: str,
        mime_type: str = "",
        source_info: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        project_id = _clean_project_id(project_id)
        source = Path(source_path).resolve()
        try:
            size = source.stat().st_size
        except OSError as exc:
            raise ProjectStoreError("媒体源文件不存在") from exc
        if size <= 0:
            raise ProjectStoreError("媒体文件为空")
        if size > 1024 * 1024 * 1024:
            raise ProjectStoreError("媒体文件超过 1GB 限制")
        with self._lock:
            self.open_project(project_id)
            media_root = self.root / project_id / "media"
        temporary = media_root / f".incoming.{uuid.uuid4().hex}.tmp"
        digest_builder = hashlib.sha256()
        copied_size = 0
        try:
            with source.open("rb") as source_handle, temporary.open("wb") as destination:
                for chunk in iter(lambda: source_handle.read(1024 * 1024), b""):
                    copied_size += len(chunk)
                    if copied_size > 1024 * 1024 * 1024:
                        raise ProjectStoreError("媒体文件超过 1GB 限制")
                    digest_builder.update(chunk)
                    destination.write(chunk)
                destination.flush()
                os.fsync(destination.fileno())
            if copied_size <= 0:
                raise ProjectStoreError("媒体文件为空")
            digest = digest_builder.hexdigest()
            with self._lock:
                self.open_project(project_id)
                index = self._load_media_index(project_id)
                existing = self._existing_media_record(project_id, index, digest, source_info)
                if existing is not None:
                    return existing
                suffix = Path(str(filename or source.name)).suffix.lower()
                if not re.fullmatch(r"\.[a-z0-9]{1,10}", suffix):
                    suffix = mimetypes.guess_extension(str(mime_type or "").split(";", 1)[0].strip()) or ".bin"
                stored_name = f"{digest}{suffix}"
                media_path = media_root / stored_name
                if not media_path.exists():
                    os.replace(temporary, media_path)
                record = self._new_media_record(digest, stored_name, filename or source.name, mime_type, copied_size, source_info)
                index["items"][digest] = record
                _write_json_atomic(media_root / "index.json", index, keep_backup=False)
                return copy.deepcopy(record)
        except ProjectStoreError:
            raise
        except OSError as exc:
            raise ProjectStoreError("项目媒体归档失败") from exc
        finally:
            temporary.unlink(missing_ok=True)

    def media_record(self, project_id: str, asset_id: str) -> Dict[str, Any]:
        project_id = _clean_project_id(project_id)
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,200}", str(asset_id or "")):
            raise ProjectStoreError("媒体资源标识无效")
        with self._lock:
            self.open_project(project_id)
            index = self._load_media_index(project_id)
            record = index["items"].get(asset_id)
            if not isinstance(record, dict):
                raise ProjectNotFound("项目媒体不存在")
            media_root = (self.root / project_id / "media").resolve()
            path = (media_root / str(record.get("filename") or "")).resolve()
            if os.path.commonpath([str(media_root), str(path)]) != str(media_root) or not path.is_file():
                raise ProjectNotFound("项目媒体不存在")
            cleaned_source = self._media_source(record["source"]) if isinstance(record.get("source"), dict) else None
            cleaned_sources = [self._media_source(item) for item in record.get("sources") or [] if isinstance(item, dict)] if isinstance(record.get("sources"), list) else None
            source_changed = cleaned_source is not None and record.get("source") != cleaned_source
            sources_changed = cleaned_sources is not None and record.get("sources") != cleaned_sources
            if source_changed:
                record["source"] = cleaned_source
            if sources_changed:
                record["sources"] = cleaned_sources
            if source_changed or sources_changed:
                _write_json_atomic(media_root / "index.json", index, keep_backup=False)
            result = copy.deepcopy(record)
            result.setdefault("kind", self._media_kind(str(result.get("mime_type") or ""), str(result.get("filename") or "")))
            result["path"] = str(path)
            return result

    def complete_photoshop_round_trip(
        self,
        project_id: str,
        source_node_id: str,
        source_media_id: str,
        request_id: str,
        content: bytes,
        filename: str,
        mime_type: str = "image/png",
    ) -> Dict[str, Any]:
        """Atomically attach a Photoshop return as a new project result.

        The source node/media are checked while holding the project lock.  The
        source remains untouched; the returned bytes become a project media
        original and a fresh ``ps_edit_result`` node plus ``derived_from`` edge
        are appended to the current canvas.
        """
        project_id = _clean_project_id(project_id)
        source_node_id = str(source_node_id or "").strip()
        source_media_id = str(source_media_id or "").strip()
        request_id = str(request_id or "").strip()
        if not source_node_id or not source_media_id or not request_id:
            raise InvalidMutation("Photoshop 往返缺少绑定标识")
        if not content:
            raise InvalidMutation("返回媒体为空")
        with self._lock:
            opened = self.open_project(project_id)
            canvas = opened["canvas"]
            source = next(
                (node for node in canvas.get("nodes") or []
                 if isinstance(node, dict) and str(node.get("id") or "") == source_node_id),
                None,
            )
            if source is None:
                raise ProjectNotFound("来源节点不存在")
            source_assets = {
                str(image.get("asset_id") or "")
                for image in source.get("images") or []
                if isinstance(image, dict) and image.get("asset_id")
            }
            if source_media_id not in source_assets:
                raise InvalidMutation("来源媒体不属于来源节点")
            source_record = self.media_record(project_id, source_media_id)
            if source_record.get("kind") != "image":
                raise InvalidMutation("Photoshop 往返仅支持图片来源")
            try:
                source_x = float(source.get("x") or 0)
                source_y = float(source.get("y") or 0)
                source_w = float(source.get("w") or 320)
                source_h = float(source.get("h") or 240)
            except (TypeError, ValueError) as exc:
                raise InvalidMutation("来源节点尺寸无效") from exc

            media_index = self._load_media_index(project_id)
            digest = hashlib.sha256(content).hexdigest()
            preexisting = isinstance(media_index.get("items", {}).get(digest), dict)
            result_media = self.import_media(
                project_id,
                content,
                filename or "photoshop-edit.png",
                mime_type or "image/png",
                {"type": "photoshop_round_trip", "name": filename or "photoshop-edit.png"},
            )
            result_node_id = f"ps-edit-{uuid.uuid4().hex}"
            edge_id = f"derived-{uuid.uuid4().hex}"
            node = {
                "id": result_node_id,
                "type": "ps_edit_result",
                "title": Path(filename or "photoshop-edit.png").stem[:120] or "Photoshop 编辑结果",
                "x": source_x + 360,
                "y": source_y,
                "w": source_w,
                "h": source_h,
                "source_node_id": source_node_id,
                "source_media_id": source_media_id,
                "request_id": request_id,
                "images": [{
                    "asset_id": result_media["asset_id"],
                    "kind": "image",
                    "name": Path(filename or "photoshop-edit.png").name,
                }],
                "outputKind": "image",
            }
            edge = {
                "id": edge_id,
                "from": source_node_id,
                "to": result_node_id,
                "kind": "derived_from",
                "request_id": request_id,
            }
            previous_canvas = copy.deepcopy(self._canvases.get(project_id))
            project_dir = self._project_directory(project_id)
            durable_paths = [
                project_dir / "canvas.json",
                project_dir / "canvas.json.bak",
                project_dir / "history.json",
                project_dir / "project.json",
            ]
            previous_durable = {
                path: path.read_bytes() if path.is_file() else None
                for path in durable_paths
            }
            try:
                next_canvas = copy.deepcopy(canvas)
                next_canvas.pop("logs", None)
                next_canvas.setdefault("nodes", []).append(node)
                next_canvas.setdefault("connections", []).append(edge)
                next_canvas["revision"] = int(next_canvas.get("revision") or 0) + 1
                next_canvas["updated_at"] = max(_now_ms(), int(next_canvas.get("updated_at") or 0) + 1)
                self._canvases[project_id] = next_canvas
                self._schedule_snapshot(project_id)
                # A Photoshop return is an explicit round-trip commit.  Flush
                # it before reporting success so a process exit cannot leave
                # a durable media original without its result node/edge.
                self.flush(project_id)
            except Exception:
                # Scheduling a durable snapshot is part of this operation's
                # commit boundary.  If it fails, restore the in-memory canvas
                # before removing the newly imported media so a retry cannot
                # observe a half-committed Photoshop result.
                if previous_canvas is None:
                    self._canvases.pop(project_id, None)
                else:
                    self._canvases[project_id] = previous_canvas
                cleanup_error = None
                if not preexisting:
                    try:
                        self.delete_media_if_unreferenced(project_id, result_media["asset_id"])
                    except Exception as exc:
                        # Continue restoring canvas/history even if media
                        # cleanup itself fails; otherwise a transient index or
                        # filesystem error can leave a half-committed canvas.
                        cleanup_error = exc
                # ``flush`` writes canvas/history/project as separate atomic
                # files.  Restore the pre-call bytes if a later companion file
                # fails, otherwise a restart could load a node whose media was
                # already rolled back.
                for path, content_before in previous_durable.items():
                    if content_before is None:
                        path.unlink(missing_ok=True)
                        continue
                    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.rollback")
                    try:
                        temporary.write_bytes(content_before)
                        os.replace(temporary, path)
                    finally:
                        temporary.unlink(missing_ok=True)
                if cleanup_error is not None:
                    raise cleanup_error
                raise
            return {
                "media": copy.deepcopy(result_media),
                "result_node": copy.deepcopy(node),
                "edge": copy.deepcopy(edge),
                "canvas": copy.deepcopy(next_canvas),
            }

    def list_media(self, project_id: str) -> List[Dict[str, Any]]:
        project_id = _clean_project_id(project_id)
        with self._lock:
            self.open_project(project_id)
            records = []
            for asset_id in self._load_media_index(project_id)["items"]:
                try:
                    record = self.media_record(project_id, asset_id)
                except ProjectNotFound:
                    continue
                record.pop("path", None)
                records.append(record)
            return sorted(records, key=lambda item: (int(item.get("created_at") or 0), str(item.get("asset_id") or "")), reverse=True)

    def delete_media_if_unreferenced(self, project_id: str, asset_id: str) -> bool:
        project_id = _clean_project_id(project_id)
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
            _write_json_atomic(media_root / "index.json", index, keep_backup=False)
            return True

    def delete_generated_original(
        self,
        project_id: str,
        asset_id: str,
        *,
        base_revision: int,
    ) -> Dict[str, Any]:
        """Remove one generated original from a project without cascading outputs.

        The media bytes move into the project-owned seven-day trash area.  The
        canvas and history are rewritten in the same store transaction so every
        direct asset reference disappears together, while prompts, references,
        settings, nodes, and downstream results remain available to the user.
        """
        project_id = _clean_project_id(project_id)
        asset_id = str(asset_id or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,200}", asset_id):
            raise ProjectStoreError("媒体资源标识无效")
        with self._project_operation_lock(project_id):
            with self._lock:
                opened = self.open_project(project_id)
                current_revision = int(opened["canvas"].get("revision") or 0)
                if int(base_revision) != current_revision:
                    raise RevisionConflict(opened["canvas"])
                self._cleanup_expired_deleted_media_locked(project_id)
                media_index = self._load_media_index(project_id)
                original = media_index["items"].get(asset_id)
                if not isinstance(original, dict):
                    raise ProjectNotFound("项目媒体不存在")
                if not self._is_generated_original(original):
                    raise ProjectStoreError("只能删除项目生成原图")
                if self._active_generation_references_asset(opened["canvas"], asset_id):
                    raise ProjectStoreError("该原图正在被生成任务引用，请等待任务完成后再删除")

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
                    raise ProjectNotFound("项目媒体不存在")
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
                deleted_at = _now_ms()
                snapshot_filename = f"{asset_id}-{deleted_at}.json"
                trash_item = copy.deepcopy(original)
                trash_item.update(
                    {
                        "asset_id": asset_id,
                        "deleted_at": deleted_at,
                        "expires_at": deleted_at + MEDIA_TRASH_RETENTION_MS,
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
                        cleaned["status"] = "original_deleted"
                        cleaned["originalDeleted"] = True
                        self._append_deleted_asset_tombstone(
                            cleaned, "deletedOutputTombstones", asset_id, original,
                        )
                    else:
                        self._append_deleted_asset_tombstone(
                            cleaned, "deletedReferenceTombstones", asset_id, original,
                        )
                    cleaned_history.append(cleaned)

                nodes = []
                for node in copy.deepcopy(opened["canvas"].get("nodes") or []):
                    node_id = str(node.get("id") or "")
                    displays_original = asset_id in self._collect_asset_ids(node.get("images") or [])
                    owns_original = displays_original and (
                        node_id in history_producer_ids
                        or self._has_explicit_generation_metadata(node)
                    )
                    directly_references_original = asset_id in self._collect_asset_ids(node)
                    cleaned_node = self._remove_asset_references(node, asset_id) or {}
                    if owns_original:
                        cleaned_node["needsRegeneration"] = True
                        cleaned_node["generationState"] = "ready"
                        cleaned_node["resultDeleted"] = True
                        cleaned_node["pending"] = 0
                        cleaned_node["running"] = False
                        cleaned_node["queued"] = False
                        for key in ("pendingTasks", "runFinishedAt", "runElapsedMs"):
                            cleaned_node.pop(key, None)
                        self._append_deleted_asset_tombstone(
                            cleaned_node, "deletedOutputTombstones", asset_id, original,
                        )
                    elif directly_references_original:
                        self._append_deleted_asset_tombstone(
                            cleaned_node, "deletedInputTombstones", asset_id, original,
                        )
                    nodes.append(cleaned_node)

                canvas = copy.deepcopy(opened["canvas"])
                canvas.pop("logs", None)
                canvas["nodes"] = nodes
                canvas["revision"] = current_revision + 1
                canvas["updated_at"] = max(_now_ms(), int(canvas.get("updated_at") or 0) + 1)
                before_canvas = copy.deepcopy(opened["canvas"])
                before_canvas.pop("logs", None)
                metadata = _read_json(self._safe_project_path(project_dir, "project.json"))
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

    def restore_deleted_original(
        self,
        project_id: str,
        asset_id: str,
        *,
        base_revision: int,
    ) -> Dict[str, Any]:
        """Restore one recently deleted generated original and its direct refs."""
        project_id = _clean_project_id(project_id)
        asset_id = str(asset_id or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,200}", asset_id):
            raise ProjectStoreError("媒体资源标识无效")
        with self._project_operation_lock(project_id):
            with self._lock:
                opened = self.open_project(project_id)
                current_revision = int(opened["canvas"].get("revision") or 0)
                if int(base_revision) != current_revision:
                    raise RevisionConflict(opened["canvas"])
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
                    raise ProjectNotFound("项目回收区中不存在该媒体")
                undo = item.get("undo")
                if not isinstance(undo, dict) or not isinstance(undo.get("snapshot_filename"), str):
                    raise ProjectStoreError("该项目回收记录不可撤销")
                project_dir = self._project_directory(project_id)
                undo_path = self._safe_project_path(
                    project_dir,
                    (Path("trash") / "undo" / undo["snapshot_filename"]).as_posix(),
                )
                try:
                    undo_snapshot = _read_json(undo_path)
                except (OSError, ValueError, json.JSONDecodeError) as exc:
                    raise ProjectStoreError("该项目回收记录不可撤销") from exc
                if not isinstance(undo_snapshot, dict) or not isinstance(undo_snapshot.get("nodes"), list) or not isinstance(undo_snapshot.get("history"), list):
                    raise ProjectStoreError("该项目回收记录不可撤销")

                media_index = self._load_media_index(project_id)
                if asset_id in media_index["items"]:
                    raise ProjectStoreError("项目媒体已恢复")
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
                    raise ProjectNotFound("项目回收区媒体不存在")
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
                canvas["updated_at"] = max(_now_ms(), int(canvas.get("updated_at") or 0) + 1)
                history = self._restore_deleted_history_records(
                    copy.deepcopy(self._histories.get(project_id, [])),
                    undo_snapshot["history"],
                    asset_id,
                )
                before_canvas = copy.deepcopy(opened["canvas"])
                before_canvas.pop("logs", None)
                before_history = copy.deepcopy(self._histories.get(project_id, []))
                metadata = _read_json(self._safe_project_path(project_dir, "project.json"))
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
                return {"canvas": result, "restored": copy.deepcopy(restored_record)}

    def purge_deleted_media(self, project_id: str, asset_id: str | None = None) -> int:
        """Permanently remove project media trash entries after explicit purge."""
        project_id = _clean_project_id(project_id)
        target_asset = str(asset_id or "").strip() if asset_id is not None else None
        if target_asset is not None and not re.fullmatch(r"[A-Za-z0-9._-]{1,200}", target_asset):
            raise ProjectStoreError("媒体资源标识无效")
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
                    relative_names = [
                        f"media/{item.get('media_filename')}",
                    ]
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
                    _write_json_atomic(
                        self._safe_project_path(project_dir, "trash/index.json"),
                        {"items": remaining},
                        keep_backup=False,
                    )
                return removed

    def cleanup_expired_deleted_media(self, project_id: str | None = None) -> int:
        with self._lock:
            if project_id is not None:
                project_id = _clean_project_id(project_id)
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
        project_id = _clean_project_id(project_id)
        with self._lock:
            self.open_project(project_id)
            self._cleanup_expired_deleted_media_locked(project_id)
            return copy.deepcopy(self._load_media_trash_index(project_id).get("items", []))

    def _cleanup_expired_deleted_media_locked(self, project_id: str, now: int | None = None) -> int:
        try:
            project_dir = self._project_directory(project_id)
            metadata_path = self._safe_project_path(project_dir, "project.json")
        except ProjectNotFound:
            return 0
        if not metadata_path.is_file():
            return 0
        index = self._load_media_trash_index(project_id)
        timestamp = int(now if now is not None else _now_ms())
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
            relative_names = [
                f"media/{item.get('media_filename')}",
            ]
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
            _write_json_atomic(
                self._safe_project_path(project_dir, "trash/index.json"),
                {"items": remaining},
                keep_backup=False,
            )
        return removed

    def _load_media_trash_index(self, project_id: str) -> Dict[str, Any]:
        try:
            project_dir = self._project_directory(project_id)
            payload = _read_json(self._safe_project_path(project_dir, "trash/index.json"))
            if not isinstance(payload, dict):
                return {"items": []}
            items = payload.get("items")
            return {"items": copy.deepcopy(items) if isinstance(items, list) else []}
        except (OSError, ValueError, json.JSONDecodeError):
            return {"items": []}

    @classmethod
    def _safe_project_path(cls, project_dir: Path, relative_name: Any) -> Path:
        if not isinstance(relative_name, str) or not relative_name.strip():
            raise ProjectStoreError("项目媒体路径无效")
        relative = Path(relative_name)
        if relative.is_absolute() or relative.drive or any(part == ".." for part in relative.parts):
            raise ProjectStoreError("项目媒体路径越界")
        project_dir = Path(project_dir)
        if cls._is_path_redirect(project_dir):
            raise ProjectStoreError("项目目录不能是符号链接或重解析路径")
        try:
            resolved_root = project_dir.resolve(strict=True)
        except (OSError, FileNotFoundError) as exc:
            raise ProjectStoreError("项目目录路径无效") from exc
        current = project_dir
        for part in relative.parts:
            if part in {"", "."}:
                continue
            current = current / part
            if cls._is_path_redirect(current):
                raise ProjectStoreError("项目路径不能经过符号链接或重解析点")
        try:
            candidate = (project_dir / relative).resolve(strict=False)
            if os.path.commonpath(
                [os.path.normcase(str(resolved_root)), os.path.normcase(str(candidate))]
            ) != os.path.normcase(str(resolved_root)):
                raise ProjectStoreError("项目媒体路径越界")
        except (OSError, ValueError) as exc:
            raise ProjectStoreError("项目媒体路径无效") from exc
        return candidate

    @classmethod
    def _is_generated_original(cls, record: Dict[str, Any]) -> bool:
        sources = []
        if isinstance(record.get("source"), dict):
            sources.append(record["source"])
        if isinstance(record.get("sources"), list):
            sources.extend(item for item in record["sources"] if isinstance(item, dict))
        return any(str(source.get("type") or "") == "online_generation" for source in sources)

    @staticmethod
    def _has_explicit_generation_metadata(node: Dict[str, Any]) -> bool:
        """Distinguish an output owner from a node that merely reuses its media.

        History send/drag creates an ordinary image node with the same ``images``
        entry, so image membership alone is not proof of ownership.  A real
        generator keeps its run snapshot on the node even when the corresponding
        history record has already been removed.
        """
        return any(
            key in node
            for key in (
                "runPrompt",
                "runModelPrompt",
                "runSettings",
                "runAt",
                "runStartedAt",
                "runFinishedAt",
                "runElapsedMs",
            )
        )

    @classmethod
    def _active_generation_references_asset(cls, canvas: Dict[str, Any], asset_id: str) -> bool:
        for node in canvas.get("nodes") or []:
            if not isinstance(node, dict):
                continue
            tasks = [task for task in node.get("pendingTasks") or [] if isinstance(task, dict)]
            if not tasks:
                continue
            for task in tasks:
                explicit_ids = set()
                for key in ("referenceAssetIds", "reference_asset_ids", "assetIds", "asset_ids"):
                    values = task.get(key)
                    if isinstance(values, list):
                        explicit_ids.update(str(value) for value in values if isinstance(value, str))
                explicit_ids.update(cls._collect_asset_ids(task))
                if asset_id in explicit_ids:
                    return True
            node_references = cls._collect_asset_ids(
                {
                    "runInputRefs": node.get("runInputRefs") or [],
                    "runPromptRefs": node.get("runPromptRefs") or [],
                    "manualReferenceImages": node.get("manualReferenceImages") or [],
                }
            )
            if asset_id in node_references:
                return True
        return False

    @classmethod
    def _restore_deleted_history_records(
        cls,
        current_history: List[Dict[str, Any]],
        snapshots: List[Any],
        asset_id: str,
    ) -> List[Dict[str, Any]]:
        snapshots_by_id = {
            str(record.get("id")): record
            for record in snapshots
            if isinstance(record, dict) and record.get("id")
        }
        restored_history = []
        for record in current_history:
            snapshot = snapshots_by_id.get(str(record.get("id") or "")) if isinstance(record, dict) else None
            if not isinstance(snapshot, dict):
                restored_history.append(record)
                continue
            output_deleted = cls._has_deleted_asset_tombstone(record, "deletedOutputTombstones", asset_id)
            reference_deleted = cls._has_deleted_asset_tombstone(record, "deletedReferenceTombstones", asset_id)
            if not output_deleted and not reference_deleted:
                restored_history.append(record)
                continue
            restored = cls._merge_deleted_asset_references(record, snapshot, asset_id)
            if output_deleted:
                restored.pop("originalDeleted", None)
                restored["status"] = snapshot.get("status") or "succeeded"
            cls._remove_deleted_asset_tombstone(restored, "deletedOutputTombstones", asset_id)
            cls._remove_deleted_asset_tombstone(restored, "deletedReferenceTombstones", asset_id)
            restored_history.append(restored)
        return restored_history

    @classmethod
    def _restore_deleted_nodes(
        cls,
        current_nodes: List[Dict[str, Any]],
        snapshots: List[Any],
        asset_id: str,
    ) -> List[Dict[str, Any]]:
        snapshots_by_id = {
            str(node.get("id")): node
            for node in snapshots
            if isinstance(node, dict) and node.get("id")
        }
        restored_nodes = []
        generation_keys = (
            "needsRegeneration", "generationState", "resultDeleted", "pending",
            "running", "queued", "pendingTasks", "runFinishedAt", "runElapsedMs",
        )
        for node in current_nodes:
            snapshot = snapshots_by_id.get(str(node.get("id") or "")) if isinstance(node, dict) else None
            if not isinstance(snapshot, dict):
                restored_nodes.append(node)
                continue
            output_deleted = cls._has_deleted_asset_tombstone(node, "deletedOutputTombstones", asset_id)
            input_deleted = cls._has_deleted_asset_tombstone(node, "deletedInputTombstones", asset_id)
            if not output_deleted and not input_deleted:
                restored_nodes.append(node)
                continue
            restored = cls._merge_deleted_asset_references(node, snapshot, asset_id)
            if output_deleted:
                for key in generation_keys:
                    if key in snapshot:
                        restored[key] = copy.deepcopy(snapshot[key])
                    else:
                        restored.pop(key, None)
            cls._remove_deleted_asset_tombstone(restored, "deletedOutputTombstones", asset_id)
            cls._remove_deleted_asset_tombstone(restored, "deletedInputTombstones", asset_id)
            restored_nodes.append(restored)
        return restored_nodes

    @classmethod
    def _merge_deleted_asset_references(cls, current: Any, snapshot: Any, asset_id: str) -> Any:
        """Merge only references removed by one original-delete operation.

        The current object remains authoritative for prompts, positions, labels and
        all later edits.  Snapshot values are consulted only along branches that
        contained the deleted project media ID.
        """
        if isinstance(snapshot, dict) and str(snapshot.get("asset_id") or "") == asset_id:
            return copy.deepcopy(snapshot)
        if isinstance(snapshot, list):
            result = copy.deepcopy(current) if isinstance(current, list) else []
            for snapshot_index, snapshot_item in enumerate(snapshot):
                if asset_id not in cls._collect_asset_ids(snapshot_item):
                    continue
                if isinstance(snapshot_item, dict) and str(snapshot_item.get("asset_id") or "") == asset_id:
                    if any(
                        isinstance(item, dict) and str(item.get("asset_id") or "") == asset_id
                        for item in result
                    ):
                        continue
                    result.insert(min(snapshot_index, len(result)), copy.deepcopy(snapshot_item))
                    continue
                stable_id = str(snapshot_item.get("id") or "") if isinstance(snapshot_item, dict) else ""
                match_index = next(
                    (
                        index for index, item in enumerate(result)
                        if stable_id and isinstance(item, dict) and str(item.get("id") or "") == stable_id
                    ),
                    None,
                )
                if match_index is None and snapshot_index < len(result):
                    match_index = snapshot_index
                if match_index is None:
                    result.append(cls._merge_deleted_asset_references(None, snapshot_item, asset_id))
                else:
                    result[match_index] = cls._merge_deleted_asset_references(
                        result[match_index], snapshot_item, asset_id,
                    )
            return result
        if isinstance(snapshot, dict):
            result = copy.deepcopy(current) if isinstance(current, dict) else {}
            for key, snapshot_value in snapshot.items():
                if asset_id not in cls._collect_asset_ids(snapshot_value):
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
            result = cls._remove_asset_references(child, asset_id) if isinstance(child, (dict, list)) else child
            if result is not None:
                cleaned[key] = result
        return cleaned

    @staticmethod
    def _media_kind(mime_type: str, filename: str) -> str:
        mime = str(mime_type or mimetypes.guess_type(filename)[0] or "").lower()
        if mime.startswith("video/"):
            return "video"
        if mime.startswith("audio/"):
            return "audio"
        return "image" if mime.startswith("image/") else "file"

    @classmethod
    def _media_source(cls, source: Dict[str, Any]) -> Dict[str, Any]:
        cleaned = {
            key: str(source.get(key) or "")[:2048]
            for key in ("locator", "name", "type")
            if source.get(key) not in (None, "")
        }
        if cleaned.get("locator"):
            cleaned["locator"] = cls._safe_source_locator(cleaned["locator"])
        return cleaned

    @staticmethod
    def _safe_source_locator(locator: str) -> str:
        value = str(locator or "").strip()[:2048]
        try:
            parsed = urllib.parse.urlsplit(value)
        except ValueError:
            return value.split("?", 1)[0].split("#", 1)[0]
        if parsed.scheme in {"http", "https"}:
            host = parsed.hostname or ""
            try:
                port = parsed.port
            except ValueError:
                port = None
            if port:
                host = f"{host}:{port}"
            return urllib.parse.urlunsplit((parsed.scheme, host, parsed.path, "", ""))
        if parsed.scheme == "file":
            return urllib.parse.urlunsplit((parsed.scheme, parsed.hostname or "", parsed.path, "", ""))
        return value.split("?", 1)[0].split("#", 1)[0]

    def _existing_media_record(
        self,
        project_id: str,
        index: Dict[str, Any],
        digest: str,
        source_info: Dict[str, Any] | None,
    ) -> Dict[str, Any] | None:
        existing = index["items"].get(digest)
        if not isinstance(existing, dict):
            return None
        try:
            self.media_record(project_id, digest)
        except ProjectNotFound:
            return None
        if source_info and self._merge_media_source(existing, source_info):
            _write_json_atomic(self.root / project_id / "media" / "index.json", index, keep_backup=False)
        return copy.deepcopy(existing)

    @classmethod
    def _merge_media_source(cls, record: Dict[str, Any], source_info: Dict[str, Any]) -> bool:
        incoming = cls._media_source(source_info)
        if not incoming:
            return False
        sources = [cls._media_source(item) for item in record.get("sources") or [] if isinstance(item, dict)]
        if not sources and isinstance(record.get("source"), dict):
            sources.append(cls._media_source(record["source"]))
        if incoming not in sources:
            sources.append(incoming)
        changed = record.get("sources") != sources or record.get("source") != sources[0]
        record["sources"] = sources
        record["source"] = sources[0]
        return changed

    @classmethod
    def _new_media_record(
        cls,
        digest: str,
        stored_name: str,
        filename: str,
        mime_type: str,
        size: int,
        source_info: Dict[str, Any] | None,
    ) -> Dict[str, Any]:
        record = {
            "asset_id": digest,
            "filename": stored_name,
            "name": Path(str(filename or stored_name)).name,
            "mime_type": str(mime_type or mimetypes.guess_type(stored_name)[0] or "application/octet-stream"),
            "size": size,
            "sha256": digest,
            "kind": cls._media_kind(mime_type, stored_name),
            "created_at": _now_ms(),
        }
        if source_info:
            cls._merge_media_source(record, source_info)
        return record

    def delete_history_record(self, project_id: str, log_id: str, *, base_revision: int) -> Dict[str, Any]:
        project_id = _clean_project_id(project_id)
        with self._lock:
            opened = self.open_project(project_id)["canvas"]
            current_revision = int(opened.get("revision") or 0)
            if int(base_revision) != current_revision:
                raise RevisionConflict(opened)
            history = copy.deepcopy(self._histories.get(project_id, []))
            if not any(str(item.get("id") or "") == log_id for item in history):
                raise ProjectNotFound("生成历史不存在")
            history = [item for item in history if str(item.get("id") or "") != log_id]
            canvas = copy.deepcopy(opened)
            canvas.pop("logs", None)
            canvas["revision"] = current_revision + 1
            canvas["updated_at"] = max(_now_ms(), int(canvas.get("updated_at") or 0) + 1)
            self._canvases[project_id] = canvas
            self._histories[project_id] = history
            self._schedule_snapshot(project_id)
            result = copy.deepcopy(canvas)
            result["logs"] = copy.deepcopy(history)
            return result

    def begin_media_generation(
        self,
        project_id: str,
        result_node_id: str,
        task_record: Dict[str, Any],
        *,
        base_revision: int,
        media_kind: str = "image",
    ) -> Dict[str, Any]:
        project_id = _clean_project_id(project_id)
        node_id = str(result_node_id or "").strip()
        task = copy.deepcopy(task_record)
        task_id = str(task.get("taskId") or "").strip()
        if not node_id or not task_id:
            raise InvalidMutation("媒体生成任务缺少节点或任务标识")
        if media_kind not in {"image", "video"}:
            raise InvalidMutation("生成任务媒体类型不受支持")
        with self._lock:
            opened = self.open_project(project_id)["canvas"]
            if int(base_revision) != int(opened.get("revision") or 0):
                raise RevisionConflict(opened)
            canvas = copy.deepcopy(opened)
            canvas.pop("logs", None)
            node = next((item for item in canvas.get("nodes") or [] if str(item.get("id") or "") == node_id), None)
            if node is None or str(node.get("type") or "") not in {"image", "smart-image"}:
                raise InvalidMutation("媒体生成任务必须绑定已保存的媒体节点")
            if node.get("pendingTasks"):
                raise InvalidMutation("该结果节点已有媒体生成任务正在运行")
            tasks = [item for item in node.get("pendingTasks") or [] if str(item.get("taskId") or "") != task_id]
            tasks.append(task)
            node["pendingTasks"] = tasks
            node["pending"] = max(int(node.get("pending") or 0), int(task.get("count") or 1), len(tasks))
            node["running"] = False
            node.pop("queued", None)
            return self._commit_generation_canvas(project_id, canvas)

    def begin_image_generation(
        self,
        project_id: str,
        result_node_id: str,
        task_record: Dict[str, Any],
        *,
        base_revision: int,
    ) -> Dict[str, Any]:
        """Backward-compatible image specialization of begin_media_generation."""
        return self.begin_media_generation(
            project_id,
            result_node_id,
            task_record,
            base_revision=base_revision,
            media_kind="image",
        )

    def fail_media_generation(
        self,
        project_id: str,
        result_node_id: str,
        task_id: str,
        history_record: Dict[str, Any],
    ) -> Dict[str, Any]:
        project_id = _clean_project_id(project_id)
        node_id = str(result_node_id or "").strip()
        clean_history = copy.deepcopy(history_record)
        with self._lock:
            opened = self.open_project(project_id)["canvas"]
            canvas = copy.deepcopy(opened)
            canvas.pop("logs", None)
            node = next((item for item in canvas.get("nodes") or [] if str(item.get("id") or "") == node_id), None)
            if node is not None:
                tasks = [item for item in node.get("pendingTasks") or [] if str(item.get("taskId") or "") != str(task_id or "")]
                if tasks:
                    node["pendingTasks"] = tasks
                    node["pending"] = max(len(tasks), sum(max(1, int(item.get("count") or 1)) for item in tasks))
                else:
                    node.pop("pendingTasks", None)
                    node["pending"] = 0
                node["running"] = False
                node.pop("queued", None)
            history = copy.deepcopy(self._histories.get(project_id, []))
            history_id = str(clean_history.get("id") or "") if isinstance(clean_history, dict) else ""
            if history_id:
                history = [item for item in history if str(item.get("id") or "") != history_id]
            history.append(clean_history)
            self._histories[project_id] = history[-5000:]
            return self._commit_generation_canvas(project_id, canvas)

    def fail_image_generation(
        self,
        project_id: str,
        result_node_id: str,
        task_id: str,
        history_record: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Backward-compatible image specialization of fail_media_generation."""
        return self.fail_media_generation(project_id, result_node_id, task_id, history_record)

    def _commit_generation_canvas(self, project_id: str, canvas: Dict[str, Any]) -> Dict[str, Any]:
        canvas["revision"] = int(canvas.get("revision") or 0) + 1
        canvas["updated_at"] = max(_now_ms(), int(canvas.get("updated_at") or 0) + 1)
        self._canvases[project_id] = canvas
        self._schedule_snapshot(project_id)
        result = copy.deepcopy(canvas)
        result["logs"] = copy.deepcopy(self._histories.get(project_id, []))
        return result

    def complete_media_generation(
        self,
        project_id: str,
        result_node_id: str,
        media: List[Dict[str, Any]],
        history_record: Dict[str, Any],
        *,
        media_kind: str = "image",
    ) -> Dict[str, Any]:
        """Atomically attach generated project originals to a visible node and history.

        Online tasks finish independently of the browser's save cadence, so this
        mutation intentionally operates on the latest server revision instead of
        accepting a stale browser revision.  Existing node metadata is retained.
        """
        project_id = _clean_project_id(project_id)
        node_id = str(result_node_id or "").strip()
        if not node_id:
            raise InvalidMutation("生成结果缺少画布节点标识")
        clean_media = copy.deepcopy(media)
        clean_history = copy.deepcopy(history_record)
        if media_kind not in {"image", "video"}:
            raise InvalidMutation("生成结果媒体类型不受支持")
        if not clean_media:
            raise InvalidMutation("生成结果没有项目媒体原件")
        if self._contains_external_media_reference(clean_media) or self._contains_external_media_reference(clean_history):
            raise InvalidMutation("生成结果只能引用项目媒体资源 ID")

        with self._lock:
            opened = self.open_project(project_id)["canvas"]
            generated_asset_ids = self._collect_asset_ids(clean_media)
            history_asset_ids = self._collect_asset_ids(clean_history) - generated_asset_ids
            for asset_id in generated_asset_ids:
                try:
                    record = self.media_record(project_id, asset_id)
                except ProjectStoreError as exc:
                    raise InvalidMutation(f"项目媒体不存在: {asset_id}") from exc
                if record.get("kind") != media_kind:
                    raise InvalidMutation(f"生成结果媒体类型不是 {media_kind}")
            for asset_id in history_asset_ids:
                try:
                    self.media_record(project_id, asset_id)
                except ProjectStoreError as exc:
                    raise InvalidMutation(f"项目媒体不存在: {asset_id}") from exc
            canvas = copy.deepcopy(opened)
            canvas.pop("logs", None)
            nodes = canvas.setdefault("nodes", [])
            node = next((item for item in nodes if str(item.get("id") or "") == node_id), None)
            if node is not None and str(node.get("type") or "") not in {"image", "smart-image"}:
                raise InvalidMutation("生成结果只能写入图片节点")
            if node is None:
                node = {
                    "id": node_id,
                    "type": "smart-image",
                    "x": 0,
                    "y": 0,
                    "created_at": _now_ms(),
                }
                nodes.append(node)
            node["images"] = clean_media
            node["title"] = (
                "Videos" if len(clean_media) > 1 else "Video"
            ) if media_kind == "video" else ("Group" if len(clean_media) > 1 else "Image")
            node["outputKind"] = media_kind
            # A successful regeneration supersedes the producer-only state left
            # by deleting its previous output.  Input tombstones are separate:
            # they continue to describe missing upstream material until that
            # reference is explicitly restored.
            for key in (
                "needsRegeneration",
                "generationState",
                "resultDeleted",
                "deletedOutputTombstones",
            ):
                node.pop(key, None)
            node["pending"] = 0
            node["running"] = False
            node.pop("pendingTasks", None)
            node.pop("queued", None)
            history = copy.deepcopy(self._histories.get(project_id, []))
            history.append(clean_history)
            history = history[-5000:]
            self._histories[project_id] = history
            return self._commit_generation_canvas(project_id, canvas)

    def complete_image_generation(
        self,
        project_id: str,
        result_node_id: str,
        images: List[Dict[str, Any]],
        history_record: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Backward-compatible image specialization of complete_media_generation."""
        return self.complete_media_generation(
            project_id,
            result_node_id,
            images,
            history_record,
            media_kind="image",
        )

    def apply_mutations(
        self,
        project_id: str,
        mutations: Iterable[Dict[str, Any]],
        *,
        base_revision: int,
    ) -> Dict[str, Any]:
        project_id = _clean_project_id(project_id)
        items = list(mutations)
        if not items:
            return {"project_id": project_id, "applied": 0, "revision": self.canvas(project_id)["revision"]}
        with self._lock:
            canvas = self.open_project(project_id)["canvas"]
            current_revision = int(canvas.get("revision") or 0)
            if int(base_revision) != current_revision:
                raise RevisionConflict(canvas)
            canvas.pop("logs", None)
            history = copy.deepcopy(self._histories.get(project_id, []))
            for mutation in items:
                normalized = copy.deepcopy(mutation)
                if normalized.get("op") == "set_canvas_meta" and "logs" in normalized:
                    logs = normalized.pop("logs")
                    if not isinstance(logs, list):
                        raise InvalidMutation("logs 必须是数组")
                    if self._contains_external_media_reference(logs):
                        raise InvalidMutation("生成历史只能引用项目媒体资源 ID")
                    for asset_id in self._collect_asset_ids(logs):
                        try:
                            self.media_record(project_id, asset_id)
                        except ProjectStoreError as exc:
                            raise InvalidMutation(f"项目媒体不存在: {asset_id}") from exc
                    incoming = copy.deepcopy(logs[-5000:])
                    incoming_by_id = {
                        str(item.get("id")): item
                        for item in incoming
                        if isinstance(item, dict) and str(item.get("id") or "")
                    }
                    existing_ids = {
                        str(item.get("id"))
                        for item in history
                        if isinstance(item, dict) and str(item.get("id") or "")
                    }
                    incoming_has_idless = any(
                        not isinstance(item, dict) or not str(item.get("id") or "")
                        for item in incoming
                    )
                    merged = []
                    seen_ids = set()
                    for item in history:
                        item_id = str(item.get("id") or "") if isinstance(item, dict) else ""
                        if item_id and item_id in incoming_by_id:
                            merged.append(incoming_by_id[item_id])
                            seen_ids.add(item_id)
                        elif item_id or not incoming_has_idless:
                            merged.append(item)
                    for item in incoming:
                        item_id = str(item.get("id") or "") if isinstance(item, dict) else ""
                        if not item_id or (item_id not in seen_ids and item_id not in existing_ids):
                            merged.append(item)
                            if item_id:
                                seen_ids.add(item_id)
                    history = merged[-5000:]
                self._apply_mutation(canvas, normalized)
            canvas["revision"] = int(canvas.get("revision") or 0) + 1
            canvas["updated_at"] = max(_now_ms(), int(canvas.get("updated_at") or 0) + 1)
            self._canvases[project_id] = canvas
            self._histories[project_id] = history
            self._schedule_snapshot(project_id)
            return {
                "project_id": project_id,
                "canvas_id": canvas["id"],
                "applied": len(items),
                "revision": canvas["revision"],
                "updated_at": canvas["updated_at"],
            }

    def flush(self, project_id: str) -> Dict[str, Any]:
        project_id = _clean_project_id(project_id)
        with self._lock:
            timer = self._timers.pop(project_id, None)
            if timer is not None and timer is not threading.current_thread():
                timer.cancel()
            canvas = self._canvases.get(project_id)
            if canvas is None:
                canvas = self.open_project(project_id)["canvas"]
            persisted_canvas = self._project_canvas_for_persistence(canvas)
            _write_json_atomic(self.root / project_id / "canvas.json", persisted_canvas, keep_backup=True)
            history = self._histories.get(project_id)
            if history is None:
                history = self._load_history(project_id)
                self._histories[project_id] = history
            _write_json_atomic(self.root / project_id / "history.json", {"records": history}, keep_backup=False)
            metadata_path = self.root / project_id / "project.json"
            metadata = _read_json(metadata_path)
            metadata["name"] = canvas.get("title") or metadata.get("name") or "未命名项目"
            metadata["updated_at"] = canvas["updated_at"]
            _write_json_atomic(metadata_path, metadata, keep_backup=False)
            self._saved_revisions[project_id] = int(canvas.get("revision") or 0)
            return {"project_id": project_id, "revision": canvas["revision"], "saved_at": canvas["updated_at"]}

    def save_status(self, project_id: str) -> Dict[str, Any]:
        project_id = _clean_project_id(project_id)
        with self._lock:
            canvas = self.open_project(project_id)["canvas"]
            revision = int(canvas.get("revision") or 0)
            saved_revision = int(self._saved_revisions.get(project_id, revision))
            return {
                "project_id": project_id,
                "revision": revision,
                "saved_revision": saved_revision,
                "saved": saved_revision >= revision,
            }

    def copy_library_to(self, target: Path | str) -> Dict[str, Any]:
        """Copy and fully verify this library without changing the active root.

        The caller may switch to ``target`` only after this method returns.  The
        source is deliberately retained as a safety copy; deleting it is a
        separate user-owned cleanup decision.
        """
        destination = _validated_projects_root(target)
        source = self.root.resolve()
        if os.path.normcase(str(destination)) == os.path.normcase(str(source)):
            raise ProjectStoreError("新项目库与当前项目库相同")
        try:
            common = Path(os.path.commonpath([str(source), str(destination)]))
            if common == source or common == destination:
                raise ProjectStoreError("新旧项目库不能互相嵌套")
        except ValueError:
            pass
        if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
            raise ProjectStoreError("目标项目库必须为空")

        destination.parent.mkdir(parents=True, exist_ok=True)
        staging = destination.parent / f".{destination.name}.firefly-migration-{uuid.uuid4().hex}.tmp"
        destination_was_empty = destination.is_dir()
        destination_committed = False
        with self._lock:
            try:
                pending_projects = set(self._canvases) | set(self._timers)
                for project_id in pending_projects:
                    self.flush(project_id)
                source_manifest = _library_manifest(source)
                if source.exists():
                    shutil.copytree(source, staging, copy_function=shutil.copy2)
                else:
                    staging.mkdir()
                copied_manifest = _library_manifest(staging)
                if copied_manifest != source_manifest:
                    raise ProjectStoreError("项目库复制完整性校验失败")
                if destination_was_empty:
                    destination.rmdir()
                os.replace(staging, destination)
                destination_committed = True
                final_manifest = _library_manifest(destination)
                if final_manifest != source_manifest:
                    raise ProjectStoreError("项目库切换前完整性校验失败")
            except Exception:
                if staging.exists():
                    shutil.rmtree(staging, ignore_errors=True)
                if destination_committed and destination.exists():
                    shutil.rmtree(destination, ignore_errors=True)
                if destination_was_empty and not destination.exists():
                    destination.mkdir(parents=True, exist_ok=True)
                raise
        files = source_manifest["files"]
        return {
            "source": str(source),
            "target": str(destination),
            "file_count": len(files),
            "directory_count": len(source_manifest["directories"]),
            "total_bytes": sum(int(item["size"]) for item in files.values()),
            "source_retained": True,
            "target_preexisted": destination_was_empty,
            "verified": True,
        }

    def close(self) -> None:
        with self._lock:
            pending = list(self._timers)
        for project_id in pending:
            self.flush(project_id)

    def _project_record(self, metadata: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": metadata["id"],
            "name": metadata.get("name") or "未命名项目",
            "canvas_id": metadata["canvas_id"],
            "created_at": metadata.get("created_at", 0),
            "updated_at": metadata.get("updated_at", 0),
        }

    @staticmethod
    def _metadata_for_canvas(metadata: Dict[str, Any], canvas: Dict[str, Any]) -> Dict[str, Any]:
        result = copy.deepcopy(metadata)
        result["name"] = canvas.get("title") or result.get("name") or "未命名项目"
        result["updated_at"] = int(canvas.get("updated_at") or result.get("updated_at") or 0)
        return result

    def _load_canvas_with_recovery(self, project_id: str) -> Dict[str, Any]:
        project_dir = self._project_directory(project_id)
        primary = self._safe_project_path(project_dir, "canvas.json")
        backup = self._safe_project_path(project_dir, "canvas.json.bak")
        try:
            canvas = _read_json(primary)
        except (OSError, ValueError, json.JSONDecodeError) as primary_error:
            try:
                canvas = _read_json(backup)
            except (OSError, ValueError, json.JSONDecodeError) as backup_error:
                raise ProjectStoreError("画布文件和备份均不可读取") from backup_error
            _write_json_atomic(primary, self._project_canvas_for_persistence(canvas), keep_backup=False)
        if canvas.get("project_id") != project_id or canvas.get("id") != project_id:
            raise ProjectStoreError("画布不属于请求的项目")
        legacy_logs = canvas.pop("logs", None)
        had_application_state = "viewport" in canvas or "settings" in canvas
        canvas.pop("viewport", None)
        canvas.pop("settings", None)
        if had_application_state:
            # One-time migration for projects created before the persistence
            # boundary was tightened. Keep both recovery copies canonical.
            canonical = self._project_canvas_for_persistence(canvas)
            _write_json_atomic(primary, canonical, keep_backup=False)
            _write_json_atomic(backup, canonical, keep_backup=False)
        if isinstance(legacy_logs, list) and project_id not in self._histories:
            self._histories[project_id] = copy.deepcopy(legacy_logs[-5000:])
        return canvas

    def _load_history(self, project_id: str) -> List[Dict[str, Any]]:
        try:
            project_dir = self._project_directory(project_id)
            payload = _read_json(self._safe_project_path(project_dir, "history.json"))
            records = payload.get("records")
            return copy.deepcopy(records) if isinstance(records, list) else []
        except (OSError, ValueError, json.JSONDecodeError):
            return []

    def _load_media_index(self, project_id: str) -> Dict[str, Any]:
        try:
            project_dir = self._project_directory(project_id)
            payload = _read_json(self._safe_project_path(project_dir, "media/index.json"))
            items = payload.get("items")
            return {"items": copy.deepcopy(items) if isinstance(items, dict) else {}}
        except (OSError, ValueError, json.JSONDecodeError):
            return {"items": {}}

    def _schedule_snapshot(self, project_id: str) -> None:
        previous = self._timers.pop(project_id, None)
        if previous is not None:
            previous.cancel()
        if self.snapshot_delay <= 0:
            return
        timer = threading.Timer(self.snapshot_delay, self.flush, args=(project_id,))
        timer.daemon = True
        self._timers[project_id] = timer
        timer.start()

    def _apply_mutation(self, canvas: Dict[str, Any], mutation: Dict[str, Any]) -> None:
        if not isinstance(mutation, dict):
            raise InvalidMutation("变更必须是对象")
        operation = mutation.get("op")
        if operation == "upsert_node":
            node = copy.deepcopy(mutation.get("node"))
            if not isinstance(node, dict) or not str(node.get("id") or ""):
                raise InvalidMutation("节点缺少 id")
            if self._contains_external_media_reference(node):
                raise InvalidMutation("项目节点只能引用项目媒体资源 ID")
            for asset_id in self._collect_asset_ids(node):
                try:
                    self.media_record(str(canvas.get("project_id") or ""), asset_id)
                except ProjectStoreError as exc:
                    raise InvalidMutation(f"项目媒体不存在: {asset_id}") from exc
            canvas["nodes"] = self._upsert(canvas.get("nodes") or [], node)
        elif operation == "remove_node":
            node_id = str(mutation.get("node_id") or "")
            if not node_id:
                raise InvalidMutation("缺少 node_id")
            canvas["nodes"] = [item for item in canvas.get("nodes") or [] if str(item.get("id")) != node_id]
            canvas["connections"] = [
                edge for edge in canvas.get("connections") or []
                if node_id not in self._edge_endpoints(edge)
            ]
        elif operation == "upsert_edge":
            edge = copy.deepcopy(mutation.get("edge"))
            if not isinstance(edge, dict) or not self._entity_id(edge):
                raise InvalidMutation("连线缺少稳定标识")
            canvas["connections"] = self._upsert(canvas.get("connections") or [], edge)
        elif operation == "remove_edge":
            edge_id = str(mutation.get("edge_id") or "")
            if not edge_id:
                raise InvalidMutation("缺少 edge_id")
            canvas["connections"] = [edge for edge in canvas.get("connections") or [] if self._entity_id(edge) != edge_id]
        elif operation == "set_viewport":
            viewport = mutation.get("viewport")
            if not isinstance(viewport, dict):
                raise InvalidMutation("viewport 必须是对象")
            # Kept as a compatibility no-op for stale clients. Viewport is
            # persisted by the browser per project, never by ProjectStore.
        elif operation == "set_canvas_meta":
            for key in ("title", "icon"):
                if key in mutation:
                    canvas[key] = copy.deepcopy(mutation[key])
        else:
            raise InvalidMutation(f"不支持的变更类型: {operation}")

    def _upsert(self, values: List[Dict[str, Any]], incoming: Dict[str, Any]) -> List[Dict[str, Any]]:
        incoming_id = self._entity_id(incoming)
        result = copy.deepcopy(values)
        for index, item in enumerate(result):
            if self._entity_id(item) == incoming_id:
                result[index] = incoming
                return result
        result.append(incoming)
        return result

    @staticmethod
    def _entity_id(value: Dict[str, Any]) -> str:
        explicit = str(value.get("id") or "")
        if explicit:
            return explicit
        source = value.get("from") or value.get("source") or value.get("fromNode") or value.get("sourceNodeId")
        target = value.get("to") or value.get("target") or value.get("toNode") or value.get("targetNodeId")
        if source and target:
            return f"{source}->{target}:{value.get('fromPort') or value.get('sourcePort') or ''}:{value.get('toPort') or value.get('targetPort') or ''}"
        return ""

    @staticmethod
    def _edge_endpoints(edge: Dict[str, Any]) -> set[str]:
        return {
            str(edge.get(key))
            for key in ("from", "to", "source", "target", "fromNode", "toNode", "sourceNodeId", "targetNodeId")
            if edge.get(key) is not None
        }

    @classmethod
    def _contains_external_media_reference(cls, value: Any) -> bool:
        if isinstance(value, list):
            return any(cls._contains_external_media_reference(item) for item in value)
        if not isinstance(value, dict):
            return False
        media_keys = {"image", "images", "video", "videos", "audio", "media", "outputs", "references", "input_images"}
        locator_keys = {"url", "src", "path", "output", "output_url", "outputurl", "uri"}
        for key, child in value.items():
            normalized_key = str(key).lower()
            looks_like_locator = normalized_key in locator_keys or normalized_key.endswith(("url", "uri", "path", "src"))
            if looks_like_locator and child not in (None, "", []):
                return True
            if normalized_key in media_keys and cls._invalid_project_media_value(child):
                return True
            if isinstance(child, (dict, list)) and cls._contains_external_media_reference(child):
                return True
        return False

    @classmethod
    def _invalid_project_media_value(cls, value: Any) -> bool:
        if isinstance(value, list):
            return any(cls._invalid_project_media_value(item) for item in value)
        if not isinstance(value, dict):
            return value not in (None, "")
        asset_id = value.get("asset_id")
        if not isinstance(asset_id, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,200}", asset_id.strip()):
            return True
        return any(
            key in value and value[key] not in (None, "", [])
            for key in (
                "url", "src", "path", "output", "output_url", "outputUrl", "uri", "href",
                "file", "local_path", "localPath", "remote_url", "remoteUrl", "preview_url", "previewUrl",
            )
        )

    @classmethod
    def _collect_asset_ids(cls, value: Any) -> set[str]:
        if isinstance(value, list):
            result: set[str] = set()
            for item in value:
                result.update(cls._collect_asset_ids(item))
            return result
        if not isinstance(value, dict):
            return set()
        result = set()
        asset_id = value.get("asset_id")
        if isinstance(asset_id, str) and asset_id.strip():
            result.add(asset_id.strip())
        for child in value.values():
            if isinstance(child, (dict, list)):
                result.update(cls._collect_asset_ids(child))
        return result
