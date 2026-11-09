"""#286: turn 停顿看门狗单测——busy 会话 120s 无事件先注入可操作的 stall error 事件。

语义钉：
- busy + idle>TURN_STALL_NOTIFY_SECONDS → 事件流收到 {"type":"error","stall":true}，只发一次；
- agent_start 新 turn 重置 _stall_notified（可再次提醒）；
- idle 未过阈值不发；status 非 busy 不发。
"""
import asyncio
import time
import unittest

from app.services import agent_host as agent_host_module
from app.services.agent_host import AgentHostManager, AgentSession, TURN_STALL_NOTIFY_SECONDS


def run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TurnStallWatchdogTests(unittest.TestCase):
    def setUp(self):
        self.manager = AgentHostManager()
        self.session = AgentSession(project_id="p1", node_id="n1", model="bailian/qwen-plus")
        self.session.status = "busy"
        self.session._last_activity = time.time()
        self.captured = []
        self.session.event_handlers.append(lambda s, e: self.captured.append(e))
        self.manager.sessions["n1"] = self.session

    def tearDown(self):
        run_async(self.manager.cleanup_all())

    def test_stall_notified_once_after_threshold(self):
        self.session._last_activity = time.time() - (TURN_STALL_NOTIFY_SECONDS + 10)
        run_async(self.manager._idle_watchdog_pass(time.time()))
        stalls = [e for e in self.captured if e.get("stall")]
        self.assertEqual(len(stalls), 1, "过阈值应恰发一条 stall error")
        self.assertEqual(stalls[0]["type"], "error")
        self.assertIn("模型", stalls[0]["message"])
        # 再扫一轮不重复堆叠
        run_async(self.manager._idle_watchdog_pass(time.time()))
        self.assertEqual(len([e for e in self.captured if e.get("stall")]), 1)

    def test_no_notice_under_threshold_or_idle_status(self):
        run_async(self.manager._idle_watchdog_pass(time.time()))
        self.assertEqual(self.captured, [], "未过阈值不应发事件")
        self.session._last_activity = time.time() - (TURN_STALL_NOTIFY_SECONDS + 10)
        self.session.status = "running"
        run_async(self.manager._idle_watchdog_pass(time.time()))
        self.assertEqual(self.captured, [], "非 busy 会话不应发事件")

    def test_new_turn_resets_stall_flag(self):
        self.session._stall_notified = True
        run_async(self.manager._dispatch_event(self.session, {"type": "agent_start"}))
        self.assertFalse(self.session._stall_notified)


if __name__ == "__main__":
    unittest.main()
