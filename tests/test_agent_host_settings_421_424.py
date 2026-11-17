"""Gate1 pure fixtures plus gate2 fake-native router transactions (no CLI spawn)."""
from __future__ import annotations

from copy import deepcopy
import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest


# Keep the gate1 loader: the pure functions remain importable without loading
# main/the service graph; orchestration dependencies are imported only on use.
_SPEC = importlib.util.spec_from_file_location(
    "agent_node_host_settings_421_424",
    Path(__file__).resolve().parents[1] / "app/services/agent_node_host_settings.py",
)
_SETTINGS = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_SETTINGS)
read_host_fields = _SETTINGS.read_host_fields
build_host_settings_fields = _SETTINGS.build_host_settings_fields

FIELD_KEYS = {
    "agentCli", "agentHostSettings", "agentHostSessions", "model",
    "extSessionId", "branches", "current_branch",
}
EMPTY_SETTINGS = {"model": "", "effort": None}
EMPTY_SESSION = {"extSessionId": "", "branches": [], "current_branch": ""}
BAD_EFFORTS = ("", " ", "\t\n", "ultra", "ULTRA", " Ultra ", False, 0, 1, [], {})


def legacy_node(host="pi"):
    return {
        "agentCli": host,
        "model": f"{host}/saved-model",
        "extSessionId": f"{host}-native-session",
        "branches": [{"id": f"{host}-branch", "metadata": {"labels": ["keep"]}}],
        "current_branch": f"{host}-branch",
    }


def mapped_node(active="pi"):
    node = {"agentCli": active, "agentHostSettings": {}, "agentHostSessions": {}}
    for host, effort in (("pi", "high"), ("codex", "xhigh")):
        old = legacy_node(host)
        node["agentHostSettings"][host] = {"model": old["model"], "effort": effort}
        node["agentHostSessions"][host] = {
            key: old[key] for key in ("extSessionId", "branches", "current_branch")
        }
    return node


def assert_projection(fields, host):
    assert set(fields) == FIELD_KEYS
    assert fields["agentCli"] == host
    assert fields["model"] == fields["agentHostSettings"][host]["model"]
    for key in ("extSessionId", "branches", "current_branch"):
        assert fields[key] == fields["agentHostSessions"][host][key]
    assert fields["branches"] is not fields["agentHostSessions"][host]["branches"]


@pytest.mark.parametrize("prior", ["pi", "codex"])
def test_legacy_migrates_only_to_prior_host_before_selecting_another(prior):
    node = legacy_node(prior)
    before = deepcopy(node)
    fields = read_host_fields(node)
    assert_projection(fields, prior)
    assert fields["agentHostSettings"] == {prior: {"model": node["model"], "effort": None}}
    assert fields["agentHostSessions"] == {
        prior: {key: node[key] for key in ("extSessionId", "branches", "current_branch")}
    }
    selected = "codex" if prior == "pi" else "pi"
    result = build_host_settings_fields(node, selected, "new/model", "future-effort")
    assert_projection(result, selected)
    assert result["agentHostSettings"][prior] == fields["agentHostSettings"][prior]
    assert result["agentHostSettings"][selected] == {"model": "new/model", "effort": "future-effort"}
    assert result["agentHostSessions"][prior] == fields["agentHostSessions"][prior]
    assert result["agentHostSessions"][selected] == EMPTY_SESSION
    assert result["extSessionId"] == result["current_branch"] == ""
    assert result["branches"] == []
    assert node == before


def test_absent_fields_default_to_pi_and_do_not_eagerly_populate_codex():
    node = {}
    fields = read_host_fields(node)
    assert fields == {
        "agentCli": "pi", "agentHostSettings": {"pi": EMPTY_SETTINGS},
        "agentHostSessions": {"pi": EMPTY_SESSION}, "model": "",
        "extSessionId": "", "branches": [], "current_branch": "",
    }
    result = build_host_settings_fields(node, "codex", "", None)
    assert_projection(result, "codex")
    assert result["agentHostSettings"] == {"pi": EMPTY_SETTINGS, "codex": EMPTY_SETTINGS}
    assert result["agentHostSessions"] == {"pi": EMPTY_SESSION, "codex": EMPTY_SESSION}
    assert node == {}


@pytest.mark.parametrize("host", [None, "", " ", "\t", " pi ", "codex "])
def test_legacy_host_and_null_fields_match_historical_empty_readers(host):
    node = {"agentCli": host, "model": None, "extSessionId": None,
            "branches": None, "current_branch": None}
    before = deepcopy(node)
    expected = "codex" if host == "codex " else "pi"
    fields = read_host_fields(node)
    assert_projection(fields, expected)
    assert fields["agentHostSettings"] == {expected: EMPTY_SETTINGS}
    assert fields["agentHostSessions"] == {expected: EMPTY_SESSION}
    assert node == before


def test_missing_legacy_host_keeps_existing_binding_only_under_pi():
    node = legacy_node()
    del node["agentCli"]
    result = build_host_settings_fields(node, "codex", "", None)
    assert result["agentHostSessions"]["pi"]["extSessionId"] == "pi-native-session"
    assert result["agentHostSessions"]["codex"] == EMPTY_SESSION


@pytest.mark.parametrize("host", ["pi", "codex"])
def test_legacy_same_host_update_keeps_binding_and_replaces_only_settings(host):
    node = legacy_node(host)
    result = build_host_settings_fields(node, host, "", None)
    assert_projection(result, host)
    assert result["agentHostSettings"] == {host: EMPTY_SETTINGS}
    assert result["agentHostSessions"][host]["extSessionId"] == node["extSessionId"]
    assert result["branches"] == node["branches"]


@pytest.mark.parametrize("active", ["pi", "codex"])
def test_new_maps_override_even_malformed_stale_legacy_projections(active):
    node = mapped_node(active)
    node.update(model=None, extSessionId=["wrong-host"], branches="stale", current_branch=False)
    before = deepcopy(node)
    fields = read_host_fields(node)
    assert_projection(fields, active)
    assert fields["agentHostSettings"] == node["agentHostSettings"]
    assert fields["agentHostSessions"] == node["agentHostSessions"]
    assert fields["extSessionId"] == f"{active}-native-session"
    assert node == before


