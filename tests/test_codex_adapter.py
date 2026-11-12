"""#318 票 B：codex 宿主 adapter + per-turn 进程模型（ADR 0024）。

覆盖：
- argv：首轮无 resume；-s read-only 强制；-c mcp_servers/developer_instructions 注入
  （内层双引号 TOML 值）；prompt 走 stdin（argv 尾 "-"）；resume options 在子命令前。
- 事件映射（0.162 实测词汇）：thread.started/turn.started/item{agent_message,
  reasoning,command_execution,error}/turn.completed/token_count/未知 type→raw。
- host per-turn：假 CLI 真实进程 → session_started/message/usage 事件流；
  ext_session_id 捕获；resume 失败降级（error 事件 + 新会话重发一次）；
  busy 拒收并发 prompt。
假 CLI：tests/stubs/fake_codex(.cmd)（#258 已有）+ fake_codex_resume_fail(.cmd)（本票）。
"""
import asyncio
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main  # noqa: F401  # _app_main 解析顺序要求先导入
from app.services import agent_host as ah
from app.services.agent_host import AgentHostManager
from app.services.agent_adapters import CodexAdapter, SpawnParams, map_codex_event
from app.services.agent_adapters.codex_adapter import _toml_value

FAKE_CODEX = str(Path(__file__).resolve().parent / "stubs" / "fake_codex.cmd")
FAKE_CODEX_RESUME_FAIL = str(Path(__file__).resolve().parent / "stubs" / "fake_codex_resume_fail.cmd")


def _spawn_params(**over):
    base = dict(
        resolved_cli="codex",
        session_dir=".",
        system_prompt="SYS PROMPT TEXT",
        tools_path="tools.py",
        node_id="node-1",
        model="",
        continue_session=False,
        agent_cli="codex",
        ext_session_id=None,
        mcp_url="http://127.0.0.1:9999/mcp?project=p&node=n",
    )
    base.update(over)
    return SpawnParams(**base)


class AdapterArgvTests(unittest.TestCase):
    def setUp(self):
        self._which = shutil.which
        shutil.which = lambda name, path=None: FAKE_CODEX if name == "codex" else self._which(name, path)

    def tearDown(self):
        shutil.which = self._which

    def test_first_turn_argv(self):
        argv = CodexAdapter().build_argv(_spawn_params())
        assert argv is not None
        assert argv[0] == FAKE_CODEX
        assert "exec" in argv
        # 沙盒硬约束：用户全局默认 danger-full-access，适配器必须显式 read-only
        i = argv.index("-s")
        assert argv[i + 1] == "read-only"
        # prompt 走 stdin（实测坑：参数位不可靠）
        assert argv[-1] == "-"
        assert "--json" in argv
        assert "resume" not in argv
        injects = [a for a in argv if a.startswith("mcp_servers.firefly.url=") or "mcp_servers.firefly.url=" in a]
        assert injects, argv
        assert injects[0] == 'mcp_servers.firefly.url="http://127.0.0.1:9999/mcp?project=p&node=n"'
        # 实测坑：MCP 工具默认审批 + exec never = 自动拒；必须 approve（真闸门在 Firefly）
        amode = [a for a in argv if "default_tools_approval_mode=" in a]
        assert amode and amode[0] == 'mcp_servers.firefly.default_tools_approval_mode="approve"', argv
        dev = [a for a in argv if "developer_instructions=" in a]
        assert dev and dev[0].endswith('"SYS PROMPT TEXT"')

    def test_resume_argv_options_before_subcommand(self):
        argv = CodexAdapter().build_resume_argv(_spawn_params(), "tid-42")
        r = argv.index("resume")
        assert argv[r + 1] == "tid-42"
        # options 必须在 resume 之前（实测 argparse 坑）
        assert "-s" in argv[:r] and "--json" in argv[r:]
        assert argv[-1] == "-"

    def test_toml_value_escapes(self):
        assert _toml_value('a"b') == '"a\\"b"'
        assert _toml_value("l1\nl2") == '"l1\\nl2"'


