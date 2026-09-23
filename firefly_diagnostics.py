"""Local structured diagnostic log for Firefly Canvas.

Writes one JSONL stream to ``<FIREFLY_RUNTIME_ROOT>/logs/diagnostics.jsonl``
and never touches the network: file I/O only.  When ``FIREFLY_RUNTIME_ROOT``
is unset the emitter is a no-op so embedded or legacy callers are unaffected.

Guarantees (issue #37 contract):

* Stable event names and levels for generation, provider, persistence, sync,
  deletion, recovery and cleanup.
* Every record carries the correlation fields the caller supplies (request,
  project, task, provider, model, state) plus a sanitised ``failure`` list
  that preserves the full exception chain (type + message per link).
* API keys, full prompts, media content and full local paths are redacted
  before anything is written.
* The active file rotates at 100 MB and archives older than seven days are
  purged on the next write; ``clear()`` removes everything.
* This module never uploads: it imports no HTTP client and performs local
  file I/O only.
"""

import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_MAX_BYTES = 100 * 1024 * 1024  # 100 MB rotation cap
DEFAULT_MAX_AGE_DAYS = 7
ACTIVE_NAME = "diagnostics.jsonl"
ARCHIVE_GLOB = "diagnostics.*.jsonl"

REDACTED = "<redacted>"
MAX_FIELD_LENGTH = 200
MAX_COLLECTION_ITEMS = 20

# Stable event name -> default level (issue #37 acceptance criterion 1).
STABLE_LEVELS: Dict[str, str] = {
    "generation.started": "info",
    "generation.succeeded": "info",
    "generation.failed": "error",
    "provider.request": "info",
    "provider.response": "info",
    "provider.failed": "error",
    "persistence.saved": "debug",
    "sync.applied": "info",
    "deletion.completed": "info",
    "recovery.restored": "info",
    "cleanup.executed": "info",
}

# Correlation keys that pass through untouched; everything else is sanitised.
SAFE_CORRELATION_KEYS = {
    "event",
    "level",
    "ts",
    "request_id",
    "project_id",
    "task_id",
    "provider_id",
    "model",
    "state",
    "node_id",
    "asset_id",
    "history_id",
    "deletion_id",
    "media_kind",
    "revision",
    "ops",
    "merged",
    "deleted_count",
    "failed_count",
    "deleted_bytes",
    "duration_ms",
    "attempt",
}

# Keys that are always redacted or replaced, whatever the value shape.
SENSITIVE_KEYS = {
    "prompt",
    "prompts",
    "api_key",
    "apikey",
    "key",
    "token",
    "authorization",
    "password",
    "secret",
    "content",
    "media",
    "image",
    "images",
    "video",
    "videos",
    "audio",
    "data",
    "bytes",
    "payload",
    "request_body",
    "response_body",
    "request_payload",
    "file",
    "path",
    "local_path",
    "output",
    "url",
    "urls",
}

_API_KEY_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._~+/=\-]{8,}"),
]
_CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_-]?key|wallet[_-]?api[_-]?key|access[_-]?key|"
    r"secret[_-]?access[_-]?key|key|access[_-]?token|refresh[_-]?token|"
    r"authorization|password|secret)\b(\s*[:=]\s*)"
    r"(?:['\"][^'\"]*['\"]|[^\s,;]+)"
)
_WINDOWS_DRIVE_PATH = re.compile(r"[A-Za-z]:[\\/][^\s'\"]*")
_POSIX_ABS_PATH = re.compile(r"(?<![\w./\\])(?:/|\\\\)(?:[^/\s'\"]+[/\\]){2}[^\s'\"]*")

_lock = threading.Lock()


def _runtime_root() -> Optional[Path]:
    value = os.environ.get("FIREFLY_RUNTIME_ROOT", "").strip()
    return Path(value) if value else None


def log_dir() -> Optional[Path]:
    """Directory holding diagnostic streams, or None when logging is off."""
    root = _runtime_root()
    return root / "logs" if root else None


def enabled() -> bool:
    return log_dir() is not None


def _prompt_hash(prompt: str) -> str:
    digest = hashlib.sha256(str(prompt).encode("utf-8", "replace")).hexdigest()[:16]
    return f"prompt_sha256:{digest}"


def _redact_text(text: str, sensitive_values: Any = ()) -> str:
    values = sensitive_values if isinstance(sensitive_values, (list, tuple, set)) else (sensitive_values,)
    for value in sorted(
        {str(item) for item in values if item is not None and len(str(item)) >= 4},
        key=len,
        reverse=True,
    ):
        text = text.replace(value, REDACTED)
    for pattern in _API_KEY_PATTERNS:
        text = pattern.sub(REDACTED, text)
    text = _CREDENTIAL_ASSIGNMENT.sub(
        lambda match: f"{match.group(1)}{match.group(2)}{REDACTED}",
        text,
    )

    def _basename(match: "re.Match[str]") -> str:
        candidate = match.group(0).replace("\\", "/").rstrip("/")
        return Path(candidate).name or REDACTED

    # Full local paths never enter the log: keep only the basename.
    text = _WINDOWS_DRIVE_PATH.sub(_basename, text)
    text = _POSIX_ABS_PATH.sub(_basename, text)
    if len(text) > MAX_FIELD_LENGTH:
        text = text[:MAX_FIELD_LENGTH] + "…"
    return text


