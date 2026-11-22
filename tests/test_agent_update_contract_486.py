"""Raw ownership, no-op and exact-boundary update contracts through real storage (#486)."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from queue import Queue
from threading import Event

from fastapi import HTTPException
import pytest

import main
from app.routers import api_agent_tools as tools
from app.services import agent_ops_store
from app.services.agent_ops_context import agent_ops_scope
from app.services.agent_ops_undo import undo_ops
from tests.test_agent_plan_399 import env
from tests.test_agent_update_ownership_486 import update_http_486
from tests.test_agent_update_transports_486 import (
    invoke,
    prepare_carrier,
    result,
    update_transport_state_486 as state,
)


FIELDS = (("title", "Reviewed"), ("text", "Reviewed text"), ("prompt", "Reviewed prompt"),
          ("x", 17), ("y", 21), ("w", 340), ("h", 220), ("status", "ready"))


@pytest.mark.parametrize("transport", ["REST", "MCP-flat", "MCP-legacy"])
@pytest.mark.parametrize("key,value", FIELDS)
def test_every_ordinary_field_requires_one_approval_and_preserves_raw_metadata(
        update_http_486, transport, key, value):
    http = update_http_486
    before = state(http)
    receipt = result(invoke(http, transport, {key: value}), transport)
    assert receipt["code"] == "CONFIRM_PENDING"
    event = http.events.get(timeout=5)
    assert event["params"]["patch"] == {key: value}
    assert event["params"]["before"] == {key: {"present": True, "value": before["node"][key]}
                                        if key in before["node"] else {"present": False}}
    assert state(http) == before
    http.confirm(event, True)
    http.settle()
    after = http.node()
    assert after == {**before["node"], key: value, "updated_at": after["updated_at"]}
    assert len(http.operations()) == 1 and not http.session.confirm_handlers


@pytest.mark.parametrize("transport", ["REST", "MCP-flat", "MCP-legacy", "MCP-hosted", "MCP-external-SID"])
@pytest.mark.parametrize("protection", [
    {}, {"createdBy": "agent", "agentNodeId": "other", "userTouchedAt": None, "promptEditedByUser": False},
    {"createdBy": "agent", "agentNodeId": "actor", "userTouchedAt": 0, "promptEditedByUser": False},
    {"createdBy": "agent", "agentNodeId": "actor", "userTouchedAt": False, "promptEditedByUser": False},
    {"createdBy": "agent", "agentNodeId": "actor", "userTouchedAt": None, "promptEditedByUser": 0},
    {"createdBy": "agent", "agentNodeId": "actor", "userTouchedAt": None, "promptEditedByUser": "false"},
    {"createdBy": "agent", "agentNodeId": "actor", "userTouchedAt": None, "promptEditedByUser": True},
    {"createdBy": "agent", "agentNodeId": "actor", "userTouchedAt": None, "promptEditedByUser": False, "locked": True},
], ids=["human", "another-agent", "touch-zero", "touch-false", "edited-zero", "edited-string", "edited-true", "locked"])
def test_raw_protection_is_not_laundered_by_projected_defaults(update_http_486, protection, transport):
    http = update_http_486
    http.human_change(protection)
    prepare_carrier(http, transport)
    before = state(http)
    receipt = result(invoke(http, transport, {"text": "Explicitly reviewed"}), transport)
    assert receipt["code"] == "CONFIRM_PENDING", receipt
    event = http.events.get(timeout=5)
    assert state(http) == before
    http.confirm(event, True)
    http.settle()
    after = http.node()
    assert after == {**before["node"], "text": "Explicitly reviewed", "updated_at": after["updated_at"]}
    assert len(http.operations()) == 1


@pytest.mark.parametrize("transport", ["REST", "MCP-flat", "MCP-legacy"])
@pytest.mark.parametrize("owned", [False, True], ids=["protected", "owned"])
@pytest.mark.parametrize("patch", [{}, {"text": "Keep the human-authored wording"}, {"runSettings": {}},
                                  {"text": "Keep the human-authored wording", "runSettings": {}}])
def test_true_noop_changes_neither_node_revision_journal_nor_broadcast(
        update_http_486, monkeypatch, owned, patch, transport):
    http = update_http_486
    if owned:
        http.human_change({"createdBy": "agent", "agentNodeId": "actor",
                           "userTouchedAt": None, "promptEditedByUser": False, "locked": False})
    before = state(http)
    phases = []
    original = main.manager.broadcast_canvas_updated

    async def observe(*args, **kwargs):
        phases.append(deepcopy(kwargs))
        return await original(*args, **kwargs)

    monkeypatch.setattr(main.manager, "broadcast_canvas_updated", observe)
    response = result(invoke(http, transport, patch), transport)
    assert response["success"] is True, response
    http.settle()
    assert state(http) == before and http.events.empty()
    assert phases == [] and all(task.done() for task in http.tasks) and not http.session.confirm_handlers


@pytest.mark.parametrize("transport", ["REST", "MCP-flat", "MCP-legacy"])
@pytest.mark.parametrize("present,initial,value,pending", [
    (False, None, None, True), (True, None, None, False), (True, False, False, False),
    (True, False, None, True), (True, 0, False, True),
])
def test_key_presence_and_json_scalar_identity_survive_undo(
        update_http_486, present, initial, value, pending, transport):
    http = update_http_486
    if present:
        http.human_change({"status": initial})
    before = state(http)
    response = result(invoke(http, transport, {"status": value}), transport)
    if not pending:
        assert response["success"] is True
        assert state(http) == before and http.events.empty()
        return
    assert response["code"] == "CONFIRM_PENDING"
    event = http.events.get(timeout=5)
    assert event["params"]["before"] == {"status": {"present": True, "value": initial} if present else {"present": False}}
    http.confirm(event, True)
    http.settle()
    after = http.node()
    assert "status" in after and type(after["status"]) is type(value) and after["status"] == value
    operation, = http.operations()
    undo = http.client.post(f"/api/projects/{http.project}/ops/undo", headers={"X-Agent-Project": http.project},
                            json={"op_id": operation["op_id"]})
    assert undo.status_code == 200, undo.text
    http.store.flush(http.project)
    disk = json.loads((http.store.root / http.project / "canvas.json").read_text(encoding="utf-8"))
    restored = next(node for node in disk["nodes"] if node["id"] == "human-prompt")
    assert http.node() == restored == before["node"]


@pytest.mark.parametrize("patch", [
    {"title": "not partly written", "createdBy": "agent"},
    {"title": "not partly written", "agentNodeId": "actor"},
    {"title": "not partly written", "source": "agent"},
    {"title": "not partly written", "userTouchedAt": None},
    {"title": "not partly written", "promptEditedByUser": False},
    {"title": "not partly written", "x": True},
    {"title": "not partly written", "w": "240"},
    {"title": "not partly written", "runSettings": {"count": 0}},
    {"title": "not partly written", "runSettings": {"unknown": 1}},
])
@pytest.mark.parametrize("transport", ["REST", "MCP-flat", "MCP-legacy"])
def test_invalid_mixed_patch_never_confirms_or_partially_writes(update_http_486, patch, transport):
    http = update_http_486
    before = state(http)
    http.store.flush(http.project)
    path = http.store.root / http.project / "canvas.json"
    before_disk = path.read_bytes()
    response = invoke(http, transport, patch)
    if transport == "REST":
        assert response.status_code == 400, response.text
        assert response.json()["detail"]["code"] == "INVALID_ARGUMENT", response.text
    else:
        result(response, transport, error="INVALID_ARGUMENT")
    assert state(http) == before and http.events.empty() and not http.session.confirm_handlers
    assert path.read_bytes() == before_disk


@pytest.mark.parametrize("identity", ["absent", "wrong-project", "empty-actor", "wrong-tool", "missing-actor", "not-an-agent"])
@pytest.mark.parametrize("patch", [{}, {"text": "Cannot bypass scope"}])
def test_core_requires_real_matching_scope_even_for_noop(update_http_486, identity, patch):
    http = update_http_486
    before = state(http)

    async def update():
        if identity == "absent":
            return await tools.update_node_core(http.project, "human-prompt", patch)
        project = http.foreign if identity == "wrong-project" else http.project
        actor = None if identity == "empty-actor" else (
            "missing" if identity == "missing-actor" else "human-prompt" if identity == "not-an-agent" else "actor")
        tool = "canvas.create_node" if identity == "wrong-tool" else "canvas.update_node"
        with agent_ops_scope(project_id=project, agent_node_id=actor, tool=tool, args={"updates": patch}):
            return await tools.update_node_core(http.project, "human-prompt", patch)

    with pytest.raises(HTTPException) as caught:
        http.client.portal.call(update)
    missing = identity in ("missing-actor", "not-an-agent")
    assert caught.value.status_code == (404 if missing else 400)
    assert caught.value.detail["code"] == ("NODE_NOT_FOUND" if missing else "INVALID_ARGUMENT")
    assert state(http) == before and http.events.empty() and not http.session.confirm_handlers


def test_owned_direct_update_keeps_provenance_and_undo_exactly(update_http_486):
    http = update_http_486
    node_id = http.add_owned()
    before = http.node(node_id)
    response = http.update({"text": "An owned edit"}, node=node_id)
    assert response.status_code == 200 and response.json()["success"] is True, response.text
    assert response.json()["prev"] == {"text": before["text"]}
    assert http.events.empty()
    after = http.node(node_id)
    assert after == {**before, "text": "An owned edit", "updated_at": after["updated_at"]}
    operation, = http.operations()
    undo = http.client.post(f"/api/projects/{http.project}/ops/undo", headers={"X-Agent-Project": http.project},
                            json={"op_id": operation["op_id"]})
    assert undo.status_code == 200, undo.text
    assert http.node(node_id) == before


@pytest.mark.parametrize("change", ["content", "metadata", "delete", "recreate"])
def test_real_approval_before_latest_build_cannot_overwrite_changed_inputs(update_http_486, monkeypatch, change):
    http = update_http_486
    monkeypatch.setattr(tools, "AGENT_WRITE_CONFIRM_TIMEOUT_SECONDS", 5)
    before = http.node()
    applied, persisted = [], []
    path = http.store.root / http.project / "canvas.json"

    async def human_after_decision(session, event):
        if event["type"] != "confirm_resolved" or not event["approved"]:
            return
        removal = [{"op": "remove_node", "node_id": "human-prompt"}]
        winner = ({**before, "text": "A human winner", "customField": {"human": True}} if change == "content" else
                  {**before, "locked": True, "userTouchedAt": 11111} if change == "metadata" else
                  {**before, "text": "Recreated human node", "created_at": 11111})
        mutations = removal if change in ("delete", "recreate") else []
        if change != "delete":
            mutations = [*mutations, {"op": "upsert_node", "node": winner}]
        http.store.apply_mutations(http.project, mutations, base_revision=http.store.canvas(http.project)["revision"])
        applied.append(deepcopy(http.store.canvas(http.project)))
        http.store.flush(http.project)
        persisted.append(path.read_bytes())

    http.session.event_handlers.append(human_after_decision)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(http.update, {"text": "The old approved patch"})
        event = http.events.get(timeout=5)
        try:
            http.confirm(event, True)
            response = future.result(timeout=10)
        finally:
            if not future.done():
                http.confirm(event, False)
    assert response.status_code == 409 and response.json()["detail"]["code"] == "CONFIRM_STALE", response.text
    assert len(applied) == 1 and http.store.canvas(http.project) == applied[0]
    assert len(persisted) == 1 and path.read_bytes() == persisted[0]
    assert http.operations() == [] and not http.session.confirm_handlers


def test_approved_start_barrier_rechecks_target_and_closes_end_without_journal(update_http_486, monkeypatch):
    http = update_http_486
    monkeypatch.setattr(tools, "AGENT_WRITE_CONFIRM_TIMEOUT_SECONDS", 5)
    started, release, phases = Queue(), Event(), []
    original = main.manager.broadcast_canvas_updated

    async def broadcast(*args, **kwargs):
        phases.append(deepcopy(kwargs))
        if kwargs.get("phase") == "start":
            started.put(True)
            assert await asyncio.to_thread(release.wait, 5)
        return await original(*args, **kwargs)

    monkeypatch.setattr(main.manager, "broadcast_canvas_updated", broadcast)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(http.update, {"text": "An approved but stale edit"})
        event = http.events.get(timeout=5)
        try:
            http.confirm(event, True)
            started.get(timeout=5)
            http.human_change({"text": "A later human winner", "locked": True, "customField": {"keep": 1}})
            winner = state(http)
            http.store.flush(http.project)
            path = http.store.root / http.project / "canvas.json"
            winner_disk = path.read_bytes()
        finally:
            release.set()
            if not future.done() and not phases:
                http.confirm(event, False)
        response = future.result(timeout=10)
    assert response.status_code == 409 and response.json()["detail"]["code"] == "CONFIRM_STALE", response.text
    assert state(http) == winner and winner["ops"] == []
    assert path.read_bytes() == winner_disk
    assert [phase["phase"] for phase in phases] == ["start", "end"]
    assert phases[0]["op_id"] == phases[1]["op_id"] and phases[1]["touched_node_ids"] == []


def test_caller_and_confirmation_event_cannot_mutate_sealed_patch(update_http_486, monkeypatch):
    http = update_http_486
    monkeypatch.setattr(tools, "AGENT_WRITE_CONFIRM_TIMEOUT_SECONDS", 5)
    http.human_change({"runSettings": {"count": 1, "quality": "auto"}})
    patch = {"title": "Approved title", "runSettings": {"count": 2}}
    bound_args = deepcopy(patch)
    before = http.node()

    async def mutate_event(session, event):
        if event["type"] == "confirm" and event.get("tool") == "canvas.update_node":
            event["params"]["patch"]["title"] = "Not approved"
            event["params"]["patch"]["runSettings"]["count"] = 9

    http.session.event_handlers.append(mutate_event)

    async def update():
        with agent_ops_scope(project_id=http.project, agent_node_id="actor", tool="canvas.update_node",
                             args={"node_id": "human-prompt", "updates": patch}):
            return await tools.update_node_core(http.project, "human-prompt", patch)

    future = http.client.portal.start_task_soon(update)
    event = http.events.get(timeout=5)
    try:
        patch["title"] = "Changed caller input"
        patch["runSettings"]["count"] = 8
        assert http.node() == before
        http.confirm(event, True)
        outcome = future.result(timeout=10)
    finally:
        if not future.done():
            http.confirm(event, False)
            future.result(timeout=10)
    assert outcome["success"] is True
    after = http.node()
    assert after == {**before, "title": "Approved title", "runSettings": {"count": 2, "quality": "auto"},
                     "updated_at": after["updated_at"]}
    operation, = http.operations()
    assert operation["args"] == {"node_id": "human-prompt", "updates": bound_args}


@pytest.mark.parametrize("second_target", ["human-prompt", "another-protected"], ids=["same-target", "other-target"])
def test_concurrent_protected_rpcs_never_share_an_approval(update_http_486, monkeypatch, second_target):
    http = update_http_486
    monkeypatch.setattr(tools, "AGENT_WRITE_CONFIRM_TIMEOUT_SECONDS", 5)
    if second_target != "human-prompt":
        http.store.apply_mutations(http.project, [{"op": "upsert_node", "node": {
            **http.node(), "id": second_target, "text": "Another human-authored prompt",
        }}], base_revision=http.store.canvas(http.project)["revision"])
    before = http.node()
    second_before = http.node(second_target)
    revision = http.store.canvas(http.project)["revision"]
    http.store.flush(http.project)
    path = http.store.root / http.project / "canvas.json"
    before_disk = path.read_bytes()
    first_patch = {"text": "Only the first sealed patch is approved"}
    second_patch = {"title": "A distinct patch needs its own approval"}
    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(http.update, first_patch)
        first = http.events.get(timeout=5)
        second_future = executor.submit(http.update, second_patch, node=second_target)
        second = http.events.get(timeout=5)
        try:
            assert first["confirm_id"] != second["confirm_id"]
            assert first["params"]["op_id"] != second["params"]["op_id"]
            assert first["params"]["patch"] == first_patch and second["params"]["patch"] == second_patch
            assert first["target_node_ids"] == ["human-prompt"] and second["target_node_ids"] == [second_target]
            assert len(http.session.confirm_handlers) == 2
            assert http.node() == before and http.node(second_target) == second_before
            assert path.read_bytes() == before_disk and http.operations() == []
            http.confirm(first, True)
            first_response = first_future.result(timeout=10)
            assert first_response.status_code == 200 and first_response.json()["success"] is True, first_response.text
            after_first = state(http)
            assert after_first["node"] == {**before, **first_patch, "updated_at": after_first["node"]["updated_at"]}
            assert after_first["revision"] == revision + 1
            operation, = after_first["ops"]
            assert operation["op_id"] == first["params"]["op_id"]
            assert operation["args"] == {"node_id": "human-prompt", "updates": first_patch}
            if second_target != "human-prompt":
                assert http.node(second_target) == second_before
            committed_disk = path.read_bytes()
            assert json.loads(committed_disk)["nodes"] == http.store.canvas(http.project)["nodes"]
            assert not second_future.done() and len(http.session.confirm_handlers) == 1
            http.confirm(first, True)
            assert state(http) == after_first and path.read_bytes() == committed_disk
            assert not second_future.done() and len(http.session.confirm_handlers) == 1
            # Keep the waiter's conservative whole-canvas invalidation; separate RPCs never inherit approval.
            http.confirm(second, True)
            second_response = second_future.result(timeout=10)
        finally:
            for event, future in ((first, first_future), (second, second_future)):
                if not future.done():
                    http.confirm(event, False)
    assert second_response.status_code == 409, second_response.text
    assert second_response.json()["detail"]["code"] == "CONFIRM_STALE", second_response.text
    http.settle()
    assert state(http) == after_first and path.read_bytes() == committed_disk
    assert not http.session.confirm_handlers


@pytest.mark.parametrize("failure", ["sidecar", "before_replace"])
def test_protected_approved_write_fault_preserves_persistence_and_undo_contract(update_http_486, monkeypatch, failure):
    http = update_http_486
    monkeypatch.setattr(tools, "AGENT_WRITE_CONFIRM_TIMEOUT_SECONDS", 5)
    turn = "protected-write-fault-486"
    http.session._current_turn_id = http.session._last_user_turn_id = turn
    before = http.node()
    http.store.flush(http.project)
    path = http.store.root / http.project / "canvas.json"
    before_disk = path.read_bytes()
    patch = {"text": "A protected write approved before the persistence fault"}
    original_append, original_flush = agent_ops_store.append_operation, http.store.flush
    original_broadcast = main.manager.broadcast_canvas_updated
    phases, fault_calls = [], []
    injected = OSError("protected sidecar append" if failure == "sidecar" else "protected before replace")

    async def broadcast(*args, **kwargs):
        phases.append(deepcopy(kwargs))
        return await original_broadcast(*args, **kwargs)

    def fail(*args, **kwargs):
        fault_calls.append((args, kwargs))
        raise injected

    monkeypatch.setattr(main.manager, "broadcast_canvas_updated", broadcast)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(http.update, patch)
        event = http.events.get(timeout=5)
        try:
            assert event["params"]["patch"] == patch
            assert http.node() == before and path.read_bytes() == before_disk and http.operations() == []
            with monkeypatch.context() as fault:
                if failure == "sidecar":
                    fault.setattr(agent_ops_store, "append_operation", fail)
                else:
                    fault.setattr(http.store, "flush", fail)
                http.confirm(event, True)
                response = future.result(timeout=10)
                http.settle()
        finally:
            if not future.done():
                http.confirm(event, False)
    assert agent_ops_store.append_operation is original_append and http.store.flush == original_flush
    assert len(fault_calls) == 1
    op_id = event["params"]["op_id"]
    journal = tools._journal_for(http.store)
    status = journal.result(http.project, op_id)
    errors = [task.exception() for task in http.tasks if task.done() and not task.cancelled() and task.exception()]
    after = http.node()
    assert after == {**before, **patch, "updated_at": after["updated_at"]}
    assert [phase["phase"] for phase in phases] == ["start", "end"]
    assert all(phase["op_id"] == op_id for phase in phases)
    assert phases[0]["touched_node_ids"] == ["human-prompt"]
    assert phases[1]["touched_node_ids"] == (["human-prompt"] if failure == "sidecar" else [])
    assert http.operations() == [] and not http.session.confirm_handlers
    if failure == "sidecar":
        assert response.status_code == 200 and response.json()["success"] is True, response.text
        assert response.json()["op_id"] == op_id and response.json()["touched_node_ids"] == ["human-prompt"]
        assert fault_calls[0][0][2]["op_id"] == op_id and errors == []
        assert status["status"] == "append_failed" and status["error"] == f"OSError: {injected}"
        assert next(node for node in json.loads(path.read_bytes())["nodes"] if node["id"] == "human-prompt") == after
    else:
        assert response.status_code == 500 and response.json()["detail"]["code"] == "INTERNAL_ERROR", response.text
        assert "journal pending" in response.json()["detail"]["message"]
        assert str(injected) in response.json()["detail"]["message"]
        error, = errors
        assert isinstance(error, HTTPException) and error.__cause__ is injected
        assert status["status"] == "pending" and fault_calls[0][0] == (http.project,)
        assert path.read_bytes() == before_disk
    pending_disk = path.read_bytes()
    undo = undo_ops(http.store, http.project, op_id=op_id)
    assert undo["status"] == ("conflict" if failure == "sidecar" else "pending") and undo["reverted"] == []
    assert http.node() == after and path.read_bytes() == pending_disk
    original_flush(http.project)
    assert next(node for node in json.loads(path.read_bytes())["nodes"] if node["id"] == "human-prompt") == after
    if failure == "sidecar":
        assert journal.result(http.project, op_id)["status"] == "append_failed" and http.operations() == []
        recovered_disk = path.read_bytes()
        for target in ({"op_id": op_id}, {"turn_id": turn}):
            undo = undo_ops(http.store, http.project, **target)
            assert undo["status"] == "conflict" and undo["reverted"] == []
            assert "sidecar append failed" in undo["error"]
        assert http.node() == after and path.read_bytes() == recovered_disk
    else:
        assert journal.result(http.project, op_id)["status"] == "settled"
        operation, = http.operations()
        assert operation["op_id"] == op_id and operation["turn_id"] == turn
        assert operation["args"] == {"node_id": "human-prompt", "updates": patch}
        undo = undo_ops(http.store, http.project, op_id=op_id)
        assert undo["status"] == "reverted" and undo["reverted"] == [op_id]
        restored = next(node for node in json.loads(path.read_bytes())["nodes"] if node["id"] == "human-prompt")
        assert http.node() == restored == before
        assert http.operations()[0]["undone"] is True


@pytest.mark.parametrize("transport", ["REST", "MCP-flat", "MCP-legacy"])
def test_old_approval_cannot_authorize_a_new_rpc_after_real_undo(update_http_486, transport):
    http = update_http_486
    before = http.node()
    patch = {"text": "A replacement requiring a fresh approval"}
    first_receipt = result(invoke(http, transport, patch), transport)
    assert first_receipt["code"] == "CONFIRM_PENDING"
    first = http.events.get(timeout=5)
    http.confirm(first, True)
    http.settle()
    operation, = http.operations()
    assert http.node()["text"] == patch["text"]
    undo = http.client.post(f"/api/projects/{http.project}/ops/undo", headers={"X-Agent-Project": http.project},
                            json={"op_id": operation["op_id"]})
    assert undo.status_code == 200, undo.text
    assert http.node() == before
    undone = state(http)
    path = http.store.root / http.project / "canvas.json"
    undone_disk = path.read_bytes()
    second_receipt = result(invoke(http, transport, patch), transport)
    assert second_receipt["code"] == "CONFIRM_PENDING"
    second = http.events.get(timeout=5)
    assert second["confirm_id"] != first["confirm_id"]
    assert second["params"]["op_id"] != first["params"]["op_id"]
    http.confirm(first, True)
    assert state(http) == undone and len(http.session.confirm_handlers) == 1
    http.confirm(second, False)
    http.settle()
    assert state(http) == undone and not http.session.confirm_handlers
    assert path.read_bytes() == undone_disk
