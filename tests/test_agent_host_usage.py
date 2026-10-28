"""#258: pi sidecar 用量采集 hook 单测（agent_host agent_start/agent_end → ops.db usage_turns）。

采集点语义：
- agent_start 记 turn 起点；agent_end 汇总 messages 中 assistant usage 写 ops.db（best-effort）
- provider/model 来自 session.model（"provider/model" 斜杠拆分；无斜杠 provider=pi）
- 遥测失败绝不影响事件流（event_handlers 照常收到事件）
- 无 usage / 全 0 不记行
"""
import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from firefly_projects import ProjectStore
from app.services import agent_host as agent_host_module
from app.services.agent_host import AgentHostManager, AgentSession


class _FakeMain:
    """模拟 main 模块：project_store_call 路由到真实 ProjectStore（SQLite 隔离）。"""

    def __init__(self, store):
        self.store = store

    def project_store_call(self, method_name, *args, **kwargs):
        return getattr(self.store, method_name)(*args, **kwargs)


def _run(coro):
    return asyncio.run(coro)


class AgentHostUsageTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name) / "projects"
        self.store = ProjectStore(self.root, snapshot_delay=60.0)
        self.project_id = self.store.create_project("Agent用量")["id"]
        self.manager = AgentHostManager()
        self._old_main = agent_host_module._app_main
        agent_host_module._app_main = _FakeMain(self.store)
        self.session = AgentSession(project_id=self.project_id, node_id="node-1", model="anthropic/claude-test")

    def tearDown(self):
        agent_host_module._app_main = self._old_main
        self.store.close()
        self.temp_dir.cleanup()

    def _dispatch_turn(self, messages):
        async def scenario():
            await self.manager._dispatch_event(self.session, {"type": "agent_start"})
            await self.manager._dispatch_event(self.session, {"type": "agent_end", "messages": messages})
        _run(scenario())

    def test_agent_end_records_usage_turn(self):
        self._dispatch_turn([
            {"role": "user", "content": "hi"},
            {"role": "assistant", "usage": {
                "input": 12, "output": 5, "cacheRead": 4, "cacheWrite": 1, "totalTokens": 17,
                "cost": {"input": 0.0001, "output": 0.0002, "cacheRead": 0.0, "cacheWrite": 0.0, "total": 0.0003}}},
        ])
        summary = self.store.list_usage_summary(self.project_id)
        self.assertEqual(summary["turns"], 1)
        total = summary["total"]
        self.assertEqual(total["input_tokens"], 12)
        self.assertEqual(total["output_tokens"], 5)
        self.assertEqual(total["cache_read_input_tokens"], 4)
        self.assertEqual(total["cache_creation_input_tokens"], 1)
        self.assertEqual(total["total_tokens"], 17)
        self.assertEqual(total["cost_usd"], 0.0003)
        self.assertIsNotNone(total["avg_duration_seconds"])
        row_model = summary["by_model"][0]
        self.assertEqual(row_model["provider"], "anthropic")
        self.assertEqual(row_model["model"], "anthropic/claude-test")

    def test_agent_end_without_usage_records_nothing(self):
        self._dispatch_turn([{"role": "user"}, {"role": "assistant"}])
        self.assertEqual(self.store.list_usage_summary(self.project_id)["turns"], 0)

    def test_record_failure_never_breaks_event_flow(self):
        class _BrokenMain(_FakeMain):
            def project_store_call(self, *a, **kw):
                raise RuntimeError("boom")

        agent_host_module._app_main = _BrokenMain(self.store)
        called = []

        async def handler(session, event):
            called.append(event.get("type"))

        self.manager.on_event(self.session, handler)
        self._dispatch_turn([{"role": "assistant", "usage": {"input": 1, "output": 1}}])
        # 遥测失败（_record_turn_usage 吞掉）不影响事件流
        self.assertEqual(called, ["agent_start", "agent_end"])

    def test_model_without_slash_provider_is_pi(self):
        self.session.model = "qwen3.8-27b"
        self._dispatch_turn([{"role": "assistant", "usage": {"input": 1, "output": 1}}])
        model = self.store.list_usage_summary(self.project_id)["by_model"][0]
        self.assertEqual(model["provider"], "pi")
        self.assertEqual(model["model"], "qwen3.8-27b")

    def test_usage_across_two_turns_accumulates_rows(self):
        self._dispatch_turn([{"role": "assistant", "usage": {"input": 1, "output": 1}}])
        self._dispatch_turn([{"role": "assistant", "usage": {"input": 2, "output": 3}}])
        summary = self.store.list_usage_summary(self.project_id)
        self.assertEqual(summary["turns"], 2)  # 每 turn 一行，跨 turn 不合并
        self.assertEqual(summary["total"]["input_tokens"], 3)
        self.assertEqual(summary["total"]["output_tokens"], 4)


if __name__ == "__main__":
    unittest.main()
