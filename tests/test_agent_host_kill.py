"""#239: agent_host.py 子进程超时必杀 + 退出树杀（防 pi sidecar 僵尸/进程树泄漏）。

验收对照：
- 假 CLI 挂死 → run_command wait_for 超时 → finally 必杀整棵进程树，无残留进程
- 退出路径 ``returncode is None``（reader 取消/异常退出时进程仍活着）→ 必杀进程树
- stop_session 必杀进程树（Windows taskkill /T /F，killer 进程需 await，退出需等待）
- 追加验收：重启孤儿终态化 pass / 错误五元组归一 / stderr 尾 800 字截断 / 确定性测试模式

确定性测试模式（对照 ComfyArt FakeProvider gate + stop_requested 注入）：
``tests/stubs/fake_pi.py`` + ``fake_pi_hang.cmd`` 构造真实短命假 sidecar
（cmd.exe -> python -> 可选孙进程，镜像真实 pi 的 Windows 进程树），env 变量即 gate：
FAKE_PI_NO_STATE / FAKE_PI_ECHO_STATE / FAKE_PI_EXIT / FAKE_PI_GRANDCHILD /
FAKE_PI_STDERR_LINES；pidfile 让断言精确验证「顶层 + 孙进程都死、无残留」。
所有 spawned 进程保证在测试结束前被杀干净（S0 subprocess-leak 守卫）。
"""
import asyncio
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main
from firefly_projects import ProjectStore
from app.services import agent_host
from app.services.agent_host import AgentHostManager
from tests.fake_agent_host import STUB_PI_CLI, install_stub_pi_cli, restore_stub_pi_cli

FAKE_PI_HANG_CMD = Path(__file__).resolve().parent / "stubs" / "fake_pi_hang.cmd"


def pid_alive(pid: int) -> bool:
    """无第三方依赖的进程存活检查（口径同 tools/quality_gate.py process_is_running）。"""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        synchronize = 0x00100000
        wait_timeout = 0x00000102
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.WaitForSingleObject.argtypes = (ctypes.c_uint32, ctypes.c_uint32)
        kernel32.WaitForSingleObject.restype = ctypes.c_uint32
        kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
        handle = kernel32.OpenProcess(synchronize, False, pid)
        if not handle:
            return False
        try:
            return kernel32.WaitForSingleObject(handle, 0) == wait_timeout
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False
    return True


def wait_until(predicate, timeout: float = 10.0, interval: float = 0.05) -> bool:
    """轮询断言条件（杀进程 + 退出码落定需要少量时间）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if predicate():
                return True
        except Exception:
            pass
        _LOOP_PUMP[0] = interval
        time.sleep(0)
        _pump_loop()
    try:
        return bool(predicate())
    except Exception:
        return False


_LOOP_PUMP: list = [None]  # [interval]；测试基类注入 loop 后，轮询间隙泵 event loop


def _pump_loop() -> None:
    """真实服务里 event loop 一直转；单测中轮询是同步的，必须显式泵一下，
    后台任务（reader finally / idle watchdog）才能推进。"""
    import main as _main  # noqa: F401
    loop = _AGENT_LOOP[0]
    interval = _LOOP_PUMP[0]
    if loop is None or interval is None or loop.is_closed():
        return
    try:
        loop.run_until_complete(asyncio.sleep(interval))
    except RuntimeError:
        pass
    except Exception:
        pass


_PREVIOUS_PI_CLI = None
_AGENT_LOOP: list = [None]


def setUpModule():
    """#179 同款纪律：本模块绝不允许解析到真实 pi CLI（"pi" -> pi.cmd）。"""
    global _PREVIOUS_PI_CLI
    _PREVIOUS_PI_CLI = install_stub_pi_cli()


def tearDownModule():
    restore_stub_pi_cli(_PREVIOUS_PI_CLI)


