"""A2 pure-service contract: no router, store, browser, or generation fixtures."""
from __future__ import annotations

import copy
import itertools
import json
import math
import random
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.constants import NODE_TYPE_AGENT, NODE_TYPE_GROUP, NODE_TYPE_IMAGE, NODE_TYPE_LOOP, NODE_TYPE_PROMPT
from app.services import agent_node_state as state
from app.services.agent_node_state import ownership_fields, project_nodes


OWNERSHIP_KEYS = {"createdBy", "agentNodeId", "userTouchedAt", "promptEditedByUser"}
SUMMARY_KEYS = {
    "id", "type", "title", "x", "y", "w", "h", "group_id", *OWNERSHIP_KEYS,
    "agentStatus", "lastFailure", "incoming", "outgoing", "status",
}
OPTIONAL_KEYS = {"prompt_preview", "refs", "run"}
GROUP_KEYS = {
    "id", "type", "title", "x", "y", "w", "h", "items", "createdBy",
    "agentNodeId", "refFrame", "chatFrame",
}


def node(node_id="n", node_type=NODE_TYPE_IMAGE, **values):
    return {"id": node_id, "type": node_type, **values}


def project_one(value=None, **kwargs):
    return project_nodes({"nodes": [node() if value is None else value]}, **kwargs)["nodes"][0]


def by_id(result):
    return {item["id"]: item for item in result["nodes"]}


class OwnershipDefaults(unittest.TestCase):
    def test_new_user_and_agent_defaults_have_only_four_fields(self):
        self.assertEqual(ownership_fields(), {
            "createdBy": "user", "agentNodeId": None,
            "userTouchedAt": None, "promptEditedByUser": False,
        })
        self.assertEqual(ownership_fields("agent-甲"), {
            "createdBy": "agent", "agentNodeId": "agent-甲",
            "userTouchedAt": None, "promptEditedByUser": False,
        })

    def test_defaults_are_fresh_and_identifier_is_opaque(self):
        first = ownership_fields("agent-1")
        first["promptEditedByUser"] = True
        first["createdBy"] = "user"
        second = ownership_fields("agent-1")
        self.assertIsNot(first, second)
        self.assertEqual(set(second), OWNERSHIP_KEYS)
        self.assertEqual(second["createdBy"], "agent")
        self.assertFalse(second["promptEditedByUser"])
        self.assertEqual(ownership_fields(" agent-1 ")["agentNodeId"], " agent-1 ")

    def test_invalid_creation_context_is_a_shaped_error(self):
        for value in (None, 0, False, [], {}, " ", "\t\n"):
            with self.subTest(value=value), self.assertRaises(HTTPException) as caught:
                ownership_fields(value)
            self.assertEqual(caught.exception.status_code, 400)
            self.assertEqual(caught.exception.detail["code"], "INVALID_ARGUMENT")

    def test_legacy_node_defaults_do_not_infer_agent_from_type_or_other_ids(self):
        for node_type in (NODE_TYPE_IMAGE, NODE_TYPE_PROMPT, NODE_TYPE_AGENT):
            with self.subTest(node_type=node_type):
                result = project_one(node("agent-result", node_type, agentNodeId="agent-1"))
                self.assertEqual({key: result[key] for key in OWNERSHIP_KEYS}, ownership_fields())

    def test_corrupt_creation_source_preserves_legal_protection_fields(self):
        for source in (None, "", "unknown", "Agent", 1, True, [], {}):
            with self.subTest(source=source):
                result = project_one(node(createdBy=source, agentNodeId="agent-1",
                                          userTouchedAt=123.5, promptEditedByUser=True))
                self.assertEqual(result["createdBy"], "user")
                self.assertIsNone(result["agentNodeId"])
                self.assertEqual(result["userTouchedAt"], 123.5)
                self.assertIs(result["promptEditedByUser"], True)

    def test_agent_without_valid_id_is_user_and_user_cannot_inherit_stale_id(self):
        for agent_id in (None, "", " ", "\n", False, 123, [], {}):
            with self.subTest(agent_id=agent_id):
                result = project_one(node(createdBy="agent", agentNodeId=agent_id,
                                          userTouchedAt=0, promptEditedByUser=True))
                self.assertEqual(result["createdBy"], "user")
                self.assertIsNone(result["agentNodeId"])
                self.assertEqual(result["userTouchedAt"], 0)
                self.assertIs(result["promptEditedByUser"], True)
        result = project_one(node(createdBy="user", agentNodeId="agent-1"))
        self.assertIsNone(result["agentNodeId"])

    def test_protection_types_are_not_truthiness_coerced(self):
        for value in (None, "123", True, False, -1, math.inf, -math.inf, math.nan, [], {}):
            with self.subTest(timestamp=value):
                self.assertIsNone(project_one(node(userTouchedAt=value))["userTouchedAt"])
        for value in (0, 1, 123.25, 10**400):
            with self.subTest(timestamp=value):
                self.assertEqual(project_one(node(userTouchedAt=value))["userTouchedAt"], value)
        for value in (None, "true", 1, 0, [], {}, False):
            with self.subTest(flag=value):
                self.assertIs(project_one(node(promptEditedByUser=value))["promptEditedByUser"], False)

    def test_created_by_filters_creation_history_not_current_control(self):
        canvas = {"nodes": [
            node("a", **ownership_fields("agent-1"), title="owned"),
            node("b", createdBy="agent", agentNodeId="agent-2", userTouchedAt=99,
                 promptEditedByUser=True),
            node("u", **ownership_fields()),
            node("old", agentNodeId="agent-1"),
            node("corrupt", createdBy="agent", agentNodeId=" "),
        ]}
        agents = project_nodes(canvas, created_by="agent")
        self.assertEqual(list(by_id(agents)), ["a", "b"])
        self.assertEqual(agents["count"], 2)
        users = project_nodes(canvas, created_by="user")
        self.assertEqual(list(by_id(users)), ["u", "old", "corrupt"])
        self.assertEqual(users["count"], 3)


