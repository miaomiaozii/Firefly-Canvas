"""#279: 画布工具回连地址兜底 8000 ≠ 实际监听端口（默认 3000）。

sidecar 启动成功后所有画布工具调用打到 8000 全失败：spawn 注入
FIREFLY_AGENT_API_BASE 的旧兜底是 8000，而服务实际默认监听 3000
（main.py 末尾 uvicorn port=FIREFLY_PORT 或 3000）。

验收对照：
- resolve_agent_api_base：显式 FIREFLY_AGENT_API_BASE 优先 →
  FIREFLY_PORT 端口 → 默认 3000（与真实默认端口一致）
- ensure_agent_api_base_env：把生成的基址写入 env（显式值不动）
- spawn_session 注入的 env 基址跟随 FIREFLY_PORT / 默认 3000
  （mock Popen，不依赖起真服务、不绑端口）
- main.py 启动时把基址写入 os.environ；canvas-tools.ts 兜底同步 3000
"""
import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main
from firefly_projects import ProjectStore
from app.services import agent_host
from tests.test_agent_cli_resolution import _FakeProcess


class ResolveAgentApiBaseTests(unittest.TestCase):
    """resolve_agent_api_base：显式值 → FIREFLY_PORT → 默认 3000。"""

    def test_explicit_base_wins(self):
        env = {"FIREFLY_AGENT_API_BASE": "http://10.0.0.5:9999", "FIREFLY_PORT": "3001"}
        self.assertEqual(
            agent_host.resolve_agent_api_base(env), "http://10.0.0.5:9999"
        )

    def test_follows_firefly_port(self):
        self.assertEqual(
            agent_host.resolve_agent_api_base({"FIREFLY_PORT": "4321"}),
            "http://127.0.0.1:4321",
        )

    def test_default_is_3000(self):
        self.assertEqual(agent_host.resolve_agent_api_base({}), "http://127.0.0.1:3000")

    def test_invalid_port_falls_back_to_3000(self):
        for bad in ("", "abc", "0", "-1", "70000"):
            self.assertEqual(
                agent_host.resolve_agent_api_base({"FIREFLY_PORT": bad}),
                "http://127.0.0.1:3000",
                msg=f"FIREFLY_PORT={bad!r}",
            )

    def test_ensure_env_writes_base(self):
        env = {"FIREFLY_PORT": "4321"}
        self.assertEqual(agent_host.ensure_agent_api_base_env(env), "http://127.0.0.1:4321")
        self.assertEqual(env["FIREFLY_AGENT_API_BASE"], "http://127.0.0.1:4321")

    def test_ensure_env_preserves_explicit(self):
        env = {"FIREFLY_AGENT_API_BASE": "http://x:1", "FIREFLY_PORT": "4321"}
        self.assertEqual(agent_host.ensure_agent_api_base_env(env), "http://x:1")
        self.assertEqual(env["FIREFLY_AGENT_API_BASE"], "http://x:1")


