"""Gate 1 pure config fixtures: no main, stores, CLI, services, or user paths."""
from __future__ import annotations

from copy import deepcopy
import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest


# app.services.__init__ eagerly imports the application service graph. Load just
# this stdlib-only module so these unit fixtures cannot open application state.
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
