"""#354：model/autonomy 播种回归（会话创建/重启后以节点为权威源）。

与 test_agent_session_lifecycle_353.py 共用夹具风格；对应
api_agent_nodes._apply_node_host 的 #354 播种段与 send_agent_prompt 接线。
"""

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from firefly_projects import ProjectStore

import main
import app.core as core
from app.routers.api_agent_nodes import AGENT_AUTONOMY_WHITELIST, _apply_node_host, send_agent_prompt
from app.services import agent_host
class LifecycleBase(unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.temp_dir = tempfile.TemporaryDirectory(prefix="firefly-lifecycle-353-")
        self.addCleanup(self.temp_dir.cleanup)
        self._previous_store = getattr(main, "FIREFLY_PROJECT_STORE", None)
        self.addCleanup(self._restore_store)
        self.store = ProjectStore(Path(self.temp_dir.name) / "projects", snapshot_delay=0)
        main.FIREFLY_PROJECT_STORE = self.store
        self.project = self.store.create_project("Lifecycle 353")
        self.project_id = self.project["id"]
        self.manager = agent_host_manager_singleton()
        self._sessions_backup = dict(self.manager.sessions)
        self.addCleanup(self._restore_sessions)
        self.loop = _loop()
        self.addCleanup(self.loop.close)

    def _restore_store(self):
        main.FIREFLY_PROJECT_STORE = self._previous_store

    def _restore_sessions(self):
        self.manager.sessions.clear()
        self.manager.sessions.update(self._sessions_backup)

    def _run(self, coro):
        return self.loop.run_until_complete(coro)

    def _agent_node(self, node_id, **fields):
        node = {"id": node_id, "type": "agent", "kind": "agent", "title": f"Agent {node_id}",
                "x": 0, "y": 0}
        node.update(fields)
        rev = int(self.store.canvas(self.project_id)["revision"])
        self.store.apply_mutations(self.project_id, [{"op": "upsert_node", "node": node}],
                                   base_revision=rev)
        return node

    def _filler_node(self, node_id="filler-img"):
        """H39 空画布保护：删到零节点会被 RevisionConflict 拒——测试画布留一个不死节点。"""
        return self._agent_node(node_id, type="image", kind="image")

    def _live_session(self, node_id, ext=None, last_activity=0.0):
        key = f"{self.project_id}:{node_id}"
        session = self.manager.sessions[key] = _new_session(self.project_id, node_id)
        session.status = "running"
        session.process = FakeProc()
        session._last_activity = last_activity
        if ext:
            session.ext_session_id = ext
        return session

    def _sessions_dir(self, node_id):
        return Path(self.store.root) / self.project_id / "agent_nodes" / node_id / "sessions"


def agent_host_manager_singleton():
    from app.services.agent_host import agent_host_manager
    return agent_host_manager


def _new_session(project_id, node_id):
    return agent_host.AgentSession(project_id=project_id, node_id=node_id)


def _loop():
    # 绝不调 asyncio.set_event_loop：会在主线程永久标 _set_called=True，
    # 后续测试（test_agent_host_kill/test_quality_gate 等 380+ 条）的
    # get_event_loop() 就从「自动新建」变成 RuntimeError: no current event loop。
    return asyncio.new_event_loop()



def agent_host_manager_singleton():
    from app.services.agent_host import agent_host_manager
    return agent_host_manager


def _loop():
    # 绝不调 asyncio.set_event_loop：会在主线程永久标 _set_called=True，
    # 后续测试（test_agent_host_kill/test_quality_gate 等 380+ 条）的
    # get_event_loop() 就从「自动新建」变成 RuntimeError: no current event loop。
    return asyncio.new_event_loop()
class NodeModelAutonomySeedingTests(LifecycleBase):
    """#354：重启后（新会话对象）model/autonomy 从节点播种，spawn 用节点值。"""

    def test_seed_from_node_on_fresh_session(self):
        self._agent_node("agent-m1", model="provider/deepseek-v3", autonomy="autonomous")
        session = self._run(self.manager.get_or_create_session(self.project_id, "agent-m1"))
        self.assertEqual(session.model, "")
        self.assertEqual(session.autonomy, "cautious")
        _apply_node_host(self.project_id, "agent-m1", session)
        self.assertEqual(session.model, "provider/deepseek-v3")
        self.assertEqual(session.autonomy, "autonomous")

    def test_invalid_node_autonomy_not_seeded(self):
        self._agent_node("agent-m2", autonomy="yolo")
        session = self._run(self.manager.get_or_create_session(self.project_id, "agent-m2"))
        _apply_node_host(self.project_id, "agent-m2", session)
        self.assertEqual(session.autonomy, "cautious")
        self.assertIn("yolo", "cautious standard autonomous yolo")  # 白名单外不播种
        for ok in AGENT_AUTONOMY_WHITELIST:
            self.assertNotEqual(session.autonomy, "yolo")

    def test_prompt_after_restart_spawns_with_node_model(self):
        """模拟服务器重启：注册表清空 → /prompt 首建会话即播种节点 model，
        spawn_session 收到播种后的会话（不传请求级 model）。"""
        self._agent_node("agent-m3", model="provider/glm-4", autonomy="standard")
        self.manager.sessions.pop(f"{self.project_id}:agent-m3", None)

        captured = {}

        async def fake_spawn(sess, model="", autonomy=""):
            captured["model"] = sess.model
            captured["autonomy"] = sess.autonomy
            captured["arg_model"] = model
            return True

        async def fake_send(sess, message, images=None):
            captured["sent"] = message

        with patch.object(self.manager, "spawn_session", fake_spawn), \
                patch.object(self.manager, "send_prompt", fake_send):
            out = self._run(send_agent_prompt(self.project_id, "agent-m3",
                                              message="重启后第一问", images=[]))
        self.assertTrue(out["success"])
        self.assertEqual(captured["model"], "provider/glm-4")
        self.assertEqual(captured["autonomy"], "standard")
        self.assertEqual(captured.get("arg_model", ""), "")
        self.assertEqual(captured["sent"], "重启后第一问")


