"""Tests for Issue #243: MCP streamable-HTTP endpoint (ADR 0022).

Covers:
- protocol subset: initialize (version negotiation + session id), ping,
  tools/list, tools/call, JSON-RPC error codes (-32601/-32602/-32600/-32700),
  batch rejection, GET 405
- three-level project arbitration (ADR 0022)
- materialized external Agent node (决议 Q12-a)
- confirmation gate on destructive tools (approve / reject / timeout)
- canvas_updated broadcast after mutations + viewport targeted dispatch
- event recorder (dirty flag + persist interval aggregation)
- generate.wait timeout honoring (server core)
- #240 same-origin guard on the MCP endpoint
"""
import asyncio
import json
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main
from firefly_projects import ProjectStore
from app.core import CANVAS_TASKS, CANVAS_TASK_LOCK
from app.project_sessions import register_project_session, unregister_project_session
from app.routers import api_mcp
from app.routers.api_agent_tools import wait_for_task_core
from app.services.agent_host import agent_host_manager
from tests.fake_agent_host import FakeAgentProcess, install_stub_pi_cli, restore_stub_pi_cli


def run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def setUpModule():
    """Never let this module resolve the real pi CLI ('pi' -> pi.cmd)."""
    global _PREVIOUS_PI_CLI
    _PREVIOUS_PI_CLI = install_stub_pi_cli()


def tearDownModule():
    restore_stub_pi_cli(_PREVIOUS_PI_CLI)


class FakeTab:
    """In-memory canvas tab: records every text frame the server sends."""

    def __init__(self, client_id=None):
        self.client_id = client_id or f"client-{uuid.uuid4().hex[:8]}"
        self.sent = []

    async def send_text(self, text):
        self.sent.append(json.loads(text))

    def sent_of(self, mtype):
        return [m for m in self.sent if m.get("type") == mtype]


