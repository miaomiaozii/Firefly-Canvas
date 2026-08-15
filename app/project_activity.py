"""Project-scoped admission and exclusive cleanup coordination."""
from __future__ import annotations

from contextlib import contextmanager
from threading import Lock, RLock
from typing import Dict, Iterator


PROJECT_LIFECYCLE_LOCKS: Dict[str, Lock] = {}
PROJECT_LIFECYCLE_LOCKS_GUARD = Lock()
_ACTIVITY_LOCKS: Dict[str, RLock] = {}
_ACTIVITY_LOCKS_GUARD = Lock()
_CLEANUP_PROJECTS: set[str] = set()
_ACTIVE_DOWNLOADS: Dict[str, int] = {}


class ProjectCleanupActive(RuntimeError):
    """Raised when a new runtime owner races with permanent cleanup."""


def project_lifecycle_lock(project_id: str) -> Lock:
    clean_project_id = str(project_id or "").strip()
    with PROJECT_LIFECYCLE_LOCKS_GUARD:
        return PROJECT_LIFECYCLE_LOCKS.setdefault(clean_project_id, Lock())


def _project_activity_lock(project_id: str) -> RLock:
    with _ACTIVITY_LOCKS_GUARD:
        return _ACTIVITY_LOCKS.setdefault(project_id, RLock())


@contextmanager
def project_cleanup_lease(project_id: str) -> Iterator[None]:
    """Exclude lifecycle mutations and reject new owners during cleanup."""
    clean_project_id = str(project_id or "").strip()
    lifecycle_lock = project_lifecycle_lock(clean_project_id)
    activity_lock = _project_activity_lock(clean_project_id)
    with lifecycle_lock:
        with activity_lock:
            if clean_project_id in _CLEANUP_PROJECTS:
                raise ProjectCleanupActive("项目正在执行永久清理")
            _CLEANUP_PROJECTS.add(clean_project_id)
        try:
            yield
        finally:
            with activity_lock:
                _CLEANUP_PROJECTS.discard(clean_project_id)


def _raise_if_cleanup_active(project_id: str) -> None:
    if project_id in _CLEANUP_PROJECTS:
        raise ProjectCleanupActive("项目正在执行永久清理，暂不能开始新的项目活动")


def assert_project_owner_admissible(project_id: str) -> None:
    clean_project_id = str(project_id or "").strip()
    with _project_activity_lock(clean_project_id):
        _raise_if_cleanup_active(clean_project_id)


@contextmanager
def project_owner_admission(project_id: str) -> Iterator[None]:
    """Atomically reject owner registration while cleanup owns the project."""
    clean_project_id = str(project_id or "").strip()
    with _project_activity_lock(clean_project_id):
        _raise_if_cleanup_active(clean_project_id)
        yield


def begin_project_download(project_id: str) -> None:
    clean_project_id = str(project_id or "").strip()
    with project_owner_admission(clean_project_id):
        _ACTIVE_DOWNLOADS[clean_project_id] = int(
            _ACTIVE_DOWNLOADS.get(clean_project_id, 0)
        ) + 1


def finish_project_download(project_id: str) -> None:
    clean_project_id = str(project_id or "").strip()
    with _project_activity_lock(clean_project_id):
        remaining = int(_ACTIVE_DOWNLOADS.get(clean_project_id, 0)) - 1
        if remaining > 0:
            _ACTIVE_DOWNLOADS[clean_project_id] = remaining
        else:
            _ACTIVE_DOWNLOADS.pop(clean_project_id, None)


def project_has_active_download(project_id: str) -> bool:
    clean_project_id = str(project_id or "").strip()
    with _project_activity_lock(clean_project_id):
        return int(_ACTIVE_DOWNLOADS.get(clean_project_id, 0)) > 0
