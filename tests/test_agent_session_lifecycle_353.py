"""#353/#354 Agent 会话生命周期护栏（issue/353-session-lifecycle）。

#353：
  (a) 删节点 → 宿主进程必须终止、会话从注册表摘除、agent_nodes/<nid> 目录清理。
      两条删除路径都测：REST/MCP 共用的 delete_node 协程；UI 画布保存
      （mutate_firefly_canvas 批内 remove_node）。
  (b) 健康 running 会话空闲超 RUNNING_IDLE_REAP_SECONDS → watchdog 温和回收
      （stop_session：杀进程、不 respawn、注册表保留）。
  (c) 回收后再提问续上原会话：extSessionId 在回收后仍留在会话与节点上；
      _apply_node_host 恢复 + _pi_session_binding 绑定到既有 <ext>.jsonl →
      spawn argv 走 --session-id <ext>（pi_adapter 层已有回归，这里测链路缝合点）。
#354：会话创建（含服务器重启后重建）时 model/autonomy 从节点播种，spawn 用节点值。

假进程对象（FakeProc）不拉真子进程；pi 播种断言只到 argv 构造层。
"""

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from firefly_projects import ProjectStore

import main
import app.core as core
from app.routers.api_agent_nodes import (
    AGENT_AUTONOMY_WHITELIST,
    _apply_node_host,
    send_agent_prompt,
)
from app.routers.api_agent_tools import delete_node
from app.services import agent_host
from app.services.agent_adapters.pi_adapter import PiAdapter, SpawnParams

_PREVIOUS_PI_CLI = None


class FakeProc:
    """最小 Popen 替身：poll()=None（活着），terminate/kill 记录调用。"""

    def __init__(self):
        self.terminated = False
        self.killed = False

    def poll(self):
        return None if not (self.terminated or self.killed) else 0

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        return 0


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
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop


class DropNodeSessionTests(LifecycleBase):
    """#353 (a)：删节点杀进程 + 摘除 + 目录清理（REST/MCP 与 UI 两条路径）。"""

    def test_delete_node_drops_process_and_dir(self):
        self._filler_node()
        self._agent_node("agent-d1")
        session = self._live_session("agent-d1", ext="pi-ext-d1")
        proc = session.process  # stop_session 收尾会置 session.process=None，先拿引用
        sdir = self._sessions_dir("agent-d1")
        sdir.mkdir(parents=True)
        (sdir / "pi-ext-d1.jsonl").write_text('{"hello":1}', encoding="utf-8")

        result = self._run(delete_node(self.project_id, "agent-d1"))
        self.assertTrue(result["success"])
        # 进程被终止
        self.assertTrue(proc.terminated or proc.killed,
                        "宿主进程必须收到终止信号")
        # 注册表摘除
        self.assertNotIn(f"{self.project_id}:agent-d1", self.manager.sessions)
        # agent_nodes/<nid> 目录删除
        self.assertFalse((Path(self.store.root) / self.project_id / "agent_nodes" / "agent-d1").exists())
        # canvas 里节点确实没了
        canvas = self.store.open_project(self.project_id)["canvas"]
        self.assertNotIn("agent-d1", [n.get("id") for n in canvas.get("nodes", [])])

    def test_ui_canvas_remove_node_drops_process(self):
        """UI 删除路径：PATCH canvas 批内 remove_node → 同样杀进程摘会话。"""
        self._filler_node()
        self._agent_node("agent-d2")
        session = self._live_session("agent-d2")
        proc = session.process
        rev = int(self.store.canvas(self.project_id)["revision"])
        batch = core.CanvasMutationBatch(mutations=[{"op": "remove_node", "node_id": "agent-d2"}],
                                         base_revision=rev)
        self._run(_mutate_canvas(self.project_id, batch))
        self.assertTrue(proc.terminated or proc.killed)
        self.assertNotIn(f"{self.project_id}:agent-d2", self.manager.sessions)

    def test_delete_non_agent_node_is_noop(self):
        self._filler_node()
        self._agent_node("img-2", type="image", kind="image")
        # 无 agent 会话时删除不炸、不误摘他人
        self._live_session("agent-keep")
        result = self._run(delete_node(self.project_id, "img-2"))
        self.assertTrue(result["success"])
        self.assertIn(f"{self.project_id}:agent-keep", self.manager.sessions)

    def test_drop_is_idempotent(self):
        self._filler_node()
        self._agent_node("agent-d3")
        self._live_session("agent-d3")
        self.assertTrue(self._run(self.manager.drop_node_session(self.project_id, "agent-d3")))
        self.assertFalse(self._run(self.manager.drop_node_session(self.project_id, "agent-d3")))