class AgentHostKillBase(unittest.TestCase):
    """公共装置：隔离 ProjectStore + 短命假 sidecar + 关掉自愈 respawn（避免断言残留被重生进程污染）。"""

    fake_env = {}

    def setUp(self):
        super().setUp()
        self.temp_dir = tempfile.TemporaryDirectory(prefix="firefly-agent-kill-")
        self.addCleanup(self.temp_dir.cleanup)
        _AGENT_LOOP[0] = None  # 本测试用例尚无 loop
        self._previous_store = getattr(main, "FIREFLY_PROJECT_STORE", None)
        self.addCleanup(self._restore_store)
        self.store = ProjectStore(Path(self.temp_dir.name) / "projects", snapshot_delay=0)
        main.FIREFLY_PROJECT_STORE = self.store
        self.project = self.store.create_project("Agent Kill Test")
        self.project_id = self.project["id"]
        self.manager = AgentHostManager()
        # 确定性模式：禁用自愈 respawn，让「无残留」断言精确（respawn 本身有独立回归用例）
        self._previous_constants = {}
        self._patch("MAX_CONSECUTIVE_RESPAWNS", 0)
        self.addCleanup(self._restore_constants)
        # PI_CLI 指向假 sidecar（stub 层保证不会落到真实 pi）
        self._previous_pi_cli = agent_host.PI_CLI
        self.addCleanup(self._restore_pi_cli)
        self._previous_pi_env = os.environ.get("FIREFLY_PI_CLI")
        agent_host.PI_CLI = str(FAKE_PI_HANG_CMD)
        os.environ["FIREFLY_PI_CLI"] = str(FAKE_PI_HANG_CMD)
        self.addCleanup(self._restore_pi_env)
        # env 门控
        self.env_backup = {}
        for key, value in self.fake_env.items():
            self._set_fake_env(key, value)
        self.addCleanup(self._restore_env)
        # 保存主线程当前 loop：本模块用完必须原样恢复（置 None 会击穿
        # test_agent_tui 等依赖 asyncio.get_event_loop() 的既有模块）
        self._previous_current_loop = asyncio.get_event_loop()
        self.loop = asyncio.new_event_loop()
        _AGENT_LOOP[0] = self.loop
        self.addCleanup(self._close_loop)

    # -- 装置辅助 --

    def _patch(self, name: str, value) -> None:
        # setdefault：同一名二次 patch（如测试体内再收紧上限）不得覆盖首次记录的原值
        self._previous_constants.setdefault(name, getattr(agent_host, name))
        setattr(agent_host, name, value)

    def _set_fake_env(self, key: str, value: str) -> None:
        self.env_backup.setdefault(key, os.environ.get(key))
        os.environ[key] = value

    def _restore_store(self):
        main.FIREFLY_PROJECT_STORE = self._previous_store

    def _restore_constants(self):
        for name, value in self._previous_constants.items():
            setattr(agent_host, name, value)

    def _restore_pi_cli(self):
        agent_host.PI_CLI = str(STUB_PI_CLI)

    def _restore_pi_env(self):
        if self._previous_pi_env is None:
            os.environ.pop("FIREFLY_PI_CLI", None)
        else:
            os.environ["FIREFLY_PI_CLI"] = self._previous_pi_env

    def _restore_env(self):
        for key, value in self.env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _close_loop(self):
        # 最后保险：无论测试成败，清杀所有仍存活的假 sidecar（S0 subprocess-leak 守卫）
        try:
            for session in list(self.manager.sessions.values()):
                try:
                    self.loop.run_until_complete(self.manager.stop_session(session))
                except Exception:
                    pass
        finally:
            previous = self._previous_current_loop
            try:
                self.loop.close()
            finally:
                asyncio.set_event_loop(previous if not previous.is_closed() else asyncio.new_event_loop())

    def _run(self, coro):
        return self.loop.run_until_complete(coro)

    def _session(self, node_id: str = "node-1"):
        return self._run(self.manager.get_or_create_session(self.project_id, node_id))

    def _spawn(self, session) -> bool:
        return self._run(self.manager.spawn_session(session))

    def _read_pid(self, name: str) -> int:
        path = Path(self.temp_dir.name) / name
        self.assertTrue(
            wait_until(lambda: path.exists() and path.read_text().strip(), timeout=15.0),
            f"pidfile {path} 未被写入",
        )
        return int(path.read_text().strip())