class SummaryProjection(unittest.TestCase):
    def test_empty_and_malformed_canvas_collections_are_safe(self):
        for canvas in ({}, {"nodes": []}, None, [], "bad", {"nodes": {}},
                       {"nodes": [None, 1, "n", []], "connections": {}},
                       {"nodes": (node(),)}):
            with self.subTest(canvas=canvas):
                self.assertEqual(project_nodes(canvas), {"nodes": [], "count": 0, "groups": []})

    def test_minimal_summary_and_default_full_blocks(self):
        result = project_one()
        self.assertEqual(set(result), SUMMARY_KEYS | OPTIONAL_KEYS)
        self.assertEqual(result["id"], "n")
        self.assertEqual(result["type"], NODE_TYPE_IMAGE)
        self.assertEqual((result["x"], result["y"]), (0, 0))
        for key in ("w", "h", "group_id", "agentStatus", "lastFailure"):
            self.assertIsNone(result[key])
        self.assertEqual(result["title"], "")
        self.assertEqual(result["status"], "")
        self.assertEqual(result["incoming"], [])
        self.assertEqual(result["outgoing"], [])
        self.assertEqual(result["prompt_preview"], "")
        self.assertEqual(result["refs"], [])
        self.assertEqual(result["run"], {"model": None, "aspect_ratio": None, "count": None})

    def test_legacy_title_status_and_text_fallbacks(self):
        result = project_one(node(title="", text="甲😀" * 60, status="", generationState="running"))
        self.assertEqual(result["title"], ("甲😀" * 60)[:50])
        self.assertEqual(result["status"], "running")
        result = project_one(node(title="Named", text="other", status="failed", generationState="done"))
        self.assertEqual(result["title"], "Named")
        self.assertEqual(result["status"], "failed")
        result = project_one(node(title={"raw": "secret"}, text=[], status=["failed"], generationState=8))
        self.assertEqual(result["title"], "")
        self.assertEqual(result["status"], "")

    def test_dimensions_only_use_actual_finite_canvas_values(self):
        for value in (None, "300", True, False, [], {}, math.inf, -math.inf, math.nan):
            with self.subTest(value=value):
                result = project_one(node(x=value, y=value, w=value, h=value,
                                          width=2048, height=1024, images=[{"width": 300, "height": 200}]))
                self.assertEqual((result["x"], result["y"]), (0, 0))
                self.assertIsNone(result["w"])
                self.assertIsNone(result["h"])
        for value in (0, -1, 22, 0.5, 10**400):
            with self.subTest(value=value):
                result = project_one(node(x=value, y=value, w=value, h=value))
                self.assertEqual([result[key] for key in ("x", "y", "w", "h")], [value] * 4)
        self.assertIsNone(project_one(node(width=2048, height=1024))["w"])

    def test_agent_status_is_stored_string_not_inferred_state_machine(self):
        for value in ("", "pending", "arbitrary-future-status"):
            with self.subTest(value=value):
                self.assertEqual(project_one(node(agentStatus=value))["agentStatus"], value)
        for value in (None, 1, True, [], {}):
            with self.subTest(value=value):
                self.assertIsNone(project_one(node(agentStatus=value, status="running"))["agentStatus"])

    def test_last_failure_only_projects_a_valid_failure_shape(self):
        failure = {"code": "FUTURE_CODE", "message": "A failure.", "hint": "", "raw": {"token": "secret"}}
        result = project_one(node(lastFailure=failure))["lastFailure"]
        self.assertEqual(result, {"code": "FUTURE_CODE", "message": "A failure.", "hint": ""})
        self.assertIsNot(result, failure)
        for value in (None, "error", [], {}, {"code": "C"},
                      {"code": "C", "message": "M", "hint": None},
                      {"code": {}, "message": "M", "hint": "H"},
                      {"code": " ", "message": "M", "hint": "H"},
                      {"code": "C", "message": "", "hint": "H"}):
            with self.subTest(value=value):
                self.assertIsNone(project_one(node(lastFailure=value))["lastFailure"])

    def test_connections_keep_order_duplicates_dangling_and_cross_group_context(self):
        canvas = {"nodes": [node("a"), node("b"), node("g", NODE_TYPE_GROUP, items=["b"])],
                  "connections": [
                      {"from": "a", "to": "b", "kind": "flow"},
                      {"from": "ghost", "to": "b", "kind": "input"},
                      {"from": "a", "to": "b"}, {"from": "b", "to": "a"},
                      {"from": "b", "to": "b"}, None, [], {},
                      {"from": [], "to": "b"}, {"from": "a", "to": {}},
                      {"from": "", "to": "b"}, {"from": "b", "to": False},
                  ]}
        result = project_nodes(canvas, group_id="g", fields=[])
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["nodes"][0]["incoming"], ["a", "ghost", "a", "b"])
        self.assertEqual(result["nodes"][0]["outgoing"], ["a", "b"])


