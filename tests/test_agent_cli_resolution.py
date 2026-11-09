"""#278: Agent 节点在 Windows 上完全不可用 —— pi 子进程启动失败（WinError 2）。

根因：Windows 上 npm 安装的 pi 只有 pi.cmd 包装器，CreateProcess 不补 .cmd
后缀，直接 Popen(["pi", ...]) 得到 "[WinError 2] 系统找不到指定的文件。"

验收对照：
- resolve_pi_cli：FIREFLY_PI_CLI 原值优先 → shutil.which(PI_CLI)
  （Windows 上能解析到 pi.cmd 包装器）→ None
- build_pi_launch_command：.cmd/.bat 包装器用 cmd /c 数组前缀启动
  （不用 shell=True 字符串拼接）；非包装器原样启动
- spawn_session：解析失败不启动进程，会话终态 crashed，last_error 给出
  可操作提示（安装 pi / 设置 FIREFLY_PI_CLI），快照投影可见
- spawn_session：解析到 .cmd 时 Popen argv 带 cmd /c 前缀（进程树仍由
  _kill_process_tree 的 taskkill /T /F 覆盖，不产生 cmd.exe 僵尸）
全部 mock / 临时文件，不依赖真实 pi，不绑端口、不起真服务。
"""
import asyncio
import io
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
from app.services.agent_host import (
    AgentHostManager,
    build_pi_launch_command,
    resolve_pi_cli,
)


class _FakeProcess:
    """Popen 对象的最小替身：立即报告已退出，不启动真实进程。"""

    def __init__(self):
        self.pid = 99999
        self.returncode = 0
        self.stdin = None
        self.stdout = io.StringIO("")
        self.stderr = io.StringIO("")

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode

    def terminate(self):
        pass


class ResolvePiCliTests(unittest.TestCase):
    """resolve_pi_cli：显式 env 值优先 → which 解析 → None。"""

    def test_explicit_env_wins(self):
        env = {"FIREFLY_PI_CLI": r"D:\tools\pi.exe", "PATH": "/nonexistent"}
        self.assertEqual(resolve_pi_cli(env), r"D:\tools\pi.exe")

    def test_returns_none_when_missing(self):
        previous_path = os.environ.get("PATH")
        previous_env = os.environ.pop("FIREFLY_PI_CLI", None)
        previous_cli = agent_host.PI_CLI
        try:
            with tempfile.TemporaryDirectory() as empty:
                agent_host.PI_CLI = "pi"
                os.environ["PATH"] = empty
                self.assertIsNone(resolve_pi_cli())
        finally:
            agent_host.PI_CLI = previous_cli
            if previous_env is not None:
                os.environ["FIREFLY_PI_CLI"] = previous_env
            os.environ["PATH"] = previous_path

    @unittest.skipIf(os.name != "nt", "Windows only: pi.cmd 包装器解析")
    def test_which_resolves_cmd_wrapper(self):
        previous_path = os.environ.get("PATH")
        previous_env = os.environ.pop("FIREFLY_PI_CLI", None)
        previous_cli = agent_host.PI_CLI
        try:
            with tempfile.TemporaryDirectory() as tmp:
                (Path(tmp) / "pi.cmd").write_text("@echo fake pi\n", encoding="utf-8")
                agent_host.PI_CLI = "pi"
                os.environ["PATH"] = tmp
                resolved = resolve_pi_cli()
                self.assertIsNotNone(resolved, "shutil.which 应能解析到 pi.cmd 包装器")
                self.assertEqual(os.path.basename(resolved).lower(), "pi.cmd")
        finally:
            agent_host.PI_CLI = previous_cli
            if previous_env is not None:
                os.environ["FIREFLY_PI_CLI"] = previous_env
            os.environ["PATH"] = previous_path


class BuildPiLaunchCommandTests(unittest.TestCase):
    """build_pi_launch_command：包装器加 cmd /c 前缀，其余原样。"""

    def test_unresolved_returns_none(self):
        self.assertIsNone(build_pi_launch_command(None, ["--mode", "rpc"]))

    def test_plain_executable_unchanged(self):
        self.assertEqual(
            build_pi_launch_command("/usr/local/bin/pi", ["--mode", "rpc"]),
            ["/usr/local/bin/pi", "--mode", "rpc"],
        )

    def test_exe_unchanged(self):
        self.assertEqual(
            build_pi_launch_command(r"D:\tools\pi.exe", ["--mode", "rpc"]),
            [r"D:\tools\pi.exe", "--mode", "rpc"],
        )

    def test_cmd_wrapper_prefixed(self):
        self.assertEqual(
            build_pi_launch_command(r"C:\Users\me\AppData\Roaming\npm\pi.cmd", ["--mode", "rpc"]),
            ["cmd", "/c", r"C:\Users\me\AppData\Roaming\npm\pi.cmd", "--mode", "rpc"],
        )

    def test_bat_wrapper_prefixed(self):
        self.assertEqual(
            build_pi_launch_command(r"C:\tools\pi.bat", []),
            ["cmd", "/c", r"C:\tools\pi.bat"],
        )