class TestTimeoutKillsProcessTree(AgentHostKillBase):
    """验收核心：假 CLI 挂死 → 必杀无残留进程。"""

    fake_env = {"FAKE_PI_GRANDCHILD": "1"}

    def setUp(self):
        super().setUp()
        self._set_fake_env("FAKE_PI_PIDFILE", str(Path(self.temp_dir.name) / "fake.pid"))
        self._set_fake_env("FAKE_PI_GRANDCHILD_PIDFILE", str(Path(self.temp_dir.name) / "gchild.pid"))

    def test_hung_cli_timeout_kills_tree_with_no_residuals(self):
        session = self._session()
        self.assertTrue(self._spawn(session))
        fake_pid = self._read_pid("fake.pid")
        gchild_pid = self._read_pid("gchild.pid")
        self.assertTrue(pid_alive(fake_pid))
        self.assertTrue(pid_alive(gchild_pid))

        # 挂死的 CLI：run_command wait_for 超时 → finally 必杀整棵进程树
        response = self._run(self.manager.run_command(session, {"type": "fork", "entryId": "entry-1"}, timeout=1.5))
        self.assertIsNone(response)
        self.assertEqual(session.result, {"status": "error", "error_kind": "timeout_hard"})

        # 无残留：顶层 python 与孙进程都必须已死，退出码落定
        self.assertTrue(
            wait_until(lambda: not pid_alive(fake_pid) and not pid_alive(gchild_pid), timeout=10.0),
            f"残留进程: fake={fake_pid} grandchild={gchild_pid}",
        )
        self.assertTrue(
            wait_until(lambda: session.status == "crashed" and session.process is not None and session.process.poll() is not None, timeout=10.0),
            "会话未落到 crashed 终态",
        )
        # respawn 已禁用（MAX=0）：不得出现新进程
        self.assertFalse(session.is_running)


class TestStopSessionKillsTree(AgentHostKillBase):
    """stop_session：terminate 宽限 → 超时树杀 → await killer + 等待退出，无僵尸。"""

    fake_env = {"FAKE_PI_GRANDCHILD": "1"}

    def setUp(self):
        super().setUp()
        self._set_fake_env("FAKE_PI_PIDFILE", str(Path(self.temp_dir.name) / "fake.pid"))
        self._set_fake_env("FAKE_PI_GRANDCHILD_PIDFILE", str(Path(self.temp_dir.name) / "gchild.pid"))

    def test_stop_session_kills_whole_tree(self):
        session = self._session()
        self.assertTrue(self._spawn(session))
        fake_pid = self._read_pid("fake.pid")
        gchild_pid = self._read_pid("gchild.pid")

        self._run(self.manager.stop_session(session))

        self.assertEqual(session.status, "stopped")
        self.assertIsNone(session.process)
        self.assertEqual(session.result, {"status": "aborted", "error_kind": "stopped"})
        self.assertTrue(
            wait_until(lambda: not pid_alive(fake_pid) and not pid_alive(gchild_pid), timeout=10.0),
            f"stop 后残留进程: fake={fake_pid} grandchild={gchild_pid}",
        )