@pytest.mark.parametrize("active,selected", [("pi", "codex"), ("codex", "pi"), ("pi", "pi")])
def test_selection_preserves_all_other_host_settings_and_all_bindings(active, selected):
    node = mapped_node(active)
    before = deepcopy(node)
    result = build_host_settings_fields(node, selected, "replacement", "custom-capability")
    other = "codex" if selected == "pi" else "pi"
    assert_projection(result, selected)
    assert result["agentHostSettings"][selected] == {"model": "replacement", "effort": "custom-capability"}
    assert result["agentHostSettings"][other] == node["agentHostSettings"][other]
    assert result["agentHostSessions"] == node["agentHostSessions"]
    assert node == before
    assert read_host_fields({**node, **result}) == result


@pytest.mark.parametrize("active", ["pi", "codex"])
def test_paired_empty_maps_add_only_active_defaults_without_importing_legacy(active):
    node = {**legacy_node(active), "agentHostSettings": {}, "agentHostSessions": {}}
    before = deepcopy(node)
    fields = read_host_fields(node)
    assert_projection(fields, active)
    assert fields["agentHostSettings"] == {active: EMPTY_SETTINGS}
    assert fields["agentHostSessions"] == {active: EMPTY_SESSION}
    assert node == before


def test_paired_sparse_maps_add_active_defaults_without_copying_inactive_session():
    node = mapped_node()
    del node["agentHostSettings"]["pi"]
    del node["agentHostSessions"]["pi"]
    before = deepcopy(node)
    fields = read_host_fields(node)
    assert fields["agentHostSettings"]["pi"] == EMPTY_SETTINGS
    assert fields["agentHostSessions"]["pi"] == EMPTY_SESSION
    assert fields["agentHostSessions"]["codex"] == node["agentHostSessions"]["codex"]
    assert node == before


def test_missing_new_schema_active_host_defaults_to_pi():
    node = {"agentHostSettings": {}, "agentHostSessions": {}}
    assert read_host_fields(node) == read_host_fields({})


@pytest.mark.parametrize("new_schema", [False, True])
@pytest.mark.parametrize("build", [False, True])
def test_every_result_is_deeply_fresh_and_projection_does_not_alias_binding(new_schema, build):
    node = mapped_node() if new_schema else legacy_node()
    before = deepcopy(node)
    call = (lambda: build_host_settings_fields(node, "pi", "picked", "high")) if build else (lambda: read_host_fields(node))
    first, second = call(), call()
    first["branches"][0]["metadata"]["labels"].append("projection-only")
    assert first["agentHostSessions"]["pi"]["branches"] == before.get("agentHostSessions", {"pi": before})["pi"]["branches"]
    first["agentHostSessions"]["pi"]["branches"][0]["metadata"]["labels"].append("binding-only")
    first["agentHostSettings"]["pi"]["model"] = "changed"
    assert node == before
    assert second == call()
    assert second["branches"][0]["metadata"]["labels"] == ["keep"]


def test_even_shared_in_memory_host_entries_become_independent():
    node = mapped_node()
    node["agentHostSettings"]["codex"] = node["agentHostSettings"]["pi"]
    node["agentHostSessions"]["codex"] = node["agentHostSessions"]["pi"]
    fields = read_host_fields(node)
    fields["agentHostSettings"]["pi"]["model"] = "separate"
    fields["agentHostSessions"]["pi"]["branches"][0]["metadata"]["labels"].append("separate")
    assert fields["agentHostSettings"]["codex"]["model"] == "pi/saved-model"
    assert fields["agentHostSessions"]["codex"]["branches"][0]["metadata"]["labels"] == ["keep"]
    assert node["agentHostSessions"]["pi"]["branches"][0]["metadata"]["labels"] == ["keep"]


@pytest.mark.parametrize("width", [0, 321, 987.5])
def test_patch_omits_width_geometry_ownership_timestamps_and_every_unrelated_field(width):
    node = {**mapped_node(), "w": width, "width": width, "h": 701, "x": 20,
            "createdBy": "agent", "agentNodeId": "creator", "userTouchedAt": 17,
            "promptEditedByUser": True, "updated_at": 29, "prompt": "keep", "other": {"nested": [1]}}
    before = deepcopy(node)
    for result in (read_host_fields(node), build_host_settings_fields(node, "codex", "", None)):
        assert set(result) == FIELD_KEYS
        merged = {**node, **result}
        for key in node.keys() - FIELD_KEYS:
            assert merged[key] == before[key]
    assert node == before


def test_read_and_build_are_idempotent_and_do_not_open_files():
    node = legacy_node("codex")
    with patch("builtins.open", side_effect=AssertionError("pure settings must not open files")):
        fields = read_host_fields(node)
        assert read_host_fields(fields) == fields
        assert build_host_settings_fields(fields, "codex", fields["model"], None) == fields


@pytest.mark.parametrize("value", [None, False, 1, "", [], ()])
def test_invalid_node_types_raise_explicit_value_error(value):
    with pytest.raises(ValueError, match="node"):
        read_host_fields(value)
    with pytest.raises(ValueError, match="node"):
        build_host_settings_fields(value, "pi", "", None)


@pytest.mark.parametrize("value", ["unknown", "Pi", "ultra", False, 0, [], {}])
@pytest.mark.parametrize("new_schema", [False, True])
def test_unknown_or_wrong_type_saved_host_never_falls_back(value, new_schema):
    node = mapped_node() if new_schema else legacy_node()
    node["agentCli"] = value
    with pytest.raises(ValueError, match="agentCli"):
        read_host_fields(node)


@pytest.mark.parametrize("value", [None, "", " ", " pi "])
def test_new_schema_does_not_use_legacy_null_or_blank_host_fallback(value):
    node = mapped_node()
    node["agentCli"] = value
    with pytest.raises(ValueError, match="agentCli"):
        read_host_fields(node)


@pytest.mark.parametrize("field", ["model", "extSessionId", "current_branch"])
@pytest.mark.parametrize("value", [False, 0, [], {}])
def test_legacy_wrong_string_types_are_not_coerced(field, value):
    node = legacy_node()
    node[field] = value
    with pytest.raises(ValueError, match=field):
        read_host_fields(node)


@pytest.mark.parametrize("value", ["", "branches", {}, False, 1, ()])
def test_legacy_wrong_branch_container_types_are_not_defaulted(value):
    node = legacy_node()
    node["branches"] = value
    with pytest.raises(ValueError, match="branches"):
        read_host_fields(node)


