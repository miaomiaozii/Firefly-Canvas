"""#391（A1）统一工具错误形状：REST/MCP 全部错误出口为
{code,message,hint,retryable,details?}；HTTP 状态码一律不变；文案无内部票号。

review 条件（2026-11-16 23:59）：
1. 不做中文关键词猜码（translate_detail 只按状态码映射）——本文件钉住映射表；
2. 状态码不变——每类错误断言与改前一致（下方 STATUS 对照表按改前 grep 原样登记）；
3. 无 [object Object]——callApi 行为在 mjs 侧钉（test-agent-canvas-tools.mjs）。
"""
import json
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main
from firefly_projects import ProjectStore
from app.services.agent_errors import (
    AGENT_ERROR_CODES,
    agent_error,
    format_agent_error_text,
    is_agent_error_shape,
    translate_detail,
)
from app.routers import api_agent_tools as _agt


SHAPE_KEYS = {"code", "message", "hint", "retryable"}


def assert_shape(test, detail):
    test.assertTrue(is_agent_error_shape(detail), f"not shaped: {detail!r}")
    test.assertIn(detail["code"], AGENT_ERROR_CODES)
    test.assertNotIn("#", str(detail["message"]))
    test.assertNotIn("#", str(detail["hint"]))


class _Core(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="firefly-391-")
        self.store = ProjectStore(Path(self.temp.name) / "projects", snapshot_delay=0)
        self._prev = main.FIREFLY_PROJECT_STORE
        main.FIREFLY_PROJECT_STORE = self.store
        self.pid = self.store.create_project("391")["id"]
        self.client = TestClient(main.app, base_url="http://127.0.0.1:8125",
                                 headers={"origin": "http://127.0.0.1:8125"})

    def tearDown(self):
        self.client.close()
        main.FIREFLY_PROJECT_STORE = self._prev
        self.temp.cleanup()

    def _exc(self, coro):
        import asyncio
        try:
            asyncio.run(coro)
        except Exception as exc:
            return exc
        self.fail("expected raise")

    # ------------------------------------------------ core 层：形状 + 状态码不变
    def test_trigger_missing_node_id_400(self):
        exc = self._exc(_agt.trigger_generation_core(self.pid, ""))
        self.assertEqual(exc.status_code, 400)
        assert_shape(self, exc.detail)
        self.assertEqual(exc.detail["code"], "INVALID_ARGUMENT")

    def test_node_not_found_404(self):
        exc = self._exc(_agt.trigger_generation_core(self.pid, "nope-1"))
        self.assertEqual(exc.status_code, 404)
        assert_shape(self, exc.detail)
        self.assertEqual(exc.detail["code"], "NODE_NOT_FOUND")

    def test_no_prompt_400(self):
        self.store.apply_mutations(self.pid, [
            {"op": "upsert_node", "node": {"id": "img-1", "type": "smart-image",
                                           "title": "t", "x": 0, "y": 0}},
        ], base_revision=0)
        exc = self._exc(_agt.trigger_generation_core(self.pid, "img-1"))
        self.assertEqual(exc.status_code, 400)
        self.assertEqual(exc.detail["code"], "NODE_NOT_GENERATABLE")
        assert_shape(self, exc.detail)

    def test_whitelist_400_with_rejected_keys(self):
        exc = self._exc(_agt.update_node_core(self.pid, "img-x", {"images": []}))
        self.assertEqual(exc.status_code, 400)
        self.assertEqual(exc.detail["code"], "INVALID_ARGUMENT")
        self.assertEqual(exc.detail["details"]["rejected_keys"], ["images"])
        assert_shape(self, exc.detail)


    def test_quota_429_shape(self):
        nid = f"agent-{uuid.uuid4().hex[:8]}"
        _agt.enforce_generation_limit(self.pid, nid, count=_agt.GENERATION_SESSION_LIMIT)
        # enforce_generation_limit 是同步函数：直接调用捕获
        caught = None
        try:
            _agt.enforce_generation_limit(self.pid, nid, count=1)
            self.fail("expected 429")
        except Exception as e:
            caught = e
        exc = caught
        self.assertEqual(exc.status_code, 429)
        self.assertEqual(exc.detail["code"], "BUDGET_EXCEEDED")
        self.assertTrue(exc.detail["retryable"])
        assert_shape(self, exc.detail)  # 文案里的 #351 已被剥除

    def test_confirm_missing_node_id_400(self):
        exc = self._exc(_agt.request_confirmation_core(self.pid, "", "t", {}, "m"))
        self.assertEqual(exc.status_code, 400)
        self.assertEqual(exc.detail["code"], "CONFIRM_MISSING_NODE_ID")

    def test_project_not_found_404(self):
        exc = self._exc(_agt.trigger_generation_core("no-such-project", "x"))
        self.assertEqual(exc.status_code, 404)
        self.assertEqual(exc.detail["code"], "PROJECT_NOT_FOUND")