class SpawnCliResolutionTests(unittest.TestCase):
    """spawn_session 集成：解析失败不启动 + last_error；.cmd 用 cmd /c 前缀。

    不用 unittest.IsolatedAsyncioTestCase：其内部 asyncio.Runner 会调用
    set_event_loop（标记 policy _set_called），导致后续模块的
    asyncio.get_event_loop() 自动创建路径抛 RuntimeError（#179 同款
    进程级状态纪律）。改为 self-loop + run_until_complete，口径同
    test_agent_host_kill：只创建/关闭自己的 loop，不碰 policy 当前 loop。
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="firefly-agent-cli-")
        self.addCleanup(self.temp_dir.cleanup)
        self.store = ProjectStore(Path(self.temp_dir.name) / "projects", snapshot_delay=0)
        self._previous_store = getattr(main, "FIREFLY_PROJECT_STORE", None)
        self.addCleanup(self._restore_store)
        main.FIREFLY_PROJECT_STORE = self.store
        project = self.store.create_project("Agent CLI Test")
        self.project_id = project["id"]
        self.manager = AgentHostManager()
        self._previous_cli = agent_host.PI_CLI
        self._previous_env = os.environ.pop("FIREFLY_PI_CLI", None)
        self.addCleanup(self._restore_pi)
        self.loop = asyncio.new_event_loop()
        self.addCleanup(self.loop.close)

    def _run(self, coro):
        return self.loop.run_until_complete(coro)

    def _restore_store(self):
        main.FIREFLY_PROJECT_STORE = self._previous_store

    def _restore_pi(self):
        agent_host.PI_CLI = self._previous_cli
        if self._previous_env is not None:
            os.environ["FIREFLY_PI_CLI"] = self._previous_env

    def test_missing_pi_records_last_error_without_popen(self):
        session = self._run(self.manager.get_or_create_session(self.project_id, "node-missing"))
        previous_path = os.environ.get("PATH")
        try:
            with tempfile.TemporaryDirectory() as empty:
                agent_host.PI_CLI = "pi"
                os.environ["PATH"] = empty
                popen = mock.MagicMock(side_effect=AssertionError("Popen 不应被调用"))
                with mock.patch.object(agent_host.subprocess, "Popen", popen):
                    self.assertFalse(self._run(self.manager.spawn_session(session)))
        finally:
            os.environ["PATH"] = previous_path
        popen.assert_not_called()
        self.assertIsNone(session.process)
        self.assertEqual(session.status, "crashed")
        self.assertIn("FIREFLY_PI_CLI", session.last_error)
        self.assertIn("安装 pi", session.last_error)
        # 快照投影让错误可见（#280 使用）
        snapshot = {s["node_id"]: s for s in self._run(self.manager.snapshot_sessions())}
        self.assertIn("FIREFLY_PI_CLI", snapshot["node-missing"]["last_error"])

    def test_cmd_wrapper_spawn_uses_cmd_prefix(self):
        session = self._run(self.manager.get_or_create_session(self.project_id, "node-cmd"))
        with tempfile.TemporaryDirectory() as cli_dir:
            (Path(cli_dir) / "pi.cmd").write_text("@echo fake pi\n", encoding="utf-8")
            previous_path = os.environ.get("PATH")
            try:
                agent_host.PI_CLI = "pi"
                os.environ["PATH"] = cli_dir
                fake_process = _FakeProcess()
                with mock.patch.object(
                    agent_host.subprocess, "Popen", return_value=fake_process
                ) as popen:
                    self._run(self.manager.spawn_session(session))
            finally:
                os.environ["PATH"] = previous_path
        # 在本测试事件循环内跑完 reader 任务，避免悬挂任务
        if session._reader_task is not None:
            try:
                self._run(asyncio.wait_for(session._reader_task, timeout=10.0))
            except Exception:
                pass
        popen.assert_called_once()
        args, kwargs = popen.call_args
        command = args[0] if args else kwargs["args"]
        self.assertEqual(command[0], "cmd")
        self.assertEqual(command[1], "/c")
        self.assertEqual(os.path.basename(command[2]).lower(), "pi.cmd")
        self.assertIn("--mode", command)


if __name__ == "__main__":
    unittest.main()