class SpawnApiBaseInjectionTests(unittest.TestCase):
    """spawn_session 注入的 env 基址跟随 FIREFLY_PORT / 默认 3000。

    事件循环口径同 test_agent_cli_resolution / test_agent_host_kill：
    自建 loop + run_until_complete，不碰 policy 当前 loop。
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="firefly-agent-api-")
        self.addCleanup(self.temp_dir.cleanup)
        self.store = ProjectStore(Path(self.temp_dir.name) / "projects", snapshot_delay=0)
        self._previous_store = getattr(main, "FIREFLY_PROJECT_STORE", None)
        self.addCleanup(self._restore_store)
        main.FIREFLY_PROJECT_STORE = self.store
        project = self.store.create_project("Agent API Test")
        self.project_id = project["id"]
        self.manager = agent_host.AgentHostManager()
        # FIREFLY_PI_CLI 固定为非包装器值：spawn 直达（mock 的）Popen
        self._previous_pi_cli = os.environ.pop("FIREFLY_PI_CLI", None)
        os.environ["FIREFLY_PI_CLI"] = "pi-fake-for-test"
        # main 模块导入时已写入 FIREFLY_AGENT_API_BASE——隔离掉，测试注入逻辑本身
        self._previous_base = os.environ.pop("FIREFLY_AGENT_API_BASE", None)
        self._previous_port = os.environ.pop("FIREFLY_PORT", None)
        self.addCleanup(self._restore_env)
        self.loop = asyncio.new_event_loop()
        self.addCleanup(self.loop.close)

    def _run(self, coro):
        return self.loop.run_until_complete(coro)

    def _restore_store(self):
        main.FIREFLY_PROJECT_STORE = self._previous_store

    def _restore_env(self):
        if self._previous_pi_cli is not None:
            os.environ["FIREFLY_PI_CLI"] = self._previous_pi_cli
        if self._previous_base is not None:
            os.environ["FIREFLY_AGENT_API_BASE"] = self._previous_base
        if self._previous_port is not None:
            os.environ["FIREFLY_PORT"] = self._previous_port

    def _spawn_with_fake(self, session) -> mock.MagicMock:
        fake_process = _FakeProcess()
        popen = mock.MagicMock(return_value=fake_process)
        with mock.patch.object(agent_host.subprocess, "Popen", popen):
            self._run(self.manager.spawn_session(session))
        # 在本测试事件循环内跑完 reader 任务，避免悬挂任务
        if session._reader_task is not None:
            try:
                self._run(asyncio.wait_for(session._reader_task, timeout=10.0))
            except Exception:
                pass
        return popen

    def test_spawn_base_follows_firefly_port(self):
        os.environ["FIREFLY_PORT"] = "4321"
        self.addCleanup(lambda: os.environ.pop("FIREFLY_PORT", None))
        session = self._run(self.manager.get_or_create_session(self.project_id, "node-port"))
        popen = self._spawn_with_fake(session)
        popen.assert_called_once()
        _, kwargs = popen.call_args
        self.assertEqual(kwargs["env"]["FIREFLY_AGENT_API_BASE"], "http://127.0.0.1:4321")

    def test_spawn_base_defaults_to_3000(self):
        session = self._run(self.manager.get_or_create_session(self.project_id, "node-default"))
        popen = self._spawn_with_fake(session)
        popen.assert_called_once()
        _, kwargs = popen.call_args
        self.assertEqual(kwargs["env"]["FIREFLY_AGENT_API_BASE"], "http://127.0.0.1:3000")


class ApiBaseWiringTests(unittest.TestCase):
    """装配检查：main.py 写入 env；代码与扩展不再残留 8000 兜底。"""

    def test_main_import_wrote_api_base(self):
        # main.py 模块导入即把基址写入 os.environ（未显式设置时按 3000 生成）
        base = os.environ.get("FIREFLY_AGENT_API_BASE")
        self.assertIsNotNone(
            base, "main.py 导入时应写入 FIREFLY_AGENT_API_BASE"
        )
        self.assertTrue(base.startswith("http://"), base)

    def test_main_wires_ensure_agent_api_base(self):
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn("ensure_agent_api_base_env", source)
        self.assertIn("FIREFLY_PORT", source)

    def test_agent_host_has_no_8000_fallback(self):
        source = (ROOT / "app" / "services" / "agent_host.py").read_text(encoding="utf-8")
        # 禁止的是旧的 8000 兜底 URL 字面量（注释里提到 8000 作为历史说明是允许的）
        self.assertNotIn('"http://127.0.0.1:8000"', source)

    def test_canvas_tools_fallback_is_3000(self):
        source = (ROOT / "app" / "agent_extensions" / "canvas-tools.ts").read_text(encoding="utf-8")
        self.assertIn('"http://127.0.0.1:3000"', source)
        self.assertNotIn(":8000", source)


if __name__ == "__main__":
    unittest.main()