@pytest.mark.parametrize("field", ["agentHostSettings", "agentHostSessions"])
@pytest.mark.parametrize("value", [None, [], "", False, 1])
def test_malformed_new_map_types_cannot_trigger_legacy_rebuilding(field, value):
    node = mapped_node()
    node[field] = value
    with pytest.raises(ValueError, match=field):
        read_host_fields(node)


@pytest.mark.parametrize("missing", ["agentHostSettings", "agentHostSessions"])
def test_partial_new_maps_are_an_explicit_error(missing):
    node = mapped_node()
    del node[missing]
    with pytest.raises(ValueError, match="both be present"):
        read_host_fields(node)


@pytest.mark.parametrize("field", ["agentHostSettings", "agentHostSessions"])
def test_unpaired_host_entries_are_an_explicit_error(field):
    node = mapped_node()
    del node[field]["codex"]
    with pytest.raises(ValueError, match="matching host keys"):
        read_host_fields(node)


@pytest.mark.parametrize("field", ["agentHostSettings", "agentHostSessions"])
@pytest.mark.parametrize("host", ["unknown", "Pi", "", 1, None])
def test_unknown_map_host_keys_are_an_explicit_error(field, host):
    node = mapped_node()
    node[field][host] = deepcopy(node[field]["pi"])
    with pytest.raises(ValueError, match=field):
        read_host_fields(node)


@pytest.mark.parametrize("field", ["agentHostSettings", "agentHostSessions"])
@pytest.mark.parametrize("value", [None, [], "", False, {}, {"extra": "not schema"}])
def test_malformed_or_incomplete_new_entries_are_an_explicit_error(field, value):
    node = mapped_node()
    node[field]["codex"] = value
    with pytest.raises(ValueError, match=field):
        read_host_fields(node)


@pytest.mark.parametrize("field,key", [
    ("agentHostSettings", "model"), ("agentHostSettings", "effort"),
    ("agentHostSessions", "extSessionId"), ("agentHostSessions", "branches"),
    ("agentHostSessions", "current_branch"),
])
def test_partial_entries_and_unknown_entry_properties_are_not_silently_rebuilt(field, key):
    for operation in ("remove", "extra"):
        node = mapped_node()
        if operation == "remove":
            del node[field]["codex"][key]
        else:
            node[field]["codex"]["extra"] = "must not discard"
        with pytest.raises(ValueError, match=field):
            read_host_fields(node)


@pytest.mark.parametrize("field,key", [
    ("agentHostSettings", "model"), ("agentHostSessions", "extSessionId"),
    ("agentHostSessions", "current_branch"),
])
@pytest.mark.parametrize("value", [None, False, 1, [], {}])
def test_wrong_new_string_types_in_inactive_hosts_are_rejected(field, key, value):
    node = mapped_node()
    node[field]["codex"][key] = value
    with pytest.raises(ValueError, match=key):
        read_host_fields(node)
    with pytest.raises(ValueError, match=key):
        build_host_settings_fields(node, "pi", "new", None)


@pytest.mark.parametrize("value", [None, "", {}, False, 1, ()])
def test_wrong_new_branches_type_is_rejected(value):
    node = mapped_node()
    node["agentHostSessions"]["codex"]["branches"] = value
    with pytest.raises(ValueError, match="branches"):
        read_host_fields(node)


@pytest.mark.parametrize("value", BAD_EFFORTS)
def test_invalid_effort_and_ultra_rejected_at_saved_and_user_boundaries(value):
    node = mapped_node()
    node["agentHostSettings"]["codex"]["effort"] = value
    before = deepcopy(node)
    with pytest.raises(ValueError, match="effort"):
        read_host_fields(node)
    # Even replacing the malformed entry must not silently repair saved data.
    with pytest.raises(ValueError, match="effort"):
        build_host_settings_fields(node, "codex", "new", None)
    assert node == before
    with pytest.raises(ValueError, match="effort"):
        build_host_settings_fields({}, "pi", "", value)


@pytest.mark.parametrize("value", [None, "off", "minimal", "low", "medium", "high", "xhigh", "future-value"])
@pytest.mark.parametrize("host", ["pi", "codex"])
def test_effort_is_not_a_closed_enum_and_is_preserved_for_later_capability_validation(value, host):
    fields = build_host_settings_fields({}, host, "", value)
    assert fields["agentHostSettings"][host]["effort"] == value
    assert read_host_fields(fields) == fields


@pytest.mark.parametrize("field,value", [
    ("host", None), ("host", ""), ("host", " pi "), ("host", "unknown"),
    ("host", False), ("host", []), ("host", {}),
    ("model", None), ("model", False), ("model", 1), ("model", []), ("model", {}),
])
def test_invalid_user_host_and_model_types_are_explicit_errors_without_mutation(field, value):
    node = mapped_node()
    before = deepcopy(node)
    arguments = {"host": "pi", "model": "", "effort": None, field: value}
    with pytest.raises(ValueError, match=field):
        build_host_settings_fields(node, **arguments)
    assert node == before


def test_model_and_native_binding_strings_and_branch_payloads_are_opaque():
    node = mapped_node()
    node["agentHostSettings"]["pi"]["model"] = " provider/model "
    node["agentHostSessions"]["pi"] = {
        "extSessionId": " native-id ", "branches": [{"future": {"payload": [1, None]}}],
        "current_branch": " branch-id ",
    }
    result = read_host_fields(node)
    assert result["model"] == " provider/model "
    assert result["extSessionId"] == " native-id "
    assert result["current_branch"] == " branch-id "
    assert result["branches"] == node["agentHostSessions"]["pi"]["branches"]


# Gate2 integration fixtures below explicitly import main under isolated roots.
import asyncio
from types import SimpleNamespace

from fastapi import HTTPException
from fastapi.testclient import TestClient

import main
from firefly_projects import ProjectStore, RevisionConflict
from app.routers import api_agent_nodes as api
from app.services.agent_host import AgentHostManager
from app.services import agent_host as ah
from app.services import agent_model_capabilities as caps


