"""#392 stage 1 transport contracts; no providers, agents or HTTP server.

Exercise the shared cores through real REST/MCP routes with temporary projects.
"""
import asyncio
import copy
import json
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.constants import NODE_TYPE_AGENT, NODE_TYPE_GROUP, NODE_TYPE_IMAGE, NODE_TYPE_PROMPT
from app.services.agent_errors import is_agent_error_shape, translate_detail
from app.services.agent_tools_manifest import TOOLS
from tests.agent_image_task_fakes import route_stub, stub_providers


def run(coro):
    return asyncio.run(coro)


class ListSchema392Tests(unittest.TestCase):
    def test_list_schema_has_optional_filters_and_enum_array_without_defaults(self):
        tool = next(tool for tool in TOOLS if tool["name"] == "canvas.list_nodes")
        schema = tool["inputSchema"]
        self.assertEqual(schema["required"], [])
        self.assertIs(schema["additionalProperties"], False)
        props = schema["properties"]
        self.assertEqual(set(props), {"group_id", "created_by", "fields"})
        self.assertEqual(props["group_id"]["type"], "string")
        self.assertEqual(props["created_by"]["enum"], ["agent", "user"])
        self.assertEqual(props["fields"]["type"], "array")
        self.assertEqual(props["fields"]["items"], {
            "type": "string", "enum": ["summary", "prompt", "refs", "run"],
        })
        self.assertTrue(all("default" not in value for value in props.values()))
        self.assertIn("省略", tool["description"])
        self.assertIn("[]", tool["description"])
        self.assertIn("summary", tool["description"])


class _Transport392(unittest.TestCase):
    def setUp(self):
        import main
        from app.routers import api_agent_tools, api_mcp
        from firefly_projects import ProjectStore

        self.main, self.tools, self.mcp = main, api_agent_tools, api_mcp
        temp = tempfile.TemporaryDirectory(prefix="e2e-tabbit-a2-transports-")
        self.addCleanup(temp.cleanup)
        self.store = ProjectStore(Path(temp.name) / "projects", snapshot_delay=0)
        self.addCleanup(self.store.close)
        self.pid = self.store.create_project("e2e-tabbit-a2-392-transports")["id"]
        self.start_patch(main, "FIREFLY_PROJECT_STORE", self.store)
        self.start_patch(api_mcp, "resolve_project", Mock(return_value=self.pid))
        # No actual WS tabs or agent sessions needed for transport calls.
        self.broadcast = self.start_patch(api_agent_tools, "_broadcast_canvas_updated", AsyncMock())
        self.start_patch(api_mcp, "mcp_event_recorder", Mock())
        self.client = TestClient(main.app, base_url="http://127.0.0.1:8125")
        self.addCleanup(self.client.close)

    def start_patch(self, obj, name, value):
        patcher = patch.object(obj, name, value)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def canvas(self):
        return self.store.canvas(self.pid)

    def seed(self, *nodes):
        return self.store.apply_mutations(self.pid, [
            {"op": "upsert_node", "node": node} for node in nodes
        ], base_revision=int(self.canvas().get("revision") or 0))

    def call(self, name, args, node="", legacy_args=False):
        response = self.client.post("/api/mcp", params={"project": self.pid, "node": node}, json={
            "jsonrpc": "2.0", "id": 392, "method": "tools/call", "params": {
                "name": name, "args" if legacy_args else "arguments": args,
            },
        })
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertNotIn("error", body, body)
        return body["result"]

    def read_result(self, result):
        self.assertFalse(result["isError"], result)
        return json.loads(result["content"][0]["text"])

    def rest_list(self, params):
        return self.client.get(f"/api/projects/{self.pid}/canvas/nodes", params=params,
                               headers={"X-Agent-Project": self.pid})