class EventMappingTests(unittest.TestCase):
    def test_thread_started(self):
        evs = map_codex_event({"type": "thread.started", "thread_id": "t1"})
        assert evs == [{"type": "session_started", "session_id": "t1"}]

    def test_turn_started(self):
        evs = map_codex_event({"type": "turn.started"})
        types = [e["type"] for e in evs]
        assert types == ["agent_start", "turn_start"]

    def test_agent_message_completed(self):
        evs = map_codex_event({"type": "item.completed",
                               "item": {"id": "1", "type": "agent_message", "text": "收到"}})
        assert evs[0]["type"] == "message_update"
        assert evs[0]["assistantMessageEvent"]["type"] == "text_delta"
        assert evs[0]["assistantMessageEvent"]["delta"] == "收到"

    def test_item_updated_message_ignored(self):
        assert map_codex_event({"type": "item.updated",
                                "item": {"id": "1", "type": "agent_message", "text": "…"}}) == []

    def test_reasoning_maps_to_thinking(self):
        evs = map_codex_event({"type": "item.completed",
                               "item": {"id": "1", "type": "reasoning", "text": "hm"}})
        assert evs[0]["assistantMessageEvent"]["type"] == "thinking_delta"

    def test_command_execution_lifecycle(self):
        s = map_codex_event({"type": "item.started",
                             "item": {"id": "c1", "type": "command_execution", "command": "ls"}})
        e = map_codex_event({"type": "item.completed",
                             "item": {"id": "c1", "type": "command_execution",
                                      "command": "ls", "aggregated_output": "ok", "exit_code": 0}})
        assert s[0]["type"] == "tool_execution_start" and s[0]["toolCallId"] == "c1"
        assert e[0]["type"] == "tool_execution_end" and e[0]["result"] == "ok"
        assert e[0]["isError"] is False

    def test_mcp_tool_call_result_extracted(self):
        # 实测坑：mcp_tool_call 结果在 item.result.content[]，非 aggregated_output
        e = map_codex_event({"type": "item.completed",
                             "item": {"id": "m1", "type": "mcp_tool_call", "server": "firefly",
                                      "tool": "canvas.list_nodes",
                                      "result": {"content": [{"type": "text", "text": "NODES"}],
                                                 "isError": False},
                                      "status": "completed"}})
        assert e[0]["result"] == "NODES" and e[0]["isError"] is False
        f = map_codex_event({"type": "item.completed",
                             "item": {"id": "m2", "type": "mcp_tool_call",
                                      "error": {"message": "requires approval"}, "status": "failed"}})
        assert f[0]["isError"] is True
        assert any(x["type"] == "error" and "approval" in x["message"] for x in f)

    def test_item_error_is_error_event(self):
        evs = map_codex_event({"type": "item.completed",
                               "item": {"id": "x", "type": "error", "message": "boom"}})
        assert evs[0] == {"type": "error", "message": "boom"}

    def test_turn_completed_triple(self):
        evs = map_codex_event({"type": "turn.completed",
                               "usage": {"input_tokens": 5, "output_tokens": 1}})
        types = [e["type"] for e in evs]
        assert types == ["turn_end", "usage", "agent_end"]
        assert evs[1]["usage"] == {"input_tokens": 5, "output_tokens": 1}
        assert evs[1]["cumulative"] is False

    def test_token_count_cumulative(self):
        evs = map_codex_event({"type": "token_count", "usage": {"input_tokens": 9}})
        assert evs[0]["type"] == "usage" and evs[0]["cumulative"] is True

    def test_unknown_item_passthrough_raw(self):
        evs = map_codex_event({"type": "item.completed", "item": {"id": "z", "type": "weird_thing"}})
        assert evs[0]["type"] == "raw_item"

    def test_unknown_top_level_ignored(self):
        assert map_codex_event({"type": "something_new"}) == []


