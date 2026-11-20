import asyncio
import json
from itertools import combinations
from urllib.parse import urlencode

import pytest
from starlette.requests import Request

import main
from app.routers import api_mcp
from app.services.agent_ops_undo import undo_ops
from firefly_projects import ProjectStore


@pytest.fixture
def project(tmp_path, monkeypatch):
    store = ProjectStore(tmp_path / "projects", snapshot_delay=0)
    project_id = store.create_project("placement")['id']
    store.apply_mutations(project_id, [{"op": "upsert_node", "node": {
        "id": "actor", "type": "smart-agent", "title": "Codex", "x": 480, "y": 100,
    }}], base_revision=0)
    monkeypatch.setattr(main, "FIREFLY_PROJECT_STORE", store)
    # The browser test covers registration of an open canvas tab.
    monkeypatch.setattr(api_mcp, "resolve_project", lambda project: project)
    try:
        yield store, project_id
    finally:
        store.close()


async def call_tool(project_id, name, args, **query):
    request = Request({"type": "http", "method": "POST", "path": "/api/mcp", "headers": [],
                       "query_string": urlencode({"project": project_id, **query}).encode()})
    reply = json.loads((await api_mcp._tools_call(
        {"params": {"name": name, "arguments": args}}, 1, "placement-session", request)).body)
    result = reply["result"]
    assert not result["isError"], result
    return json.loads(result["content"][0]["text"])


def overlaps(a, b):
    return (a["x"] < b["x"] + b["w"] and b["x"] < a["x"] + a["w"]
            and a["y"] < b["y"] + b["h"] and b["y"] < a["y"] + a["h"])


def test_five_codex_mcp_creates_land_without_overlap_and_undo_default_frame(project):
    store, project_id = project

    async def create_five():
        return [await call_tool(project_id, "canvas.create_node", {"type": kind}, node="actor")
                for kind in ("smart-prompt", "smart-image", "smart-image", "smart-conversation", "smart-prompt")]

    results = asyncio.run(create_five())
    nodes = {n["id"]: n for n in store.canvas(project_id)["nodes"]}
    positions = [(nodes[r["node_id"]]["x"], nodes[r["node_id"]]["y"]) for r in results]
    assert len(set(positions)) == 5, positions
    assert (0, 0) not in positions
    for r in results:
        assert {key: r[key] for key in ("x", "y", "w", "h")} == {
            key: nodes[r["node_id"]][key] for key in ("x", "y", "w", "h")}
        assert r["w"] > 0 and r["h"] > 0
    # A grouped empty image is visually taller than its 136px square layout.
    # Reserve the 178px canvas card, not just the image box.
    assert [(r["w"], r["h"]) for r in results[1:3]] == [(136, 178), (136, 178)]
    assert all(not overlaps(a, b) for a, b in combinations(results, 2))
    actor = nodes["actor"]
    frame = nodes[actor["chatFrameId"]]
    assert frame["type"] == "smart-group"
    assert frame["chatFrameFor"] == "actor"
    assert frame["items"] == [r["node_id"] for r in results]
    for r in results:
        assert frame["x"] < r["x"] < r["x"] + r["w"] < frame["x"] + frame["w"]
        assert frame["y"] < r["y"] < r["y"] + r["h"] < frame["y"] + frame["h"]
    for r in reversed(results):
        assert undo_ops(store, project_id, op_id=r["op_id"])["status"] == "reverted"
    assert [n["id"] for n in store.canvas(project_id)["nodes"]] == ["actor"]
    assert "chatFrameId" not in store.find_canvas_node(project_id, "actor")