class McpListArguments392Tests(_Transport392):
    def test_preserves_all_arguments_including_empty_arrays_and_invalid_types(self):
        cases = [
            {},
            {"fields": []},
            {"fields": ["prompt", "run", "prompt"], "group_id": "group /&中文", "created_by": "agent"},
            {"fields": ["summary"], "created_by": "user"},
            {"fields": "prompt", "group_id": 12, "created_by": False},
            {"fields": ["unknown"], "group_id": "", "created_by": "robot"},
            {"fields": {"bad": True}},
        ]
        for args in cases:
            with self.subTest(args=args), patch.object(self.mcp, "list_nodes_core", new=AsyncMock(
                return_value={"nodes": [], "groups": [], "count": 0},
            )) as core, patch.object(self.mcp, "ensure_external_agent_node", new=AsyncMock()) as carrier:
                self.read_result(self.call("canvas.list_nodes", args))
                core.assert_awaited_once_with(self.pid, group_id=args.get("group_id"),
                                             fields=args.get("fields"), created_by=args.get("created_by"))
                carrier.assert_not_awaited()

    def test_explicit_null_rejected_before_core_with_shared_error_shape(self):
        with patch.object(self.mcp, "list_nodes_core", new=AsyncMock()) as core:
            result = self.call("canvas.list_nodes", {"fields": None})
        core.assert_not_awaited()
        self.assertTrue(result["isError"])
        detail = result["structuredContent"]
        self.assertTrue(is_agent_error_shape(detail))
        self.assertEqual(detail["code"], "INVALID_ARGUMENT")
        self.assertIn("INVALID_ARGUMENT", result["content"][0]["text"])
        self.assertNotIn("[object Object]", result["content"][0]["text"])


class ListReadIntegration392Tests(_Transport392):
    def setUp(self):
        super().setUp()
        self.seed(
            {"id": "prompt-user", "type": NODE_TYPE_PROMPT, "text": "user prompt", "x": 0, "y": 0},
            {"id": "image-agent", "type": NODE_TYPE_IMAGE, "prompt": "agent prompt", "x": 20, "y": 0,
             "createdBy": "agent", "agentNodeId": "owner", "userTouchedAt": 1234,
             "promptEditedByUser": True, "reference_asset_ids": []},
            {"id": "group-392", "type": NODE_TYPE_GROUP, "items": ["prompt-user", "image-agent"]},
            {"id": "empty-392", "type": NODE_TYPE_GROUP, "items": []},
        )

    def test_rest_mcp_and_request_free_core_equivalent_without_read_side_effects(self):
        cases = [
            ({}, []),
            ({"fields": []}, [("fields", "summary")]),
            ({"fields": ["prompt", "run", "prompt"]}, [
                ("fields", "prompt"), ("fields", "run"), ("fields", "prompt"),
            ]),
            ({"group_id": "group-392", "created_by": "agent", "fields": ["refs"]}, [
                ("group_id", "group-392"), ("created_by", "agent"), ("fields", "refs"),
            ]),
            ({"group_id": "empty-392"}, [("group_id", "empty-392")]),
            ({"created_by": "user"}, [("created_by", "user")]),
        ]
        before = copy.deepcopy(self.canvas())
        with patch.object(self.mcp, "ensure_external_agent_node", new=AsyncMock()) as carrier:
            for index, (args, query) in enumerate(cases):
                with self.subTest(args=args):
                    expected = run(self.tools.list_nodes_core(self.pid, **args))
                    rest = self.rest_list(query)
                    self.assertEqual(rest.status_code, 200, rest.text)
                    self.assertEqual(rest.json(), expected)
                    result = self.call("canvas.list_nodes", args, node="owner" if index % 2 else "",
                                       legacy_args=bool(index % 2))
                    self.assertEqual(self.read_result(result), expected)
            carrier.assert_not_awaited()
        self.assertEqual(self.canvas(), before, "readonly adapters must not materialize or backfill")
        self.broadcast.assert_not_awaited()

    def test_rest_mcp_errors_have_same_codes_details_and_do_not_materialize(self):
        cases = [
            ({"fields": ["unknown"]}, [("fields", "unknown")], 400, "INVALID_ARGUMENT"),
            ({"fields": ["prompt,run"]}, [("fields", "prompt,run")], 400, "INVALID_ARGUMENT"),
            ({"created_by": "robot"}, [("created_by", "robot")], 400, "INVALID_ARGUMENT"),
            ({"group_id": ""}, [("group_id", "")], 400, "INVALID_ARGUMENT"),
            ({"group_id": "missing"}, [("group_id", "missing")], 404, "NODE_NOT_FOUND"),
            ({"group_id": "prompt-user"}, [("group_id", "prompt-user")], 400, "INVALID_ARGUMENT"),
        ]
        before = copy.deepcopy(self.canvas())
        for args, query, status, code in cases:
            with self.subTest(args=args):
                rest = self.rest_list(query)
                self.assertEqual(rest.status_code, status, rest.text)
                expected = translate_detail(status, rest.json()["detail"])
                self.assertEqual(expected["code"], code)
                result = self.call("canvas.list_nodes", args)
                self.assertTrue(result["isError"])
                self.assertEqual(result["structuredContent"], expected)
                if "group_id" in args and args["group_id"]:
                    self.assertEqual(expected["details"]["group_id"], args["group_id"])
        for args in ({"fields": None}, {"fields": "prompt"}, {"fields": 1}, {"fields": [None]},
                     {"group_id": 1}, {"created_by": []}):
            with self.subTest(mcp_only=args):
                result = self.call("canvas.list_nodes", args)
                self.assertTrue(result["isError"])
                self.assertEqual(result["structuredContent"]["code"], "INVALID_ARGUMENT")
        self.assertEqual(self.canvas(), before)
        self.broadcast.assert_not_awaited()


