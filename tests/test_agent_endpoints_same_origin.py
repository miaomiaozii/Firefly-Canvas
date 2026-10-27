"""Tests for Issue #240: 同源/Host 守卫 on agent-tools / agent-nodes endpoints.

守卫（app.core.ensure_agent_same_origin）与 ensure_same_origin_request 同一模型：
Host 必须为本机回环；带 Origin/Referer 的请求（浏览器页面）必须与应用来源精确匹配。
唯一差异：无 Origin/Referer 的编程式回环调用（服务端自己 spawn 的 pi sidecar，
canvas-tools.ts：http://127.0.0.1 + X-Agent-Project 头）按票面"本机回环+Host 校验"
模型放行，不能把自家 sidecar 锁在门外。

Coverage:
- 跨源浏览器请求（Origin / Referer）被 403 拒绝
- 同源（回环 Host + 匹配 Origin）请求通过
- 回环 + 无 Origin 的 sidecar 请求通过
- 非回环 Host 且无 Origin 被 403 拒绝
- 跨源 WebSocket 连接 agent-nodes stream 被拒；同源连接不回归
"""
import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main
from firefly_projects import ProjectStore
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


GUARD_DETAIL = "只允许从当前应用页面操作本机数据"


class AgentEndpointSameOriginTests(unittest.TestCase):
    """Cross-origin rejection + same-origin / sidecar pass-through for #240."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="firefly-agent-origin-tests-")
        self.store = ProjectStore(Path(self.temp_dir.name) / "projects", snapshot_delay=0)
        self.previous_store = main.FIREFLY_PROJECT_STORE
        main.FIREFLY_PROJECT_STORE = self.store
        # 浏览器式客户端：回环 Host + 匹配 Origin
        self.browser = TestClient(
            main.app, base_url="http://127.0.0.1:8123", headers={"origin": "http://127.0.0.1:8123"}
        )
        # sidecar 式客户端：回环 Host，无 Origin（canvas-tools.ts 形态）
        self.sidecar = TestClient(main.app, base_url="http://127.0.0.1:8123")
        # 远端客户端：非回环 Host
        self.remote = TestClient(main.app, base_url="http://10.0.0.5:8123")
        self.project = self.store.create_project("Origin Guard Test")
        self.project_id = self.project["id"]

    def tearDown(self):
        run_async(agent_host_manager.cleanup_all())
        self.browser.close()
        self.sidecar.close()
        self.remote.close()
        self.store.close()
        main.FIREFLY_PROJECT_STORE = self.previous_store
        self.temp_dir.cleanup()

    def test_agent_tools_cross_origin_request_rejected(self):
        """跨源 Origin 的 agent-tools 请求必须被拒。"""
        response = self.browser.get(
            f"/api/projects/{self.project_id}/canvas/nodes",
            headers={"origin": "http://evil.example"},
        )
        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(response.json()["detail"], GUARD_DETAIL)

    def test_agent_nodes_cross_origin_referer_request_rejected(self):
        """仅带跨源 Referer（无 Origin）的 agent-nodes 请求也必须被拒。"""
        response = self.browser.post(
            f"/api/projects/{self.project_id}/agent-nodes",
            json={"node_id": "n1", "prompt": "hi"},
            headers={"origin": "http://127.0.0.1:8123", "referer": "http://evil.example/page"},
        )
        # Origin 优先于 Referer；Origin 合法时请求不应被 403
        self.assertNotEqual(response.status_code, 403, response.text)
        # 无合法 Origin、仅跨源 Referer 时必须被拒
        response = self.browser.post(
            f"/api/projects/{self.project_id}/agent-nodes",
            json={"node_id": "n1", "prompt": "hi"},
            headers={"origin": "", "referer": "http://evil.example/page"},
        )
        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(response.json()["detail"], GUARD_DETAIL)

    def test_same_origin_requests_pass(self):
        """同源（回环 Host + 匹配 Origin）请求放行。"""
        response = self.browser.get(f"/api/projects/{self.project_id}/canvas/nodes")
        self.assertEqual(response.status_code, 200, response.text)

        response = self.browser.get(f"/api/projects/{self.project_id}/agent-nodes/node-x/status")
        self.assertNotEqual(response.status_code, 403, response.text)

    def test_sidecar_loopback_without_origin_passes(self):
        """pi sidecar（回环 + X-Agent-Project，无 Origin）不得被锁在门外。"""
        response = self.sidecar.get(
            f"/api/projects/{self.project_id}/canvas/nodes",
            headers={"X-Agent-Project": self.project_id},
        )
        self.assertEqual(response.status_code, 200, response.text)

    def test_remote_host_without_origin_rejected(self):
        """非回环 Host 且无 Origin 的请求必须被拒。"""
        response = self.remote.get(f"/api/projects/{self.project_id}/canvas/nodes")
        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(response.json()["detail"], GUARD_DETAIL)

    def test_agent_nodes_cross_origin_websocket_rejected(self):
        """跨源 WebSocket 连接 agent-nodes stream 必须被拒。"""
        session = run_async(agent_host_manager.get_or_create_session(self.project_id, "ws-deny"))
        session.process = FakeAgentProcess(script=[])
        denied = False
        try:
            with self.browser.websocket_connect(
                f"ws://127.0.0.1:8123/api/projects/{self.project_id}/agent-nodes/ws-deny/stream",
                headers={"origin": "http://evil.example"},
            ):
                pass
        except Exception:
            denied = True
        self.assertTrue(denied, "跨源 WebSocket 应被守卫拒绝")

    def test_same_origin_websocket_allowed(self):
        """同源 WebSocket 连接不回归（sidecar/前端路径保持可用）。"""
        session = run_async(agent_host_manager.get_or_create_session(self.project_id, "ws-ok"))
        session.process = FakeAgentProcess(script=[])
        with self.browser.websocket_connect(
            f"ws://127.0.0.1:8123/api/projects/{self.project_id}/agent-nodes/ws-ok/stream"
        ) as ws:
            first = ws.receive_json()
            self.assertEqual(first.get("type"), "session", f"首帧应为 session 确认: {first}")
