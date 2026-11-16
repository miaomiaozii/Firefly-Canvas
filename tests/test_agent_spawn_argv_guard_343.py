"""#343 guards: agent 子进程启动参数校验 + 不经 cmd.exe + MCP/pi 建节点不接受调用方 id.

覆盖票面三点与两条回归（issue #343）：
1. 含 shell 元字符的 node_id / model / 会话 id：创建入口 400、set_model 400、
   spawn 前拒绝（不 Popen、last_error 点名）；codex 会话 validate 即 crashed。
2. spawn argv 不经 cmd.exe：npm .cmd shim 解成 node.exe+脚本（钉真实 pi.cmd）；
   非规范包装器拒绝启动（finalize_spawn_argv 抛 ValueError，不发 Popen）。
3. canvas.create_node（REST 工具面 + MCP 面）：调用方传任何 id 一律 400，
   服务端自建 id 正常。
4. 回归：合法 node id（uuid8-4-4-4-12 / hex32 / node-前缀）与模型名
   （provider/id、带 . -）全部通过校验。

纪律：self-loop + run_until_complete，不碰全局 set_event_loop（#179）。
"""
import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main
from firefly_projects import ProjectStore
from app.services import agent_host
from app.services.agent_host import (
    AgentHostManager,
    finalize_spawn_argv,
    resolve_npm_cmd_wrapper,
    validate_spawn_arg,
)


class ValidateSpawnArgTests(unittest.TestCase):
    """严格白名单：shell 元字符、空白、路径穿越一律拒。"""

    META = [
        'x & calc', 'a|b', 'a>b', 'a<b', 'a^b', 'a&b', 'a;b', 'a\nb', 'a b',
        '"quoted"', "'q'", 'a$(id)', 'a`x`', 'a(b)', '%VAR%', '..\\..\\x',
        'a/b', 'a\\b', 'a|more', '^&', '&&whoami', 'a!bang',
    ]

    def test_rejects_shell_metachars(self):
        for bad in self.META:
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    validate_spawn_arg("node_id", bad)

    def test_rejects_model_metachars(self):
        for bad in ['m & calc', 'm|ls', 'm`x`', 'm$(id)', 'm v2', 'm"q"', 'm^a']:
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    validate_spawn_arg("model", bad, model=True)

    def test_accepts_legit_ids(self):
        for ok in [
            "01a1e05c-874a-4a9d-9519-af0293905c28",   # 8-4-4-4-12 uuid
            "c0c7341819af4131807e35de9a9d8e4a",       # hex32
            "node-1a2b3c4d5e6f",                      # create_node_core 生成形态
            "agent_probe2", "node.a_1",
        ]:
            with self.subTest(ok=ok):
                validate_spawn_arg("node_id", ok)

    def test_accepts_legit_models(self):
        for ok in [
            "gpt-5.1-codex", "claude-sonnet-4.5",
            "openai-codex/gpt-5.1-codex",             # provider/model（含 /）
            "vertex_ai/gemini-3-pro-preview",
            "kimi-k2:free",
        ]:
            with self.subTest(ok=ok):
                validate_spawn_arg("model", ok, model=True)


class ResolveNpmCmdWrapperTests(unittest.TestCase):
    """npm shim 解析：规范模板成功；非模板/缺脚本回 None。"""

    def test_real_pi_wrapper_on_disk(self):
        """本机真实 pi.cmd（npm 形态）能解成 node.exe + dist/index.js。"""
        wrapper = Path(r"C:\Users\Administrator\AppData\Roaming\npm\pi.cmd")
        if not wrapper.is_file():
            self.skipTest("real pi.cmd not installed")
        base = resolve_npm_cmd_wrapper(str(wrapper))
        self.assertIsNotNone(base)
        self.assertTrue(base[0].lower().endswith("node.exe"))
        self.assertTrue(base[1].endswith(".js"))

    def test_non_npm_stub_returns_none(self):
        stub = Path(__file__).resolve().parent / "stubs" / "pi_cli_stub.cmd"
        self.assertIsNone(resolve_npm_cmd_wrapper(str(stub)))

    def test_finalize_passthrough_plain_exe(self):
        self.assertEqual(finalize_spawn_argv(["x.exe", "--a"]), ["x.exe", "--a"])

    def test_finalize_refuses_unresolvable_wrapper(self):
        stub = Path(__file__).resolve().parent / "stubs" / "pi_cli_stub.cmd"
        with self.assertRaises(ValueError):
            finalize_spawn_argv([str(stub), "--mode", "rpc"])