class _Rest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="firefly-391r-")
        self.store = ProjectStore(Path(self.temp.name) / "projects", snapshot_delay=0)
        self._prev = main.FIREFLY_PROJECT_STORE
        main.FIREFLY_PROJECT_STORE = self.store
        self.pid = self.store.create_project("391r")["id"]
        self.agent_headers = {"origin": "http://127.0.0.1:8125",
                              "X-Agent-Project": self.pid}
        self.client = TestClient(main.app, base_url="http://127.0.0.1:8125")

    def tearDown(self):
        self.client.close()
        main.FIREFLY_PROJECT_STORE = self._prev
        self.temp.cleanup()

    def test_view_bad_scope_400_shaped(self):
        r = self.client.post(f"/api/projects/{self.pid}/canvas/view",
                             json={"scope": "galaxy"}, headers=self.agent_headers)
        self.assertEqual(r.status_code, 400)  # 改前即 400
        assert_shape(self, r.json()["detail"])
        self.assertEqual(r.json()["detail"]["code"], "INVALID_ARGUMENT")
        self.assertIn("canvas.list_nodes", r.json()["detail"]["hint"])

    def test_get_node_missing_404_shaped(self):
        r = self.client.get(f"/api/projects/{self.pid}/canvas/nodes/nope",
                            headers=self.agent_headers)
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json()["detail"]["code"], "NODE_NOT_FOUND")

    def test_task_missing_404_shaped(self):
        r = self.client.get(f"/api/projects/{self.pid}/generate/tasks/ghost",
                            headers=self.agent_headers)
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json()["detail"]["code"], "TASK_NOT_FOUND")

    def test_unshaped_plain_string_error_translated_by_middleware(self):
        # 兜底：任何未带形状的 agent 流量错误（含未来新路由/守卫异常）都被翻译，
        # 状态码不变，票号剥除。这里注入一个只带字符串 detail 的 500。
        prev = _agt.canvas_view_core
        def boom(*a, **k):
            from fastapi import HTTPException
            raise HTTPException(status_code=500, detail="#999：某个没形状的老错误")
        _agt.canvas_view_core = boom
        try:
            r = self.client.post(f"/api/projects/{self.pid}/canvas/view",
                                 json={"scope": "page"}, headers=self.agent_headers)
        finally:
            _agt.canvas_view_core = prev
        self.assertEqual(r.status_code, 500)
        detail = r.json()["detail"]
        assert_shape(self, detail)
        self.assertEqual(detail["code"], "INTERNAL_ERROR")

    def test_non_agent_traffic_untouched(self):
        # 不带 X-Agent-Project：同一错误也返回形状？——不应被中间件改写为强制形状；
        # core 已是形状 dict，detail 本就带形状；此处钉的是「中间件不碰非 agent 流量的状态码」
        r = self.client.get(f"/api/projects/{self.pid}/canvas/nodes/nope",
                            headers={"origin": "http://127.0.0.1:8125"})
        self.assertEqual(r.status_code, 404)


class _FakeTab391:
    def __init__(self):
        self.client_id = f"client-{uuid.uuid4().hex[:8]}"

    async def send_text(self, text):
        pass