def capabilities(host, model=""):
    directory = ["provider/old", "provider/new", "provider/narrow", "new-model", "old-model",
                 "automatic-model", "manual-model", "session-model", "offline-model", "vllm-remote/test-model"]
    return {"host": host, "status": "available", "models": [{"id": m} for m in directory],
            "defaults": {"model": "provider/old", "effort": "medium"},
            "selection": {"model": model or "provider/old",
                          "efforts": ["low"] if model == "provider/narrow" else ["low", "medium", "high"],
                          "source": {"scope": "fake-native"}}, "error": None}


class FakeSettingsManager(AgentHostManager):
    """Exercise real strict-ID run_command/apply_runtime_settings without readers."""

    def __init__(self):
        super().__init__()
        self.commands = []
        self.native = {"model": {"provider": "provider", "id": "old"}, "thinkingLevel": "medium",
                       "isStreaming": False, "isCompacting": False, "pendingMessageCount": 0,
                       "sessionId": "pi-original", "sessionFile": "original.jsonl"}
        self.failure = None
        self.stops = 0
        self.spawns = 0
        self.prompt_hook = None

    async def run_command(self, session, command, timeout=0.05, **kwargs):
        return await super().run_command(session, command, timeout=0.05, **kwargs)

    async def send_command(self, session, command):
        self.commands.append(deepcopy(command))
        kind = command["type"]
        data = {}
        if kind == "get_state":
            data = deepcopy(self.native)
        elif kind == "set_model":
            self.native["model"] = {"provider": command["provider"], "id": command["modelId"]}
            data = deepcopy(self.native["model"])
        elif kind == "set_thinking_level":
            self.native["thinkingLevel"] = command["level"]
        elif kind == "fork":
            self.native.update(sessionId="pi-fork", sessionFile="forked.jsonl")
        elif kind == "switch_session":
            self.native.update(sessionId="pi-original" if command["sessionPath"] == "original.jsonl" else "pi-fork",
                               sessionFile=command["sessionPath"])
        elif kind == "prompt":
            session.status = "busy"
            session._turn_open = True
            if self.prompt_hook:
                await self.prompt_hook()
            return
        elif kind in ("steer", "follow_up", "abort"):
            return
        elif kind == "get_fork_messages":
            data = {"messages": [{"entryId": "entry", "text": "hello"}]}
        else:
            raise AssertionError(kind)
        response = {"type": "response", "command": kind, "id": command.get("id"), "success": True, "data": data}
        if (self.failure == (kind, "wrong_id_after_write")
                and any(c["type"] == "set_thinking_level" for c in self.commands)):
            self.failure = None
            response["id"] = "stale-id"
        elif self.failure == (kind, "nack"):
            self.failure = None
            response.update(success=False, error="rejected by fake native")
        elif self.failure == (kind, "wrong_id"):
            self.failure = None
            response["id"] = "stale-id"
        elif self.failure == (kind, "clamp"):
            self.failure = None
            self.native["thinkingLevel"] = "low"
        await self._dispatch_event(session, response)

    async def live(self, project_id, node_id="agent"):
        session = await self.get_or_create_session(project_id, node_id)
        session.process = SimpleNamespace(poll=lambda: None)
        session.status = "running"
        session.session_id = session.ext_session_id = "pi-original"
        session.session_file = "original.jsonl"
        return session

    async def stop_session(self, session):
        self.stops += 1
        session.process = None
        session.status = "stopped"
        if session._reader_task is not None:
            await session._reader_task
            session._reader_task = None

    async def spawn_session(self, session, *args, **kwargs):
        self.spawns += 1
        session.process = SimpleNamespace(poll=lambda: None)
        session.status = "running"
        return True


@pytest.fixture
def env(tmp_path, monkeypatch):
    store = ProjectStore(tmp_path / "projects", snapshot_delay=0)
    monkeypatch.setattr(main, "FIREFLY_PROJECT_STORE", store)
    # Capability-first collection may import the runtime before main. Bind this
    # fixture's application explicitly, just like the runtime contract fixture.
    monkeypatch.setattr(ah, "_app_main", main)
    project_id = store.create_project("e2e-tabbit-g2-router")["id"]
    manager = FakeSettingsManager()
    monkeypatch.setattr(api, "agent_host_manager", manager)
    monkeypatch.setattr(caps, "get_capabilities", capabilities)
    node = {"id": "agent", "type": "smart-agent", "x": 5, "y": 6, "createdBy": "agent",
            "agentNodeId": "creator", "promptEditedByUser": True, "userTouchedAt": None}
    store.apply_mutations(project_id, [{"op": "upsert_node", "node": node},
                                       {"op": "upsert_node", "node": {"id": "filler", "type": "image"}}], base_revision=0)
    client = TestClient(main.app, base_url="http://127.0.0.1:3194", headers={"origin": "http://127.0.0.1:3194"})
    state = SimpleNamespace(store=store, project_id=project_id, manager=manager, client=client,
                            url=f"/api/projects/{project_id}/agent-nodes/agent")
    yield state
    client.close()
    store.close()


def saved(env):
    return next(n for n in env.store.canvas(env.project_id)["nodes"] if n["id"] == "agent")


def seed(env, node):
    rev = env.store.canvas(env.project_id)["revision"]
    env.store.apply_agent_host_mutations(env.project_id, [{"op": "upsert_node", "node": node}], base_revision=rev)


def run(coro):
    return asyncio.run(coro)


@pytest.mark.parametrize("flag,value", [("status", "starting"), ("status", "busy"), ("status", "waiting_confirm"),
    ("_turn_open", True), ("_stopping", True), ("_retrying", True), ("_response_finishing", True),
    ("confirm_handlers", [object()]), ("isStreaming", True), ("isCompacting", True), ("pendingMessageCount", 1),
    ("pendingMessageCount", 2)])
def test_busy_matrix_never_saves_or_stops(env, flag, value):
    session = run(env.manager.live(env.project_id))
    if flag in env.manager.native:
        env.manager.native[flag] = value
    else:
        setattr(session, flag, value)
    before = env.store.canvas(env.project_id)
    response = env.client.post(env.url + "/settings", json={"host": "codex"})
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "agent_busy"
    assert env.store.canvas(env.project_id) == before
    assert env.manager.stops == env.manager.spawns == 0
    assert not any(c["type"].startswith("set_") for c in env.manager.commands)