class McpReservedMetadata392Tests(_Transport392):
    def test_create_rejects_reserved_keys_including_null_in_flat_and_legacy_shapes(self):
        from app.services.agent_node_state import ownership_fields

        before = copy.deepcopy(self.canvas())
        for key in ownership_fields():
            for value in (None, False, "forged", 1234):
                for shape in ("flat", "nested", "mixed"):
                    with self.subTest(key=key, value=value, shape=shape):
                        node = {"type": NODE_TYPE_IMAGE, key: value}
                        args = node if shape == "flat" else {"node": node} if shape == "nested" else {
                            "node": {"type": NODE_TYPE_IMAGE}, key: value,
                        }
                        result = self.call("canvas.create_node", args, node="owner")
                        self.assertTrue(result["isError"], result)
                        self.assertEqual(result["structuredContent"]["code"], "INVALID_ARGUMENT")
                        self.assertEqual(result["structuredContent"]["details"]["rejected_keys"], [key])
        self.assertEqual(self.canvas(), before)
        self.broadcast.assert_not_awaited()

    def test_update_cannot_silently_strip_null_reserved_metadata(self):
        from app.services.agent_node_state import ownership_fields

        self.seed({"id": "source", "type": NODE_TYPE_IMAGE, **ownership_fields("owner")})
        before = copy.deepcopy(self.canvas())
        for key in ownership_fields():
            for nested in (False, True):
                with self.subTest(key=key, nested=nested):
                    args = {"node_id": "source", "updates": {key: None}} if nested else {
                        "node_id": "source", key: None,
                    }
                    result = self.call("canvas.update_node", args, node="owner")
                    self.assertTrue(result["isError"], result)
                    self.assertEqual(result["structuredContent"]["code"], "INVALID_ARGUMENT")
        self.assertEqual(self.canvas(), before)
        self.broadcast.assert_not_awaited()


