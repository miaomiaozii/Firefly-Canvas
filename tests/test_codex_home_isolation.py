"""#318 隔离尾单：CODEX_HOME 注入 + auth 同步（离线单测，不拉真 codex）。"""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.agent_adapters.codex_adapter import (
    codex_home_dir,
    codex_spawn_env,
    prepare_codex_home,
)


class CodexHomeIsolation(unittest.TestCase):
    def test_home_under_runtime_root(self):
        from app.constants import RUNTIME_ROOT
        home = codex_home_dir()
        self.assertEqual(home, os.path.join(RUNTIME_ROOT, "codex-home"))

    def test_spawn_env_injects_isolated_home_and_creates_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_home = os.path.join(tmp, "codex-home")
            with mock.patch(
                "app.services.agent_adapters.codex_adapter.codex_home_dir",
                return_value=fake_home,
            ):
                env = codex_spawn_env({"PATH": "x"})
            self.assertEqual(env["CODEX_HOME"], fake_home)
            self.assertTrue(os.path.isdir(fake_home))

    def test_explicit_external_codex_home_wins(self):
        env = codex_spawn_env({"CODEX_HOME": "D:/somewhere/else"})
        self.assertEqual(env["CODEX_HOME"], "D:/somewhere/else")

    def test_auth_synced_from_global_when_newer(self):
        with tempfile.TemporaryDirectory() as tmp:
            global_dir = os.path.join(tmp, "fakehome")
            codex_dir = os.path.join(global_dir, ".codex")
            os.makedirs(codex_dir)
            auth = os.path.join(codex_dir, "auth.json")
            with open(auth, "w") as f:
                f.write('{"tokens":"secret"}')
            home = os.path.join(tmp, "isolated")
            with mock.patch(
                "app.services.agent_adapters.codex_adapter.os.path.expanduser",
                return_value=global_dir,
            ):
                prepare_codex_home(home)
                with open(os.path.join(home, "auth.json")) as f:
                    self.assertEqual(f.read(), '{"tokens":"secret"}')
                # 幂等：再跑一次不报错、内容不变
                prepare_codex_home(home)
                with open(os.path.join(home, "auth.json")) as f:
                    self.assertEqual(f.read(), '{"tokens":"secret"}')

    def test_no_global_auth_no_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = os.path.join(tmp, "isolated")
            with mock.patch(
                "app.services.agent_adapters.codex_adapter.os.path.expanduser",
                return_value=os.path.join(tmp, "nonexistent"),
            ):
                prepare_codex_home(home)
            self.assertTrue(os.path.isdir(home))
            self.assertFalse(os.path.exists(os.path.join(home, "auth.json")))


if __name__ == "__main__":
    unittest.main()