class IdleReclaimTests(LifecycleBase):
    """#353 (b)(c)：running 空闲回收；回收后续上原会话的链路缝合点。"""

    def setUp(self):
        super().setUp()
        self._prev_reap = agent_host.RUNNING_IDLE_REAP_SECONDS
        self.addCleanup(self._restore_reap)

    def _restore_reap(self):
        agent_host.RUNNING_IDLE_REAP_SECONDS = self._prev_reap

    def test_idle_running_session_is_reclaimed(self):
        self._agent_node("agent-i1", extSessionId="pi-ext-i1")
        session = self._live_session("agent-i1", ext="pi-ext-i1",
                                     last_activity=1.0)
        proc = session.process
        agent_host.RUNNING_IDLE_REAP_SECONDS = 5
        self._run(self.manager._idle_watchdog_pass(now=100.0))
        self.assertEqual(session.status, "stopped")
        self.assertTrue(proc.terminated)
        # 温和回收：注册表保留、extSessionId 保留、会话目录保留（可续上）
        self.assertIn(f"{self.project_id}:agent-i1", self.manager.sessions)
        self.assertEqual(session.ext_session_id, "pi-ext-i1")

    def test_recent_running_session_not_reclaimed(self):
        self._agent_node("agent-i2")
        session = self._live_session("agent-i2", last_activity=95.0)
        proc = session.process
        agent_host.RUNNING_IDLE_REAP_SECONDS = 5
        self._run(self.manager._idle_watchdog_pass(now=100.0))
        self.assertEqual(session.status, "running")
        self.assertFalse(proc.terminated)

    def test_idle_codex_without_process_untouched(self):
        self._agent_node("agent-i3", agentCli="codex")
        session = self._live_session("agent-i3", last_activity=1.0)
        session.agent_cli = "codex"
        session.process = None  # codex per-turn：空闲无进程
        agent_host.RUNNING_IDLE_REAP_SECONDS = 5
        self._run(self.manager._idle_watchdog_pass(now=100.0))
        self.assertEqual(session.status, "running")

    def test_resume_after_reclaim_binds_ext_session_id(self):
        """回收 → 再提问：_apply_node_host 从节点恢复 extSessionId，
        pi 绑定解析到既有 <ext>.jsonl，spawn argv 用 --session-id <ext>。"""
        self._agent_node("agent-r1", extSessionId="pi-ext-r1")
        session = self._live_session("agent-r1", ext="pi-ext-r1", last_activity=1.0)
        agent_host.RUNNING_IDLE_REAP_SECONDS = 5
        self._run(self.manager._idle_watchdog_pass(now=100.0))
        self.assertEqual(session.status, "stopped")

        # 再提问前：_apply_node_host（/prompt 的第一步）确认 ext 仍在
        _apply_node_host(self.project_id, "agent-r1", session)
        self.assertEqual(session.ext_session_id, "pi-ext-r1")

        # pi 真实会话文件名形如 <name>_<uuid>.jsonl（binding 正则：`_` + 36 位 uuid，
        # 见 agent_host.py 绑定实现），同产品真实形状。
        ext_id = "6f1a2b3c-4d5e-6f70-8192-a3b4c5d6e7f8"
        session.ext_session_id = ext_id
        sdir = self._sessions_dir("agent-r1")
        sdir.mkdir(parents=True)
        (sdir / f"firefly-agent-agent-r1_{ext_id}.jsonl").write_text('{"turn":1}', encoding="utf-8")
        bound = agent_host.AgentHostManager._pi_session_binding(str(sdir),
                                                                session.ext_session_id or session.session_id)
        self.assertEqual(bound, ext_id)

        # argv 缝合点：PiAdapter 收到 pi_session_id=bound → --session-id <ext>
        argv = PiAdapter().build_argv(SpawnParams(
            resolved_cli="pi", session_dir=str(sdir), system_prompt="p",
            tools_path="t.js", node_id="agent-r1", pi_session_id=bound,
        ))
        self.assertIn("--session-id", argv)
        self.assertEqual(argv[argv.index("--session-id") + 1], ext_id)
        self.assertNotIn("--continue", argv)


async def _mutate_canvas(project_id, batch):
    from app.routers.api_project_library import mutate_firefly_canvas
    return await mutate_firefly_canvas(project_id, batch)


if __name__ == "__main__":
    unittest.main()
