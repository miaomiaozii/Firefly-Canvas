"""#278: Agent 节点在 Windows 上完全不可用 —— pi 子进程启动失败（WinError 2）。

根因：Windows 上 npm 安装的 pi 只有 pi.cmd 包装器，CreateProcess 不补 .cmd
后缀，直接 Popen(["pi", ...]) 得到 "[WinError 2] 系统找不到指定的文件。"

验收对照：
- resolve_pi_cli：FIREFLY_PI_CLI 原值优先 → shutil.which(PI_CLI)
  （Windows 上能解析到 pi.cmd 包装器）→ None
- build_pi_launch_command：#343 新口径——npm 包装器解成 node.exe+脚本直接启动；解不出回 None（拒绝，不回落 cmd /c）
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
    """build_pi_launch_command（#343 新口径）：包装器解成 node+脚本；解不出回 None。"""

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

    def test_npm_cmd_wrapper_resolved_to_node_and_script(self):
        """npm 规范模板包装器 → [node, 脚本绝对路径, *args]，不再经 cmd.exe。"""
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "pi.cmd").write_text(
                '@ECHO off\r\nSETLOCAL\r\nCALL :find_dp0\r\n'
                'endLocal & goto #_undefined_# 2>NUL || title %COMSPEC% & '
                '"%_prog%"  "%dp0%\\..\\pkg\\bin\\pi.js" %*\r\n',
                encoding="utf-8",
            )
            script = d / ".." / "pkg" / "bin" / "pi.js"
            script.parent.mkdir(parents=True, exist_ok=True)
            script.write_text("// fake", encoding="utf-8")
            cmd = build_pi_launch_command(str(d / "pi.cmd"), ["--mode", "rpc"])
        self.assertIsNotNone(cmd)
        self.assertNotEqual(cmd[0], "cmd")
        self.assertTrue(cmd[0].lower().endswith("node.exe"))
        self.assertEqual(os.path.normpath(cmd[1]), os.path.normpath(str(script)))
        self.assertEqual(cmd[2:], ["--mode", "rpc"])

    def test_non_npm_wrapper_refused_no_cmd_fallback(self):
        """非 npm 模板的 .cmd/.bat（如测试 stub）：拒绝启动，绝不回落 cmd /c。"""
        self.assertIsNone(build_pi_launch_command(r"C:\tools\pi.bat", []))
        self.assertIsNone(
            build_pi_launch_command(str(Path(__file__).resolve().parent / "stubs" / "pi_cli_stub.cmd"), [])
        )


def _stub_path(stubs_dir):
    # PATH = stubs + node.exe 所在目录（wrapper 解析需要 node，但不引入真实 pi/codex 路径）
    import shutil
    node = shutil.which('node')
    import os
    return str(stubs_dir) + os.pathsep + (os.path.dirname(node) if node else '')