class QueryValidation(unittest.TestCase):
    def assert_argument_error(self, **kwargs):
        with self.assertRaises(HTTPException) as caught:
            project_nodes({"nodes": [node()]}, **kwargs)
        self.assertEqual(caught.exception.status_code, 400)
        detail = caught.exception.detail
        self.assertEqual(detail["code"], "INVALID_ARGUMENT")
        self.assertIsInstance(detail["message"], str)
        self.assertTrue(detail["message"])
        self.assertIsInstance(detail["hint"], str)
        self.assertTrue(detail["hint"])
        self.assertIs(detail["retryable"], False)
        return caught.exception

    def test_invalid_fields_are_never_coerced_into_an_array(self):
        for fields in ("prompt", "prompt,run", "", 0, True, {}, (), {"prompt"},
                       [None], [1], [False], [[]], [{}], ["Prompt"], [" prompt"],
                       ["prompt,run"], ["summary", "unknown"]):
            with self.subTest(fields=fields):
                self.assert_argument_error(fields=fields)

    def test_none_is_internal_omitted_sentinel(self):
        self.assertEqual(project_one(), project_one(fields=None, group_id=None, created_by=None))
        self.assertEqual(set(project_one(fields=None)), SUMMARY_KEYS | OPTIONAL_KEYS)

    def test_all_field_combinations_always_include_summary(self):
        for count in range(5):
            for selection in itertools.combinations(("summary", "prompt", "refs", "run"), count):
                with self.subTest(fields=selection):
                    expected = SUMMARY_KEYS | {
                        {"prompt": "prompt_preview", "refs": "refs", "run": "run"}[field]
                        for field in selection if field != "summary"
                    }
                    self.assertEqual(set(project_one(fields=list(selection))), expected)
        self.assertEqual(project_one(fields=[]), project_one(fields=["summary"]))

    def test_repeated_fields_are_deduplicated(self):
        self.assertEqual(project_one(fields=["prompt", "run", "prompt", "summary", "run"]),
                         project_one(fields=["summary", "prompt", "run"]))
        with patch.object(state, "_project_prompt", return_value="p") as prompt:
            self.assertEqual(project_one(fields=["prompt"] * 10)["prompt_preview"], "p")
        self.assertEqual(prompt.call_count, 1)

    def test_unrequested_blocks_are_not_parsed_or_returned(self):
        with patch.object(state, "_project_prompt", side_effect=AssertionError("prompt parsed")), \
             patch.object(state, "_project_refs", side_effect=AssertionError("refs parsed")), \
             patch.object(state, "_project_run", side_effect=AssertionError("run parsed")):
            self.assertEqual(set(project_one(fields=[])), SUMMARY_KEYS)
        for field, helper in (("prompt", "_project_prompt"), ("refs", "_project_refs"), ("run", "_project_run")):
            with self.subTest(field=field), patch.object(state, helper, wraps=getattr(state, helper)) as call:
                project_one(fields=[field])
                self.assertEqual(call.call_count, 1)

    def test_filtered_out_nodes_do_not_parse_optional_blocks(self):
        with patch.object(state, "_project_prompt", side_effect=AssertionError("parsed filtered node")), \
             patch.object(state, "_project_refs", side_effect=AssertionError("parsed filtered node")), \
             patch.object(state, "_project_run", side_effect=AssertionError("parsed filtered node")):
            self.assertEqual(project_nodes({"nodes": [node()]}, created_by="agent")["count"], 0)

    def test_invalid_group_and_creator_filters_are_shaped_errors(self):
        for value in ("", " ", "\t\n", 1, 0, False, [], {}, ("g",)):
            with self.subTest(group_id=value):
                self.assert_argument_error(group_id=value)
        for value in ("", " ", "Agent", "all", 1, False, [], {}, ("agent",)):
            with self.subTest(created_by=value):
                self.assert_argument_error(created_by=value)

    def test_group_not_found_vs_existing_non_group_have_distinct_statuses(self):
        with self.assertRaises(HTTPException) as caught:
            project_nodes({"nodes": [node()]}, group_id="missing")
        self.assertEqual(caught.exception.status_code, 404)
        self.assertEqual(caught.exception.detail["code"], "NODE_NOT_FOUND")
        self.assertEqual(caught.exception.detail["details"], {"group_id": "missing"})
        self.assertIs(caught.exception.detail["retryable"], False)
        error = self.assert_argument_error(group_id="n")
        self.assertEqual(error.detail["details"], {"group_id": "n"})


