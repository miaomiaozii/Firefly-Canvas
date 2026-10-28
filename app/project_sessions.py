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


def open_project_ids() -> list:
    """Sorted unique project ids that have at least one open canvas transport.

    #243: feeds the MCP three-level arbitration (app/routers/api_mcp.py).
    """
    with _lock:
        return sorted({owner for owner in _sessions.values()})


def project_session_tokens(project_id: str) -> list:
    """Live transport tokens registered for one project (registry iteration).

    #243: lets the MCP endpoint resolve the owning tab's client_id for
    targeted command dispatch (ADR 0022 单 client 定向下发).
    """
    clean_project_id = str(project_id or "").strip()
    with _lock:
        return [token for token, owner in _sessions.items() if owner == clean_project_id]


def session_project(token: object) -> str:
    """The project owned by this transport token ("" when unregistered).

    The registry is keyed by ``id(token)``; this helper is the object-keyed
    lookup so callers holding the live transport object (e.g. a ConnectionManager
    connection) can resolve its project without reconstructing ids.
    """
    with _lock:
        return _sessions.get(id(token), "")