class McpTestBase(unittest.TestCase):
    """Shared fixtures: temp store, loopback TestClient, one project."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="firefly-mcp-tests-")
        self.store = ProjectStore(Path(self.temp_dir.name) / "projects", snapshot_delay=0)
        self.previous_store = main.FIREFLY_PROJECT_STORE
        main.FIREFLY_PROJECT_STORE = self.store
        # sidecar-style client: loopback Host, no Origin (canvas-tools.ts form)
        self.client = TestClient(main.app, base_url="http://127.0.0.1:8124")
        self.project = self.store.create_project("MCP Test")
        self.project_id = self.project["id"]
        self.tabs = []

    def tearDown(self):
        run_async(agent_host_manager.cleanup_all())
        for tab in self.tabs:
            unregister_project_session(tab)
            if tab in main.manager.active_connections:
                main.manager.active_connections.remove(tab)
            main.manager.connection_clients.pop(tab, None)
            main.manager.user_connections.pop(tab.client_id, None)
        self.client.close()
        self.store.close()
        main.FIREFLY_PROJECT_STORE = self.previous_store
        self.temp_dir.cleanup()

    # -- helpers -----------------------------------------------------------

    def open_tab(self, project_id=None):
        tab = FakeTab()
        pid = project_id or self.project_id
        register_project_session(tab, pid)
        main.manager.active_connections.append(tab)
        main.manager.connection_clients[tab] = tab.client_id
        main.manager.user_connections[tab.client_id] = tab
        self.tabs.append(tab)
        return tab

    def mcp(self, payload, project=""):
        url = f"/api/mcp?project={project}" if project else "/api/mcp"
        return self.client.post(url, json=payload)

    def tool_call(self, name, args, request_id=1, project=""):
        return self.mcp(
            {"jsonrpc": "2.0", "id": request_id, "method": "tools/call",
             "params": {"name": name, "args": args}},
            project=project,
        )

    def tool_result(self, resp):
        """Unpack an MCP tools/call result; asserts HTTP 200 + JSON-RPC ok."""
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertNotIn("error", body, body)
        return body["result"]

    def materialized_agent_node(self):
        canvas = self.store.canvas(self.project_id)
        nodes = [n for n in canvas.get("nodes", []) if n.get("type") == "smart-agent"]
        self.assertEqual(len(nodes), 1, f"expected exactly one materialized agent node, got {nodes}")
        return nodes[0]


class McpProtocolTests(McpTestBase):
    """Protocol subset behavior (pure JSON, no SSE)."""

    def test_initialize_negotiates_default_version_and_mints_session(self):
        resp = self.mcp({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        self.assertEqual(resp.status_code, 200, resp.text)
        sid = resp.headers.get("mcp-session-id")
        self.assertTrue(sid, "Mcp-Session-Id header must be present")
        body = resp.json()
        self.assertEqual(body["jsonrpc"], "2.0")
        self.assertEqual(body["id"], 1)
        self.assertEqual(body["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual(body["result"]["serverInfo"]["name"], "firefly-canvas")

    def test_initialize_echoes_client_session_id(self):
        resp = self.mcp({"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": {}})
        minted = resp.headers.get("mcp-session-id")
        resp2 = self.client.post(
            "/api/mcp", json={"jsonrpc": "2.0", "id": 3, "method": "initialize", "params": {}},
            headers={"Mcp-Session-Id": minted},
        )
        self.assertEqual(resp2.headers.get("mcp-session-id"), minted)

    def test_initialize_unsupported_version_negotiates_down(self):
        resp = self.mcp(
            {"jsonrpc": "2.0", "id": 4, "method": "initialize",
             "params": {"protocolVersion": "2099-01-01"}}
        )
        body = resp.json()
        self.assertEqual(body["result"]["protocolVersion"], "2025-06-18")

    def test_supported_version_is_kept(self):
        resp = self.mcp(
            {"jsonrpc": "2.0", "id": 5, "method": "initialize",
             "params": {"protocolVersion": "2025-03-26"}}
        )
        self.assertEqual(resp.json()["result"]["protocolVersion"], "2025-03-26")

    def test_ping(self):
        resp = self.mcp({"jsonrpc": "2.0", "id": 6, "method": "ping"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["result"], {})

    def test_tools_list_returns_full_surface_without_fake_get_selection(self):
        resp = self.mcp({"jsonrpc": "2.0", "id": 7, "method": "tools/list"})
        tools = resp.json()["result"]["tools"]
        names = {t["name"] for t in tools}
        self.assertEqual(len(tools), 19, names)  # 20 in canvas-tools.ts minus fake get_selection
        # ADR 0022 已知缺口「假 canvas.get_selection」→ 摘除，不得再出现
        self.assertNotIn("canvas.get_selection", names)
        for expected in (
            "canvas.list_nodes", "canvas.get_node", "canvas.create_node",
            "canvas.update_node", "canvas.delete_node", "canvas.connect",
            "canvas.disconnect", "canvas.view", "canvas.view_media", "canvas.edit_crop",
            "generate.trigger", "generate.status",
            "generate.wait", "media.upload", "media.list", "media.delete",
            "viewport.focus", "viewport.zoom", "viewport.state",
        ):
            self.assertIn(expected, names)
        # destructive tools must document the confirmation gate
        by_name = {t["name"]: t for t in tools}
        self.assertIn("确认门", by_name["canvas.delete_node"]["description"])
        self.assertIn("确认门", by_name["media.delete"]["description"])
        # every tool has an inputSchema with required fields
        for tool in tools:
            self.assertEqual(tool["inputSchema"]["type"], "object")
            self.assertIsInstance(tool["inputSchema"]["required"], list)

    def test_unknown_method(self):
        resp = self.mcp({"jsonrpc": "2.0", "id": 8, "method": "resources/list"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["error"]["code"], -32601)

    def test_unknown_tool(self):
        resp = self.tool_call("canvas.get_selection", {}, project=self.project_id)
        self.assertEqual(resp.status_code, 200)
        result = resp.json()["result"]
        self.assertTrue(result["isError"])
        self.assertIn("Unknown tool", result["content"][0]["text"])

    def test_parse_error(self):
        resp = self.client.post("/api/mcp", content=b"{not json",
                                headers={"Content-Type": "application/json"})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"]["code"], -32700)

    def test_batch_request_rejected(self):
        resp = self.mcp([{"jsonrpc": "2.0", "id": 1, "method": "ping"},
                         {"jsonrpc": "2.0", "id": 2, "method": "ping"}])
        self.assertEqual(resp.status_code, 400)
        error = resp.json()["error"]
        self.assertEqual(error["code"], -32600)
        self.assertEqual(error["message"], "MCP batch requests are not supported")

    def test_non_jsonrpc_message_rejected(self):
        resp = self.mcp({"hello": "world"})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"]["code"], -32600)

    def test_get_returns_405(self):
        resp = self.client.get("/api/mcp")
        self.assertEqual(resp.status_code, 405)
        self.assertEqual(resp.headers.get("allow"), "POST")


class McpProjectArbitrationTests(McpTestBase):
    """Three-level project arbitration (ADR 0022)."""

    def test_no_open_tabs_errors(self):
        result = self.tool_result(self.tool_call("canvas.list_nodes", {}))
        self.assertTrue(result["isError"])
        self.assertIn("没有打开的画布标签页", result["content"][0]["text"])

    def test_multiple_open_tabs_require_explicit_project(self):
        other = self.store.create_project("Second Project")
        self.open_tab()
        self.open_tab(project_id=other["id"])
        result = self.tool_result(self.tool_call("canvas.list_nodes", {}))
        self.assertTrue(result["isError"])
        self.assertIn("多个项目", result["content"][0]["text"])
        self.assertIn("?project=", result["content"][0]["text"])

    def test_single_open_tab_auto_resolved(self):
        self.open_tab()
        self.tool_call("canvas.create_node",
                       {"node": {"type": "prompt", "id": "auto-res-1", "title": "t"}},
                       request_id=10)
        result = self.tool_result(self.tool_call("canvas.list_nodes", {}, request_id=11))
        self.assertFalse(result["isError"], result)
        text = result["content"][0]["text"]
        self.assertIn("auto-res-1", text)

    def test_explicit_project_wins_over_single_tab(self):
        other = self.store.create_project("Explicit Target")
        self.open_tab()          # tab for self.project
        self.open_tab(project_id=other["id"])  # tab for the explicit target
        self.tool_call("canvas.create_node",
                       {"node": {"type": "prompt", "id": "other-node-1"}},
                       request_id=12, project=other["id"])
        self.tool_call("canvas.create_node",
                       {"node": {"type": "prompt", "id": "home-node-1"}},
                       request_id=12, project=self.project_id)
        # ambiguous without ?project=
        ambiguous = self.tool_result(self.tool_call("canvas.list_nodes", {}, request_id=13))
        self.assertTrue(ambiguous["isError"])
        self.assertIn("多个项目", ambiguous["content"][0]["text"])
        # explicit target returns only that project's nodes
        result2 = self.tool_result(
            self.tool_call("canvas.list_nodes", {}, request_id=14, project=other["id"]))
        text2 = result2["content"][0]["text"]
        self.assertIn("other-node-1", text2)
        self.assertNotIn("home-node-1", text2)

    def test_explicit_project_without_tab_errors(self):
        other = self.store.create_project("Never Opened")
        self.open_tab()
        result = self.tool_result(
            self.tool_call("canvas.list_nodes", {}, request_id=15, project=other["id"]))
        self.assertTrue(result["isError"])
        self.assertIn("没有打开的画布标签页", result["content"][0]["text"])


class McpToolBehaviorTests(McpTestBase):
    """Tool execution, materialization, broadcast, viewport, event recorder."""

    def test_tools_call_materializes_agent_node_once(self):
        self.open_tab()
        first = self.tool_result(self.tool_call("canvas.list_nodes", {}, request_id=21))
        self.assertFalse(first["isError"], first)
        node = self.materialized_agent_node()
        self.assertTrue(node.get("mcp_session"), "agent node must carry its MCP session id")
        # a second call reuses (does not duplicate) the materialized node
        self.tool_result(self.tool_call("canvas.list_nodes", {}, request_id=22))
        canvas = self.store.canvas(self.project_id)
        self.assertEqual(
            len([n for n in canvas["nodes"] if n.get("type") == "smart-agent"]), 1)

    def test_create_node_and_get(self):
        self.open_tab()
        result = self.tool_result(self.tool_call("canvas.create_node", {
            "node": {"type": "prompt", "id": "mcp-node-1", "title": "from mcp", "x": 5, "y": 6}
        }, request_id=31))
        self.assertFalse(result["isError"], result)
        self.assertIn("mcp-node-1", result["content"][0]["text"])
        canvas = self.store.canvas(self.project_id)
        nodes = {n["id"]: n for n in canvas["nodes"]}
        self.assertEqual(nodes["mcp-node-1"]["type"], "prompt")
        result2 = self.tool_result(
            self.tool_call("canvas.get_node", {"node_id": "mcp-node-1"}, request_id=32))
        self.assertIn("from mcp", result2["content"][0]["text"])

    def test_connect_and_disconnect(self):
        self.open_tab()
        for nid in ("conn-a", "conn-b"):
            self.tool_result(self.tool_call("canvas.create_node",
                                            {"node": {"type": "prompt", "id": nid}},
                                            request_id=41))
        result = self.tool_result(self.tool_call("canvas.connect", {
            "edge": {"from": "conn-a", "to": "conn-b"}}, request_id=42))
        self.assertFalse(result["isError"], result)
        canvas = self.store.canvas(self.project_id)
        self.assertTrue(any(
            str(e.get("from")) == "conn-a" and str(e.get("to")) == "conn-b"
            for e in canvas.get("connections", [])))
        result2 = self.tool_result(self.tool_call("canvas.disconnect", {
            "from_node": "conn-a", "to_node": "conn-b"}, request_id=43))
        self.assertFalse(result2["isError"], result2)
        canvas = self.store.canvas(self.project_id)
        self.assertFalse(any(
            str(e.get("from")) == "conn-a" and str(e.get("to")) == "conn-b"
            for e in canvas.get("connections", [])))

    def test_mutation_broadcasts_canvas_updated_to_tab(self):
        tab = self.open_tab()
        result = self.tool_result(self.tool_call("canvas.create_node",
                                                {"node": {"type": "prompt", "id": "bc-1"}},
                                                request_id=51))
        self.assertFalse(result["isError"], result)
        frames = tab.sent_of("canvas_updated")
        self.assertTrue(frames, f"tab must receive canvas_updated, got: {tab.sent}")
        self.assertEqual(frames[-1]["canvas_id"], self.project_id)

    def test_viewport_focus_targets_single_tab(self):
        tab = self.open_tab()
        self.tool_result(self.tool_call("canvas.create_node",
                                        {"node": {"type": "prompt", "id": "vp-1", "x": 10, "y": 20}},
                                        request_id=61))
        other = self.store.create_project("Other Project")
        other_tab = self.open_tab(project_id=other["id"])
        result = self.tool_result(self.tool_call("viewport.focus", {"node_id": "vp-1"},
                                                 request_id=62, project=self.project_id))
        self.assertFalse(result["isError"], result)
        body = json.loads(result["content"][0]["text"])
        self.assertTrue(body["dispatched"], "owning tab is connected, dispatch expected")
        cmds = tab.sent_of("canvas_viewport_command")
        self.assertTrue(cmds, f"owning tab must receive the viewport command: {tab.sent}")
        self.assertEqual(cmds[-1]["command"], "focus")
        self.assertEqual(cmds[-1]["node_id"], "vp-1")
        self.assertEqual(cmds[-1]["x"], 10)
        self.assertFalse(other_tab.sent_of("canvas_viewport_command"),
                         "viewport command must be targeted, not broadcast")

    def test_viewport_zoom_without_connected_tab_still_updates_state(self):
        self.open_tab()  # tab registered but... we test state fallback
        # disconnect the tab's transport to simulate a closed tab
        tab = self.tabs[0]
        main.manager.user_connections.pop(tab.client_id, None)
        main.manager.connection_clients.pop(tab, None)
        main.manager.active_connections.remove(tab)
        result = self.tool_result(self.tool_call("viewport.zoom", {"level": 1.5},
                                                 request_id=63))
        body = json.loads(result["content"][0]["text"])
        self.assertFalse(body["dispatched"], "no live tab -> dispatch must be false")
        state = self.tool_result(self.tool_call("viewport.state", {}, request_id=64))
        self.assertEqual(json.loads(state["content"][0]["text"])["scale"], 1.5)

    def test_generate_wait_timeout_honored(self):
        task_id = "task-mcp-wait-test"
        with CANVAS_TASK_LOCK:
            CANVAS_TASKS[task_id] = {
                "id": task_id, "type": "project-online-image",
                "project_id": self.project_id, "status": "running",
                "created_at": time.time(), "updated_at": time.time(),
                "result": None, "error": "",
            }
        try:
            t0 = time.time()
            res = run_async(wait_for_task_core(self.project_id, task_id, 1))
            elapsed = time.time() - t0
        finally:
            with CANVAS_TASK_LOCK:
                CANVAS_TASKS.pop(task_id, None)
        self.assertEqual(res["status"], "timeout")
        self.assertLess(elapsed, 5, "timeout=1 must be honored (not the old 120s default)")
        self.assertIn("1s", res["error"])

    def test_successful_call_is_recorded(self):
        self.open_tab()
        # drain any leftovers from other tests
        api_mcp.mcp_event_recorder.flush()
        self.tool_result(self.tool_call("canvas.list_nodes", {}, request_id=71))
        batch = api_mcp.mcp_event_recorder.flush()
        tools = [e["tool"] for e in batch]
        self.assertIn("canvas.list_nodes", tools)
        self.assertTrue(all(e["ok"] for e in batch if e["tool"] == "canvas.list_nodes"))

    def test_event_recorder_aggregates_within_interval(self):
        batches = []
        rec = api_mcp.McpEventRecorder(sink=batches.append, persist_interval_s=0.05)

        async def run():
            rec.record({"tool": "a"})
            rec.record({"tool": "b"})
            rec.record({"tool": "c"})
            await asyncio.sleep(0.25)

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(run())
        finally:
            loop.close()
        self.assertEqual(len(batches), 1, "one aggregated batch, not per-event flushes")
        self.assertEqual([e["tool"] for e in batches[0]], ["a", "b", "c"])
        self.assertTrue(all("ts" in e for e in batches[0]))

    def test_failed_call_is_recorded(self):
        # no open tab -> arbitration failure is also an event
        api_mcp.mcp_event_recorder.flush()
        result = self.tool_result(self.tool_call("canvas.list_nodes", {}, request_id=72))
        self.assertTrue(result["isError"])
        batch = api_mcp.mcp_event_recorder.flush()
        bad = [e for e in batch if not e["ok"]]
        self.assertTrue(bad, f"failure must be recorded, batch={batch}")
        self.assertIn("没有打开的画布标签页", bad[0]["error"])


class McpConfirmationGateTests(McpTestBase):
    """Destructive tools must pass the materialized confirmation gate.

    The gate is exercised on a single event loop: the destructive tool blocks
    in ``request_confirmation_core``, dispatches its confirm event to the
    owning session's event handlers, and the human's decision is delivered by
    invoking the gate's registered confirm handler directly (the same handler
    the WS stream endpoint calls on a real 'confirm' frame).

    Residual risk (recorded, not a code bug): the full WS round-trip — a real
    browser reading the confirm frame over the network and replying with the
    decision — cannot be driven under the starlette TestClient, whose single
    anyio portal serializes the in-flight gate request against a competing
    WS-frame read (verified by diagnostic: the confirm frame is written by the
    gate but only reaches the client-side reader at connection close). Production
    runs one loop with a real socket, so no such serialization exists. What is
    verified here is the dispatch mechanism the WS relies on (confirm event ->
    session.event_handlers) plus the gate's approve/reject/timeout decision
    logic end to end through the real ``_impl_delete_node`` -> ``_confirm_gate``
    -> ``request_confirmation_core`` path.
    """

    def _setup_delete_scenario(self):
        self.open_tab()
        self.tool_result(self.tool_call("canvas.create_node",
                                        {"node": {"type": "prompt", "id": "doomed-1"}},
                                        request_id=81))
        # materialize the agent node + its session
        self.tool_result(self.tool_call("canvas.list_nodes", {}, request_id=82))
        agent_node_id = self.materialized_agent_node()["id"]
        session = run_async(agent_host_manager.get_or_create_session(
            self.project_id, agent_node_id))
        # get_or_create_session only returns the cached session while it is
        # running (live process) — mirror the real WS stream endpoint which
        # spawns the session, so the gate and this test see ONE session.
        session.process = FakeAgentProcess(script=[])
        return session, agent_node_id

    def _run_gated_delete(self, approve):
        """Run the real destructive tool through the confirmation gate on one
        event loop, delivering the human's decision via the gate's own confirm
        handler.

        ``approve`` is True/False to decide, or None to let the gate time out.
        Returns (tool_result_dict | raised_McpToolError, [confirm_events]).
        """
        from app.routers.api_mcp import _impl_delete_node
        session, agent_node_id = self._setup_delete_scenario()
        captured = []
        outcome = {}

        async def scenario():
            async def decide_handler(sess, event):
                captured.append(event)
                if event.get("type") == "confirm" and approve is not None:
                    for handler in list(sess.confirm_handlers):
                        await handler(event["confirm_id"], approve)
            agent_host_manager.on_event(session, decide_handler)
            try:
                outcome["result"] = await _impl_delete_node(
                    self.project_id, agent_node_id, {"node_id": "doomed-1"})
            except Exception as exc:  # noqa: BLE001 — gate raises McpToolError
                outcome["error"] = exc
            finally:
                session.event_handlers.remove(decide_handler)

        run_async(scenario())
        confirms = [e for e in captured if e.get("type") == "confirm"]
        self.assertEqual(
            len(confirms), 1,
            f"gate must dispatch exactly one confirm event, got {captured}")
        self.assertEqual(confirms[0]["tool"], "canvas.delete_node")
        return (outcome.get("error") or outcome.get("result")), confirms

    def test_delete_node_approval(self):
        result, _ = self._run_gated_delete(approve=True)
        self.assertTrue(result.get("success"), result)
        self.assertEqual(result.get("node_id"), "doomed-1")
        canvas = self.store.canvas(self.project_id)
        self.assertFalse(any(n.get("id") == "doomed-1" for n in canvas["nodes"]))
        # the owning tab saw the canvas_updated broadcast
        self.assertTrue(self.tabs[0].sent_of("canvas_updated"))

    def test_delete_node_rejection(self):
        outcome, _ = self._run_gated_delete(approve=False)
        self.assertIsInstance(outcome, api_mcp.McpToolError)
        self.assertIn("拒绝", str(outcome))
        canvas = self.store.canvas(self.project_id)
        self.assertTrue(any(n.get("id") == "doomed-1" for n in canvas["nodes"]),
                        "rejected deletion must not touch the canvas")

    def test_delete_node_timeout(self):
        previous = api_mcp.CONFIRM_TIMEOUT_SECONDS
        api_mcp.CONFIRM_TIMEOUT_SECONDS = 1.0
        try:
            t0 = time.time()
            outcome, _ = self._run_gated_delete(approve=None)
            elapsed = time.time() - t0
        finally:
            api_mcp.CONFIRM_TIMEOUT_SECONDS = previous
        self.assertIsInstance(outcome, api_mcp.McpToolError)
        self.assertIn("确认超时", str(outcome))
        self.assertLess(elapsed, 15)
        canvas = self.store.canvas(self.project_id)
        self.assertTrue(any(n.get("id") == "doomed-1" for n in canvas["nodes"]),
                        "timed-out confirmation must not delete")


class McpSameOriginTests(McpTestBase):
    """#240 guard applies to the MCP endpoint as well (ADR 0022 v1 boundary)."""

    def test_remote_origin_rejected(self):
        remote = TestClient(main.app, base_url="http://10.0.0.5:8124")
        try:
            resp = remote.post(
                "/api/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            )
            self.assertEqual(resp.status_code, 403, resp.text)
        finally:
            remote.close()

    def test_cross_origin_browser_request_rejected(self):
        resp = self.client.post(
            "/api/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            headers={"origin": "http://evil.example"},
        )
        self.assertEqual(resp.status_code, 403, resp.text)


if __name__ == "__main__":
    unittest.main()