def test_busy_precedes_metadata_probe_and_unfinished_reader_is_busy(env, monkeypatch):
    async def scenario():
        session = await env.manager.get_or_create_session(env.project_id, "agent")
        finish = asyncio.Event()
        reader = session._reader_task = asyncio.create_task(finish.wait())
        def no_probe(*args):
            raise AssertionError("busy must be rejected before metadata discovery")
        monkeypatch.setattr(caps, "get_capabilities", no_probe)
        try:
            with pytest.raises(HTTPException) as failed:
                await api.set_agent_settings(env.project_id, "agent", {"host": "codex"})
            assert failed.value.status_code == 409 and failed.value.detail["code"] == "agent_busy"
        finally:
            finish.set()
            await reader
    run(scenario())


def test_live_idle_pi_settings_ack_and_ownership(env, monkeypatch):
    session = run(env.manager.live(env.project_id))
    events, confirms = session.event_handlers, session.confirm_handlers
    monkeypatch.setattr(main, "now_ms", lambda: 123456)
    response = env.client.post(env.url + "/settings", json={"host": "pi", "model": "provider/new", "effort": "high", "user_edit": True})
    assert response.status_code == 200, response.text
    data, node = response.json(), saved(env)
    assert node["agentHostSettings"]["pi"] == {"model": "provider/new", "effort": "high"}
    assert (node["createdBy"], node["agentNodeId"], node["promptEditedByUser"], node["userTouchedAt"]) == ("agent", "creator", True, 123456)
    assert data["effort_state"] == {"requested": "high", "submitted": "high", "effective": "high", "source": "pi:get_state"}
    assert session.event_handlers is events and session.confirm_handlers is confirms
    assert env.manager.stops == env.manager.spawns == 0
    assert session.allow_session_discovery is False
    mutations = [c for c in env.manager.commands if c["type"].startswith("set_")]
    assert [c["type"] for c in mutations] == ["set_model", "set_thinking_level"]
    assert all(c["id"].startswith("settings-") for c in mutations)


@pytest.mark.parametrize("body", [{"host": "other"}, {"model": []}, {"effort": "ultra"}, {"effort": 4},
    {"model": "provider/narrow", "effort": "high"}, {"model": "--bad", "effort": "low"}, {"extSessionId": "forged"}])
def test_invalid_pair_has_no_partial_effects(env, body):
    before = env.store.canvas(env.project_id)
    response = env.client.post(env.url + "/settings", json=body)
    assert response.status_code == 400, response.text
    assert env.store.canvas(env.project_id) == before
    assert not env.manager.commands
    assert env.manager.stops == 0


def test_unavailable_metadata_never_guesses_effort(env, monkeypatch):
    def unknown(host, model=""):
        result = capabilities(host, model)
        result.update(status="unavailable", error={"code": "offline"})
        result["selection"]["efforts"] = []
        return result
    monkeypatch.setattr(caps, "get_capabilities", unknown)
    before = env.store.canvas(env.project_id)
    response = env.client.post(env.url + "/settings", json={"model": "provider/new", "effort": "high"})
    assert response.status_code == 503
    assert env.store.canvas(env.project_id) == before
    response = env.client.post(env.url + "/settings", json={"model": None, "effort": ""})
    assert response.status_code == 200, response.text
    assert response.json()["capabilities"]["status"] == "unavailable"
    assert response.json()["effort_state"]["effective"] is None


@pytest.mark.parametrize("field,value", [("model", ""), ("model", None), ("effort", ""), ("effort", None)])
def test_reset_requires_verified_production_default_or_explicit_failure(env, field, value):
    node = {**saved(env), **build_host_settings_fields(saved(env), "pi", "provider/old", "high")}
    seed(env, node)
    before = env.store.canvas(env.project_id)
    response = env.client.post(env.url + "/settings", json={field: value})
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "agent_default_unverified"
    assert env.store.canvas(env.project_id) == before
    assert not env.manager.commands


def test_host_roundtrip_restores_own_binding_branches_and_listener_identity(env):
    node = {**saved(env), **build_host_settings_fields(saved(env), "pi", "provider/old", "low")}
    node["agentHostSessions"]["pi"] = {"extSessionId": "pi-original", "branches": [{"id": "pb", "session_id": "pi-original", "session_file": "original.jsonl"}], "current_branch": "pb"}
    node = {**node, **build_host_settings_fields(node, "codex", "provider/new", "high")}
    node["agentHostSessions"]["codex"] = {"extSessionId": "codex-thread", "branches": [], "current_branch": ""}
    node = {**node, **build_host_settings_fields(node, "pi", "provider/old", "low")}
    seed(env, node)
    session = run(env.manager.live(env.project_id))
    events, confirms = session.event_handlers, session.confirm_handlers
    for host, native_id in (("codex", "codex-thread"), ("pi", "pi-original"), ("codex", "codex-thread")):
        response = env.client.post(env.url + "/settings", json={"host": host})
        assert response.status_code == 200, response.text
        assert session.ext_session_id == native_id
        assert session.event_handlers is events and session.confirm_handlers is confirms
        assert run(env.manager.get_or_create_session(env.project_id, "agent")) is session
        assert saved(env)["agentHostSessions"] == node["agentHostSessions"]
        assert response.json()["effort_state"]["effective"] is None
    assert env.manager.spawns == 0
    assert env.manager.stops == 3
    restarted = AgentHostManager()
    restored = run(restarted.get_or_create_session(env.project_id, "agent"))
    with patch.object(api, "agent_host_manager", restarted):
        api._apply_node_host(env.project_id, "agent", restored)
    assert (restored.agent_cli, restored.ext_session_id, restored.model, restored.reasoning_effort) == ("codex", "codex-thread", "provider/new", "high")
    assert restored.allow_session_discovery is False


def test_explicit_empty_binding_clears_previous_in_memory_native_identity(env):
    seed(env, {**saved(env), **build_host_settings_fields(saved(env), "pi", "", None)})
    session = run(env.manager.get_or_create_session(env.project_id, "agent"))
    session.ext_session_id, session.session_id, session.session_file = "wrong-codex", "wrong-id", "wrong.jsonl"
    session.agent_cli = "codex"
    api._apply_node_host(env.project_id, "agent", session)
    assert (session.ext_session_id, session.session_id, session.session_file) == (None, None, None)
    assert session.allow_session_discovery is False


