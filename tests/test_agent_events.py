"""#318 票 C：events.jsonl 回放日志（seq/截断/GET since/降级重放上下文）。"""
import asyncio
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main  # noqa: F401  # _app_main 解析顺序
from app.services import agent_host as ah
from app.services.agent_host import AgentHostManager

FAKE_CODEX = str(ROOT / "tests" / "stubs" / "fake_codex.cmd")
FAKE_CODEX_RESUME_FAIL = str(ROOT / "tests" / "stubs" / "fake_codex_resume_fail.cmd")


class _Base(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._real_which = shutil.which
        # 假 CLI 是 python 子进程：stdin 解码跟随 locale（GBK 环境会碎中文 prompt），
        # 强制 UTF-8 与生产真实 codex（Rust，bytes 安全）对齐。非产品 bug。
        self._old_utf8env = os.environ.get("PYTHONUTF8")
        os.environ["PYTHONUTF8"] = "1"
        self.mgr = AgentHostManager()
        self.mgr._get_project_dir = lambda pid: self.tmp.name

    def tearDown(self):
        if self._old_utf8env is None:
            os.environ.pop("PYTHONUTF8", None)
        else:
            os.environ["PYTHONUTF8"] = self._old_utf8env
        shutil.which = self._real_which
        self.tmp.cleanup()

    def install_which(self, target):
        def _which(name, path=None):
            if name == "codex":
                return target
            return self._real_which(name, path)
        shutil.which = _which

    def log_path(self, node="node-ev"):
        return Path(self.tmp.name) / "agent-events" / f"{node}.jsonl"

    def read_log(self, node="node-ev"):
        p = self.log_path(node)
        if not p.exists():
            return []
        return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]

    async def _wait_turn_done(self, session, timeout=20.0):
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if session.process is None and session._turn_open is False:
                return True
            await asyncio.sleep(0.05)
        return False


class PersistenceTests(_Base):
    async def test_turn_persists_seq_host_userprompt(self):
        self.install_which(FAKE_CODEX)
        s = await self.mgr.get_or_create_session("proj-ev", "node-ev")
        s.agent_cli = "codex"
        assert await self.mgr.spawn_session(s)
        await self.mgr.send_prompt(s, "第一问")
        assert await self._wait_turn_done(s)
        recs = self.read_log()
        assert recs, "events.jsonl 未生成"
        seqs = [r["seq"] for r in recs]
        assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
        assert all(r["host"] == "codex" for r in recs)
        types = [r["event"]["type"] for r in recs]
        assert "user_prompt" in types          # user 侧入日志
        assert "message_update" in types
        assert "agent_end" in types
        # live WS handler 收到的事件带同一 seq
        seen = []
        self.mgr.on_event(s, lambda sess, ev: seen.append(ev))
        await self.mgr.send_prompt(s, "第二问")
        assert await self._wait_turn_done(s)
        live = [e for e in seen if "seq" in e]
        assert live and all(isinstance(e["seq"], int) for e in live)
        recs2 = self.read_log()
        assert [r["seq"] for r in recs2] == sorted({r["seq"] for r in recs2})  # 跨轮单调无复用

    async def test_tool_duration_injected_live_and_log(self):
        """#322 票 D：tool_execution_end 就地注入 duration_ms，live 与 events.jsonl 同带。"""
        self.install_which(FAKE_CODEX)
        s = await self.mgr.get_or_create_session("proj-ev", "node-ev")
        s.agent_cli = "codex"
        seen = []
        self.mgr.on_event(s, lambda sess, ev: seen.append(dict(ev)))
        assert await self.mgr.spawn_session(s)
        await self.mgr.send_prompt(s, "跑个工具")
        assert await self._wait_turn_done(s)
        recs = self.read_log()
        starts = [r["event"] for r in recs if r["event"]["type"] == "tool_execution_start"]
        ends = [r["event"] for r in recs if r["event"]["type"] == "tool_execution_end"]
        assert starts and ends, "stub 的 command_execution 对未映射为 tool 事件对"
        for e in ends:
            self.assertIsInstance(e.get("duration_ms"), int)
            self.assertGreaterEqual(e["duration_ms"], 0)
        live_ends = [e for e in seen if e.get("type") == "tool_execution_end"]
        assert live_ends and all(isinstance(e.get("duration_ms"), int) for e in live_ends)
        # todo_list 项→tool 事件对，终态 args 带打勾列表（checklist 数据源）
        todo_ends = [e for e in ends if e.get("toolName") == "todo_list"]
        assert todo_ends and todo_ends[0].get("args", {}).get("todos")

    async def test_seq_survives_manager_restart(self):
        self.install_which(FAKE_CODEX)
        s = await self.mgr.get_or_create_session("proj-ev", "node-ev")
        s.agent_cli = "codex"
        assert await self.mgr.spawn_session(s)
        await self.mgr.send_prompt(s, "P1")
        assert await self._wait_turn_done(s)
        max1 = max(r["seq"] for r in self.read_log())
        # 模拟 server 重启：新 manager + 新 session
        mgr2 = AgentHostManager()
        mgr2._get_project_dir = lambda pid: self.tmp.name
        s2 = await mgr2.get_or_create_session("proj-ev", "node-ev")
        s2.agent_cli = "codex"
        assert await mgr2.spawn_session(s2)
        await mgr2.send_prompt(s2, "P2")
        assert await self._wait_turn_done(s2)
        assert max(r["seq"] for r in self.read_log()) > max1

    async def test_truncation_keeps_seq_monotonic(self):
        orig = ah.EVENTS_LOG_MAX_LINES
        ah.EVENTS_LOG_MAX_LINES = 30
        try:
            s = await self.mgr.get_or_create_session("proj-ev", "node-ev")
            for i in range(80):
                self.mgr._persist_event(s, {"type": "usage", "usage": {"i": i}})
            recs = self.read_log()
            assert len(recs) <= 30
            assert recs[-1]["seq"] == 80
            assert recs[0]["seq"] > 1  # 截头
            assert all(recs[k]["seq"] < recs[k + 1]["seq"] for k in range(len(recs) - 1))
        finally:
            ah.EVENTS_LOG_MAX_LINES = orig

    def test_load_events_since(self):
        s = self.mgr.get_session if False else None
        import asyncio as _a

        async def run():
            sess = await self.mgr.get_or_create_session("proj-ev", "node-ev")
            for i in range(5):
                self.mgr._persist_event(sess, {"type": "usage", "usage": {"i": i}})
            d0 = self.mgr.load_events("proj-ev", "node-ev", since=0)
            d3 = self.mgr.load_events("proj-ev", "node-ev", since=3)
            empty = self.mgr.load_events("proj-ev", "no-such-node", since=0)
            return d0, d3, empty
        d0, d3, empty = _a.run(run())
        assert [r["seq"] for r in d0["events"]] == [1, 2, 3, 4, 5]
        assert [r["seq"] for r in d3["events"]] == [4, 5]
        assert d3["last_seq"] == 5
        assert empty == {"events": [], "last_seq": 0}