class GroupProjection(unittest.TestCase):
    def fixture(self):
        return {"revision": 42, "updated_at": 123, "nodes": [
            node("g1", NODE_TYPE_GROUP, items=["a", "a", "missing", "g1", "g2", [], {}, 1, ""],
                 x=10, y=20, w=600, h=300, title="Section 1", refFrame=True),
            node("a", **ownership_fields("agent-1")),
            node("g2", NODE_TYPE_GROUP, items=["a", "b"], **ownership_fields("agent-2"), chatFrame=True),
            node("b", **ownership_fields()),
            node("empty", NODE_TYPE_GROUP, items=[], images=[{"asset_id": "inside-group"}]),
            node("near", x=10, y=20, group_id="g1", chatFrameId="g1", agentNodeId="g1"),
        ]}

    def test_groups_are_an_index_and_group_nodes_stay_in_nodes(self):
        result = project_nodes(self.fixture(), fields=[])
        self.assertEqual(list(by_id(result)), ["g1", "a", "g2", "b", "empty", "near"])
        self.assertEqual(result["count"], 6)
        self.assertEqual([g["id"] for g in result["groups"]], ["g1", "g2", "empty"])
        self.assertEqual(result["groups"][0], {
            "id": "g1", "type": NODE_TYPE_GROUP, "title": "Section 1", "x": 10, "y": 20,
            "w": 600, "h": 300, "items": ["a", "g2"], "createdBy": "user", "agentNodeId": None,
            "refFrame": True, "chatFrame": False,
        })
        for group in result["groups"]:
            self.assertEqual(set(group), GROUP_KEYS)
        summaries = by_id(result)
        self.assertIsNone(summaries["g1"]["group_id"])
        self.assertEqual(summaries["g2"]["group_id"], "g1")
        self.assertIsNone(summaries["near"]["group_id"])

    def test_canonical_parent_and_filters_use_first_containing_group(self):
        canvas = self.fixture()
        all_nodes = by_id(project_nodes(canvas))
        self.assertEqual(all_nodes["a"]["group_id"], "g1")
        self.assertEqual(list(by_id(project_nodes(canvas, group_id="g1"))), ["a", "g2"])
        second = project_nodes(canvas, group_id="g2")
        self.assertEqual(list(by_id(second)), ["b"])
        self.assertEqual(second["groups"][0]["items"], ["a", "b"])
        # Canvas order, not title/ID/position, determines the canonical parent.
        canvas["nodes"] = [canvas["nodes"][2], canvas["nodes"][0], canvas["nodes"][1], *canvas["nodes"][3:]]
        self.assertEqual(by_id(project_nodes(canvas))["a"]["group_id"], "g2")
        self.assertEqual(list(by_id(project_nodes(canvas, group_id="g1"))), ["g2"])

    def test_group_filter_is_direct_not_recursive_or_geometric(self):
        result = project_nodes(self.fixture(), group_id="g1")
        self.assertEqual(list(by_id(result)), ["a", "g2"])
        self.assertNotIn("g1", by_id(result))
        self.assertNotIn("b", by_id(result))
        self.assertNotIn("near", by_id(result))
        self.assertEqual([g["id"] for g in result["groups"]], ["g1"])

    def test_empty_group_and_absorbed_images_never_invent_nodes(self):
        result = project_nodes(self.fixture(), group_id="empty")
        self.assertEqual(result["nodes"], [])
        self.assertEqual(result["count"], 0)
        self.assertEqual(len(result["groups"]), 1)
        self.assertEqual(result["groups"][0]["items"], [])
        self.assertNotIn("inside-group", by_id(project_nodes(self.fixture())))

    def test_created_by_does_not_filter_groups_and_intersects_group_members(self):
        canvas = self.fixture()
        self.assertEqual([g["id"] for g in project_nodes(canvas, created_by="agent")["groups"]],
                         ["g1", "g2", "empty"])
        result = project_nodes(canvas, group_id="g1", created_by="agent")
        self.assertEqual(list(by_id(result)), ["a", "g2"])
        self.assertEqual(result["groups"][0]["createdBy"], "user")
        result = project_nodes(canvas, group_id="g2", created_by="agent")
        self.assertEqual(result["count"], 0)
        self.assertEqual([g["id"] for g in result["groups"]], ["g2"])

    def test_malformed_group_members_flags_and_duplicate_ids_are_safe(self):
        for value in (None, "n", {}, 2, ("n",)):
            with self.subTest(items=value):
                result = project_nodes({"nodes": [node("g", NODE_TYPE_GROUP, items=value, refFrame="true", chatFrame=1)]})
                self.assertEqual(result["groups"][0]["items"], [])
                self.assertIs(result["groups"][0]["refFrame"], False)
                self.assertIs(result["groups"][0]["chatFrame"], False)
        result = project_nodes({"nodes": [node("g", NODE_TYPE_GROUP, items=["n"]),
                                            node("g", NODE_TYPE_GROUP, items=["other"]),
                                            node("n"), node("other")]})
        self.assertEqual(len(result["groups"]), 1)
        self.assertEqual(result["groups"][0]["items"], ["n"])
        self.assertEqual(by_id(result)["n"]["group_id"], "g")
        self.assertIsNone(by_id(result)["other"]["group_id"])

    def test_nested_cycles_do_not_recurse_or_include_self(self):
        canvas = {"nodes": [node("g1", NODE_TYPE_GROUP, items=["g1", "g2"]),
                            node("g2", NODE_TYPE_GROUP, items=["g1", "g2"])]}
        self.assertEqual(list(by_id(project_nodes(canvas, group_id="g1"))), ["g2"])
        self.assertEqual(list(by_id(project_nodes(canvas, group_id="g2"))), ["g1"])


