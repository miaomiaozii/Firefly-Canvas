"""Public update transports and real approval lifetimes; no native/model process (#486)."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
from unittest.mock import AsyncMock

from fastapi import HTTPException
import pytest

# Routers bind main during import; register the real fixture's dependency in this module too.
import main
from app.routers import api_agent_tools as tools
from tests.test_agent_plan_399 import env
from tests.test_agent_update_ownership_486 import update_http_486


TRANSPORTS = ("REST", "MCP-flat", "MCP-legacy", "MCP-hosted", "MCP-external-SID")
PATCH = {"text": "A specifically reviewed replacement", "title": "Reviewed title"}
HOSTED_TURN = "hosted-original-turn-486"
EXTERNAL_TURN = "external-original-turn-486"
SID = "external-update-486"


def prepare_carrier(http, transport):
    if transport == "MCP-hosted":
        # Only the carrier attribution is simulated; no native runtime is installed.
        http.session._current_turn_id = HOSTED_TURN
        http.session._last_user_turn_id = HOSTED_TURN


def invoke(http, transport, patch=PATCH):
    if transport == "REST":
        return http.update(patch)
    arguments = ({"node_id": "human-prompt", "updates": patch} if transport == "MCP-legacy"
                 else {"node_id": "human-prompt", **patch})
    params = {"project": http.project, "node": "actor"}
    headers = {}
    if transport == "MCP-hosted":
        params["caller"] = "hosted"
    elif transport == "MCP-external-SID":
        params.update(caller="external", turn_id=EXTERNAL_TURN)
        headers["Mcp-Session-Id"] = SID
    return http.client.post("/api/mcp", params=params, headers=headers, json={
        "jsonrpc": "2.0", "id": 486, "method": "tools/call",
        "params": {"name": "canvas.update_node", "arguments": arguments},
    })


def result(response, transport, *, error=None):
    if transport == "REST":
        assert response.status_code == (409 if error else 200), response.text
        value = response.json()["detail"] if error else response.json()
    else:
        assert response.status_code == 200, response.text
        envelope = response.json()
        assert envelope["jsonrpc"] == "2.0" and envelope["id"] == 486, envelope
        value = envelope["result"]
        assert value["isError"] is bool(error), value
        value = value["structuredContent"] if error else json.loads(value["content"][0]["text"])
        if transport == "MCP-external-SID":
            assert response.headers["Mcp-Session-Id"] == SID
    if error:
        assert value["code"] == error, value
    return value


def update_transport_state_486(http):
    return {"node": http.node(), "revision": http.store.canvas(http.project)["revision"],
            "ops": http.operations()}


def update_transport_replay_486(http):
    response = http.client.get(f"/api/projects/{http.project}/agent-nodes/actor/events",
                               params={"host": "pi"})
    assert response.status_code == 200, response.text
    return [record["event"] for record in response.json()["events"]]


def update_transport_digest_486(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def assert_binding(http, event, before):
    assert event["target_node_ids"] == ["human-prompt"]
    params = event["params"]
    assert (params["project_id"], params["agent_node_id"], params["tool"], params["node_id"]) == (
        http.project, "actor", "canvas.update_node", "human-prompt")
    assert params["patch"] == PATCH and params["fields"] == sorted(PATCH)
    assert params["patch_sha256"] == update_transport_digest_486(PATCH)
    assert params["before_sha256"] == update_transport_digest_486(before["node"])
    assert params["before"] == {key: {"present": True, "value": before["node"][key]} for key in PATCH}
    assert params["op_id"]


def assert_live(http, event, before):
    assert update_transport_state_486(http) == before
    assert len(http.session.confirm_handlers) == 1
    events = [item for item in update_transport_replay_486(http) if item.get("confirm_id") == event["confirm_id"]]
    assert [item["type"] for item in events] == ["confirm"]
    assert events[0] == event


def assert_one_write(http, event, before, transport):
    after = update_transport_state_486(http)
    assert after["revision"] == before["revision"] + 1
    assert after["node"] == {**before["node"], **PATCH, "updated_at": after["node"]["updated_at"]}
    operation, = after["ops"]
    assert (operation["op_id"], operation["tool"], operation["agent_node_id"]) == (
        event["params"]["op_id"], "canvas.update_node", "actor")
    assert operation["touched_node_ids"] == ["human-prompt"]
    assert operation["turn_id"] == (HOSTED_TURN if transport == "MCP-hosted" else
                                    EXTERNAL_TURN if transport == "MCP-external-SID" else None)
    assert not http.session.confirm_handlers
    return after


def observe_delivery(http, monkeypatch):
    spies = []
    for name in ("send_prompt", "send_system_continuation", "spawn_session", "respawn_session"):
        spy = AsyncMock(wraps=getattr(http.host, name))
        monkeypatch.setattr(http.host, name, spy)
        spies.append(spy)
    return spies


def delivery_counts(http, spies):
    return [spy.await_count for spy in spies] + [http.host.send_command.await_count]


def assert_settlement_error(http, code):
    # A detached exception proves settlement, not a second HTTP response or a public error query.
    errors = [task.exception() for task in http.tasks if task.done() and not task.cancelled()]
    errors = [error for error in errors if error is not None]
    assert len(errors) == 1, errors
    error, = errors
    assert isinstance(error, HTTPException)
    assert error.status_code == 409 and error.detail["code"] == code, error


@pytest.mark.parametrize("transport", TRANSPORTS)
@pytest.mark.parametrize("approved", [False, True], ids=["reject", "approve"])
def test_sync_decision_uses_original_rest_or_mcp_response(update_http_486, monkeypatch, transport, approved):
    http = update_http_486
    prepare_carrier(http, transport)
    monkeypatch.setattr(tools, "AGENT_WRITE_CONFIRM_TIMEOUT_SECONDS", 5)
    before = update_transport_state_486(http)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(invoke, http, transport)
        event = None
        try:
            event = http.events.get(timeout=5)
            assert not future.done()
            assert_binding(http, event, before)
            assert_live(http, event, before)
            http.confirm(event, approved)
            response = future.result(timeout=10)
        finally:
            if event is not None and not future.done():
                http.confirm(event, False)
    value = result(response, transport, error=None if approved else "CONFIRM_REJECTED")
    http.settle()
    if approved:
        assert value["success"] is True and value["node_id"] == "human-prompt"
        assert value["prev"] == {key: before["node"][key] for key in PATCH}
        assert_one_write(http, event, before, transport)
    else:
        assert update_transport_state_486(http) == before
        assert not http.session.confirm_handlers
    resolved, = [item for item in update_transport_replay_486(http) if item["type"] == "confirm_resolved"]
    assert resolved["confirm_id"] == event["confirm_id"] and resolved["approved"] is approved
    assert resolved["reason"] == (None if approved else "rejected")


@pytest.mark.parametrize("transport", TRANSPORTS)
@pytest.mark.parametrize("autonomy", ["cautious", "standard", "autonomous"])
@pytest.mark.parametrize("approved", [False, True], ids=["late-reject", "late-approve"])
def test_zero_receipt_remains_live_in_every_autonomy_and_decides_once(
        update_http_486, monkeypatch, transport, autonomy, approved):
    http = update_http_486
    http.human_change({"autonomy": autonomy}, node="actor")
    http.session.autonomy = autonomy
    prepare_carrier(http, transport)
    spies = observe_delivery(http, monkeypatch)
    before = update_transport_state_486(http)
    phases = []
    original = main.manager.broadcast_canvas_updated

    async def observe(*args, **kwargs):
        phases.append(deepcopy(kwargs))
        return await original(*args, **kwargs)

    monkeypatch.setattr(main.manager, "broadcast_canvas_updated", observe)
    inputs = [item for item in update_transport_replay_486(http) if item["type"] in ("user_prompt", "queued_input", "system_continuation")]
    counts = delivery_counts(http, spies)
    response = invoke(http, transport)
    receipt = result(response, transport)
    assert receipt == {"approved": False, "reason": "pending", "code": "CONFIRM_PENDING",
                       "status": "awaiting_confirmation", "confirm_id": receipt["confirm_id"]}
    event = http.events.get(timeout=5)
    assert receipt["confirm_id"] == event["confirm_id"]
    assert_binding(http, event, before)
    assert_live(http, event, before)
    assert phases == []
    assert any(not task.done() for task in http.tasks)
    http.confirm(event, approved)
    http.settle()
    if approved:
        settled = assert_one_write(http, event, before, transport)
        assert [phase["phase"] for phase in phases] == ["start", "end"]
        assert phases[0]["op_id"] == phases[1]["op_id"] == event["params"]["op_id"]
        assert phases[1]["touched_node_ids"] == ["human-prompt"]
    else:
        assert phases == []
        assert update_transport_state_486(http) == before
        assert_settlement_error(http, "CONFIRM_REJECTED")
        settled = before
    assert not http.session.confirm_handlers
    resolved, = [item for item in update_transport_replay_486(http) if item["type"] == "confirm_resolved"]
    assert resolved["confirm_id"] == event["confirm_id"] and resolved["approved"] is approved
    assert resolved["reason"] == (None if approved else "rejected")
    http.confirm(event, True)
    http.settle()
    assert update_transport_state_486(http) == settled
    assert len(http.seen) == 1
    assert delivery_counts(http, spies) == counts
    assert [item for item in update_transport_replay_486(http) if item["type"] in (
        "user_prompt", "queued_input", "system_continuation")] == inputs
    # Receipt success means only pending; the original HTTP status cannot change after a late decision.
    assert response.status_code == 200


def invalidate_update_transport_486(http, lifecycle):
    if lifecycle == "stop":
        http.client.portal.call(http.host.stop_session, http.session)
    elif lifecycle == "new-session":
        assert http.client.portal.call(http.host.drop_node_session, http.project, "actor")
        replacement = http.client.portal.call(http.host.get_or_create_session, http.project, "actor")
        assert replacement is not http.session and not replacement.confirm_handlers
    elif lifecycle in ("host", "generation"):
        http.client.portal.call(http.host.select_runtime, http.session,
                               "codex" if lifecycle == "host" else "pi")
    elif lifecycle == "input-epoch":
        http.client.portal.call(http.host.send_abort, http.session)
    elif lifecycle == "user-turn":
        http.client.portal.call(http.host.send_prompt, http.session, "A real newer user input")
    elif lifecycle == "deleted-actor":
        http.store.apply_mutations(http.project, [{"op": "remove_node", "node_id": "actor"}],
                                   base_revision=http.store.canvas(http.project)["revision"])
    elif lifecycle == "human-edit":
        http.human_change({"text": "A newer human winner", "userTouchedAt": 10001,
                           "customField": {"preserve": "human"}})
    else:
        raise AssertionError(lifecycle)


@pytest.mark.parametrize("transport", ["REST", "MCP-external-SID"])
@pytest.mark.parametrize("lifecycle", ["stop", "new-session", "host", "generation", "input-epoch", "user-turn",
                                       "deleted-actor", "human-edit"])
def test_late_pending_lifecycle_cannot_reuse_old_approval(update_http_486, monkeypatch, transport, lifecycle):
    http = update_http_486
    spies = observe_delivery(http, monkeypatch)
    before = update_transport_state_486(http)
    response = invoke(http, transport)
    receipt = result(response, transport)
    assert receipt["code"] == "CONFIRM_PENDING"
    event = http.events.get(timeout=5)
    assert receipt["confirm_id"] == event["confirm_id"]
    assert_live(http, event, before)
    invalidate_update_transport_486(http, lifecycle)
    winner = update_transport_state_486(http)
    counts = delivery_counts(http, spies)
    http.confirm(event, True)
    http.settle()
    assert update_transport_state_486(http) == winner
    assert winner["ops"] == []
    assert not http.session.confirm_handlers
    assert_settlement_error(http, "CONFIRM_STALE")
    resolved, = [item for item in update_transport_replay_486(http) if item["type"] == "confirm_resolved"]
    assert resolved["confirm_id"] == event["confirm_id"]
    if lifecycle in ("deleted-actor", "human-edit"):
        # The decision event is not a write receipt; the real snapshot check rejects the commit.
        assert resolved["approved"] is True and resolved["reason"] is None
    else:
        assert resolved["approved"] is False and resolved["reason"] == "session_ended"
    http.confirm(event, True)
    http.settle()
    assert update_transport_state_486(http) == winner and len(http.seen) == 1
    assert delivery_counts(http, spies) == counts
    assert response.status_code == 200


@pytest.mark.parametrize("transport", TRANSPORTS)
def test_sync_human_winner_returns_exact_stale_protocol_error(update_http_486, monkeypatch, transport):
    http = update_http_486
    prepare_carrier(http, transport)
    monkeypatch.setattr(tools, "AGENT_WRITE_CONFIRM_TIMEOUT_SECONDS", 5)
    before = update_transport_state_486(http)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(invoke, http, transport)
        event = None
        try:
            event = http.events.get(timeout=5)
            assert not future.done()
            assert_live(http, event, before)
            invalidate_update_transport_486(http, "human-edit")
            winner = update_transport_state_486(http)
            http.confirm(event, True)
            response = future.result(timeout=10)
        finally:
            if event is not None and not future.done():
                http.confirm(event, False)
    result(response, transport, error="CONFIRM_STALE")
    http.settle()
    assert update_transport_state_486(http) == winner and winner["ops"] == []
    assert not http.session.confirm_handlers and len(http.seen) == 1


@pytest.mark.parametrize("transport", TRANSPORTS)
def test_confirmation_message_displays_the_sealed_target_before_and_patch(update_http_486, transport):
    http = update_http_486
    prepare_carrier(http, transport)
    receipt = result(invoke(http, transport), transport)
    assert receipt["code"] == "CONFIRM_PENDING"
    event = http.events.get(timeout=5)
    params = event["params"]
    assert params["node_id"] in event["message"]
    assert json.dumps(params["before"], ensure_ascii=False, sort_keys=True) in event["message"]
    assert json.dumps(params["patch"], ensure_ascii=False, sort_keys=True) in event["message"]
    http.confirm(event, False)
    http.settle()
    assert_settlement_error(http, "CONFIRM_REJECTED")


def test_confirm_id_cannot_be_used_on_another_actor_or_project(update_http_486):
    http = update_http_486
    before = update_transport_state_486(http)
    receipt = result(invoke(http, "REST"), "REST")
    assert receipt["code"] == "CONFIRM_PENDING"
    event = http.events.get(timeout=5)
    for project, actor in ((http.project, "other"), (http.foreign, "actor")):
        response = http.client.post(f"/api/projects/{project}/agent-nodes/{actor}/confirm",
            json={"confirm_id": event["confirm_id"], "approved": True})
        assert response.status_code == 200, response.text
        assert_live(http, event, before)
    foreign = http.client.get(f"/api/projects/{http.foreign}/ops")
    assert foreign.status_code == 200 and foreign.json()["operations"] == []
    http.confirm(event, False)
    http.settle()
    assert update_transport_state_486(http) == before and not http.session.confirm_handlers
    assert_settlement_error(http, "CONFIRM_REJECTED")
