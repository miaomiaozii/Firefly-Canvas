# -*- coding: utf-8 -*-
"""#356 一致性测试：单一来源 + 「同一输入 → 两路相同结果」遍历所有工具。

两层：
1) 同源层：MCP 面（app/routers/api_mcp.py）就是清单对象本身（import 同一性）；
   pi 扩展（app/agent_extensions/canvas-tools.ts）的工具注册段是
   tools/gen_canvas_tools.py 从清单 codegen 的 canvas-tools.generated.ts——
   freshness（重跑 diff 必须为空）、名字集合三方一致、schema 不变量。
2) 行为层：遍历清单工具，比对 MCP 与 REST 的返回值及完整画布终态。
   确认门工具（#242）经 gate 批准后比对 core 结果；canvas.edit_erase 消耗外部模型，
   只钉门与 schema。媒体写操作的 Agent 路径因记账尚不可用而 fail closed，用户路径仍可用。
"""
import asyncio
import copy
import json
import re
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main
from firefly_opsdb import OpsDb
from firefly_projects import ProjectStore
from app.core import CANVAS_TASKS, CANVAS_TASK_LOCK
from app.project_sessions import register_project_session, unregister_project_session
from app.routers import api_mcp
from app.services import agent_ops_delta, agent_tools_manifest
from app.services.agent_ops_journal import AgentOpsJournal, record_sides
from app.services.agent_errors import translate_detail
from app.services.agent_host import agent_host_manager
from tests.fake_agent_host import FakeAgentProcess, install_stub_pi_cli, restore_stub_pi_cli


def run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def setUpModule():
    global _PREVIOUS_PI_CLI
    _PREVIOUS_PI_CLI = install_stub_pi_cli()


def tearDownModule():
    restore_stub_pi_cli(_PREVIOUS_PI_CLI)


GEN_TS = ROOT / "app" / "agent_extensions" / "canvas-tools.generated.ts"
TOOLS_TS = ROOT / "app" / "agent_extensions" / "canvas-tools.ts"