class PromptProjection(unittest.TestCase):
    def preview(self, **values):
        return project_one(node(**values))["prompt_preview"]

    def test_prompt_node_text_first_including_current_empty(self):
        for text in ("current text", "", " "):
            with self.subTest(text=text):
                result = project_one(node(node_type=NODE_TYPE_PROMPT, text=text, promptDraftText="old draft",
                                          prompt="old prompt", runPrompt="old run"))
                self.assertEqual(result["prompt_preview"], text)
        self.assertEqual(project_one(node(node_type=NODE_TYPE_PROMPT, text=None, prompt="fallback"))["prompt_preview"],
                         "fallback")

    def test_editable_string_precedence_including_explicit_blank(self):
        order = ("promptDraftText", "prompt", "runPrompt", "text")
        for index, key in enumerate(order):
            for current in ("current", "", " \n"):
                with self.subTest(key=key, current=current):
                    values = {earlier: {"bad": "not text"} for earlier in order[:index]}
                    values.update({later: "old " + later for later in order[index + 1:]})
                    values[key] = current
                    self.assertEqual(self.preview(**values), current)
        self.assertEqual(self.preview(promptDraftHtml="<b>secret</b>", runModelPrompt="private expansion"), "")
        self.assertEqual(self.preview(promptDraftText=[], prompt={}, runPrompt=False, text=1), "")

    def test_preview_limit_is_80_unicode_codepoints_without_ellipsis(self):
        for value in ("a" * 81, "中文" * 50, "😀" * 81, "e\u0301" * 45, "👨‍👩‍👧‍👦" * 15):
            with self.subTest(value=value[:10]):
                preview = self.preview(promptDraftText=value)
                self.assertEqual(preview, value[:80])
                self.assertEqual(len(preview), 80)
                self.assertFalse(preview.endswith("…"))
        self.assertEqual(self.preview(prompt="😀" * 80), "😀" * 80)
        self.assertEqual(self.preview(prompt="a" * 79), "a" * 79)

    def test_loop_uses_real_string_fields_not_run_prompt_or_generated_defaults(self):
        current = node(node_type=NODE_TYPE_LOOP, variablePrompts=["first 《计数》", "second"],
                       variablePrompt="old", runPrompt="old execution", loopStart=10, showPrompt=True)
        self.assertEqual(project_one(current)["prompt_preview"], "first 《计数》\nsecond")
        current["variablePrompts"] = [""]
        self.assertEqual(project_one(current)["prompt_preview"], "")
        current["variablePrompts"] = ["", "new"]
        self.assertEqual(project_one(current)["prompt_preview"], "\nnew")
        current["variablePrompts"] = []
        self.assertEqual(project_one(current)["prompt_preview"], "old")
        current["variablePrompt"] = ""
        self.assertEqual(project_one(current)["prompt_preview"], "")
        self.assertEqual(project_one(node(node_type=NODE_TYPE_LOOP))["prompt_preview"], "")

    def test_loop_malformed_fields_never_stringify_objects_or_html(self):
        for values, expected in (([{}, None, "valid", 7, []], "valid"), ([{}, None, 7], ""),
                                 (["😀" * 70, "中" * 20], "😀" * 70 + "\n" + "中" * 9)):
            with self.subTest(values=values):
                self.assertEqual(project_one(node(node_type=NODE_TYPE_LOOP, variablePrompts=values,
                                                  runPrompt="stale"))["prompt_preview"], expected)
        self.assertEqual(project_one(node(node_type=NODE_TYPE_LOOP, variablePrompts={},
                                          variablePrompt="legacy"))["prompt_preview"], "legacy")
        # The stored structure is variablePrompts, not an invented loopPromptFields key.
        self.assertEqual(project_one(node(node_type=NODE_TYPE_LOOP, loopPromptFields=[{"html": "secret"}]))["prompt_preview"], "")

    def test_loop_preview_stops_before_reading_the_rest_of_a_large_list(self):
        class BoundedFields(list):
            def __iter__(self):
                yield "😀" * 100_000
                raise AssertionError("unnecessary prompt traversal")

        result = project_one(node(node_type=NODE_TYPE_LOOP, variablePrompts=BoundedFields(["placeholder"])))
        self.assertEqual(result["prompt_preview"], "😀" * 80)