class SpawnGuardIntegrationTests(unittest.TestCase):
    """spawn 前置校验：违规 id 不进 argv、不发 Popen；codex validate 即 crashed。"""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="firefly-343-")
        self.addCleanup(self.temp_dir.cleanup)
        self.store = ProjectStore(Path(self.temp_dir.name) / "projects", snapshot_delay=0)
        self._prev = getattr(main, "FIREFLY_PROJECT_STORE", None)
        self.addCleanup(lambda: setattr(main, "FIREFLY_PROJECT_STORE", self._prev))
        main.FIREFLY_PROJECT_STORE = self.store
        self.project_id = self.store.create_project("Guard 343")["id"]
        self.manager = AgentHostManager()
        self.loop = asyncio.new_event_loop()
        self.addCleanup(self.loop.close)

    def _run(self, coro):
        return self.loop.run_until_complete(coro)

    def test_pi_spawn_rejects_metachar_node_id_without_popen(self):
        evil = "n1 & calc.exe"
        session = self._run(self.manager.get_or_create_session(self.project_id, evil))
        popen = mock.MagicMock(side_effect=AssertionError("Popen 不应被调用"))
        with mock.patch.object(agent_host.subprocess, "Popen", popen):
            self.assertFalse(self._run(self.manager.spawn_session(session)))
        popen.assert_not_called()
        self.assertEqual(session.status, "crashed")
        self.assertIn("spawn guard", session.last_error)
        self.assertIn("node_id", session.last_error)

    def test_codex_validate_crashes_on_metachar_model(self):
        session = self._run(self.manager.get_or_create_session(self.project_id, "node-1"))
        session.agent_cli = "codex"
        session.model = "m;whoami"
        self.assertFalse(self._run(self.manager.spawn_session(session)))
        self.assertEqual(session.status, "crashed")
        self.assertIn("model", session.last_error)

    def test_codex_turn_finalizes_without_popen_on_bad_ext_session(self):
        """per-turn 收口：ext_session_id（codex resume 入 argv）违规 → 轮失败不 Popen。"""
        session = self._run(self.manager.get_or_create_session(self.project_id, "node-2"))
        session.agent_cli = "codex"
        session.status = "running"
        session.ext_session_id = "t|calc"
        popen = mock.MagicMock(side_effect=AssertionError("Popen 不应被调用"))
        with mock.patch.object(agent_host.subprocess, "Popen", popen):
            with self.assertRaises(ValueError):
                self._run(self.manager._codex_turn(session, "hi", allow_resume=True))
        popen.assert_not_called()

    def test_codex_turn_launches_node_for_npm_codex_cmd(self):
        """per-turn 收口：npm .cmd 解析成 node+脚本，cmd[0] 不是 cmd。"""
        session = self._run(self.manager.get_or_create_session(self.project_id, "node-3"))
        session.agent_cli = "codex"
        session.status = "running"
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            (base / "codex.cmd").write_text(
                '@ECHO off\r\nendLocal & goto #_undefined_# 2>NUL || title %COMSPEC% & '
                '"%_prog%"  "%dp0%\\codex-pkg\\bin\\codex.js" %*\r\n',
                encoding="utf-8",
            )
            script = base / "codex-pkg" / "bin" / "codex.js"
            script.parent.mkdir(parents=True, exist_ok=True)
            script.write_text("// fake", encoding="utf-8")

            captured = {}
            fake = mock.MagicMock()
            fake.poll.return_value = None
            fake.stdin = mock.MagicMock()
            fake.stdout = mock.MagicMock()
            fake.stdout.readline.return_value = ""
            fake.stderr = mock.MagicMock()
            fake.stderr.read.return_value = ""

            def _popen(cmd, **kw):
                captured["cmd"] = list(cmd)
                return fake

            prev = agent_host.shutil.which
            try:
                agent_host.shutil.which = lambda name, *a, **k: (
                    str(base / "codex.cmd") if name == "codex" else prev(name, *a, **k)
                )
                with mock.patch.object(agent_host.subprocess, "Popen", side_effect=_popen):
                    self._run(self.manager._codex_turn(session, "hi", allow_resume=False))
            except Exception as _e:
                captured["exc"] = repr(_e)  # 假进程无事件流，reader 失败不影响 argv 断言
            finally:
                agent_host.shutil.which = prev
        cmd = captured.get("cmd") or []
        self.assertTrue(cmd, f"Popen 未被调用；turn 异常：{captured.get('exc')}")
        self.assertNotEqual(cmd[0], "cmd")
        self.assertTrue(cmd[0].lower().endswith("node.exe"))
        self.assertEqual(os.path.normcase(os.path.normpath(cmd[1])), os.path.normcase(os.path.normpath(str(script))))


