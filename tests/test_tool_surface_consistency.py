# -*- coding: utf-8 -*-
"""#356 一致性测试：单一来源 + 「同一输入 → 两路相同结果」遍历所有工具。

两层：
1) 同源层：MCP 面（app/routers/api_mcp.py）就是清单对象本身（import 同一性）；
   pi 扩展（app/agent_extensions/canvas-tools.ts）的工具注册段是
   tools/gen_canvas_tools.py 从清单 codegen 的 canvas-tools.generated.ts——
   freshness（重跑 diff 必须为空）、名字集合三方一致、schema 不变量。
2) 行为层：遍历清单工具，比对 MCP 与 REST 的返回值及完整画布终态。
   #456：写入走带 X-Agent-Project 的 hosted REST 与 hosted MCP；门控工具在两路均批准后比对。
   canvas.edit_erase 消耗外部模型，只钉门与路由。
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import copy
import json
import re
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main
from firefly_opsdb import OpsDb
from firefly_projects import ProjectStore
from app.core import CANVAS_TASKS, CANVAS_TASK_LOCK
from app.project_sessions import register_project_session, unregister_project_session
from app.routers import api_agent_tools, api_mcp
from app.services import agent_ops_delta, agent_tools_manifest
from app.services.agent_ops_journal import AgentOpsJournal, record_sides
from app.services.agent_errors import translate_detail
from app.services.agent_host import AgentSession, agent_host_manager
from tests.agent_image_task_fakes import route_stub, stub_providers
from tests.test_canvas_viewport_485 import acknowledge, assert_waiting, frame, open_socket, quiet_lifetime, ready


GEN_TS = ROOT / "app" / "agent_extensions" / "canvas-tools.generated.ts"
TOOLS_TS = ROOT / "app" / "agent_extensions" / "canvas-tools.ts"


class SourceSameOriginTests(unittest.TestCase):
    """同源层：清单 = MCP 面 = pi 生成段的唯一来源。"""

    def test_mcp_surface_is_the_manifest(self):
        self.assertIs(api_mcp.TOOLS, agent_tools_manifest.TOOLS)
        self.assertEqual(api_mcp.TOOL_NAMES, agent_tools_manifest.TOOL_NAMES)
        self.assertEqual(set(api_mcp.TOOL_IMPLS.keys()), agent_tools_manifest.TOOL_NAMES)

    def test_mcp_write_descriptions_document_external_turn_ids(self):
        self.enterContext(patch.object(main.app.router, "lifespan_context", quiet_lifetime))
        self.enterContext(patch("subprocess.Popen", side_effect=AssertionError("native/provider startup forbidden")))
        client = self.enterContext(TestClient(main.app, base_url="http://127.0.0.1:8124"))
        response = client.post("/api/mcp", json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/list",
        })
        self.assertEqual(response.status_code, 200, response.text)
        tools = {tool["name"]: tool for tool in response.json()["result"]["tools"]}
        for name in sorted(agent_tools_manifest.WRITE_TOOLS):
            with self.subTest(tool=name):
                tool = tools[name]
                self.assertIn("仅外部 MCP", tool["description"])
                self.assertIn("URL 查询参数", tool["description"])
                self.assertIn("turn_id 不得复用到其他轮次", tool["description"])
                self.assertNotIn("turn_id", tool["inputSchema"]["properties"])

    def test_confirmation_descriptions_do_not_expire_approvals(self):
        descriptions = {tool['name']: tool['description'] for tool in agent_tools_manifest.TOOLS}
        for name in ('canvas.update_node', 'canvas.delete_node', 'canvas.disconnect', 'canvas.edit_crop',
                     'canvas.edit_rotate', 'canvas.edit_flip', 'canvas.edit_outpaint',
                     'canvas.edit_erase', 'canvas.batch_edit', 'generate.trigger',
                     'generate.retry', 'generate.request_batch', 'media.delete'):
            with self.subTest(tool=name):
                self.assertNotIn('阻塞最长 90s', descriptions[name])
                self.assertNotIn('90 秒有效', descriptions[name])
                self.assertIn('CONFIRM_PENDING', descriptions[name])
                self.assertIn('勿重复提交', descriptions[name])

    def test_update_description_requires_protected_confirmation_and_sealed_patch(self):
        description = next(tool['description'] for tool in agent_tools_manifest.TOOLS
                           if tool['name'] == 'canvas.update_node')
        for requirement in ('autonomous', '确认', 'patch', 'CONFIRM_PENDING'):
            self.assertIn(requirement, description)
        self.assertNotIn('仅可改未被人工编辑的自有 Agent 节点', description)

    def test_codegen_fresh(self):
        """重跑 codegen diff 必须为空（拍板 a：进 fast 门/pytest）。"""
        proc = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "gen_canvas_tools.py"), "--check"],
            capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(proc.returncode, 0, f"codegen 漂移：{proc.stderr}")

    def test_schema_invariants(self):
        for tool in agent_tools_manifest.TOOLS:
            schema = tool["inputSchema"]
            self.assertEqual(schema.get("type"), "object", tool["name"])
            self.assertIs(schema.get("additionalProperties"), False,
                          f"{tool['name']} 根 schema 必须 additionalProperties:false（#266 口径）")
            self.assertTrue(tool["description"].strip(), tool["name"])

    def test_update_node_schema_covers_public_update_fields(self):
        schema = next(tool["inputSchema"] for tool in agent_tools_manifest.TOOLS
                      if tool["name"] == "canvas.update_node")
        # #450: publish all supported updates except status, which stays off the tool surface.
        public_fields = agent_tools_manifest.UPDATABLE_NODE_FIELDS - {"status"}
        self.assertEqual(set(schema["properties"]), public_fields | {"node_id"})

    def test_media_ref_schemas(self):
        schemas = {tool["name"]: tool["inputSchema"] for tool in agent_tools_manifest.TOOLS}
        for name in ("canvas.place_media", "canvas.attach_refs", "canvas.set_ref_order"):
            self.assertIn(name, agent_tools_manifest.WRITE_TOOLS)
        place = schemas["canvas.place_media"]
        self.assertEqual(place["required"], ["asset_id"])
        self.assertEqual(set(place["properties"]), {"asset_id", "node_id", "group_id", "title"})

        attach = schemas["canvas.attach_refs"]
        self.assertEqual(attach["required"], ["node_id", "refs"])
        mode = attach["properties"]["mode"]
        self.assertEqual(set(mode["enum"]), {"append", "replace"})
        self.assertEqual(mode["default"], "append")
        ref = attach["properties"]["refs"]["items"]
        self.assertEqual(ref["required"], ["source"])
        self.assertEqual(ref["properties"]["enabled"]["type"], "boolean")
        role = ref["properties"]["role"]
        self.assertEqual(set(role["enum"]), {"product", "detail", "layout", "scene_style", "general"})
        self.assertEqual(role["default"], "general")
        source = ref["properties"]["source"]
        self.assertEqual([branch["required"] for branch in source["oneOf"]], [["asset_id"], ["node_id"]])
        for branch, keys in zip(source["oneOf"], ({"asset_id"}, {"node_id", "image_index"})):
            self.assertIs(branch["additionalProperties"], False)
            self.assertEqual(set(branch["properties"]), keys)

        order = schemas["canvas.set_ref_order"]
        self.assertEqual(order["required"], ["node_id"])
        self.assertEqual(order["anyOf"], [{"required": ["order"]}, {"required": ["role_overrides"]}])
        self.assertEqual(order["properties"]["order"]["items"]["type"], "string")
        self.assertEqual(order["properties"]["role_overrides"]["additionalProperties"]["enum"], role["enum"])

    def test_generated_names_match_manifest(self):
        gen = GEN_TS.read_text(encoding="utf-8")
        names = [m.group(1) for m in re.finditer(r'name: "([a-z_.]+)"', gen)]
        self.assertEqual(sorted(names), sorted(agent_tools_manifest.TOOL_NAMES))

    def test_executors_match_manifest(self):
        src = TOOLS_TS.read_text(encoding="utf-8")
        names = [m.group(1) for m in re.finditer(r'\n    "([a-z_.]+)": async', src)]
        self.assertEqual(sorted(names), sorted(agent_tools_manifest.TOOL_NAMES))

    def test_d13_limit_forwarded(self):
        """D13：pi search_projects 必须透传 limit（曾经声明了从不拼串）。"""
        src = TOOLS_TS.read_text(encoding="utf-8")
        self.assertIn("params.limit !== undefined", src)
        self.assertIn("&limit=", src)


class DualPathEquivalenceTests(unittest.TestCase):
    """行为层：逐工具比较「同一输入 → MCP 与 REST 孪生同结果」。"""

    ACTOR = "agent-consistency"
    TURN = "turn-consistency"
    SEED = [
        {"op": "upsert_node", "node": {"id": "prompt-1", "type": "smart-prompt", "title": "Test Prompt",
                                       "text": "A glowing firefly", "x": 100, "y": 100,
                                       "runSettings": {"provider_id": "offline"}}},
        {"op": "upsert_node", "node": {"id": "image-1", "type": "smart-image", "title": "Test Image",
                                       "x": 300, "y": 100, "runSettings": {"provider_id": "offline"}}},
        {"op": "upsert_edge", "edge": {"id": "edge-1", "from": "prompt-1", "to": "image-1", "kind": "flow"}},
        # Both transports must retain the same ownership node in the full canvas comparison.
        {"op": "upsert_node", "node": {"id": "agent-consistency", "type": "smart-agent",
                                       "title": "Agent", "x": 0, "y": 0}},
    ]

    def setUp(self):
        self.enterContext(patch("subprocess.Popen", side_effect=AssertionError("native/provider startup forbidden")))
        self._fixtures = ExitStack()
        self.addCleanup(self._fixtures.close)

    @staticmethod
    def _close_tab(tab):
        unregister_project_session(tab)
        if tab in main.manager.active_connections:
            main.manager.active_connections.remove(tab)
        main.manager.connection_clients.pop(tab, None)
        main.manager.user_connections.pop(tab.client_id, None)

    class _FakeTab:
        def __init__(self):
            self.client_id = f"client-{uuid.uuid4().hex[:8]}"
            self.sent = []

        async def send_text(self, text):
            self.sent.append(json.loads(text))

    def _open_tab(self, pid):
        tab = self._FakeTab()
        register_project_session(tab, pid)
        main.manager.active_connections.append(tab)
        main.manager.connection_clients[tab] = tab.client_id
        main.manager.user_connections[tab.client_id] = tab
        self._fixtures.callback(self._close_tab, tab)
        return tab

    def _fixture(self, *, viewport=False):
        # Each all-tool row drains its own loop and workers before the next store is selected.
        self._fixtures.close()
        tmp = self._fixtures.enter_context(tempfile.TemporaryDirectory(prefix="e2e-tabbit-356-consistency-"))
        store = ProjectStore(Path(tmp) / "projects", snapshot_delay=0)
        self._fixtures.callback(store.close)
        self._fixtures.enter_context(patch.object(main, "FIREFLY_PROJECT_STORE", store))
        # All generation in this equivalence suite is offline and deterministic.
        for patcher in (
            patch("app.routers.api_project_library.run_routed_image_candidate",
                  new=AsyncMock(side_effect=RuntimeError("Offline equivalence fixture"))),
            patch.object(main, "route_image_generation", return_value=route_stub("offline", "stub-model")),
            patch.object(main, "load_api_providers", return_value=stub_providers("offline", "stub-model")),
        ):
            self._fixtures.enter_context(patcher)
        self._fixtures.enter_context(patch.object(main.app.router, "lifespan_context", quiet_lifetime))
        client = self._fixtures.enter_context(TestClient(main.app, base_url="http://127.0.0.1:8124"))
        self._fixtures.callback(client.portal.call, agent_host_manager.cleanup_all)
        self._fixtures.callback(client.portal.call, api_agent_tools.shutdown_agent_ops_lifetime)
        pids = []
        for tag in ("A", "B"):
            p = store.create_project(f"e2e-tabbit-consistency-{tag}")
            store.apply_mutations(p["id"], copy.deepcopy(self.SEED), base_revision=0)
            pid = p["id"]
            if not viewport:
                self._open_tab(pid)
            session = AgentSession(project_id=pid, node_id=self.ACTOR, autonomy="cautious")
            session._current_turn_id = self.TURN
            agent_host_manager.sessions[f"{pid}:{self.ACTOR}"] = session
            pids.append(pid)
        return client, store, pids[0], pids[1]

    def _mcp_call(self, client, project, name, args, request_id=None):
        resp = client.post(
            f"/api/mcp?project={project}&node={self.ACTOR}&caller=hosted",
            json={"jsonrpc": "2.0", "id": request_id or uuid.uuid4().hex[:6],
                  "method": "tools/call", "params": {"name": name, "args": args}},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        if "error" in body:
            return {"ok": False, "text": str(body["error"].get("message", ""))}
        result = body["result"]
        if result.get("isError"):
            return {"ok": False, "error": result["structuredContent"]}
        texts = [c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"]
        images = [c for c in result.get("content", []) if c.get("type") == "image"]
        payload = {"ok": not result.get("isError"), "text": "\n".join(texts)}
        if images:
            payload["image_len"] = sum(len(c.get("data", "")) for c in images)
        return payload

    def _rest(self, client, method, url, json_body=None, headers=None):
        resp = client.request(method, url, json=json_body, headers=headers)
        if resp.status_code >= 400:
            detail = resp.json().get("detail") if "json" in resp.headers.get("content-type", "") else resp.text
            return {"ok": False, "error": translate_detail(resp.status_code, detail)}
        try:
            return {"ok": True, "text": json.dumps(resp.json())}
        except ValueError:
            return {"ok": True, "text": resp.text}

    _ID_RE = re.compile(r"(node|edge|asset|task|agent|client)-[0-9a-fA-F]{6,}")
    _TASK_RE = re.compile(r"agent_img_[0-9a-fA-F]{6,}")
    _PID_RE = re.compile(r"[0-9a-f]{32}")
    _MCP_WRAP_RE = re.compile(r"工具 [a-z_.]+ 执行失败：(?:\d{3}: )?")

    def _canon_text(self, text):
        t = self._ID_RE.sub(lambda m: m.group(1) + "-ID", text)
        t = self._TASK_RE.sub("TASK-ID", t)
        t = self._PID_RE.sub("PID", t)
        t = self._MCP_WRAP_RE.sub("", t)
        t = re.sub(r"\d{10,}", "TS", t)
        return t

    def _canon_payload(self, payload, *, tool=None):
        if tool and tool.startswith("viewport."):
            out = {"ok": payload["ok"], "text": ""}
            if "text" in payload:
                data = json.loads(payload["text"])
                # Only receipt correlation/time may differ; geometry never passes through regexes.
                if "command_id" in data:
                    data["command_id"] = "COMMAND-ID"
                if tool == "viewport.state" and "updated_at" in data:
                    data["updated_at"] = "OBSERVED-AT"
                out["text"] = json.dumps(data, sort_keys=True)
            if "error" in payload:
                detail = copy.deepcopy(payload["error"])
                if "command_id" in detail.get("details", {}):
                    detail["details"]["command_id"] = "COMMAND-ID"
                out["error"] = json.dumps(detail, sort_keys=True)
            return out
        text = payload.get("text", "")
        if payload["ok"] and text.startswith("{"):
            data = json.loads(text)
            if "op_id" in data:
                # Each transport mints its own op/node IDs; keep receipt shape and touched entities.
                if "touched_node_ids" in data:
                    data["touched_node_ids"] = sorted(self._canon_text(n) for n in data["touched_node_ids"])
                text = json.dumps(data, sort_keys=True)
        out = {"ok": payload["ok"], "text": self._canon_text(text)}
        if "error" in payload:
            out["error"] = self._canon_text(json.dumps(payload["error"], sort_keys=True))
        if "image_len" in payload:
            out["image_len"] = payload["image_len"]
        return out

    def _canvas_state(self, store, pid):
        canvas = store.canvas(pid)
        nodes = list(canvas.get("nodes", []))  # #350 后全量比较（含 agent 节点）
        edges = list(canvas.get("connections", []))
        for coll in (nodes, edges):
            for item in coll:
                for k in ("created_at", "updated_at", "revision"):
                    item.pop(k, None)
                # 生成运行时/记账键（pending/running/queued/pendingTasks/_generationBatchApplied）：
                # 终态后一致，但快照时机带时序噪声（r16–r18 两条 SUBFAIL 根因；等待全局任务表
                # 会引入 20s×N 卡顿）——不参与等价比较，结果节点存在性/内容仍全量比。
                for k in ("pending", "running", "queued", "pendingTasks", "_generationBatchApplied"):
                    item.pop(k, None)
                # #343 后双路必然差异字段：服务端生成 id（node-XXXX）两侧不同，不参与等价比较。
                # #392 ownership/protection fields stay in the comparison.
                item.pop("id", None)
                for k, v in list(item.items()):
                    if isinstance(v, str):
                        item[k] = self._canon_text(v)
                    elif k == "items" and isinstance(v, list):
                        # Both paths mint independent IDs for frame members.
                        item[k] = [self._canon_text(i) if isinstance(i, str) else i for i in v]
                    if k in ("x", "y") and v is None:
                        item[k] = 0
        return sorted(json.dumps(x, sort_keys=True) for x in nodes) if not edges else (
            sorted(json.dumps(n, sort_keys=True) for n in nodes),
            sorted(json.dumps(e, sort_keys=True) for e in edges),
        )

    def _assert_pair(self, a, b, label):
        ca, cb = self._canon_payload(a, tool=label), self._canon_payload(b, tool=label)
        self.assertEqual(ca, cb, f"{label}: MCP 与 REST 孪生结果不一致\n MCP={a}\n REST={b}")

    def _viewport_request(self, ws, generation, pool, call, pid, command, args, snapshot):
        future = pool.submit(call)
        assert_waiting(future)
        wire = frame(ws)
        self.assertRegex(wire["command_id"], r"^[0-9a-f]{32}$")
        self.assertEqual(wire, {
            "type": "canvas_viewport_command", "project_id": pid, "generation": generation,
            "command_id": wire["command_id"], "command": command, **args,
        })
        acknowledge(ws, wire, snapshot)
        result = future.result(timeout=2)
        self.assertTrue(result["ok"], result)
        data = json.loads(result["text"])
        if command == "state":
            self.assertIsInstance(data["updated_at"], (int, float))
            self.assertGreater(data["updated_at"], 0)
            expected = {**snapshot, "command_id": wire["command_id"], "updated_at": data["updated_at"]}
        else:
            expected = {"success": True, "dispatched": True, **args,
                        "command_id": wire["command_id"], "viewport": snapshot}
        self.assertEqual(data, expected)
        return result

    def _viewport_pair(self, name, args, method, url_path, body):
        client, store, pid_a, pid_b = self._fixture(viewport=True)
        before = [store.canvas(pid) for pid in (pid_a, pid_b)]
        command = name.removeprefix("viewport.")
        # External protocol peers provide literal observations, not browser geometry proof.
        snapshot = {"x": -420.125, "y": 73.5, "scale": args.get("level", 1.375)}
        with ThreadPoolExecutor(max_workers=2) as pool, open_socket(client, pid_a) as one, open_socket(client, pid_b) as two:
            generations = [ready(ws, pid) for ws, pid in ((one, pid_a), (two, pid_b))]
            if command != "state":
                denied = client.request(method, f"/api/projects/{pid_b}{url_path}", json=body)
                self.assertEqual(denied.status_code, 400, denied.text)
                self.assertEqual(denied.json()["detail"]["code"], "INVALID_ARGUMENT")
                self.assertIn("X-Agent-Project", denied.json()["detail"]["message"])
                self.assertEqual([store.canvas(pid) for pid in (pid_a, pid_b)], before)
                for ws in (one, two):
                    ws.send_text("ping")
                    self.assertEqual(frame(ws), {"type": "pong"})
            a = self._viewport_request(one, generations[0], pool,
                lambda: self._mcp_call(client, pid_a, name, args), pid_a, command, args, snapshot)
            b = self._viewport_request(two, generations[1], pool,
                lambda: self._rest(client, method, f"/api/projects/{pid_b}{url_path}", body,
                                   headers={"X-Agent-Project": pid_b} if command != "state" else None),
                pid_b, command, args, snapshot)
            self._assert_pair(a, b, name)
            states = []
            for ws, generation, pid, result in ((one, generations[0], pid_a, a), (two, generations[1], pid_b, b)):
                state = self._viewport_request(ws, generation, pool,
                    lambda pid=pid: self._rest(client, "GET", f"/api/projects/{pid}/viewport/state"),
                    pid, "state", {}, snapshot)
                self.assertNotEqual(json.loads(state["text"])["command_id"], json.loads(result["text"])["command_id"])
                states.append(state)
            self._assert_pair(*states, "viewport.state")
        self.assertEqual([store.canvas(pid) for pid in (pid_a, pid_b)], before)
        self.assertEqual(self._canvas_state(store, pid_a), self._canvas_state(store, pid_b),
                         f"{name}: 两路画布终态不一致")

    def _confirmed_pair(self, client, pid_a, pid_b, name, args, method, url_path, body=None):
        # Answer the real confirmation on each actor's session, not at the transport gate.
        seen = []

        async def approve(session, event):
            if event["type"] == "confirm":
                seen.append((session.project_id, session.node_id, event["tool"]))
                for handler in list(session.confirm_handlers):
                    await handler(event["confirm_id"], True)

        for pid in (pid_a, pid_b):
            session = agent_host_manager.sessions[f"{pid}:{self.ACTOR}"]
            agent_host_manager.on_event(session, approve)
        a = self._mcp_call(client, pid_a, name, args)
        b = self._rest(client, method, f"/api/projects/{pid_b}{url_path}?agent_node_id={self.ACTOR}",
                       args if method == "POST" else body, headers={"X-Agent-Project": pid_b})
        gate_tool = "canvas.delete_edge" if name == "canvas.disconnect" else name
        self.assertEqual(seen, [(pid, self.ACTOR, gate_tool) for pid in (pid_a, pid_b)], (a, b))
        return a, b

    def _assert_receipt(self, client, pid, result):
        self.assertTrue(result["ok"], result)
        payload = json.loads(result["text"])
        op_id = payload["op_id"]
        self.assertRegex(op_id, r"^[0-9a-f]{32}$")
        journal = self._rest(client, "GET", f"/api/projects/{pid}/ops?turn_id={self.TURN}")
        records = {op["op_id"]: op for op in json.loads(journal["text"])["operations"]}
        self.assertIn(op_id, records)
        self.assertEqual(records[op_id]["agent_node_id"], self.ACTOR)
        self.assertEqual(records[op_id]["turn_id"], self.TURN)
        return payload

    # -- 遍历 ------------------------------------------------------------

    def test_all_tools_dual_path_equivalence(self):
        cases = self._cases()
        self.assertEqual(
            sorted(cases.keys()), sorted(agent_tools_manifest.TOOL_NAMES),
            "一致性测试必须遍历清单全部工具（票面验收）",
        )
        for name, run_case in cases.items():
            with self.subTest(tool=name):
                run_case()

    def test_create_node_rejects_retired_conversation_types_without_mutating_canvas(self):
        client, store, project, _ = self._fixture()
        retired_node = {
            "id": "legacy-conversation",
            "type": "smart-conversation",
            "messages": [
                {"id": "question", "role": "user", "text": "Prior question"},
                {"id": "answer", "role": "assistant", "text": "Prior answer"},
            ],
        }
        store.apply_mutations(
            project, [{"op": "upsert_node", "node": retired_node}], base_revision=store.canvas(project)["revision"],
        )
        store.flush(project)
        before = store.canvas(project)
        self.assertIn(retired_node, before["nodes"])

        with patch.object(store, "apply_mutations", side_effect=AssertionError("retired creation wrote canvas")) as mutate:
            for node_type in ("smart-conversation", "conversation"):
                args = {"type": node_type, "title": "Rejected"}
                with self.subTest(transport="REST", node_type=node_type):
                    rest = client.post(
                        f"/api/projects/{project}/canvas/nodes",
                        json={"node": args, "agent_node_id": self.ACTOR},
                        headers={"X-Agent-Project": project},
                    )
                    self.assertEqual(rest.status_code, 410, rest.text)
                    self.assertEqual(rest.json()["detail"]["code"], "CONVERSATION_NODE_RETIRED")
                with self.subTest(transport="MCP", node_type=node_type):
                    mcp = self._mcp_call(client, project, "canvas.create_node", args)
                    self.assertFalse(mcp["ok"], mcp)
                    self.assertEqual(mcp.get("error", {}).get("code"), "CONVERSATION_NODE_RETIRED", mcp)
            mutate.assert_not_called()
        self.assertEqual(store.canvas(project), before)

    def test_media_ref_mcp_dispatch_uses_binding_and_payload_only(self):
        client, _, project, _ = self._fixture()
        cases = {
            "place_media": {"asset_id": "asset"},
            "attach_refs": {"node_id": "image-1", "refs": [{"source": {"asset_id": "asset"}}]},
            "set_ref_order": {"node_id": "image-1", "role_overrides": {"asset": "product"}},
        }
        for tool, args in cases.items():
            with self.subTest(tool=tool):
                core = AsyncMock(return_value={"ok": True})
                with patch(f"app.routers.api_agent_tools.{tool}_core", new=core, create=True):
                    result = self._mcp_call(client, project, f"canvas.{tool}", {
                        **args, "agent_node_id": "unbound-agent", "turn_id": "not-a-query-turn",
                    })
                self.assertTrue(result["ok"], result)
                core.assert_awaited_once_with(project, args, agent_node_id="agent-consistency")

    def test_error_comparison_keeps_structured_contract(self):
        detail = {"code": "INVALID_ARGUMENT", "message": "bad fields", "hint": "use summary", "retryable": False}
        original = self._canon_payload({"ok": False, "error": detail})
        for key, value in {"code": "NODE_NOT_FOUND", "message": "other", "hint": "next", "retryable": True,
                           "details": {"group_id": "group"}}.items():
            with self.subTest(key=key):
                self.assertNotEqual(original, self._canon_payload({"ok": False, "error": {**detail, key: value}}))
        for tool in ("viewport.focus", "viewport.zoom", "viewport.state"):
            with self.subTest(tool=tool):
                snapshot = {"x": 12345678901.5, "y": -73.125, "scale": 1.375}
                data = {"command_id": "a" * 32, **(
                    {**snapshot, "updated_at": 1234567890123} if tool == "viewport.state" else
                    {"success": True, "dispatched": True, "viewport": snapshot,
                     **({"node_id": "prompt-1"} if tool == "viewport.focus" else {"level": 2})})}

                def canon(value):
                    return self._canon_payload({"ok": True, "text": json.dumps(value)}, tool=tool)

                expected = canon(data)
                independent = {**data, "command_id": "b" * 32}
                if tool == "viewport.state":
                    independent["updated_at"] += 1
                self.assertEqual(expected, canon(independent))
                for key, value in {"x": 12345678902.5, "y": -74.125, "scale": 1.5}.items():
                    changed = copy.deepcopy(data)
                    (changed if tool == "viewport.state" else changed["viewport"])[key] = value
                    self.assertNotEqual(expected, canon(changed), (tool, key))
                if tool != "viewport.state":
                    for key in ("success", "dispatched"):
                        self.assertNotEqual(expected, canon({**data, key: False}), (tool, key))
                    key = "node_id" if tool == "viewport.focus" else "level"
                    self.assertNotEqual(expected, canon({**data, key: "other" if key == "node_id" else 3}))
                failure = {**detail, "details": {"command_id": "a" * 32, "reason": "VIEWPORT_NOT_READY"}}
                baseline = self._canon_payload({"ok": False, "error": failure}, tool=tool)
                independent = copy.deepcopy(failure)
                independent["details"]["command_id"] = "b" * 32
                self.assertEqual(baseline, self._canon_payload({"ok": False, "error": independent}, tool=tool))
                for key, value in {"code": "NODE_NOT_FOUND", "message": "other", "hint": "next",
                                   "retryable": True, "details": {"reason": "OTHER"}}.items():
                    changed = self._canon_payload({"ok": False, "error": {**failure, key: value}}, tool=tool)
                    self.assertNotEqual(baseline, changed, (tool, key))

    def _cases(self):
        def pair(name, args, method, url_path, body=None, *, expected_error=None):
            """通用形状：A 走 MCP tools/call，B 走 REST 孪生；比返回值 + 画布终态。"""
            def run():
                if name.startswith("viewport."):
                    return self._viewport_pair(name, args, method, url_path, body)
                client, store, pid_a, pid_b = self._fixture()
                rest_body = {**body, "agent_node_id": self.ACTOR} if name == "canvas.create_node" else body
                journaled = name in agent_tools_manifest.WRITE_TOOLS
                path = f"{url_path}?agent_node_id={self.ACTOR}" if journaled else url_path
                url = f"/api/projects/{pid_b}{path}"
                scoped_batch = name in {"generate.preflight", "generate.request_batch", "generate.batch_status", "generate.stop_batch"}
                headers = {"X-Agent-Project": pid_b} if journaled or scoped_batch else None
                a = self._mcp_call(client, pid_a, name, args)
                b = self._rest(client, method, url, rest_body, headers)
                if expected_error:
                    self.assertFalse(a['ok'], a)
                    self.assertEqual(a['error']['code'], expected_error)
                elif journaled:
                    for pid, result in ((pid_a, a), (pid_b, b)):
                        payload = self._assert_receipt(client, pid, result)
                        if name == "canvas.create_node":
                            nodes = {node["id"]: node for node in store.canvas(pid)["nodes"]}
                            frame = nodes[nodes[self.ACTOR]["chatFrameId"]]
                            self.assertEqual(frame["items"], [payload["node_id"]])
                            self.assertEqual(frame["chatFrameFor"], self.ACTOR)
                self._assert_pair(a, b, name)
                self.assertEqual(self._canvas_state(store, pid_a), self._canvas_state(store, pid_b),
                                 f"{name}: 两路画布终态不一致")
            return run

        def media_ref_validation(name, args, expected_code):
            def run():
                client, store, pid_a, pid_b = self._fixture()
                before = self._canvas_state(store, pid_a)
                a = self._mcp_call(client, pid_a, name, args)
                path = name.removeprefix("canvas.")
                if name == "canvas.batch_prompt_edit":
                    path += "?agent_node_id=agent-consistency"
                    body = args
                else:
                    body = {**args, "agent_node_id": "agent-consistency"}
                b = self._rest(client, "POST", f"/api/projects/{pid_b}/canvas/{path}",
                               body, {"X-Agent-Project": pid_b})
                self._assert_pair(a, b, name)
                self.assertFalse(a["ok"], a)
                self.assertEqual(a["error"]["code"], expected_code)
                for pid in (pid_a, pid_b):
                    self.assertEqual(self._canvas_state(store, pid), before)
            return run

        def plan_validation(name, args, expected_code):
            def run():
                client, store, pid_a, pid_b = self._fixture()
                before = [store.canvas(pid) for pid in (pid_a, pid_b)]
                a = self._mcp_call(client, pid_a, f"plan.{name}", args)
                b = self._rest(client, "POST", f"/api/projects/{pid_b}/plan/{name}?agent_node_id={self.ACTOR}",
                               args, {"X-Agent-Project": pid_b})
                self._assert_pair(a, b, f"plan.{name}")
                self.assertFalse(a["ok"], a)
                self.assertEqual(a["error"]["code"], expected_code)
                self.assertEqual([store.canvas(pid) for pid in (pid_a, pid_b)], before)
            return run

        def gated(name, args, method, url_path, body=None, *, expected_error=None):
            def run():
                client, store, pid_a, pid_b = self._fixture()
                a, b = self._confirmed_pair(client, pid_a, pid_b, name, args, method, url_path, body)
                self._assert_pair(a, b, name)
                if expected_error:
                    self.assertFalse(a["ok"], a)
                    self.assertEqual(a["error"]["code"], expected_error)
                else:
                    for pid, result in ((pid_a, a), (pid_b, b)):
                        self._assert_receipt(client, pid, result)
                self.assertEqual(self._canvas_state(store, pid_a), self._canvas_state(store, pid_b),
                                 f"{name}: 两路画布终态不一致")
            return run

        def tmp_upload_run():
            client, store, pid_a, pid_b = self._fixture()
            # #355：上传路径须属于各自的项目目录。
            img_a = store.root / pid_a / "356t.png"
            img_b = store.root / pid_b / "356t.png"
            for p in (img_a, img_b):
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
            try:
                a = self._mcp_call(client, pid_a, "media.upload", {"file_path": str(img_a)})
                b = self._rest(client, "POST", f"/api/projects/{pid_b}/media/upload?agent_node_id=agent-consistency",
                               {"file_path": str(img_b)}, {"X-Agent-Project": pid_b})
                self._assert_pair(a, b, "media.upload")
                self.assertTrue(a["ok"], a)
                asset = json.loads(a["text"])["asset_id"]
                for pid in (pid_a, pid_b):
                    self.assertIsNotNone(store.media_record(pid, asset))
                self._assert_pair(self._mcp_call(client, pid_a, "media.list", {}),
                                  self._rest(client, "GET", f"/api/projects/{pid_b}/media/list"), "media.list")
            finally:
                for p in (img_a, img_b):
                    p.unlink(missing_ok=True)

        def media_delete_run():
            client, store, pid_a, pid_b = self._fixture()
            data = b"\x89PNG\r\n\x1a\n" + b"0" * 32
            asset_a = store.import_media(pid_a, data, "test.png", "image/png")["asset_id"]
            asset_b = store.import_media(pid_b, data, "test.png", "image/png")["asset_id"]
            self.assertEqual(asset_a, asset_b)
            a, b = self._confirmed_pair(client, pid_a, pid_b, "media.delete", {"asset_id": asset_a},
                                        "DELETE", f"/media/{asset_b}")
            self._assert_pair(a, b, "media.delete")
            for pid, result in ((pid_a, a), (pid_b, b)):
                self._assert_receipt(client, pid, result)
                self.assertEqual(store.list_media(pid), [])
            self.assertEqual(self._canvas_state(store, pid_a), self._canvas_state(store, pid_b))

        def search_run():
            client, store, pid_a, pid_b = self._fixture()
            a = self._mcp_call(client, pid_a, "canvas.search_projects", {"query": "glowing", "limit": 3})
            b = self._rest(client, "GET", f"/api/search/projects?q=glowing&limit=3")
            pa = json.loads(a["text"])
            pb = json.loads(b["text"])
            # 跨项目搜索返回两 fixture 项目（A/B 同名种子）——结构必须一致，limit 必须被两边同时遵守
            def total(p):
                return sum(len(g.get("hits", [])) for g in p.get("groups", []))
            self.assertLessEqual(total(pa), 3, f"MCP 路径 limit 未生效: {pa}")
            self.assertLessEqual(total(pb), 3, f"REST 路径 limit 未生效: {pb}")
            self.assertEqual(pa.get("query"), pb.get("query"))

        def layout_run(name, args):
            def run():
                client, store, pid_a, pid_b = self._fixture()
                for pid in (pid_a, pid_b):
                    nodes = [
                        {"id": identity, "type": kind, "x": x, "y": y, "w": w, "h": h,
                         "createdBy": "agent", "agentNodeId": "agent-consistency", **extra}
                        for identity, kind, x, y, w, h, extra in (
                            ("card-a", "smart-prompt", 240, 360, 316, 240, {}),
                            ("card-b", "smart-prompt", 600, 460, 316, 240, {}),
                            ("frame-a", "smart-group", 700, 100, 400, 400,
                             {"layoutFrame": True, "items": ["card-c"]}),
                            ("frame-b", "smart-group", 300, 800, 500, 400,
                             {"layoutFrame": True, "items": []}),
                            ("card-c", "smart-image", 800, 220, 136, 140, {}),
                        )]
                    store.apply_mutations(pid, [{"op": "upsert_node", "node": node} for node in nodes],
                                          base_revision=store.canvas(pid)["revision"])
                a = self._mcp_call(client, pid_a, f"canvas.{name}", args)
                b = self._rest(client, "POST", f"/api/projects/{pid_b}/canvas/{name}?agent_node_id=agent-consistency",
                               args, headers={"X-Agent-Project": pid_b})
                for pid, response in ((pid_a, a), (pid_b, b)):
                    self._assert_receipt(client, pid, response)
                self._assert_pair(a, b, name)
                self.assertEqual(self._canvas_state(store, pid_a), self._canvas_state(store, pid_b))
            return run

        def seed_operation(store, pid):
            journal = AgentOpsJournal(store, OpsDb(store.root))
            try:
                with journal.mutation(pid) as operation:
                    before = store.canvas(pid)
                    store.apply_mutations(pid, [{"op": "upsert_node", "node": {
                        "id": "ops-node", "type": "smart-prompt", "text": "operation"}}],
                        base_revision=before["revision"])
                    delta = agent_ops_delta.build_delta(before, store.canvas(pid))
                    old, new = record_sides(delta)
                    operation.register({"op_id": "op-test", "turn_id": "turn-test",
                                        "agent_node_id": "agent-consistency", "tool": "test",
                                        "args": {}, "before": old, "after": new,
                                        "touched_node_ids": ["ops-node"], "created_at": "2026-01-01",
                                        "irreversible_effects": [], "undone": False}, delta)
                store.flush(pid)
                self.assertEqual(operation.result()["status"], "settled")
            finally:
                journal.close()

        def ops_list_run():
            client, store, pid_a, pid_b = self._fixture()
            for pid in (pid_a, pid_b):
                seed_operation(store, pid)
            for args, query, expected in (({}, "", ["op-test"]),
                                          ({"turn_id": "turn-test", "limit": 1}, "?turn_id=turn-test&limit=1", ["op-test"]),
                                          ({"turn_id": "other"}, "?turn_id=other", [])):
                a = self._mcp_call(client, pid_a, "ops.list", args)
                b = self._rest(client, "GET", f"/api/projects/{pid_b}/ops{query}")
                self._assert_pair(a, b, "ops.list")
                self.assertEqual([op["op_id"] for op in json.loads(a["text"])["operations"]], expected)
            self.assertEqual(self._canvas_state(store, pid_a), self._canvas_state(store, pid_b))

        def ops_undo_run():
            client, store, pid_a, pid_b = self._fixture()
            for pid in (pid_a, pid_b):
                seed_operation(store, pid)
            args = {"op_id": "op-test"}
            url = f"/api/projects/{pid_b}/ops/undo"
            before = store.canvas(pid_b)
            denied = client.post(url, json=args)
            self.assertEqual(denied.status_code, 400, denied.text)
            detail = denied.json()["detail"]
            self.assertEqual(detail["code"], "INVALID_ARGUMENT")
            self.assertIn("X-Agent-Project", detail["message"])
            self.assertEqual(store.canvas(pid_b), before)
            for status in ("reverted", "already_reverted"):
                a = self._mcp_call(client, pid_a, "ops.undo", args)
                b = self._rest(client, "POST", url, args,
                               headers={"X-Agent-Project": pid_b})
                self._assert_pair(a, b, "ops.undo")
                result = json.loads(a["text"])
                self.assertEqual(result["status"], status)
                self.assertEqual(result[status], ["op-test"])
                self.assertEqual(self._canvas_state(store, pid_a), self._canvas_state(store, pid_b))
                self.assertNotIn("ops-node", [node["id"] for node in store.canvas(pid_a)["nodes"]])
            for pid in (pid_a, pid_b):
                records = self._rest(client, "GET", f"/api/projects/{pid}/ops")
                self.assertTrue(json.loads(records["text"])["operations"][0]["undone"])

        def edit_erase_pin_only():
            # edit_erase 消耗外部模型（qwen-image-edit），不执行 core；
            # 钉：清单存在 + MCP 侧走确认门 + REST 孪生路由存在。
            src = (ROOT / "app" / "routers" / "api_mcp.py").read_text(encoding="utf-8")
            gate_call = re.search(r'_impl_edit_erase.*?_confirm_gate', src, re.S)
            self.assertTrue(gate_call, "MCP edit_erase 必须走确认门")
            client, _, _, _ = self._fixture()
            resp = client.request("GET", "/api/projects/x/canvas/edit_erase")
            self.assertNotEqual(resp.status_code, 404, "REST 孪生路由必须存在")

        def presentation_run():
            client, store, pid_a, pid_b = self._fixture()
            before = [store.canvas(pid) for pid in (pid_a, pid_b)]
            for pid in (pid_a, pid_b):
                # Only the liveness boundary is needed: presentation never writes stdin.
                agent_host_manager.sessions[f'{pid}:{self.ACTOR}'].process = type(
                    'LiveProcessBoundary', (), {'returncode': None, 'poll': lambda self: self.returncode,
                        'terminate': lambda self: setattr(self, 'returncode', 0), 'wait': lambda self: 0})()
            args = {'action': 'show', 'kind': 'plan', 'title': 'Proposal',
                    'steps': [{'id': 'inspect', 'text': 'Inspect canvas'}]}
            a = self._mcp_call(client, pid_a, 'agent.present', args)
            b = self._rest(client, 'POST', f'/api/projects/{pid_b}/agent/present?agent_node_id={self.ACTOR}',
                           args, {'X-Agent-Project': pid_b})
            self._assert_pair(a, b, 'agent.present')
            self.assertTrue(a['ok'], a)
            for pid, result in ((pid_a, a), (pid_b, b)):
                presentation = json.loads(result['text'])['presentation']
                self.assertEqual(presentation['state'], 'presented')
                log = store.root / pid / 'agent-events' / f'{self.ACTOR}.jsonl'
                plans = [entry for line in log.read_text(encoding='utf-8').splitlines()
                         if (entry := json.loads(line))['event']['type'] == 'plan']
                self.assertEqual(len(plans), 1)
                self.assertEqual(plans[0]['project_id'], pid)
                self.assertEqual(plans[0]['node_id'], self.ACTOR)
                self.assertEqual(plans[0]['event']['presentation'], presentation)
            self.assertEqual([store.canvas(pid) for pid in (pid_a, pid_b)], before)

        cases = {
            'agent.present': presentation_run,
            "chat.history": pair("chat.history", {"node_id": self.ACTOR}, "GET", f"/chat/history?node_id={self.ACTOR}"),
            "chat.search": pair("chat.search", {"query": "firefly"}, "GET", "/chat/search?query=firefly"),
            "ops.list": ops_list_run,
            "ops.undo": ops_undo_run,
            "canvas.list_nodes": pair("canvas.list_nodes", {}, "GET", "/canvas/nodes"),
            "canvas.get_node": pair("canvas.get_node", {"node_id": "prompt-1"}, "GET", "/canvas/nodes/prompt-1"),
            "canvas.create_node": pair("canvas.create_node", {"type": "smart-prompt", "title": "New"}, "POST", "/canvas/nodes", {"node": {"type": "smart-prompt", "title": "New"}}),
            "canvas.update_node": gated("canvas.update_node", {"node_id": "prompt-1", "title": "NT", "text": "NX", "x": -32.5, "y": 48, "w": 360, "h": 260}, "PATCH", "/canvas/nodes/prompt-1", {"title": "NT", "text": "NX", "x": -32.5, "y": 48, "w": 360, "h": 260}),
            "canvas.delete_node": gated("canvas.delete_node", {"node_id": "image-1"}, "DELETE", "/canvas/nodes/image-1"),
            "canvas.connect": pair("canvas.connect", {"from_node": "prompt-1", "to_node": "image-1", "kind": "input"}, "POST", "/canvas/edges", {"edge": {"from": "prompt-1", "to": "image-1", "kind": "input"}}),
            "canvas.disconnect": gated("canvas.disconnect", {"from_node": "prompt-1", "to_node": "image-1"}, "DELETE", "/canvas/edges/prompt-1/image-1"),
            "canvas.place_media": media_ref_validation("canvas.place_media", {"asset_id": "missing-asset"}, "ASSET_NOT_FOUND"),
            "canvas.attach_refs": media_ref_validation("canvas.attach_refs", {
                "node_id": "missing-node", "refs": [{"source": {"node_id": "image-1", "image_index": 0}}],
            }, "NODE_NOT_FOUND"),
            "canvas.set_ref_order": media_ref_validation("canvas.set_ref_order", {
                "node_id": "missing-node", "order": ["missing-asset"], "role_overrides": {"missing-asset": "detail"},
            }, "NODE_NOT_FOUND"),
            "canvas.group_create": layout_run("group_create", {"title": "Main", "node_ids": ["card-a", "card-b"]}),
            "canvas.group_arrange": layout_run("group_arrange", {"group_id": "frame-a", "layout": "row"}),
            "canvas.arrange_groups": layout_run("arrange_groups", {"group_ids": ["frame-a", "frame-b"], "direction": "row"}),
            "canvas.align": layout_run("align", {"node_ids": ["card-a", "card-b"], "edge": "top"}),
            "canvas.distribute": layout_run("distribute", {"node_ids": ["card-a", "card-b"], "axis": "x", "gap": 20}),
            "canvas.search_projects": search_run,
            "canvas.view": pair("canvas.view", {"scope": "page"}, "POST", "/canvas/view", {"scope": "page"}),
            "canvas.view_media": pair("canvas.view_media", {"node_id": "image-1"}, "POST", "/canvas/view_media", {"node_id": "image-1"}),
            "canvas.edit_crop": gated("canvas.edit_crop", {"node_id": "image-1", "rect": {"x": 0, "y": 0, "width": 4, "height": 4}}, "POST", "/canvas/edit_crop", expected_error="ASSET_NOT_FOUND"),
            "canvas.batch_prompt_edit": media_ref_validation("canvas.batch_prompt_edit", {
                "apply": False, "edits": [{"node_id": "missing-node", "append": "detail"}],
            }, "NODE_NOT_FOUND"),
            "plan.import_excel": plan_validation("import_excel", {"file_path": "not-an-attachment.xlsx"}, "FILE_NOT_ALLOWED"),
            "plan.edit_preview": plan_validation("edit_preview", {"plan_id": "missing", "ops": []}, "PLAN_STALE"),
            "plan.read_preview": plan_validation("read_preview", {"plan_id": "missing", "generation": 1}, "PLAN_STALE"),
            "plan.apply": plan_validation("apply", {"plan_id": "missing"}, "PLAN_STALE"),
            "canvas.batch_edit": gated("canvas.batch_edit", {"node_ids": ["image-1"], "prompt": "p"}, "POST", "/canvas/batch_edit"),
            "canvas.edit_rotate": gated("canvas.edit_rotate", {"node_id": "image-1", "angle": 90}, "POST", "/canvas/edit_rotate", expected_error="ASSET_NOT_FOUND"),
            "canvas.edit_flip": gated("canvas.edit_flip", {"node_id": "image-1", "direction": "horizontal"}, "POST", "/canvas/edit_flip", expected_error="ASSET_NOT_FOUND"),
            "canvas.edit_outpaint": gated("canvas.edit_outpaint", {"node_id": "image-1", "outpaint_size": {"width": 40, "height": 40}}, "POST", "/canvas/edit_outpaint", expected_error="ASSET_NOT_FOUND"),
            "canvas.edit_erase": edit_erase_pin_only,
            "generate.trigger": gated("generate.trigger", {"node_id": "prompt-1"}, "POST", "/generate/trigger"),
            "generate.retry": pair("generate.retry", {"task_id": "nope"}, "POST", "/generate/retry",
                                   {"task_id": "nope"}, expected_error="TASK_NOT_FOUND"),
            "generate.status": pair("generate.status", {"task_id": "nope"}, "GET", "/generate/tasks/nope"),
            "generate.wait": pair("generate.wait", {"task_id": "nope", "timeout_seconds": 5}, "GET", "/generate/tasks/nope/wait?timeout=5"),
            **{f"generate.{name}": pair(f"generate.{name}", {}, "POST",
                                          f"/generate/{name}?agent_node_id={self.ACTOR}", {})
               for name in ("preflight", "request_batch", "batch_status", "stop_batch")},
            "media.upload": tmp_upload_run,
            "media.list": pair("media.list", {}, "GET", "/media/list"),
            "media.delete": media_delete_run,
            "viewport.focus": pair("viewport.focus", {"node_id": "prompt-1"}, "POST", "/viewport/focus", {"node_id": "prompt-1"}),
            "viewport.zoom": pair("viewport.zoom", {"level": 2}, "POST", "/viewport/zoom", {"level": 2}),
            "viewport.state": pair("viewport.state", {}, "GET", "/viewport/state"),
        }
        return cases


if __name__ == "__main__":
    unittest.main()