class SpawnCliResolutionTests(unittest.TestCase):
    """spawn_session 集成：解析失败不启动 + last_error；#343：非 npm 包装器拒绝启动。

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

    def test_non_npm_cmd_wrapper_spawn_refused_without_popen(self):
        """#343：解不出的 .cmd（测试 stub 形态）→ crashed + 不 Popen，绝不经 cmd.exe。"""
        session = self._run(self.manager.get_or_create_session(self.project_id, "node-cmd"))
        previous_path = os.environ.get("PATH")
        try:
            agent_host.PI_CLI = "pi"
            os.environ["PATH"] = _stub_path(Path(__file__).resolve().parent / "stubs")
            popen = mock.MagicMock(side_effect=AssertionError("Popen 不应被调用"))
            with mock.patch.object(agent_host.subprocess, "Popen", popen):
                self.assertFalse(self._run(self.manager.spawn_session(session)))
        finally:
            os.environ["PATH"] = previous_path
        popen.assert_not_called()
        self.assertEqual(session.status, "crashed")
        self.assertIn("npm", session.last_error)

    def test_incompatible_pi_is_rejected_before_session_process_creation(self):
        session = self._run(self.manager.get_or_create_session(self.project_id, "node-incompatible"))
        result = agent_host.subprocess.CompletedProcess(["fixture-pi.exe", "--version"], 0, stdout="1.5.1\n")
        with mock.patch.dict(os.environ, {"FIREFLY_PI_CLI": "fixture-pi.exe"}), \
             mock.patch.object(agent_host.subprocess, "run", return_value=result) as probe, \
             mock.patch.object(agent_host.subprocess, "Popen", return_value=_FakeProcess()) as launch:
            self.assertFalse(self._run(self.manager.spawn_session(session)))
        if session._reader_task is not None:
            self._run(session._reader_task)
        launch.assert_not_called()
        self.assertEqual(probe.call_args.args[0], ["fixture-pi.exe", "--version"])
        self.assertEqual(session.status, "crashed")
        self.assertIsNone(session.process)
        self.assertIn("1.5.1", session.last_error)
        self.assertIn("1.6.1", session.last_error)

    def test_unverified_pi_never_starts_a_session(self):
        version_command = ["fixture-pi.exe", "--version"]
        for label, result, error in (
            ("malformed", agent_host.subprocess.CompletedProcess(version_command, 0, stdout="private text\n1.6.1\n"), None),
            ("nonzero", agent_host.subprocess.CompletedProcess(version_command, 1, stdout="1.6.1\n"), None),
            ("timeout", None, agent_host.subprocess.TimeoutExpired(version_command, 10)),
            ("unavailable", None, FileNotFoundError("private text")),
        ):
            with self.subTest(label=label), \
                 mock.patch.dict(os.environ, {"FIREFLY_PI_CLI": "fixture-pi.exe"}), \
                 mock.patch.object(agent_host.subprocess, "run", return_value=result, side_effect=error) as probe, \
                 mock.patch.object(agent_host.subprocess, "Popen") as launch:
                session = self._run(self.manager.get_or_create_session(self.project_id, f"node-{label}"))
                self.assertFalse(self._run(self.manager.spawn_session(session)))
                launch.assert_not_called()
                self.assertEqual(probe.call_args.args[0], version_command)
                self.assertGreater(probe.call_args.kwargs["timeout"], 0)
                self.assertLessEqual(probe.call_args.kwargs["timeout"], 10)
                self.assertEqual(session.status, "crashed")
                self.assertIsNone(session.process)
                self.assertIn("无法验证 CLI 版本", session.last_error)
                self.assertNotIn("private text", session.last_error)

    def test_stop_while_version_check_is_pending_does_not_restart_pi(self):
        async def scenario():
            session = await self.manager.get_or_create_session(self.project_id, "node-stop-probe")
            entered, release = asyncio.Event(), asyncio.Event()

            async def gated_probe(*args, **kwargs):
                entered.set()
                await release.wait()

            with (mock.patch.dict(os.environ, {"FIREFLY_PI_CLI": "fixture-pi.exe"}),
                  mock.patch.object(agent_host, "verify_pi_launch_version", gated_probe),
                  mock.patch.object(agent_host.subprocess, "Popen") as launch):
                task = asyncio.create_task(self.manager.spawn_session(session))
                try:
                    await asyncio.wait_for(entered.wait(), 2)
                    await self.manager.stop_session(session)
                    release.set()
                    self.assertFalse(await task)
                finally:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                launch.assert_not_called()
                self.assertIsNone(session.process)
                self.assertEqual(session.status, "stopped")

        self._run(scenario())

    def test_npm_cmd_wrapper_spawn_launches_node_not_cmd(self):
        """#343：npm 规范包装器 → Popen 收到 [node.exe, 脚本, ...]，cmd[0] 不是 cmd。"""
        session = self._run(self.manager.get_or_create_session(self.project_id, "node-npm"))
        with tempfile.TemporaryDirectory() as cli_dir:
            base = Path(cli_dir)
            (base / "pi.cmd").write_text(
                '@ECHO off\r\nendLocal & goto #_undefined_# 2>NUL || title %COMSPEC% & '
                '"%_prog%"  "%dp0%\\pi-pkg\\bin\\pi.js" %*\r\n',
                encoding="utf-8",
            )
            script = base / "pi-pkg" / "bin" / "pi.js"
            script.parent.mkdir(parents=True, exist_ok=True)
            script.write_text("// fake", encoding="utf-8")
            previous_path = os.environ.get("PATH")
            try:
                agent_host.PI_CLI = "pi"
                os.environ["PATH"] = _stub_path(base)
                fake_process = _FakeProcess()
                version = agent_host.subprocess.CompletedProcess([], 0, stdout="1.6.1\n")
                with (mock.patch.dict(os.environ, {"PI_PACKAGE_DIR": str(base / "parent-pi-package")}),
                      mock.patch.object(agent_host.subprocess, "run", return_value=version) as probe,
                      mock.patch.object(agent_host.subprocess, "Popen", return_value=fake_process) as popen):
                    self._run(self.manager.spawn_session(session))
                    self.assertEqual(os.environ["PI_PACKAGE_DIR"], str(base / "parent-pi-package"))
            finally:
                os.environ["PATH"] = previous_path
        if session._reader_task is not None:
            try:
                self._run(asyncio.wait_for(session._reader_task, timeout=10.0))
            except Exception:
                pass
        popen.assert_called_once()
        args, kwargs = popen.call_args
        command = args[0] if args else kwargs["args"]
        self.assertNotEqual(command[0], "cmd")
        self.assertTrue(command[0].lower().endswith("node.exe"))
        self.assertEqual(os.path.normpath(command[1]), os.path.normpath(str(script)))
        self.assertIn("--mode", command)
        self.assertEqual(probe.call_args.args[0], command[:2] + ["--version"])
        self.assertEqual(probe.call_args.kwargs["cwd"], kwargs["cwd"])
        self.assertNotIn("PI_PACKAGE_DIR", set(probe.call_args.kwargs["env"]))
        self.assertNotIn("PI_PACKAGE_DIR", set(kwargs["env"]))
        self.assertEqual(probe.call_args.kwargs["env"], kwargs["env"])


if __name__ == "__main__":
    unittest.main()