@pytest.mark.parametrize("failure", [("set_model", "nack"), ("set_model", "wrong_id"),
    ("set_thinking_level", "nack"), ("set_thinking_level", "wrong_id"), ("set_thinking_level", "clamp"),
    ("get_state", "wrong_id_after_write")])
def test_failed_ack_or_backread_rolls_back_without_false_persistence(env, failure):
    session = run(env.manager.live(env.project_id))
    before = env.store.canvas(env.project_id)
    env.manager.failure = failure
    response = env.client.post(env.url + "/settings", json={"model": "provider/new", "effort": "high"})
    assert response.status_code == 502, response.text
    assert env.store.canvas(env.project_id) == before
    assert env.manager.native["model"] == {"provider": "provider", "id": "old"}
    assert env.manager.native["thinkingLevel"] == "medium"
    assert session.model == "" and session.reasoning_effort is None


def test_native_busy_race_is_explicit_409_without_saving(env, monkeypatch):
    session = run(env.manager.live(env.project_id))
    before = env.store.canvas(env.project_id)
    async def became_busy(*args):
        session._turn_open = True
        raise RuntimeError("native became busy")
    monkeypatch.setattr(env.manager, "apply_runtime_settings", became_busy)
    response = env.client.post(env.url + "/settings", json={"model": "provider/new", "effort": "high"})
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "agent_busy"
    assert env.store.canvas(env.project_id) == before


@pytest.mark.parametrize("failure", [("fork", "nack"), ("fork", "wrong_id"), ("switch_session", "nack")])
def test_branch_failed_native_ack_restores_old_binding_without_cas(env, failure):
    node = {**saved(env), **build_host_settings_fields(saved(env), "pi", "", None)}
    node["agentHostSessions"]["pi"] = {"extSessionId": "pi-original", "current_branch": "original",
        "branches": [{"id": "original", "session_id": "pi-original", "session_file": "original.jsonl"},
                     {"id": "other", "session_id": "pi-fork", "session_file": "forked.jsonl"}]}
    node = {**node, **read_host_fields(node)}
    seed(env, node)
    session = run(env.manager.live(env.project_id))
    before = env.store.canvas(env.project_id)
    env.manager.failure = failure
    suffix, body = (("/fork", {"entry_id": "entry"}) if failure[0] == "fork" else ("/switch-branch", {"branch_id": "other"}))
    response = env.client.post(env.url + suffix, json=body)
    assert response.status_code == 502, response.text
    assert env.store.canvas(env.project_id) == before
    assert session.ext_session_id == env.manager.native["sessionId"] == "pi-original"
    assert session.session_file == env.manager.native["sessionFile"] == "original.jsonl"


def test_cas_retry_rebuilds_purely_and_preserves_concurrent_geometry(env, monkeypatch):
    run(env.manager.live(env.project_id))
    apply = env.store.apply_agent_host_mutations
    attempts = []
    def conflict_once(project_id, mutations, **kwargs):
        attempts.append(kwargs["base_revision"])
        if len(attempts) == 1:
            env.store.apply_mutations(project_id, [{"op": "upsert_node", "node": {**saved(env), "x": 777}}], base_revision=kwargs["base_revision"])
            raise RevisionConflict("raced geometry")
        return apply(project_id, mutations, **kwargs)
    monkeypatch.setattr(env.store, "apply_agent_host_mutations", conflict_once)
    response = env.client.post(env.url + "/settings", json={"model": "provider/new", "effort": "high"})
    assert response.status_code == 200, response.text
    assert len(attempts) == 2
    assert saved(env)["x"] == 777
    assert sum(c["type"] == "set_model" for c in env.manager.commands) == 1
    assert sum(c["type"] == "set_thinking_level" for c in env.manager.commands) == 1


@pytest.mark.parametrize("race", ["delete", "settings"])
def test_cas_deleted_or_competing_settings_never_resurrect_or_override(env, monkeypatch, race):
    session = run(env.manager.live(env.project_id))
    apply = env.store.apply_agent_host_mutations
    def competing(project_id, mutations, **kwargs):
        if race == "delete":
            env.store.apply_mutations(project_id, [{"op": "remove_node", "node_id": "agent"}], base_revision=kwargs["base_revision"])
        else:
            node = saved(env)
            apply(project_id, [{"op": "upsert_node", "node": {**node, **build_host_settings_fields(node, "pi", "provider/narrow", "low")}}], base_revision=kwargs["base_revision"])
        raise RevisionConflict("competing writer")
    monkeypatch.setattr(env.store, "apply_agent_host_mutations", competing)
    response = env.client.post(env.url + "/settings", json={"model": "provider/new", "effort": "high"})
    assert response.status_code == (404 if race == "delete" else 409), response.text
    if race == "delete":
        assert not any(n["id"] == "agent" for n in env.store.canvas(env.project_id)["nodes"])
    else:
        assert saved(env)["model"] == "provider/narrow"
    assert env.manager.native["model"]["id"] == "old"
    assert env.manager.native["thinkingLevel"] == "medium"
    assert session.model == ""


def test_settings_prompt_admission_race_releases_lock_but_abort_steer_work(env):
    async def scenario():
        session = await env.manager.live(env.project_id)
        entered, release = asyncio.Event(), asyncio.Event()
        async def admission():
            entered.set()
            await release.wait()
        env.manager.prompt_hook = admission
        prompt = asyncio.create_task(api._send_input(env.project_id, "agent", "prompt", {"message": "draft kept", "images": []}))
        await asyncio.wait_for(entered.wait(), timeout=2)
        settings = asyncio.create_task(api.set_agent_settings(env.project_id, "agent", {"host": "codex"}))
        await asyncio.sleep(0)
        assert not settings.done()
        await api._send_input(env.project_id, "agent", "steer", {"message": "steer still usable"})
        await api._send_input(env.project_id, "agent", "follow_up", {"message": "queued"})
        await api.send_agent_abort(env.project_id, "agent")
        release.set()
        assert (await prompt)["success"]
        with pytest.raises(HTTPException) as failed:
            await settings
        assert failed.value.status_code == 409 and failed.value.detail["code"] == "agent_busy"
        assert not env.manager.node_lock(env.project_id, "agent").locked()
        assert session._turn_open  # Lock was not held for the rest of inference.
    run(scenario())


