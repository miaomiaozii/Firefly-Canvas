"""Generation lifecycle undo invariants with real store and journal writes.

These tests exercise the undo boundary, not the generation tool integration.
"""
from copy import deepcopy

import pytest

from app.services import agent_ops_delta, agent_ops_store
from app.services.agent_ops_journal import AgentOpsJournal, record_sides
from app.services.agent_ops_undo import undo_ops
from firefly_opsdb import OpsDb
from firefly_projects import ProjectStore


@pytest.fixture(params=["failure", "completion"])
def lifecycle(tmp_path, request):
    store = ProjectStore(tmp_path / "projects", snapshot_delay=0)
    project_id = store.create_project("generation undo")['id']
    db = OpsDb(store.root)
    journal = AgentOpsJournal(store, db)

    def record(op_id, parent, action):
        with journal.mutation(project_id) as operation:
            before = store.canvas(project_id)
            action()
            delta = agent_ops_delta.build_delta(before, store.canvas(project_id))
            old, new = record_sides(delta)
            operation.register({
                "op_id": op_id, "parent_op_id": parent, "turn_id": "turn",
                "agent_node_id": "agent", "tool": "generate.trigger", "args": {},
                "before": old, "after": new,
                "touched_node_ids": agent_ops_delta.touched_node_ids(delta),
                "created_at": "2026-01-01", "irreversible_effects": [], "undone": False,
            }, delta)
        store.flush(project_id)
        assert operation.result()["status"] == "settled"

    record("root", None, lambda: store.apply_mutations(
        project_id, [{"op": "upsert_node", "node": {"id": "result", "type": "smart-image"}}],
        base_revision=store.canvas(project_id)["revision"]))
    record("begin", "root", lambda: store.begin_image_generation(
        project_id, "result", {"taskId": "task", "count": 1},
        base_revision=store.canvas(project_id)["revision"]))
    terminal = request.param
    if terminal == "failure":
        record(terminal, "root", lambda: store.fail_image_generation(
            project_id, "result", "task", {"id": "history", "type": "online-image", "status": "failed", "outputs": []}))
    else:
        media = store.import_media(project_id, b"image", "image.png", "image/png")
        record(terminal, "root", lambda: store.complete_image_generation(
            project_id, "result", [{"asset_id": media["asset_id"], "kind": "image"}],
            {"id": "history", "type": "online-image", "status": "succeeded"},
            task_id="task", require_pending=True))
    try:
        yield store, project_id, db, terminal
    finally:
        journal.close()
        store.close()


def test_terminal_child_conflicts_without_resurrecting_server_owned_task(lifecycle):
    store, project_id, db, terminal = lifecycle
    canvas = deepcopy(store.canvas(project_id))
    records = agent_ops_store.list_operations(db, project_id)
    result = undo_ops(store, project_id, op_id=terminal)
    assert result["status"] == "conflict"
    assert store.canvas(project_id) == canvas
    assert agent_ops_store.list_operations(db, project_id) == records


def test_root_undo_reverses_entire_tree(lifecycle):
    store, project_id, db, terminal = lifecycle
    result = undo_ops(store, project_id, op_id="root")
    assert result["status"] == "reverted"
    assert result["reverted"] == [terminal, "begin", "root"]
    assert store.canvas(project_id)["nodes"] == []
    assert all(record["undone"] for record in agent_ops_store.list_operations(db, project_id))


def test_root_undo_rejects_user_edits_without_mutation(lifecycle):
    store, project_id, db, _ = lifecycle
    canvas = store.canvas(project_id)
    node = deepcopy(canvas["nodes"][0])
    node["title"] = "user edit"
    store.apply_mutations(project_id, [{"op": "upsert_node", "node": node}],
                          base_revision=canvas["revision"])
    store.flush(project_id)
    before = deepcopy(store.canvas(project_id))
    records = agent_ops_store.list_operations(db, project_id)
    result = undo_ops(store, project_id, op_id="root")
    assert result["status"] == "conflict"
    assert store.canvas(project_id) == before
    assert agent_ops_store.list_operations(db, project_id) == records
