import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

import main
from app.routers import api_agent_ops, api_agent_tools
from app.services import agent_ops_delta, agent_ops_store
from app.services.agent_ops_journal import record_sides
from firefly_opsdb import OpsDb
from firefly_projects import ProjectStore


@pytest.fixture
def seeded(tmp_path, monkeypatch):
    store = ProjectStore(tmp_path / "projects", snapshot_delay=0)
    monkeypatch.setattr(main, "FIREFLY_PROJECT_STORE", store)
    project_id = store.create_project("running-guard")['id']
    db = OpsDb(store.root)
    for op_id, parent, node in (
        ("root", None, {"id": "n", "type": "text", "v": 1}),
        ("child", "root", {"id": "n", "type": "text", "v": 2}),
        ("other", None, {"id": "other", "type": "text"}),
    ):
        before = store.canvas(project_id)
        store.apply_mutations(project_id, [{"op": "upsert_node", "node": node}],
                              base_revision=before["revision"])
        delta = agent_ops_delta.build_delta(before, store.canvas(project_id))
        old, new = record_sides(delta)
        agent_ops_store.append_operation(db, project_id, {
            "op_id": op_id, "parent_op_id": parent, "turn_id": "turn",
            "agent_node_id": "agent", "tool": "test", "args": {},
            "before": old, "after": new, "touched_node_ids": agent_ops_delta.touched_node_ids(delta),
            "created_at": "2026-01-01", "irreversible_effects": [], "undone": False,
        })
    store.flush(project_id)
    try:
        yield store, project_id, db
    finally:
        store.close()


@pytest.mark.parametrize(("selector", "running", "reverted", "remaining"), [
    ({"op_id": "root"}, "root", ["child", "root"], {"other"}),
    ({"op_id": "root"}, "child", ["child", "root"], {"other"}),
    ({"op_id": "child"}, "child", ["child"], {"n", "other"}),
    ({"turn_id": "turn"}, "root", ["other", "child", "root"], set()),
    ({"turn_id": "turn"}, "child", ["other", "child", "root"], set()),
])
def test_running_operation_blocks_entire_undo_then_release_succeeds(seeded, selector, running, reverted, remaining):
    store, project_id, db = seeded
    canvas_path = store.root / project_id / "canvas.json"
    canvas_before = store.canvas(project_id)
    disk_before = json.loads(canvas_path.read_text(encoding="utf-8"))
    records_before = agent_ops_store.list_operations(db, project_id)
    api_agent_tools.set_agent_generation_running(store, project_id, running, "task", True)
    try:
        with patch.object(main.manager, "broadcast_canvas_updated", new_callable=AsyncMock) as broadcast:
            with patch.object(agent_ops_delta, "apply_inverse", wraps=agent_ops_delta.apply_inverse) as inverse:
                with pytest.raises(HTTPException) as exc:
                    asyncio.run(api_agent_ops.undo_ops_core(project_id, selector))
                assert exc.value.status_code == 409
                assert exc.value.detail["code"] == "CONFLICT"
                assert exc.value.detail["retryable"] is True
                details = exc.value.detail["details"]
                assert details["status"] == "generating"
                assert details["running"] == [running]
                assert details["reverted"] == []
                inverse.assert_not_called()
            assert store.canvas(project_id) == canvas_before
            assert json.loads(canvas_path.read_text(encoding="utf-8")) == disk_before
            assert agent_ops_store.list_operations(db, project_id) == records_before
            broadcast.assert_not_awaited()

            api_agent_tools.set_agent_generation_running(store, project_id, running, "task", False)
            result = asyncio.run(api_agent_ops.undo_ops_core(project_id, selector))
            assert result["status"] == "reverted"
            assert result["reverted"] == reverted
            assert {node["id"] for node in store.canvas(project_id)["nodes"]} == remaining
            assert json.loads(canvas_path.read_text(encoding="utf-8"))["nodes"] == store.canvas(project_id)["nodes"]
            assert {r["op_id"] for r in agent_ops_store.list_operations(db, project_id) if r["undone"]} == set(reverted)
            broadcast.assert_awaited_once()
    finally:
        api_agent_tools.set_agent_generation_running(store, project_id, running, "task", False)