def test_old_model_delegates_and_does_not_change_effort_or_other_host(env):
    node = {**saved(env), **build_host_settings_fields(saved(env), "codex", "provider/old", "low")}
    node = {**node, **build_host_settings_fields(node, "pi", "provider/old", "high")}
    seed(env, node)
    response = env.client.post(env.url + "/model", json={"model": "provider/new"})
    assert response.status_code == 200, response.text
    assert saved(env)["agentHostSettings"] == {"pi": {"model": "provider/new", "effort": "high"}, "codex": {"model": "provider/old", "effort": "low"}}


def test_branch_is_pi_only_busy_rejected_and_trusted_binding_atomic(env):
    session = run(env.manager.live(env.project_id))
    node = {**saved(env), **build_host_settings_fields(saved(env), "codex", "", None)}
    seed(env, node)
    for suffix, body in (("/fork", {"entry_id": "entry"}), ("/switch-branch", {"branch_id": "b"})):
        response = env.client.post(env.url + suffix, json=body)
        assert response.status_code == 400 and response.json()["detail"]["code"] == "agent_branch_unsupported"
    assert not env.manager.commands
    seed(env, {**saved(env), **build_host_settings_fields(saved(env), "pi", "", None)})
    session._retrying = True
    response = env.client.post(env.url + "/fork", json={"entry_id": "entry"})
    assert response.status_code == 409
    session._retrying = False
    response = env.client.post(env.url + "/fork", json={"entry_id": "entry", "name": "new"})
    assert response.status_code == 200, response.text
    node = saved(env)
    assert node["agentHostSessions"]["pi"] == {"extSessionId": "pi-fork", "branches": node["branches"], "current_branch": node["current_branch"]}
    assert node["extSessionId"] == "pi-fork"
    assert node["agentHostSessions"]["codex"] == {"extSessionId": "", "branches": [], "current_branch": ""}
    response = env.client.post(env.url + "/switch-branch", json={"branch_id": "branch-original"})
    assert response.status_code == 200, response.text
    assert saved(env)["extSessionId"] == session.ext_session_id == "pi-original"
    assert saved(env)["agentHostSessions"]["pi"]["current_branch"] == "branch-original"


def test_http_history_host_filter_legacy_pi_and_monotonic_node_cursor(env):
    session = run(env.manager.get_or_create_session(env.project_id, "agent"))
    path = Path(env.manager._events_log_path(session))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"seq":1,"event":{"type":"user_prompt","text":"legacy"}}\n'
                    '{"seq":2,"host":"codex","event":{"type":"user_prompt","text":"codex only"}}\n'
                    '{"seq":3,"host":"pi","event":{"type":"user_prompt","text":"pi only"}}\n', encoding="utf-8")
    original = path.read_bytes()
    for host, expected in (("pi", [1, 3]), ("codex", [2])):
        response = env.client.get(env.url + "/events", params={"host": host})
        assert response.status_code == 200, response.text
        assert [e["seq"] for e in response.json()["events"]] == expected
        assert response.json()["last_seq"] == 3
    assert path.read_bytes() == original
    seed(env, {**saved(env), **build_host_settings_fields(saved(env), "codex", "", None)})
    response = env.client.get(env.url + "/events")
    assert [e["seq"] for e in response.json()["events"]] == [2]
    api._apply_node_host(env.project_id, "agent", session)
    context, turns = env.manager._build_replay_context(session)
    assert turns == 1 and "codex only" in context and "legacy" not in context and "pi only" not in context
    env.manager._persist_event(session, {"type": "user_prompt", "text": "next codex"})
    response = env.client.get(env.url + "/events", params={"host": "codex", "since": 3})
    assert response.json()["last_seq"] == 4
    assert [e["seq"] for e in response.json()["events"]] == [4]
    assert path.read_bytes().startswith(original)
    assert env.manager.spawns == 0


def test_cas_rollback_failure_is_explicit_and_clears_false_effective_state(env, monkeypatch):
    session = run(env.manager.live(env.project_id))
    before = env.store.canvas(env.project_id)
    apply = env.manager.apply_runtime_settings
    calls = []
    async def cannot_restore(*args):
        calls.append(args[1:])
        if len(calls) > 1:
            raise RuntimeError("rollback native unavailable")
        return await apply(*args)
    async def conflict(*args, **kwargs):
        raise HTTPException(status_code=409, detail="CAS failed")
    monkeypatch.setattr(env.manager, "apply_runtime_settings", cannot_restore)
    monkeypatch.setattr(api, "_update_node_settings", conflict)
    response = env.client.post(env.url + "/settings", json={"model": "provider/new", "effort": "high"})
    assert response.status_code == 502 and response.json()["detail"]["code"] == "agent_rollback_failed"
    assert env.store.canvas(env.project_id) == before
    assert session.effort_state["effective"] is None
    assert session.effort_state["source"] == "pi:rollback-unconfirmed"
    assert session.model == "" and session.reasoning_effort is None


def test_settings_wins_admission_prompt_uses_committed_pair(env, monkeypatch):
    async def scenario():
        session = await env.manager.live(env.project_id)
        entered, release = asyncio.Event(), asyncio.Event()
        apply = env.manager.apply_runtime_settings
        async def blocked_apply(*args):
            entered.set()
            await release.wait()
            return await apply(*args)
        monkeypatch.setattr(env.manager, "apply_runtime_settings", blocked_apply)
        settings = asyncio.create_task(api.set_agent_settings(env.project_id, "agent", {"model": "provider/new", "effort": "high"}))
        await entered.wait()
        prompt = asyncio.create_task(api._send_input(env.project_id, "agent", "prompt", {"message": "unchanged draft", "images": []}))
        await asyncio.sleep(0)
        assert not prompt.done()
        release.set()
        assert (await settings)["success"] and (await prompt)["success"]
        assert (session.model, session.reasoning_effort) == ("provider/new", "high")
        assert env.manager.commands[-1]["type"] == "prompt"
        assert env.manager.commands[-1]["message"] == "unchanged draft"
    run(scenario())


