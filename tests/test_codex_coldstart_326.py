"""#326: codex post-turn cache warmup — cooldown semantics, no event-stream entry."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services import agent_host


class TestCodexWarmup(unittest.TestCase):
    def setUp(self):
        self.saved = dict(agent_host._codex_warmup_last)

    def tearDown(self):
        agent_host._codex_warmup_last.clear()
        agent_host._codex_warmup_last.update(self.saved)

    def test_cooldown_is_per_node(self):
        agent_host._codex_warmup_last.clear()
        with patch.object(agent_host.shutil, "which", return_value="codex"), \
             patch.object(agent_host.subprocess, "run"):
            self.assertTrue(agent_host.maybe_warm_codex("node-a"))
            self.assertFalse(agent_host.maybe_warm_codex("node-a"))
            # 评审修正后：另一节点不得被全局窗口饿死
            self.assertTrue(agent_host.maybe_warm_codex("node-b"))

    def test_missing_cli_no_thread(self):
        agent_host._codex_warmup_last.clear()
        with patch.object(agent_host.shutil, "which", return_value=None), \
             patch.object(agent_host.threading, "Thread") as thread:
            self.assertFalse(agent_host.maybe_warm_codex("node-c"))
        thread.assert_not_called()

    def test_warm_argv_is_version_only(self):
        agent_host._codex_warmup_last.clear()
        threads = []
        real_thread = agent_host.threading.Thread

        def _sync_thread(*a, **k):
            t = real_thread(*a, **k)
            threads.append(t)
            return t

        with patch.object(agent_host.shutil, "which", return_value="/x/codex"), \
             patch.object(agent_host.subprocess, "run") as run, \
             patch.object(agent_host.threading, "Thread", _sync_thread):
            agent_host.maybe_warm_codex("node-d")
        for t in threads:
            t.join(2)  # 确定性：无 sleep 竞态
        argv = run.call_args[0][0]
        self.assertEqual(argv, ["/x/codex", "--version"])


if __name__ == "__main__":
    unittest.main()