class RefsProjection(unittest.TestCase):
    def test_authoritative_asset_list_sanitizes_ids_and_numbers_stored_order(self):
        result = project_one(node(reference_asset_ids=[" z-2 ", "a.1", "z-2", "", " ", None,
                                                        False, 2, {}, [], "https://host/image.png",
                                                        "data:image/png;base64,abcd", "a/b", "x" * 201],
                                  runInputRefs=[{"asset_id": "stale"}], images=[{"asset_id": "output"}]))
        self.assertEqual(result["refs"], [
            {"n": 1, "role": "general", "asset_id": "z-2"},
            {"n": 2, "role": "general", "asset_id": "a.1"},
            {"n": 3, "role": "general", "asset_id": "z-2"},
        ])

    def test_present_empty_or_corrupt_asset_list_never_falls_back(self):
        for value in ([], None, "asset-1", {}, False, [None], [{"asset_id": "not-an-id-string"}]):
            with self.subTest(value=value):
                self.assertEqual(project_one(node(reference_asset_ids=value,
                                                  runInputRefs=[{"asset_id": "stale"}]))["refs"], [])

    def test_missing_asset_list_reads_only_valid_snapshot_asset_ids(self):
        refs = [None, "not an object", {"url": "https://host/image.png"},
                {"asset_id": " a-1 ", "url": "data:image/png;base64,secret", "name": "private"},
                {"asset_id": "a-2", "role": "detail", "extra": {"secret": True}},
                {"asset_id": "", "role": "product"}, {"asset_id": 2},
                {"asset_id": "a-3", "role": "original"}]
        self.assertEqual(project_one(node(runInputRefs=refs))["refs"], [
            {"n": 1, "role": "general", "asset_id": "a-1"},
            {"n": 2, "role": "detail", "asset_id": "a-2"},
            {"n": 3, "role": "general", "asset_id": "a-3"},
        ])

    def test_only_product_roles_are_preserved_without_translating_local_edit_roles(self):
        roles = ["product", "detail", "layout", "scene_style", "general",
                 "original", "references", "prompt", "Product", " detail ", None, {}, []]
        refs = project_one(node(runInputRefs=[{"asset_id": "asset-1", "role": role} for role in roles]))["refs"]
        self.assertEqual([ref["role"] for ref in refs], roles[:5] + ["general"] * (len(roles) - 5))
        self.assertEqual([ref["n"] for ref in refs], list(range(1, len(roles) + 1)))

    def test_no_assets_are_invented_from_images_urls_connections_or_other_snapshots(self):
        canvas = {"nodes": [node("source", images=[{"asset_id": "upstream"}]),
                            node("target", images=[{"asset_id": "own-output"}],
                                 runInputRefs=[{"url": "data:image/png;base64,secret"}],
                                 runPromptRefs=[{"asset_id": "not-input-snapshot"}],
                                 promptDraftHtml="<img src='secret'>")],
                  "connections": [{"from": "source", "to": "target", "kind": "input", "role": "product"}]}
        self.assertEqual(by_id(project_nodes(canvas))["target"]["refs"], [])
        for value in (None, {}, "asset-1", 3):
            with self.subTest(value=value):
                self.assertEqual(project_one(node(runInputRefs=value))["refs"], [])