class ReplayContextTests(_Base):
    def _seed_log(self, node, turns):
        p = self.log_path(node)
        p.parent.mkdir(parents=True, exist_ok=True)
        seq = 0
        lines = []
        for i in range(turns):
            for ev in (
                {"type": "user_prompt", "text": f"问{i}"},
                {"type": "message_update",
                 "assistantMessageEvent": {"type": "text_delta", "delta": f"答{i}"}},
                {"type": "agent_end"},
            ):
                seq += 1
                lines.append(json.dumps({"seq": seq, "ts": 0, "host": "codex",
                                         "project_id": "p", "node_id": node, "event": ev}))
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")

    async def test_recent_n_and_under_n(self):
        s = await self.mgr.get_or_create_session("proj-ev", "node-r1")
        self._seed_log("node-r1", 25)
        block, used = self.mgr._build_replay_context(s)
        assert used == ah.EVENTS_REPLAY_TURNS == 20
        assert "问24" in block and "答24" in block
        assert "问4" not in block and "问5" in block  # 只留最近 20
        s2 = await self.mgr.get_or_create_session("proj-ev", "node-r2")
        self._seed_log("node-r2", 3)
        block2, used2 = self.mgr._build_replay_context(s2)
        assert used2 == 3 and "问0" in block2  # 不足按实际全量

    async def test_degrade_retry_carries_context(self):
        self.install_which(FAKE_CODEX_RESUME_FAIL)
        s = await self.mgr.get_or_create_session("proj-ev", "node-dg")
        s.agent_cli = "codex"
        events = []
        self.mgr.on_event(s, lambda sess, ev: events.append(ev))
        assert await self.mgr.spawn_session(s)
        await self.mgr.send_prompt(s, "第一暗号PURPLE")
        assert await self._wait_turn_done(s)
        events.clear()
        await self.mgr.send_prompt(s, "ECHO_MODE 第二问")  # resume 必败 → 降级
        loop = asyncio.get_event_loop()
        deadline = loop.time() + 25
        while loop.time() < deadline:
            if any(e["type"] == "message_update" for e in events) and s.process is None:
                break
            await asyncio.sleep(0.05)
        errs = [e for e in events if e["type"] == "error"]
        assert errs and "重放" in errs[0]["message"]
        msgs = [e for e in events if e["type"] == "message_update"]
        text = "".join(
            (m.get("assistantMessageEvent") or {}).get("delta", "") for m in msgs)
        assert "canvas-history-replay" in text  # 重放块前置注入进重试 prompt
        assert "第一暗号PURPLE" in text          # user 侧在重放内


if __name__ == "__main__":
    unittest.main()