class _FakeWhichMixin:
    """shutil.which 全局替换：codex → 指定假 CLI（agent_host 与 adapter 同时生效）。"""

    def install_which(self, fake_path):
        self._real_which = shutil.which
        target = fake_path

        def _which(name, path=None):
            if name == "codex":
                return target
            return self._real_which(name, path)

        shutil.which = _which

    def uninstall_which(self):
        shutil.which = self._real_which


class HostPerTurnTests(_FakeWhichMixin, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mgr = AgentHostManager()
        self.mgr._get_project_dir = lambda pid: self.tmp.name
        self.events = []

        async def collect(session, event):
            self.events.append(event)

        self.session = await self.mgr.get_or_create_session("proj-test", "node-x")
        self.session.agent_cli = "codex"
        self.mgr.on_event(self.session, collect)

    def tearDown(self):
        self.uninstall_which()
        self.tmp.cleanup()

    async def _wait_turn_done(self, timeout=20.0):
        # 轮末特征：agent_end 或 error（降级路径先 error 后再起一轮）
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if self.session.process is None and any(
                e["type"] in ("agent_end",) for e in self.events
            ):
                return True
            await asyncio.sleep(0.05)
        return False

    async def test_full_turn_event_flow(self):
        self.install_which(FAKE_CODEX)
        assert await self.mgr.spawn_session(self.session) is True
        assert self.session.status == "running"
        assert self.session.process is None  # 无常驻进程
        await self.mgr.send_prompt(self.session, "你好")
        assert await self._wait_turn_done(), [e["type"] for e in self.events]
        types = [e["type"] for e in self.events]
        for expected in ("session_started", "message_update", "agent_end", "usage"):
            assert expected in types, types
        texts = [e for e in self.events if e["type"] == "message_update"]
        assert texts[0]["assistantMessageEvent"]["delta"] == "FAKE LAST"
        assert self.session.ext_session_id == "fake-1"

    async def test_resume_failure_degrades_once_with_visible_error(self):
        self.install_which(FAKE_CODEX_RESUME_FAIL)
        # 第一轮（无 resume）成功 → ext_session_id=fake-first
        assert await self.mgr.spawn_session(self.session)
        await self.mgr.send_prompt(self.session, "第一")
        assert await self._wait_turn_done()
        assert self.session.ext_session_id == "fake-first"
        n_msg = sum(1 for e in self.events if e["type"] == "message_update")
        # 第二轮 resume 必失败 → error 事件可见 + 自动新会话重发（第二次 message）
        self.events.clear()
        await self.mgr.send_prompt(self.session, "第二")
        loop = asyncio.get_event_loop()
        deadline = loop.time() + 25
        while loop.time() < deadline:
            if any(e["type"] == "error" for e in self.events) and \
               sum(1 for e in self.events if e["type"] == "message_update") >= 1 and \
               self.session.process is None:
                break
            await asyncio.sleep(0.05)
        types = [e["type"] for e in self.events]
        assert "error" in types, types
        err = next(e for e in self.events if e["type"] == "error")
        assert "降级" in err["message"]
        assert any(e["type"] == "message_update" for e in self.events), types
        assert self.session.ext_session_id == "fake-first"  # 降级后新 id 重新捕获

    async def test_busy_rejects_concurrent_prompt(self):
        self.install_which(FAKE_CODEX_RESUME_FAIL)
        assert await self.mgr.spawn_session(self.session)
        await self.mgr.send_prompt(self.session, "PROMPT_SLEEP")
        assert self.session.process is not None  # 进程在跑
        with self.assertRaises(RuntimeError):
            await self.mgr.send_prompt(self.session, "并发第二")
        # 收尾：abort（terminate 单轮）→ 等 reader 清场
        await self.mgr.send_abort(self.session)
        loop = asyncio.get_event_loop()
        deadline = loop.time() + 15
        while loop.time() < deadline and self.session.process is not None:
            await asyncio.sleep(0.05)
        assert self.session.process is None


if __name__ == "__main__":
    unittest.main()