class RunProjection(unittest.TestCase):
    def test_saved_settings_take_precedence_and_only_three_values_are_projected(self):
        value = node(model="old-model", count=8, aspect_ratio="1:1", runSettings={
            "model": " new-model ", "count": 3, "aspect_ratio": " 7:5 ", "ratio": "wide",
            "provider_id": "secret", "api_key": "never-project", "other": {"blob": "x" * 10_000},
        })
        self.assertEqual(project_one(value)["run"], {"model": "new-model", "count": 3, "aspect_ratio": "7:5"})

    def test_legacy_top_level_values_and_per_field_fallback(self):
        for settings in (None, [], "bad", {}, {"model": "", "count": 0, "aspect_ratio": []}):
            with self.subTest(settings=settings):
                self.assertEqual(project_one(node(model="legacy", count=2, aspect_ratio="4:3",
                                                  runSettings=settings))["run"],
                                 {"model": "legacy", "count": 2, "aspect_ratio": "4:3"})
        self.assertEqual(project_one(node(model="legacy", count=2, aspect_ratio="4:3",
                                          runSettings={"model": "current", "count": False, "ratio": "story"}))["run"],
                         {"model": "current", "count": 2, "aspect_ratio": "9:16"})

    def test_all_current_browser_ratio_presets(self):
        presets = {
            "square": "1:1", "portrait": "2:3", "landscape": "3:2", "portrait43": "3:4",
            "landscape43": "4:3", "story": "9:16", "wide": "16:9", "ultrawide": "21:9",
            "ultratall": "9:21", "portrait45": "4:5", "landscape45": "5:4", "portrait14": "1:4",
            "landscape14": "4:1", "portrait18": "1:8", "landscape18": "8:1",
        }
        for ratio, expected in presets.items():
            with self.subTest(ratio=ratio):
                self.assertEqual(project_one(node(runSettings={"ratio": ratio}))["run"]["aspect_ratio"], expected)

    def test_custom_and_source_ratios_only_use_explicit_saved_ratio(self):
        for ratio in ("custom", "source"):
            for custom in ("7:5", " 1.5:2 ", "1000:777"):
                with self.subTest(ratio=ratio, custom=custom):
                    self.assertEqual(project_one(node(runSettings={"ratio": ratio, "customRatio": custom}))["run"]["aspect_ratio"],
                                     custom.strip())
            value = node(width=2048, height=1024, w=600, h=300, images=[{"width": 600, "height": 400}],
                         runSettings={"ratio": ratio, "customSize": "2048x1024", "width": 2048,
                                      "height": 1024, "customRatioWidth": 7, "customRatioHeight": 5})
            self.assertIsNone(project_one(value)["run"]["aspect_ratio"])
            value["aspect_ratio"] = "5:4"
            self.assertEqual(project_one(value)["run"]["aspect_ratio"], "5:4")

    def test_invalid_ratio_values_do_not_fabricate_square_or_derive_size(self):
        for value in (None, "", "auto", "custom", "0:3", "1:0", "-1:2", "NaN:2", "1/2", "2048x1024", [], {}, True):
            with self.subTest(value=value):
                self.assertIsNone(project_one(node(aspect_ratio=value,
                                                   runSettings={"aspect_ratio": value, "ratio": value,
                                                                "customRatio": value}))["run"]["aspect_ratio"])
        self.assertEqual(project_one(node(runSettings={"aspect_ratio": "bad", "ratio": "wide"}))["run"]["aspect_ratio"], "16:9")
        self.assertIsNone(project_one(node(runSettings={"customRatio": "7:5"}))["run"]["aspect_ratio"])

    def test_model_and_count_require_reliably_saved_values(self):
        for value in (None, "", " ", 1, False, [], {}):
            with self.subTest(model=value):
                self.assertIsNone(project_one(node(model=value, runSettings={"model": value}))["run"]["model"])
        for value in (None, "2", 0, -1, True, False, 1.5, 2.0, math.inf, math.nan, [], {}):
            with self.subTest(count=value):
                self.assertIsNone(project_one(node(count=value, runSettings={"count": value}))["run"]["count"])
        for value in (1, 8, 99):
            with self.subTest(count=value):
                self.assertEqual(project_one(node(runSettings={"count": value}))["run"]["count"], value)
        self.assertEqual(project_one(node(provider_id="saved-provider", size="1024x1024", resolution="1k"))["run"],
                         {"model": None, "count": None, "aspect_ratio": None})