class GenerationIdentity392Tests(_Transport392):
    def setUp(self):
        super().setUp()
        # #401 的重试作用域包含 MCP session，固定输入才能精确检查透传身份。
        self.mcp_session_id = "transport-392-session"
        self.client.headers[self.mcp.SESSION_HEADER] = self.mcp_session_id
        # #396/#454 要求真实预检；#463 的混合张数用于区分节点数和图片预算。
        self.seed(
            {"id": "source", "type": NODE_TYPE_IMAGE, "prompt": "before source",
             "runSettings": {"provider_id": "mock", "count": 2}},
            {"id": "source-2", "type": NODE_TYPE_IMAGE, "prompt": "before source-2",
             "runSettings": {"provider_id": "mock", "count": 1}, "promptEditedByUser": True},
        )
        self.start_patch(self.main, "load_api_providers", Mock(return_value=stub_providers()))
        self.start_patch(self.main, "route_image_generation", Mock(return_value=route_stub()))
        self.plans = []
        prepare = self.tools.prepare_generation

        async def record_plan(*args, **kwargs):
            plan = await prepare(*args, **kwargs)
            self.plans.append(plan)
            return plan

        self.prepared = self.start_patch(self.tools, "prepare_generation", AsyncMock(side_effect=record_plan))

    def test_trigger_and_batch_forward_explicit_resolved_node_not_argument_override(self):
        self.seed({"id": "owner", "type": NODE_TYPE_AGENT})
        before = copy.deepcopy(self.canvas())
        for name, args, core_name, core_args, plan_count, count, gate_owner, gate_name, gate_args in [
            ("generate.trigger", {"node_id": "source"}, "trigger_generation_core",
             (self.pid, "source"), 1, 2, self.mcp, "_confirm_gate", {"node_id": "source", "n": 2}),
            ("canvas.batch_edit", {"node_ids": ["source", "source-2"], "prompt": "edit"},
             "batch_edit_core", (self.pid, ["source", "source-2"], "edit"), 2, 3,
             self.tools, "confirm_batch_prompt", {"node_count": 2, "user_edited_count": 1, "n": 3}),
        ]:
            confirmation = AsyncMock(return_value={"approved": True})
            start = len(self.plans)
            # 同时观察工具门和共享确认核，不用替身门绕过真实确认参数构造（#463/#467）。
            gate = AsyncMock(wraps=getattr(gate_owner, gate_name))
            with self.subTest(tool=name), patch.object(self.mcp, core_name, new=AsyncMock(
                return_value={"ok": True},
            )) as core, patch.object(gate_owner, gate_name, gate), patch.object(
                self.mcp, "request_confirmation_core", confirmation,
            ), patch.object(self.tools, "request_confirmation_core", confirmation), patch.object(
                self.tools, "enforce_generation_limit",
            ) as limit, patch.object(self.mcp, "ensure_external_agent_node", new=AsyncMock()) as carrier:
                rejected = self.call(name, {**args, "agent_node_id": "forged"}, node="owner")
                self.assertTrue(rejected["isError"], rejected)
                self.assertTrue(is_agent_error_shape(rejected["structuredContent"]), rejected)
                self.assertEqual(rejected["structuredContent"]["code"], "INVALID_ARGUMENT")
                self.assertEqual(len(self.plans), start)
                core.assert_not_awaited()
                gate.assert_not_awaited()
                confirmation.assert_not_awaited()
                limit.assert_not_called()
                carrier.assert_not_awaited()

                self.assertEqual(self.read_result(self.call(name, args, node="owner")), {"ok": True})
                plans = self.plans[start:]
                self.assertEqual(len(plans), plan_count)
                self.assertEqual(sum(plan.request.count for plan in plans), count)
                prepared = {"plan": plans[0], "retry_caller": self.mcp.agent_batch.Caller(
                    self.pid, "owner", self.mcp_session_id,
                )} if name == "generate.trigger" else {"plans": tuple(plans)}
                core.assert_awaited_once_with(*core_args, agent_node_id="owner", **prepared)
                self.assertEqual(core.await_args.args, core_args)
                self.assertEqual(core.await_args.kwargs["agent_node_id"], "owner")
                core.assert_awaited_once()
                self.assertEqual(gate.await_args.args[:3], (self.pid, "owner", name))
                gate.assert_awaited_once()
                confirmation.assert_awaited_once()
                self.assertEqual(confirmation.await_args.args[:4], (self.pid, "owner", name, gate_args))
                limit.assert_called_once_with(self.pid, "owner", count=count)
                carrier.assert_not_awaited()
        self.assertEqual(self.canvas(), before)

    def test_generation_arguments_cannot_override_bound_identity(self):
        self.seed({"id": "owner", "type": NODE_TYPE_AGENT})
        before = copy.deepcopy(self.canvas())
        # #396（2cc0361）明确拒绝付费工具的额外字段，不再静默忽略伪造身份。
        for name, args, core_name in [
            ("generate.trigger", {"node_id": "source", "agent_node_id": "forged"}, "trigger_generation_core"),
            ("canvas.batch_edit", {"node_ids": ["source"], "prompt": "edit", "agent_node_id": "forged"},
             "batch_edit_core"),
        ]:
            confirmation = AsyncMock(return_value={"approved": True})
            with self.subTest(tool=name), patch.object(self.mcp, core_name, new=AsyncMock()) as core, patch.object(
                self.mcp, "request_confirmation_core", confirmation,
            ), patch.object(self.tools, "request_confirmation_core", confirmation), patch.object(
                self.tools, "enforce_generation_limit",
            ) as limit, patch.object(self.mcp, "ensure_external_agent_node", new=AsyncMock()) as carrier:
                result = self.call(name, args, node="owner")
                self.assertTrue(result["isError"], result)
                self.assertTrue(is_agent_error_shape(result["structuredContent"]), result)
                self.assertEqual(result["structuredContent"]["code"], "INVALID_ARGUMENT")
                self.prepared.assert_not_awaited()
                core.assert_not_awaited()
                confirmation.assert_not_awaited()
                limit.assert_not_called()
                carrier.assert_not_awaited()
        self.assertEqual(self.canvas(), before)
        self.broadcast.assert_not_awaited()

    def test_external_generation_tools_use_self_owned_carrier_as_context(self):
        before_nodes = copy.deepcopy(self.canvas()["nodes"])
        before = {node["id"]: node for node in before_nodes}
        confirmation = AsyncMock(return_value={"approved": True})
        with patch.object(self.mcp, "trigger_generation_core", new=AsyncMock(
            return_value={"task_id": "stub-task"},
        )) as trigger, patch.object(self.mcp, "batch_edit_core", new=AsyncMock(
            return_value={"tasks": []},
        )) as batch, patch.object(self.mcp, "request_confirmation_core", confirmation), patch.object(
            self.tools, "request_confirmation_core", confirmation,
        ), patch.object(self.tools, "enforce_generation_limit") as limit:
            self.read_result(self.call("generate.trigger", {"node_id": "source"}))
            self.read_result(self.call("canvas.batch_edit", {"node_ids": ["source"], "prompt": "edit"}))
        all_nodes = self.canvas()["nodes"]
        self.assertEqual(len(all_nodes), 3)
        nodes = {node["id"]: node for node in all_nodes}
        self.assertEqual({key: nodes.pop(key) for key in before}, before)
        self.assertEqual(len(nodes), 1)
        carriers = [node for node in all_nodes if node["type"] == NODE_TYPE_AGENT]
        self.assertEqual(len(carriers), 1)
        carrier = carriers[0]
        self.assertEqual(carrier["type"], NODE_TYPE_AGENT)
        self.assertEqual([node for node in all_nodes if node["type"] != NODE_TYPE_AGENT], before_nodes)
        self.assertEqual(carrier["createdBy"], "agent")
        self.assertEqual(carrier["agentNodeId"], carrier["id"])
        self.assertIsNone(carrier["userTouchedAt"])
        self.assertIs(carrier["promptEditedByUser"], False)
        self.assertEqual(len(self.plans), 2)
        trigger.assert_awaited_once_with(self.pid, "source", agent_node_id=carrier["id"], plan=self.plans[0],
                                         retry_caller=self.mcp.agent_batch.Caller(
                                             self.pid, carrier["id"], self.mcp_session_id))
        batch.assert_awaited_once_with(self.pid, ["source"], "edit", agent_node_id=carrier["id"],
                                      plans=(self.plans[1],))
        for core, expected_args in ((trigger, (self.pid, "source")),
                                    (batch, (self.pid, ["source"], "edit"))):
            core.assert_awaited_once()
            self.assertEqual(core.await_args.args, expected_args)
            self.assertEqual(core.await_args.kwargs["agent_node_id"], carrier["id"])
        self.assertEqual([args.args[:3] for args in confirmation.await_args_list], [
            (self.pid, carrier["id"], "generate.trigger"), (self.pid, carrier["id"], "canvas.batch_edit"),
        ])
        self.assertEqual([(args.args, args.kwargs) for args in limit.call_args_list], [
            ((self.pid, carrier["id"]), {"count": 2}), ((self.pid, carrier["id"]), {"count": 2}),
        ])


