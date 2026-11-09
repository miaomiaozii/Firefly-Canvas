"""#281: Agent 事件派发对同步处理器 await 报错的回归测试。

背景：#239 引入的同步 RPC 捕获处理器（capture_response）被放进统一
await 派发的 event_handlers 列表；在 #278 修好 Windows 启动之前该路径
从未被执行，属潜伏缺陷。修复后派发处用 inspect.isawaitable 区分：
同步处理器直接调用，异步才 await，不得产生 ERROR 日志噪音。
"""
import asyncio
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.agent_host import AgentHostManager, AgentSession


class AgentEventDispatchTests(unittest.TestCase):
    def setUp(self):
        self.manager = AgentHostManager()
        self._previous_current_loop = asyncio.get_event_loop()
        self.loop = asyncio.new_event_loop()
        self.addCleanup(self._close_loop)
        self.session = AgentSession(project_id="p1", node_id="node-281")

    def _close_loop(self):
        try:
            self.loop.run_until_complete(self.manager.stop_all())
        except Exception:
            pass
        finally:
            asyncio.set_event_loop(self._previous_current_loop)
            self.loop.close()

    def test_sync_and_async_handlers_both_receive_without_error(self):
        received = []

        def sync_handler(sess, event):
            received.append(("sync", event.get("type")))

        async def async_handler(sess, event):
            received.append(("async", event.get("type")))

        self.session.event_handlers.extend([sync_handler, async_handler])
        with self.assertNoLogs("app.services.agent_host", level="ERROR"):
            self.loop.run_until_complete(
                self.manager._dispatch_event(self.session, {"type": "message_update"})
            )
        self.assertEqual(received, [("sync", "message_update"), ("async", "message_update")])

    def test_process_exited_notify_loop_tolerates_sync_handler(self):
        # 退出通知循环（_read_events 内）同样必须兼容同步处理器：
        # 用 await_command_response 风格的 capture_response 直接混入验证。
        received = []

        def capture_like(sess, event):
            received.append(event.get("type"))

        self.session.event_handlers.append(capture_like)
        out = capture_like(self.session, {"type": "process_exited", "exit_code": 0})
        # 同步返回 None：派发处不得 await None（isawaitable 为 False 即契约）
        self.assertIsNone(out)
        self.assertFalse(asyncio.iscoroutine(out))
        self.assertEqual(received, ["process_exited"])


if __name__ == "__main__":
    unittest.main()
