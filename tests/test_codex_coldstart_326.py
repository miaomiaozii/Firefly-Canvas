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
        self.saved = agent_host._codex_warmup_last

    def tearDown(self):
        agent_host._codex_warmup_last = self.saved

    def test_cooldown_window(self):
        agent_host._codex_warmup_last = 0.0
        with patch.object(agent_host.shutil, "which", return_value="codex"), \
             patch.object(agent_host.subprocess, "run") as run:
            first = agent_host.maybe_warm_codex()
            second = agent_host.maybe_warm_codex()
        self.assertTrue(first)
        self.assertFalse(second)
        for _ in range(50):
            if agent_host.maybe_warm_codex():
                self.fail("cooldown must suppress repeat warmups")

    def test_missing_cli_no_thread(self):
        agent_host._codex_warmup_last = 0.0
        with patch.object(agent_host.shutil, "which", return_value=None), \
             patch.object(agent_host.threading, "Thread") as thread:
            self.assertFalse(agent_host.maybe_warm_codex())
        thread.assert_not_called()

    def test_warm_argv_is_version_only(self):
        agent_host._codex_warmup_last = 0.0
        with patch.object(agent_host.shutil, "which", return_value="/x/codex"), \
             patch.object(agent_host.subprocess, "run") as run:
            agent_host.maybe_warm_codex()
        import time as _t
        _t.sleep(0.2)
        argv = run.call_args[0][0]
        self.assertEqual(argv, ["/x/codex", "--version"])


if __name__ == "__main__":
    unittest.main()