def test_host_switch_waits_for_old_reader_before_cas_and_target_selection(env, monkeypatch):
    async def scenario():
        session = await env.manager.live(env.project_id)
        exited = asyncio.Event()
        session._reader_task = asyncio.create_task(exited.wait())
        switch = asyncio.create_task(api.set_agent_settings(env.project_id, "agent", {"host": "codex"}))
        for _ in range(100):
            if env.manager.stops:
                break
            await asyncio.sleep(0.001)
        assert env.manager.stops == 1 and not switch.done()
        assert saved(env)["agentCli"] == session.agent_cli == "pi"
        exited.set()
        assert (await switch)["success"]
        assert saved(env)["agentCli"] == session.agent_cli == "codex"
        assert env.manager.spawns == 0
    run(scenario())


def test_session_only_model_preserves_effort_without_creating_node(env):
    session = run(env.manager.get_or_create_session(env.project_id, "session-only"))
    session.reasoning_effort = "high"
    before = env.store.canvas(env.project_id)
    response = env.client.post(env.url.replace("/agent-nodes/agent", "/agent-nodes/session-only") + "/model", json={"model": "provider/new"})
    assert response.status_code == 200, response.text
    assert not response.json()["persisted"] and response.json()["effort"] == "high"
    assert env.store.canvas(env.project_id) == before


def test_normal_create_preserves_initial_model_without_trusted_binding_authority(env, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("ordinary create must stay untrusted")
    monkeypatch.setattr(env.store, "apply_agent_host_mutations", forbidden)
    response = env.client.post(f"/api/projects/{env.project_id}/agent-nodes", json={"node_id": "new-agent", "model": "provider/new"})
    assert response.status_code == 200, response.text
    session = env.manager.sessions[f"{env.project_id}:new-agent"]
    assert session.model == "provider/new"
    node = next(n for n in env.store.canvas(env.project_id)["nodes"] if n["id"] == "new-agent")
    assert node["agentHostSettings"]["pi"]["model"] == "provider/new"
    assert node["agentHostSessions"]["pi"]["extSessionId"] == ""
    assert session.allow_session_discovery is False


def test_legacy_unmapped_binding_retains_discovery_compatibility(env):
    node = {"id": "agent", "type": "smart-agent", "model": "provider/old"}
    seed(env, node)
    session = run(env.manager.get_or_create_session(env.project_id, "agent"))
    session.ext_session_id = "learned-legacy-id"
    api._apply_node_host(env.project_id, "agent", session)
    assert session.ext_session_id == "learned-legacy-id"
    assert session.allow_session_discovery is True
    assert "agentHostSettings" not in saved(env)


def test_stop_failure_or_cas_conflict_never_selects_new_host(env, monkeypatch):
    session = run(env.manager.live(env.project_id))
    before = env.store.canvas(env.project_id)
    async def cannot_stop(session):
        raise RuntimeError("old reader still alive")
    with patch.object(env.manager, "stop_session", cannot_stop):
        response = env.client.post(env.url + "/settings", json={"host": "codex"})
    assert response.status_code == 502 and response.json()["detail"]["code"] == "agent_stop_failed"
    assert env.store.canvas(env.project_id) == before and session.agent_cli == "pi"
    async def conflict(*args, **kwargs):
        raise HTTPException(status_code=409, detail="CAS failed")
    monkeypatch.setattr(api, "_update_node_settings", conflict)
    response = env.client.post(env.url + "/settings", json={"host": "codex"})
    assert response.status_code == 409
    assert env.store.canvas(env.project_id) == before and session.agent_cli == "pi"
    assert env.manager.spawns == 0


def test_branch_commits_binding_and_branches_in_one_cas_and_rolls_back_deletion(env, monkeypatch):
    session = run(env.manager.live(env.project_id))
    apply = env.store.apply_agent_host_mutations
    writes = []
    def capture(project_id, mutations, **kwargs):
        writes.extend(deepcopy(mutations))
        return apply(project_id, mutations, **kwargs)
    monkeypatch.setattr(env.store, "apply_agent_host_mutations", capture)
    response = env.client.post(env.url + "/fork", json={"entry_id": "entry"})
    assert response.status_code == 200, response.text
    assert len(writes) == 1
    assert writes[0]["node"]["extSessionId"] == "pi-fork"
    assert len(writes[0]["node"]["agentHostSessions"]["pi"]["branches"]) == 2
    def deletion(project_id, mutations, **kwargs):
        env.store.apply_mutations(project_id, [{"op": "remove_node", "node_id": "agent"}], base_revision=kwargs["base_revision"])
        raise RevisionConflict(env.store.canvas(project_id))
    monkeypatch.setattr(env.store, "apply_agent_host_mutations", deletion)
    # Start from original so native rollback has an independently known path.
    session.session_id = session.ext_session_id = "pi-original"
    session.session_file = "original.jsonl"
    response = env.client.post(env.url + "/fork", json={"entry_id": "entry"})
    assert response.status_code == 404, response.text
    assert session.session_file == "original.jsonl" and session.ext_session_id == "pi-original"
    assert not any(n["id"] == "agent" for n in env.store.canvas(env.project_id)["nodes"])


def test_ws_listener_survives_switch_and_filters_active_and_readonly_host(env):
    with env.client.websocket_connect("ws://127.0.0.1:3194" + env.url + "/stream") as ws:
        initial = ws.receive_json()
        assert initial["host"] == "pi"
        session = env.manager.sessions[f"{env.project_id}:agent"]
        handlers = session.event_handlers
        with env.client.websocket_connect("ws://127.0.0.1:3194" + env.url + "/stream?host=pi") as readonly:
            assert not readonly.receive_json()["read_only"]
            response = env.client.post(env.url + "/settings", json={"host": "codex"})
            assert response.status_code == 200, response.text
            assert session.event_handlers is handlers and len(handlers) == 2
            readonly.send_json({"type": "prompt", "message": "must not spawn", "request_id": "ro"})
            assert readonly.receive_json()["code"] == "agent_host_read_only"
            # Use the test portal's event loop: no real reader or background task.
            ws.portal.call(handlers[0], session, {"type": "message_update", "host": "pi", "text": "late-old-host"})
            ws.portal.call(env.manager._dispatch_event, session, {"type": "message_update", "host": "codex", "text": "new-host"})
            assert ws.receive_json()["text"] == "new-host"
            readonly.portal.call(handlers[1], session, {"type": "message_update", "text": "legacy-pi"})
            assert readonly.receive_json()["text"] == "legacy-pi"
    assert len(session.event_handlers) == 0
    assert env.manager.spawns == 0