class SourceSameOriginTests(unittest.TestCase):
    """同源层：清单 = MCP 面 = pi 生成段的唯一来源。"""

    def test_mcp_surface_is_the_manifest(self):
        self.assertIs(api_mcp.TOOLS, agent_tools_manifest.TOOLS)
        self.assertEqual(api_mcp.TOOL_NAMES, agent_tools_manifest.TOOL_NAMES)
        self.assertEqual(set(api_mcp.TOOL_IMPLS.keys()), agent_tools_manifest.TOOL_NAMES)

    def test_mcp_write_descriptions_document_external_turn_ids(self):
        client = TestClient(main.app, base_url="http://127.0.0.1:8124")
        self.addCleanup(client.close)
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

    SEED = [
        {"op": "upsert_node", "node": {"id": "prompt-1", "type": "smart-prompt", "title": "Test Prompt", "text": "A glowing firefly", "x": 100, "y": 100}},
        {"op": "upsert_node", "node": {"id": "image-1", "type": "smart-image", "title": "Test Image", "x": 300, "y": 100}},
        {"op": "upsert_edge", "edge": {"id": "edge-1", "from": "prompt-1", "to": "image-1", "kind": "flow"}},
        # #350：MCP 不再自动物化 agent 节点——归属节点两项目同种，终态全等比较。
        {"op": "upsert_node", "node": {"id": "agent-consistency", "type": "smart-agent",
                                       "title": "Agent", "x": 0, "y": 0}},
    ]

    def setUp(self):
        self._dirs = []
        self._stores = []
        self._tabs = []
        self._prev_store = main.FIREFLY_PROJECT_STORE

    def tearDown(self):
        run_async(agent_host_manager.cleanup_all())
        for tab in self._tabs:
            unregister_project_session(tab)
            if tab in main.manager.active_connections:
                main.manager.active_connections.remove(tab)
            main.manager.connection_clients.pop(tab, None)
            main.manager.user_connections.pop(tab.client_id, None)
        main.FIREFLY_PROJECT_STORE = self._prev_store
        for store in self._stores:
            store.close()
        for d in self._dirs:
            d.cleanup()

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
        self._tabs.append(tab)
        return tab

    def _fixture(self):
        """一个干净的 (client, store, pid_a, pid_b)：a 走 MCP，b 走 REST 孪生。"""
        tmp = tempfile.TemporaryDirectory(prefix="e2e-tabbit-356-consistency-")
        self._dirs.append(tmp)
        store = ProjectStore(Path(tmp.name) / "projects", snapshot_delay=0)
        self._stores.append(store)
        main.FIREFLY_PROJECT_STORE = store
        # All generation in this equivalence suite is offline and deterministic.
        for patcher in (
            patch("app.routers.api_agent_tools._run_agent_generation", new=AsyncMock()),
            patch.object(main, "route_image_generation", return_value=SimpleNamespace(
                provider_id="offline", model="stub-model")),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        client = TestClient(main.app, base_url="http://127.0.0.1:8124")
        pids = []
        for tag in ("A", "B"):
            p = store.create_project(f"e2e-tabbit-consistency-{tag}")
            store.apply_mutations(p["id"], copy.deepcopy(self.SEED), base_revision=0)
            self._open_tab(p["id"])
            pids.append(p["id"])
        return client, store, pids[0], pids[1]

    def _mcp_call(self, client, project, name, args, request_id=None):
        # #350：&node= 精确绑定归属节点（不再依赖共享外部节点物化）。
        resp = client.post(
            f"/api/mcp?project={project}&node=agent-consistency",
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

    def _canon_payload(self, payload):
        out = {"ok": payload["ok"], "text": self._canon_text(payload.get("text", ""))}
        if "error" in payload:
            out["error"] = self._canon_text(json.dumps(payload["error"], sort_keys=True))
        # #350 后拍板 c 作废：MCP 不再 upsert agent 节点，返回值不再剔除任何节点。
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
        ca, cb = self._canon_payload(a), self._canon_payload(b)
        self.assertEqual(ca, cb, f"{label}: MCP 与 REST 孪生结果不一致\n MCP={a}\n REST={b}")

    # -- gated helpers（#242 门：单事件循环 + confirm handler 直接批准）----

    def _materialize_agent(self, client, store, pid):
        # #350：agent 节点由 SEED 预置（MCP 不再自动物化）；此处只取归属 id。
        canvas = store.canvas(pid)
        agents = [n for n in canvas.get("nodes", []) if n.get("type") == "smart-agent"]
        self.assertTrue(agents, "#350：SEED 应预置归属 agent 节点")
        return agents[0]["id"]

    def _gated_impl(self, store, pid, agent_node_id, impl, args):
        session = run_async(agent_host_manager.get_or_create_session(pid, agent_node_id))
        session.process = FakeAgentProcess(script=[])
        outcome = {}

        async def scenario():
            async def approve_handler(sess, event):
                if event.get("type") == "confirm":
                    for handler in list(sess.confirm_handlers):
                        await handler(event["confirm_id"], True)
            agent_host_manager.on_event(session, approve_handler)
            try:
                outcome["result"] = await impl(pid, agent_node_id, args)
            except HTTPException as exc:
                outcome["error"] = translate_detail(exc.status_code, exc.detail)
            except Exception as exc:  # noqa: BLE001
                outcome["error"] = translate_detail(500, str(exc))
            finally:
                if approve_handler in session.event_handlers:
                    session.event_handlers.remove(approve_handler)

        run_async(scenario())
        if "error" in outcome:
            return {"ok": False, "error": outcome["error"]}
        return {"ok": True, "text": json.dumps(outcome["result"])}

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

    def _cases(self):
        def pair(name, args, method, url_path, body=None):
            """通用形状：A 走 MCP tools/call，B 走 REST 孪生；比返回值 + 画布终态。"""
            def run():
                client, store, pid_a, pid_b = self._fixture()
                a = self._mcp_call(client, pid_a, name, args)
                rest_body = {**body, "agent_node_id": "agent-consistency"} if name == "canvas.create_node" else body
                is_agent_write = name in {"canvas.create_node", "canvas.update_node", "canvas.connect"}
                path = f"{url_path}?agent_node_id=agent-consistency" if is_agent_write else url_path
                headers = {"X-Agent-Project": pid_b} if is_agent_write else None
                b = self._rest(client, method, f"/api/projects/{pid_b}{path}", rest_body, headers)
                if is_agent_write:
                    for pid, result in ((pid_a, a), (pid_b, b)):
                        op_id = json.loads(result["text"])["op_id"]
                        self.assertRegex(op_id, r"^[0-9a-f]{32}$")
                        records = self._rest(client, "GET", f"/api/projects/{pid}/ops")
                        self.assertEqual(json.loads(records["text"])["operations"][0]["op_id"], op_id)
                        if name == "canvas.create_node":
                            payload = json.loads(result["text"])
                            nodes = {node["id"]: node for node in store.canvas(pid)["nodes"]}
                            frame = nodes[nodes["agent-consistency"]["chatFrameId"]]
                            self.assertEqual(frame["items"], [payload["node_id"]])
                            self.assertEqual(frame["chatFrameFor"], "agent-consistency")
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
                b = self._rest(client, "POST", f"/api/projects/{pid_b}/canvas/{path}",
                               {**args, "agent_node_id": "agent-consistency"}, {"X-Agent-Project": pid_b})
                self._assert_pair(a, b, name)
                self.assertFalse(a["ok"], a)
                self.assertEqual(a["error"]["code"], expected_code)
                for pid in (pid_a, pid_b):
                    self.assertEqual(self._canvas_state(store, pid), before)
            return run

        def gated(name, args, impl, method, url_path, body=None):
            """确认门工具（#357 后含 disconnect）：MCP 经门批准后打 core；
            REST 孪生不带 X-Agent-Project 头 = 浏览器用户路径，不进服务端门（#347 ffDialog 在发起侧）。"""
            def run():
                client, store, pid_a, pid_b = self._fixture()
                agent_id = self._materialize_agent(client, store, pid_a)
                a = self._gated_impl(store, pid_a, agent_id, getattr(api_mcp, impl), args)
                # Same initiating context, even while this test bypasses the REST gate.
                rest_path = f"{url_path}?agent_node_id={agent_id}" if name in {"canvas.batch_edit", "generate.trigger"} else url_path
                b = self._rest(client, method, f"/api/projects/{pid_b}{rest_path}", body)
                self._assert_pair(a, b, name)
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
            with patch.object(api_mcp, "_confirm_gate", new=AsyncMock()), \
                 patch("app.routers.api_agent_tools._server_confirm_gate", new=AsyncMock()):
                a = self._mcp_call(client, pid_a, "media.delete", {"asset_id": asset_a})
                b = self._rest(client, "DELETE", f"/api/projects/{pid_b}/media/{asset_b}?agent_node_id=agent-consistency",
                               headers={"X-Agent-Project": pid_b})
            self._assert_pair(a, b, "media.delete")
            self.assertTrue(a["ok"], a)
            for pid in (pid_a, pid_b):
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
                    self.assertTrue(response["ok"], response)
                    body = json.loads(response["text"])
                    op_id = body.pop("op_id")
                    journal = self._rest(client, "GET", f"/api/projects/{pid}/ops")
                    self.assertEqual(json.loads(journal["text"])["operations"][0]["op_id"], op_id)
                    body["touched_node_ids"] = sorted(self._canon_text(n) for n in body["touched_node_ids"])
                    response["text"] = json.dumps(body, sort_keys=True)
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
            for status in ("reverted", "already_reverted"):
                args = {"op_id": "op-test"}
                a = self._mcp_call(client, pid_a, "ops.undo", args)
                b = self._rest(client, "POST", f"/api/projects/{pid_b}/ops/undo", args)
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
            client = TestClient(main.app, base_url="http://127.0.0.1:8124")
            resp = client.request("GET", "/api/projects/x/canvas/edit_erase")
            self.assertNotEqual(resp.status_code, 404, "REST 孪生路由必须存在")

        cases = {
            "ops.list": ops_list_run,
            "ops.undo": ops_undo_run,
            "canvas.list_nodes": pair("canvas.list_nodes", {}, "GET", "/canvas/nodes"),
            "canvas.get_node": pair("canvas.get_node", {"node_id": "prompt-1"}, "GET", "/canvas/nodes/prompt-1"),
            "canvas.create_node": pair("canvas.create_node", {"type": "smart-prompt", "title": "New"}, "POST", "/canvas/nodes", {"node": {"type": "smart-prompt", "title": "New"}}),
            "canvas.update_node": pair("canvas.update_node", {"node_id": "prompt-1", "title": "NT", "text": "NX", "x": -32.5, "y": 48, "w": 360, "h": 260}, "PATCH", "/canvas/nodes/prompt-1", {"title": "NT", "text": "NX", "x": -32.5, "y": 48, "w": 360, "h": 260}),
            "canvas.delete_node": gated("canvas.delete_node", {"node_id": "image-1"}, "_impl_delete_node", "DELETE", "/canvas/nodes/image-1"),
            "canvas.connect": pair("canvas.connect", {"from_node": "prompt-1", "to_node": "image-1", "kind": "input"}, "POST", "/canvas/edges", {"edge": {"from": "prompt-1", "to": "image-1", "kind": "input"}}),
            "canvas.disconnect": gated("canvas.disconnect", {"from_node": "prompt-1", "to_node": "image-1"}, "_impl_disconnect", "DELETE", "/canvas/edges/prompt-1/image-1"),
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
            "canvas.edit_crop": gated("canvas.edit_crop", {"node_id": "image-1", "rect": {"x": 0, "y": 0, "width": 4, "height": 4}}, "_impl_edit_crop", "POST", "/canvas/edit_crop", {"node_id": "image-1", "rect": {"x": 0, "y": 0, "width": 4, "height": 4}}),
            "canvas.batch_edit": gated("canvas.batch_edit", {"node_ids": ["image-1"], "prompt": "p"}, "_impl_batch_edit", "POST", "/canvas/batch_edit", {"node_ids": ["image-1"], "prompt": "p"}),
            "canvas.edit_rotate": gated("canvas.edit_rotate", {"node_id": "image-1", "angle": 90}, "_impl_edit_rotate", "POST", "/canvas/edit_rotate", {"node_id": "image-1", "angle": 90}),
            "canvas.edit_flip": gated("canvas.edit_flip", {"node_id": "image-1", "direction": "horizontal"}, "_impl_edit_flip", "POST", "/canvas/edit_flip", {"node_id": "image-1", "direction": "horizontal"}),
            "canvas.edit_outpaint": gated("canvas.edit_outpaint", {"node_id": "image-1", "outpaint_size": {"width": 40, "height": 40}}, "_impl_edit_outpaint", "POST", "/canvas/edit_outpaint", {"node_id": "image-1", "outpaint_size": {"width": 40, "height": 40}}),
            "canvas.edit_erase": edit_erase_pin_only,
            "generate.trigger": gated("generate.trigger", {"node_id": "prompt-1"}, "_impl_trigger", "POST", "/generate/trigger", {"node_id": "prompt-1"}),
            "generate.status": pair("generate.status", {"task_id": "nope"}, "GET", "/generate/tasks/nope"),
            "generate.wait": pair("generate.wait", {"task_id": "nope", "timeout_seconds": 5}, "GET", "/generate/tasks/nope/wait?timeout=5"),
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