def sanitize_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, (bytes, bytearray)):
        return REDACTED  # media content never enters the log
    if isinstance(value, (list, tuple)):
        return [sanitize_value(item) for item in list(value)[:MAX_COLLECTION_ITEMS]]
    if isinstance(value, dict):
        return {
            str(key)[:100]: sanitize_value(item)
            for key, item in list(value.items())[:MAX_COLLECTION_ITEMS]
        }
    return _redact_text(str(value))


def sanitize_field(key: str, value: Any) -> Any:
    lower = str(key).lower()
    if lower in SENSITIVE_KEYS:
        if lower == "prompt" and isinstance(value, str):
            return _prompt_hash(value)
        return REDACTED
    if str(key) not in SAFE_CORRELATION_KEYS:
        return sanitize_value(value)
    if isinstance(value, (bytes, bytearray, list, tuple, dict)):
        return sanitize_value(value)
    if isinstance(value, (bool, int, float)):
        return value  # scalars keep their JSON type; only strings are redacted
    return _redact_text(str(value))


def exception_chain(
    exc: BaseException,
    sensitive_values: Any = (),
) -> List[Dict[str, str]]:
    """Collapse an exception chain into redacted type/message pairs."""
    chain: List[Dict[str, str]] = []
    seen = set()
    current: Optional[BaseException] = exc
    while current is not None and len(chain) < 8:
        marker = id(current)
        if marker in seen:
            break
        seen.add(marker)
        raw = getattr(current, "message", None) or str(current)
        chain.append({
            "type": type(current).__name__,
            "message": _redact_text(str(raw), sensitive_values),
        })
        current = current.__cause__ or current.__context__
    return chain


def emit(event_name: str, level: Optional[str] = None, **fields: Any) -> bool:
    """Append one sanitised diagnostic record; returns False when disabled."""
    if event_name not in STABLE_LEVELS:
        raise ValueError(f"unstable diagnostic event name: {event_name}")
    directory = log_dir()
    if directory is None:
        return False
    record: Dict[str, Any] = {
        "ts": int(time.time() * 1000),
        "event": event_name,
        "level": level or STABLE_LEVELS[event_name],
    }
    exception = fields.pop("exception", None) or fields.pop("exc", None)
    sensitive_values = fields.pop("sensitive_values", ())
    if exception is not None:
        record["failure"] = exception_chain(exception, sensitive_values)
    for key, value in fields.items():
        if value is None or key in {"exception", "exc"}:
            continue
        record[key] = sanitize_field(key, value)
    with _lock:
        directory.mkdir(parents=True, exist_ok=True)
        _rotate(directory)
        with open(directory / ACTIVE_NAME, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return True


def _rotate(directory: Path) -> None:
    """Purge archives older than the age cap, then rotate at the size cap."""
    now = time.time()
    for path in directory.glob(ARCHIVE_GLOB):
        try:
            if now - path.stat().st_mtime > DEFAULT_MAX_AGE_DAYS * 86400:
                path.unlink(missing_ok=True)
        except OSError:
            continue
    active = directory / ACTIVE_NAME
    try:
        if active.stat().st_size >= DEFAULT_MAX_BYTES:
            stamp = time.strftime("%Y%m%d%H%M%S", time.localtime())
            archive = directory / f"diagnostics.{stamp}.jsonl"
            counter = 1
            while archive.exists():
                archive = directory / f"diagnostics.{stamp}.{counter}.jsonl"
                counter += 1
            active.replace(archive)
    except OSError:
        pass


def clear() -> None:
    """Explicitly delete every diagnostic stream in the runtime log dir."""
    directory = log_dir()
    if directory is None:
        return
    with _lock:
        for path in directory.glob(ARCHIVE_GLOB):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                continue
        try:
            (directory / ACTIVE_NAME).unlink(missing_ok=True)
        except OSError:
            pass


def read_events(directory: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Parse the active stream plus archives in chronological order."""
    root = directory or log_dir()
    events: List[Dict[str, Any]] = []
    if root is None:
        return events
    paths = sorted(
        list(root.glob(ARCHIVE_GLOB)) + ([root / ACTIVE_NAME] if (root / ACTIVE_NAME).exists() else []),
        key=lambda path: (path.name != ACTIVE_NAME, path.name),
    )
    for path in paths:
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                events.append(json.loads(line))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    return events
