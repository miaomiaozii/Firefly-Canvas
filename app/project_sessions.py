"""Process-local ownership registry for open project canvas sessions."""
from __future__ import annotations

import threading
from typing import Dict

from app.project_activity import project_owner_admission


_lock = threading.Lock()
_sessions: Dict[int, str] = {}


def register_project_session(token: object, project_id: str) -> None:
    """Associate one live transport token with exactly one project."""
    clean_project_id = str(project_id or "").strip()
    if not clean_project_id:
        return
    with project_owner_admission(clean_project_id):
        with _lock:
            _sessions[id(token)] = clean_project_id


def unregister_project_session(token: object) -> None:
    """Release a transport token; repeated release is harmless."""
    with _lock:
        _sessions.pop(id(token), None)


def project_has_open_session(project_id: str) -> bool:
    """Return whether any registered canvas transport owns this project."""
    clean_project_id = str(project_id or "").strip()
    with _lock:
        return any(owner == clean_project_id for owner in _sessions.values())