class TestReaderExitKillsLiveProcess(AgentHostKillBase):
    """退出路径 returncode is None → 必杀进程树（reader 取消/异常退出时进程仍活着）。"""

    fake_env = {"FAKE_PI_GRANDCHILD": "1"}

    def setUp(self):
        super().setUp()
        self._set_fake_env("FAKE_PI_PIDFILE", str(Path(self.temp_dir.name) / "fake.pid"))
        self._set_fake_env("FAKE_PI_GRANDCHILD_PIDFILE", str(Path(self.temp_dir.name) / "gchild.pid"))

    def test_reader_exit_with_live_process_kills_tree(self):
        session = self._session()
        self.assertTrue(self._spawn(session))
        fake_pid = self._read_pid("fake.pid")
        gchild_pid = self._read_pid("gchild.pid")

        # 模拟 reader 在进程仍活着时退出（returncode is None）
        reader_task = session._reader_task
        self.assertIsNotNone(reader_task)
        reader_task.cancel()
        self._run(reader_task)  # reader finally 内必杀整棵进程树并等待退出

        self.assertTrue(
            wait_until(lambda: not pid_alive(fake_pid) and not pid_alive(gchild_pid), timeout=10.0),
            f"reader 退出后残留进程: fake={fake_pid} grandchild={gchild_pid}",
        )
        self.assertTrue(
            wait_until(lambda: session.status == "crashed", timeout=10.0),
            "会话未落到 crashed 终态",
        )


class TestSpawnReadyTimeout(AgentHostKillBase):
    """spawn 就绪等待超时：不回报 get_state 的 sidecar 也要被杀，不留僵尸 starting。"""

    fake_env = {"FAKE_PI_NO_STATE": "1"}

    def setUp(self):
        super().setUp()
        self._set_fake_env("FAKE_PI_PIDFILE", str(Path(self.temp_dir.name) / "fake.pid"))

    def test_spawn_ready_timeout_kills_sidecar(self):
        self._patch("RPC_SPAWN_TIMEOUT_SECONDS", 1.5)
        session = self._session()
        self.assertFalse(self._spawn(session))
        fake_pid = self._read_pid("fake.pid")
        self.assertTrue(
            wait_until(lambda: not pid_alive(fake_pid), timeout=10.0),
            f"就绪等待超时后 sidecar 未被必杀: {fake_pid}",
        )
        self.assertFalse(session.is_running)


class TestRunCommandSuccess(AgentHostKillBase):
    """run_command 成功路径：有响应则正常返回，不得误杀。"""

    fake_env = {"FAKE_PI_ECHO_STATE": "1"}

    def setUp(self):
        super().setUp()
        self._set_fake_env("FAKE_PI_PIDFILE", str(Path(self.temp_dir.name) / "fake.pid"))

    def test_run_command_returns_response_without_killing(self):
        session = self._session()
        self.assertTrue(self._spawn(session))
        fake_pid = self._read_pid("fake.pid")
        response = self._run(self.manager.run_command(session, {"type": "get_state"}, timeout=3.0))
        self.assertIsNotNone(response)
        self.assertTrue(response.get("success"))
        self.assertTrue(pid_alive(fake_pid), "成功响应后 sidecar 被误杀")


class TestOrphanReap(AgentHostKillBase):
    """追加验收 1：重启孤儿终态化 pass（进程已逝但 status 仍非终态 → 批量置 aborted）。"""

    def test_reap_terminalizes_dead_running_sessions(self):
        zombie = self._session("orphan-dead")
        zombie.status = "running"  # 模拟重启前遗留的僵尸 running（进程已不在）
        reaped = self._run(self.manager.reap_orphan_sessions())
        self.assertEqual(reaped, 1)
        self.assertEqual(zombie.status, "aborted")
        self.assertEqual(zombie.result, {"status": "aborted", "error_kind": "stopped"})
        # 幂等：二次 pass 不再动作
        self.assertEqual(self._run(self.manager.reap_orphan_sessions()), 0)

    def test_reap_leaves_live_sessions_alone(self):
        session = self._session("live-node")
        self.assertTrue(self._spawn(session))
        self.assertEqual(self._run(self.manager.reap_orphan_sessions()), 0)
        self.assertEqual(session.status, "running")