class PurityAndIndexing(unittest.TestCase):
    def test_read_never_changes_canvas_revision_node_values_or_field_arguments(self):
        canvas = GroupProjection().fixture()
        canvas["nodes"][1].update(promptDraftText="", runPrompt="old",
                                   runInputRefs=[{"asset_id": "asset-1", "url": "secret"}],
                                   runSettings={"ratio": "wide"},
                                   lastFailure={"code": "C", "message": "M", "hint": "H", "raw": "secret"})
        original = copy.deepcopy(canvas)
        fields = ["prompt", "refs", "run", "prompt"]
        for kwargs in ({}, {"fields": fields}, {"fields": []}, {"group_id": "g1"},
                       {"created_by": "agent"}, {"group_id": "g2", "created_by": "user"}):
            with self.subTest(kwargs=kwargs):
                project_nodes(canvas, **kwargs)
                self.assertEqual(canvas, original)
                self.assertEqual(fields, ["prompt", "refs", "run", "prompt"])
        with self.assertRaises(HTTPException):
            project_nodes(canvas, group_id="missing")
        self.assertEqual(canvas, original)

    def test_output_mutations_cannot_modify_input_or_another_projection(self):
        canvas = {"nodes": [node("g", NODE_TYPE_GROUP, items=["n"]),
                            node("n", runInputRefs=[{"asset_id": "a"}], runSettings={"model": "m"},
                                 lastFailure={"code": "C", "message": "M", "hint": "H"})],
                  "connections": [{"from": "g", "to": "n"}]}
        original = copy.deepcopy(canvas)
        first = project_nodes(canvas)
        untouched = copy.deepcopy(first)
        first["groups"][0]["items"].append("invented")
        summary = by_id(first)["n"]
        summary["incoming"].clear()
        summary["outgoing"].append("invented")
        summary["refs"][0]["asset_id"] = "different"
        summary["run"]["model"] = "different"
        summary["lastFailure"]["code"] = "different"
        first["nodes"].clear()
        self.assertEqual(canvas, original)
        self.assertEqual(project_nodes(canvas), untouched)

    def test_read_accepts_frozen_input_without_attempting_writes(self):
        def no_write(*args, **kwargs):
            raise AssertionError("input write attempted")

        class FrozenDict(dict):
            __setitem__ = __delitem__ = update = setdefault = pop = clear = no_write

        class FrozenList(list):
            __setitem__ = __delitem__ = append = extend = insert = pop = clear = sort = no_write

        def freeze(value):
            if isinstance(value, dict):
                return FrozenDict({key: freeze(item) for key, item in value.items()})
            if isinstance(value, list):
                return FrozenList(freeze(item) for item in value)
            return value

        canvas = freeze(GroupProjection().fixture())
        self.assertEqual(project_nodes(canvas)["count"], 6)

    def test_node_connections_and_member_collections_are_indexed_once(self):
        class CountedList(list):
            iterations = 0

            def __iter__(self):
                self.iterations += 1
                return super().__iter__()

        items = CountedList([f"n-{i}" for i in range(2000)])
        nodes = CountedList([node("g", NODE_TYPE_GROUP, items=items), *[node(item) for item in items]])
        items.iterations = 0
        connections = CountedList({"from": f"n-{i}", "to": f"n-{i + 1}"} for i in range(1999))
        result = project_nodes({"nodes": nodes, "connections": connections}, group_id="g", fields=[])
        self.assertEqual(result["count"], 2000)
        self.assertEqual((nodes.iterations, items.iterations, connections.iterations), (1, 1, 1))
        self.assertEqual(result["nodes"][-1]["incoming"], ["n-1998"])

    def test_corrupt_legacy_json_never_raises_or_exports_nonfinite_or_nested_raw_data(self):
        rng = random.Random(392)
        bad = [None, False, True, 0, -1, 1.5, math.nan, math.inf, -math.inf, "", " ",
               "n", "agent", "user", "running", [], {}, [None, {}], {"secret": ["raw"]}]
        keys = ["id", "type", "title", "text", "x", "y", "w", "h", *sorted(OWNERSHIP_KEYS),
                "agentStatus", "lastFailure", "status", "generationState", "promptDraftText", "prompt",
                "runPrompt", "variablePrompts", "variablePrompt", "reference_asset_ids", "runInputRefs",
                "runSettings", "model", "count", "aspect_ratio", "items", "refFrame", "chatFrame"]
        for index in range(250):
            values = {key: copy.deepcopy(rng.choice(bad)) for key in keys}
            value = node("n", rng.choice([NODE_TYPE_IMAGE, NODE_TYPE_LOOP, NODE_TYPE_PROMPT]), **{
                key: item for key, item in values.items() if key not in ("id", "type")
            })
            group = node("g", NODE_TYPE_GROUP, items=["n", [], {}, None, "g", "missing"])
            canvas = {"revision": 100, "nodes": [value, group, values, rng.choice(bad)],
                      "connections": [rng.choice(bad), {"from": rng.choice(bad), "to": rng.choice(bad)}]}
            before = json.dumps(canvas, ensure_ascii=False, sort_keys=True)
            with self.subTest(index=index):
                result = project_nodes(canvas)
                json.dumps(result, allow_nan=False)
                self.assertEqual(result["count"], len(result["nodes"]))
                self.assertEqual(json.dumps(canvas, ensure_ascii=False, sort_keys=True), before)
                for summary in result["nodes"]:
                    self.assertLessEqual(len(summary["prompt_preview"]), 80)
                    self.assertIsInstance(summary["title"], str)
                    self.assertIsInstance(summary["status"], str)
                    self.assertEqual(set(summary), SUMMARY_KEYS | OPTIONAL_KEYS)


if __name__ == "__main__":
    unittest.main()
