"""#488: freeze startup source identity before importing backend dependencies."""
from pathlib import Path

from app.backend_source import source_digest

_REPO_ROOT = Path(__file__).resolve().parents[1]

# The refresh tool uses the pure digest module, so failed checks still allow cleanup.
# main imports this snapshot before backend dependencies, never first from a request.
PROCESS_BACKEND_ID = source_digest(_REPO_ROOT)


def backend_version_info() -> dict:
    try:
        current = source_digest(_REPO_ROOT)
    except OSError:
        # Public diagnostics must not disclose private paths or claim a match.
        current = None
    return {
        "schema": 1,
        "loaded": PROCESS_BACKEND_ID,
        "source": current,
        "restart_required": current != PROCESS_BACKEND_ID if current is not None else None,
    }