class TestIdleWatchdog(AgentHostKillBase):
    """追加验收 1 的持续面：busy 会话 15min 无活动（测试中压到 0.5s）→ 树杀 + timeout_idle。"""

    def test_idle_busy_session_is_killed_with_timeout_idle(self):
        self._patch("IDLE_TIMEOUT_SECONDS", 0.5)
        self._set_fake_env("FAKE_PI_PIDFILE", str(Path(self.temp_dir.name) / "fake.pid"))
        session = self._session()
        self.assertTrue(self._spawn(session))
        fake_pid = self._read_pid("fake.pid")
        session.status = "busy"
        session._last_activity = time.time() - 10

        self._run(self.manager.start_idle_watchdog(interval=0.1))
        self.addCleanup(self._stop_watchdog)

        self.assertTrue(
            wait_until(lambda: session.status == "crashed" and not pid_alive(fake_pid), timeout=10.0),
            "idle 挂死的 busy 会话未被 reap",
        )
        self.assertEqual(session.result, {"status": "error", "error_kind": "timeout_idle"})

    def _stop_watchdog(self):
        self.loop.run_until_complete(self.manager.stop_idle_watchdog())


class TestStderrTail(AgentHostKillBase):
    """追加验收 3：stderr 尾截断（deque 只留尾 800 字）+ drain 线程防管道背压挂死。"""

    fake_env = {"FAKE_PI_STDERR_LINES": "100"}  # 100 行 × 81 字节 = 8100 字 > 管道缓冲区

    def test_stderr_tail_keeps_last_800_chars(self):
        session = self._session()
        self.assertTrue(self._spawn(session), "spawn 不应被 stderr 背压卡死（drain 线程必须在工作）")
        expected = ("".join(f"{i:04d}" + "e" * 76 + "\n" for i in range(100)))[-800:]
        self.assertTrue(wait_until(lambda: len(session.stderr_tail) >= 800, timeout=10.0), "stderr 尾未排空")
        self.assertEqual(session.stderr_tail, expected)
        self.assertEqual(len(session.stderr_tail), 800)


class TestResultQuintuple(AgentHostKillBase):
    """追加验收 2：错误五元组 {status ∈ done|aborted|error, error_kind ∈ timeout_idle|timeout_hard|crash|stopped|llm_error}。"""

    def test_llm_error_event_normalizes_result(self):
        session = self._session()
        self._run(self.manager._dispatch_event(session, {"type": "error", "error": "boom"}))
        self.assertEqual(session.result, {"status": "error", "error_kind": "llm_error"})

    def test_crash_exit_normalizes_result(self):
        self._set_fake_env("FAKE_PI_EXIT", "3")
        self._set_fake_env("FAKE_PI_PIDFILE", str(Path(self.temp_dir.name) / "fake.pid"))
        session = self._session()
        self.assertTrue(self._spawn(session))
        self.assertTrue(
            wait_until(lambda: session.status == "crashed" and session.process is not None and session.process.poll() is not None, timeout=15.0),
            "崩溃退出的会话未落到 crashed 终态",
        )
        self.assertEqual(session.process.poll(), 3)
        self.assertEqual(session.result, {"status": "error", "error_kind": "crash"})


class TestAutoRespawnCap(AgentHostKillBase):
    """回归：崩溃自愈 respawn 保留（--continue），但连续崩溃超上限必须停止并终态化。"""

    def test_crash_loop_is_capped_and_finalized(self):
        self._patch("MAX_CONSECUTIVE_RESPAWNS", 2)
        self._set_fake_env("FAKE_PI_EXIT", "1")
        session = self._session()
        self.assertTrue(self._spawn(session))
        self.assertTrue(
            wait_until(
                lambda: session.status == "crashed" and session._respawn_count == 2
                and session.process is not None and session.process.poll() is not None,
                timeout=30.0,
            ),
            f"respawn 上限未收敛: count={session._respawn_count} status={session.status}",
        )
        self.assertEqual(session.result, {"status": "error", "error_kind": "crash"})
        self.assertFalse(session.is_running)


if __name__ == "__main__":
    unittest.main()