class _Mcp(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="firefly-391m-")
        self.store = ProjectStore(Path(self.temp.name) / "projects", snapshot_delay=0)
        self._prev = main.FIREFLY_PROJECT_STORE
        main.FIREFLY_PROJECT_STORE = self.store
        self.pid = self.store.create_project("391m")["id"]
        self.client = TestClient(main.app, base_url="http://127.0.0.1:8125",
                                 headers={"origin": "http://127.0.0.1:8125"})
        # 打开一个假画布 tab：MCP 工具先过会话守卫才会走到 NODE_NOT_FOUND
        from app.project_sessions import register_project_session
        self.tab = _FakeTab391()
        register_project_session(self.tab, self.pid)
        main.manager.active_connections.append(self.tab)
        main.manager.connection_clients[self.tab] = self.tab.client_id
        main.manager.user_connections[self.tab.client_id] = self.tab

    def tearDown(self):
        from app.project_sessions import unregister_project_session
        unregister_project_session(self.tab)
        if self.tab in main.manager.active_connections:
            main.manager.active_connections.remove(self.tab)
        main.manager.connection_clients.pop(self.tab, None)
        main.manager.user_connections.pop(self.tab.client_id, None)
        self.client.close()
        main.FIREFLY_PROJECT_STORE = self._prev
        self.temp.cleanup()

    def _call(self, name, args, node=""):
        url = f"/api/mcp?project={self.pid}" + (f"&node={node}" if node else "")
        r = self.client.post(url, json={"jsonrpc": "2.0", "id": 1,
                                        "method": "tools/call",
                                        "params": {"name": name, "arguments": args}})
        return r.json()["result"]

    def test_unknown_tool_isError_structured(self):
        result = self._call("no.such_tool", {})
        self.assertTrue(result["isError"])
        assert_shape(self, result["structuredContent"])

    def test_node_not_found_isError_structured_same_as_rest(self):
        # 只读工具不带 node：走 NODE_NOT_FOUND（get_node）
        result = self._call("canvas.get_node", {"node_id": "ghost"})
        self.assertTrue(result["isError"])
        sc = result["structuredContent"]
        assert_shape(self, sc)
        self.assertEqual(sc["code"], "NODE_NOT_FOUND")
        # text 单行含 code 与 hint（旧客户端只读 text 也不丢信息）
        text = result["content"][0]["text"]
        self.assertIn("NODE_NOT_FOUND", text)
        self.assertIn("canvas.list_nodes", text)
        self.assertNotIn("[object Object]", text)

    def test_modelrouting_passthrough(self):
        d = translate_detail(400, {"code": "no_compatible_image_model",
                                   "message": "没有可用模型", "incompatible_parameters": []})
        self.assertEqual(d["code"], "INVALID_ARGUMENT")
        self.assertEqual(d["details"]["routing"]["code"], "no_compatible_image_model")


class _Translator(unittest.TestCase):
    def test_status_only_mapping(self):
        # 猜码禁止：文案带「节点」也只得 NOT_FOUND（状态码 404）
        d = translate_detail(404, "某条中文错误提到了节点")
        self.assertEqual(d["code"], "NOT_FOUND")
        self.assertEqual(d["message"], "某条中文错误提到了节点")
        for status, code in [(400, "INVALID_ARGUMENT"), (409, "CONFLICT"),
                             (429, "BUDGET_EXCEEDED"), (500, "INTERNAL_ERROR"),
                             (502, "UPSTREAM_MODEL_ERROR"), (503, "SERVER_NOT_READY")]:
            self.assertEqual(translate_detail(status, "x")["code"], code)

    def test_shaped_passthrough_strips_tickets(self):
        exc = agent_error(409, "CONFIRM_REJECTED", "#123：用户拒绝了：tool", "问用户")
        d = translate_detail(409, exc.detail)
        self.assertNotIn("#123", d["message"])

    def test_format_text_line(self):
        d = translate_detail(404, "gone")
        text = format_agent_error_text(d)
        self.assertIn("NOT_FOUND", text)
        self.assertIn("下一步", text)


if __name__ == "__main__":
    unittest.main()