class McpCarrierCas392Tests(_Transport392):
    def seed_carrier(self, **fields):
        # Bindings belong to the runtime; ordinary canvas writes cannot seed them.
        node = {"id": "carrier", "type": NODE_TYPE_AGENT, "mcp_session": "old-session", **fields}
        self.store.apply_agent_host_mutations(self.pid, [{"op": "upsert_node", "node": node}],
                                              base_revision=self.canvas()["revision"])

    @contextmanager
    def interleave_carrier_cas(self, build_concurrent, *, trusted_writer=False):
        from firefly_projects import RevisionConflict

        # ensure_external_agent_node selects this trusted entry in the snapshot helper.
        original = self.store.apply_agent_host_mutations
        attempts, revisions, conflicts = [], [], []

        def interleave(project_id, mutations, *, base_revision, **kwargs):
            attempts.append(copy.deepcopy(mutations))
            revisions.append(base_revision)
            if len(attempts) == 1:
                write = original if trusted_writer else self.store.apply_mutations
                write(project_id, build_concurrent(self.canvas()), base_revision=base_revision)
            try:
                return original(project_id, mutations, base_revision=base_revision, **kwargs)
            except RevisionConflict as conflict:
                conflicts.append((base_revision, conflict.canvas["revision"]))
                raise

        with patch.object(self.store, "apply_agent_host_mutations", side_effect=interleave):
            yield attempts
        self.assertEqual(len(conflicts), 1, "the first carrier write must hit the real store CAS conflict")
        stale, current = conflicts[0]
        self.assertEqual(current, stale + 1)
        self.assertEqual(revisions, [stale, current], "retry must use the concurrent writer's revision")
        self.assertEqual(self.canvas()["revision"], current + 1)

    def test_new_carrier_is_self_owned_and_same_session_is_idempotent(self):
        node_id = run(self.mcp.ensure_external_agent_node(self.pid, "session-one"))
        before = copy.deepcopy(self.canvas())
        node = before["nodes"][0]
        self.assertEqual(node["id"], node_id)
        self.assertEqual(node["createdBy"], "agent")
        self.assertEqual(node["agentNodeId"], node_id)
        self.assertIsNone(node["userTouchedAt"])
        self.assertIs(node["promptEditedByUser"], False)
        self.assertEqual(run(self.mcp.ensure_external_agent_node(self.pid, "session-one")), node_id)
        self.assertEqual(self.canvas(), before)

    def test_rebind_keeps_existing_ownership_protection_and_legacy_missing_fields(self):
        from app.services.agent_node_state import ownership_fields

        for metadata in ({}, {"createdBy": "user", "agentNodeId": None, "userTouchedAt": 111,
                              "promptEditedByUser": True},
                         {"createdBy": "agent", "agentNodeId": "original-owner", "userTouchedAt": 222,
                          "promptEditedByUser": True}):
            with self.subTest(metadata=metadata):
                self.seed_carrier(title="keep title", agentGoal="keep goal", **metadata)
                before = copy.deepcopy(self.canvas()["nodes"][0])
                self.assertEqual(before["mcp_session"], "old-session")
                self.assertEqual({key: before[key] for key in ownership_fields() if key in before}, metadata)
                self.assertEqual(run(self.mcp.ensure_external_agent_node(self.pid, "new-session")), "carrier")
                after = copy.deepcopy(self.canvas()["nodes"][0])
                self.assertEqual(after.pop("mcp_session"), "new-session")
                before.pop("mcp_session")
                after.pop("updated_at", None)
                before.pop("updated_at", None)
                self.assertEqual(after, before)

    def test_rebind_conflict_rebuilds_from_latest_carrier_not_stale_upsert(self):
        self.seed_carrier(createdBy="agent", agentNodeId="original-owner", userTouchedAt=None,
                          promptEditedByUser=False, title="before", agentGoal="before")

        def build_concurrent(latest):
            node = {**latest["nodes"][0], "title": "human title", "agentGoal": "human goal",
                    "userTouchedAt": 9876, "promptEditedByUser": True, "latestField": ["retain"]}
            return [{"op": "upsert_node", "node": node}]

        with self.interleave_carrier_cas(build_concurrent) as attempts:
            self.assertEqual(run(self.mcp.ensure_external_agent_node(self.pid, "new-session")), "carrier")
        self.assertEqual(len(attempts), 2)
        node = self.canvas()["nodes"][0]
        self.assertEqual(node["mcp_session"], "new-session")
        self.assertEqual(node["createdBy"], "agent")
        self.assertEqual(node["agentNodeId"], "original-owner")
        self.assertEqual(node["userTouchedAt"], 9876)
        self.assertIs(node["promptEditedByUser"], True)
        self.assertEqual(node["title"], "human title")
        self.assertEqual(node["agentGoal"], "human goal")
        self.assertEqual(node["latestField"], ["retain"])
        self.assertNotIn("latestField", attempts[0][0]["node"])
        self.assertEqual(attempts[1][0]["node"]["latestField"], ["retain"])
        self.broadcast.assert_awaited_once()

    def test_creation_conflict_reuses_minted_id_timestamp_and_ownership(self):
        def build_concurrent(_latest):
            return [{"op": "upsert_node", "node": {
                "id": "unrelated", "type": NODE_TYPE_PROMPT, "text": "concurrent addition",
            }}]

        with self.interleave_carrier_cas(build_concurrent) as attempts:
            node_id = run(self.mcp.ensure_external_agent_node(self.pid, "this-session"))
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[0], attempts[1], "CAS retries must not mint new identity/creation time")
        node = attempts[1][0]["node"]
        self.assertEqual(node["id"], node_id)
        self.assertEqual(node["agentNodeId"], node_id)
        self.assertEqual(node["createdBy"], "agent")
        self.assertEqual({node["id"] for node in self.canvas()["nodes"]}, {"unrelated", node_id})
        self.broadcast.assert_awaited_once()

    def test_concurrent_carrier_creation_is_reused_on_retry_without_duplicate(self):
        def build_concurrent(_latest):
            return [{"op": "upsert_node", "node": {
                "id": "concurrent-carrier", "type": NODE_TYPE_AGENT, "mcp_session": "concurrent",
                "createdBy": "user", "agentNodeId": None, "userTouchedAt": 123,
                "promptEditedByUser": True,
            }}]

        with self.interleave_carrier_cas(build_concurrent, trusted_writer=True) as attempts:
            node_id = run(self.mcp.ensure_external_agent_node(self.pid, "this-session"))
        self.assertEqual(node_id, "concurrent-carrier")
        self.assertEqual(len(attempts), 2)
        self.assertNotEqual(attempts[0][0]["node"]["id"], "concurrent-carrier")
        self.assertEqual(attempts[1][0]["node"]["id"], "concurrent-carrier")
        nodes = self.canvas()["nodes"]
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0]["mcp_session"], "this-session")
        self.assertEqual(nodes[0]["createdBy"], "user")
        self.assertIsNone(nodes[0]["agentNodeId"])
        self.assertEqual(nodes[0]["userTouchedAt"], 123)
        self.assertIs(nodes[0]["promptEditedByUser"], True)
        self.broadcast.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
