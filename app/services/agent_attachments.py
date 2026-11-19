"""Project-local, streamed attachments for an Agent input (#403)."""
from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path
from urllib.parse import unquote

from fastapi import HTTPException, Request

from app.services.agent_host import InvalidEventsCursor, validate_events_identifiers

# Server-owned per-file limit, in bytes. Browsers use the 413 response as authority.
AGENT_ATTACHMENT_MAX_BYTES = int(os.environ.get("FIREFLY_AGENT_ATTACHMENT_MAX_BYTES", str(64 * 1024 * 1024)))


def attachment_root(project_root: Path, project_id: str, node_id: str) -> Path:
    try:
        validate_events_identifiers(project_id, node_id)
    except InvalidEventsCursor as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    project = (project_root / project_id).resolve()
    root = (project / "agent_nodes" / node_id / "sessions" / "attachments").resolve()
    if not root.is_relative_to(project):
        raise HTTPException(status_code=400, detail="Invalid attachment directory")
    return root


async def save_attachment(request: Request, root: Path) -> dict:
    raw_name = unquote(request.headers.get("x-attachment-name", ""))
    name = raw_name.replace("\\", "/").split("/")[-1]
    if not name or len(name) > 255 or any(ord(c) < 32 for c in name):
        raise HTTPException(status_code=400, detail="Invalid attachment name")
    mime = request.headers.get("content-type", "application/octet-stream").split(";", 1)[0]
    suffix = Path(name).suffix
    suffix = suffix if re.fullmatch(r"\.[A-Za-z0-9]{1,16}", suffix) else ""
    attachment_id = uuid.uuid4().hex
    root.mkdir(parents=True, exist_ok=True)
    folder = root / attachment_id
    folder.mkdir()
    content = folder / ("content" + suffix)
    size = 0
    try:
        with content.open("xb") as file:
            async for chunk in request.stream():
                size += len(chunk)
                if size > AGENT_ATTACHMENT_MAX_BYTES:
                    raise HTTPException(status_code=413, detail={
                        "code": "attachment_too_large", "max_bytes": AGENT_ATTACHMENT_MAX_BYTES,
                    })
                file.write(chunk)
        record = {"id": attachment_id, "name": name, "type": mime, "size": size, "file": content.name}
        (folder / "metadata.json").write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        return {key: value for key, value in record.items() if key != "file"} | {"path": str(content)}
    except BaseException:
        content.unlink(missing_ok=True)
        (folder / "metadata.json").unlink(missing_ok=True)
        folder.rmdir()
        raise


def resolve_attachments(root: Path, refs: object) -> list[dict]:
    if not isinstance(refs, list) or len(refs) > 20:
        raise HTTPException(status_code=400, detail="attachments must be an array of at most 20 IDs")
    result = []
    for item in refs:
        if not isinstance(item, str) or not re.fullmatch(r"[0-9a-f]{32}", item):
            raise HTTPException(status_code=400, detail="Invalid attachment ID")
        folder = root / item
        metadata = folder / "metadata.json"
        if not folder.resolve().is_relative_to(root.resolve()) or not metadata.is_file():
            raise HTTPException(status_code=404, detail="Attachment not found")
        record = json.loads(metadata.read_text(encoding="utf-8"))
        path = (folder / record["file"]).resolve()
        if path.parent != folder.resolve() or not path.is_file():
            raise HTTPException(status_code=404, detail="Attachment not found")
        result.append({key: value for key, value in record.items() if key != "file"} | {"path": str(path)})
    return result