def os_path_normpath(p):
    import os
    return os.path.normpath(p)


class ApiValidation343Tests(unittest.TestCase):
    """HTTP 创建入口：违规 node_id/model → 400；create_node 传 id → 一律 400。"""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="firefly-343-api-")
        self.addCleanup(self.temp_dir.cleanup)
        self.store = ProjectStore(Path(self.temp_dir.name) / "projects", snapshot_delay=0)
        self._prev = main.FIREFLY_PROJECT_STORE
        main.FIREFLY_PROJECT_STORE = self.store
        self.client = TestClient(main.app, base_url="http://127.0.0.1:8125",
                                 headers={"origin": "http://127.0.0.1:8125"})
        self.pid = self.store.create_project("Guard 343 API")["id"]

    def tearDown(self):
        main.FIREFLY_PROJECT_STORE = self._prev

    def test_create_agent_node_rejects_metachar_id_and_model(self):
        r = self.client.post(
            f"/api/projects/{self.pid}/agent-nodes",
            json={"node_id": "n & calc", "model": "gpt-5.1"},
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn("node_id", r.text)
        r = self.client.post(
            f"/api/projects/{self.pid}/agent-nodes",
            json={"node_id": "ok-node", "model": "gpt|calc"},
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn("model", r.text)

    def test_set_model_rejects_metachar(self):
        self.store.apply_mutations(self.pid, [
            {"op": "upsert_node", "node": {"id": "agent-1", "type": "agent",
                                           "x": 0, "y": 0}},
        ], base_revision=0)
        r = self.client.post(
            f"/api/projects/{self.pid}/agent-nodes/agent-1/model",
            json={"model": "a`whoami`"},
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn("model", r.text)

    def test_create_node_rejects_any_caller_id(self):
        r = self.client.post(
            f"/api/projects/{self.pid}/canvas/nodes",
            json={"node": {"id": "whatever-1", "type": "smart-prompt"}},
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn("#343", r.text)

    def test_create_node_server_generates_id(self):
        r = self.client.post(
            f"/api/projects/{self.pid}/canvas/nodes",
            json={"node": {"type": "smart-prompt", "title": "T"}},
        )
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["node_id"].startswith("node-"))

    def test_mcp_create_node_rejects_caller_id(self):
        # MCP 网关层：调用方塞 id（无论 node 对象还是平铺）→ create_node_core 升位拒 400。
        # 直接测 _impl（HTTP tools/call 需打开标签，属 338 网关语义，不在本断言范围）。
        import asyncio as _a
        from app.routers import api_mcp
        from fastapi import HTTPException
        for args in ({"node": {"id": "evil", "type": "smart-prompt"}},
                     {"type": "smart-prompt", "id": "evil"}):
            with self.subTest(args=args):
                with self.assertRaises(HTTPException) as ctx:
                    _a.run(api_mcp._impl_create_node(self.pid, "", args))
                self.assertEqual(ctx.exception.status_code, 400)
                self.assertTrue("#343" in str(ctx.exception.detail)
                                or "caller" in str(ctx.exception.detail).lower(),
                                str(ctx.exception.detail))


if __name__ == "__main__":
    unittest.main()
