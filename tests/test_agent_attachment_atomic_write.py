import asyncio
import json
from pathlib import Path

import pytest
from starlette.requests import Request

import firefly_json_io
from app.services.agent_attachments import resolve_attachments, save_attachment


@pytest.mark.parametrize("replace_fails", [False, True])
def test_attachment_metadata_becomes_visible_only_at_atomic_replace(tmp_path, monkeypatch, replace_fails):
    async def receive():
        return {"type": "http.request", "body": b"attachment body", "more_body": False}

    request = Request({"type": "http", "headers": [(b"x-attachment-name", b"a.txt")]}, receive)
    root = tmp_path / "attachments"
    original_replace = firefly_json_io.os.replace
    attempted = []

    def inspect_replace(source, destination):
        destination = Path(destination)
        if destination.name != "metadata.json":
            return original_replace(source, destination)
        source = Path(source)
        assert source != destination and source.parent == destination.parent
        assert not destination.exists()
        record = json.loads(source.read_text(encoding="utf-8"))
        assert record["name"] == "a.txt"
        assert record["size"] == len(b"attachment body")
        assert (destination.parent / record["file"]).read_bytes() == b"attachment body"
        attempted.append(destination)
        if replace_fails:
            raise OSError("simulated replace failure")
        original_replace(source, destination)

    monkeypatch.setattr(firefly_json_io.os, "replace", inspect_replace)
    if replace_fails:
        with pytest.raises(OSError, match="simulated replace failure"):
            asyncio.run(save_attachment(request, root))
        assert list(root.iterdir()) == []
    else:
        saved = asyncio.run(save_attachment(request, root))
        assert resolve_attachments(root, [saved["id"]]) == [saved]
        assert {p.name for p in (root / saved["id"]).iterdir()} == {"content.txt", "metadata.json"}
    assert len(attempted) == 1
