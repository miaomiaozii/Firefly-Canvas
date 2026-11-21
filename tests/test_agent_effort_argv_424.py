"""#424 gate 1: effort serialization only; no CLI processes or host services.

Capability/config services own supported-value validation. These tests assert
argv/default compatibility, not whether a host/model applies reasoning effort.
"""
import sys
import tempfile
import unittest
from dataclasses import fields
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services import agent_host
from app.services.agent_adapters import CodexAdapter, PiAdapter, SpawnParams
from app.services.agent_adapters.codex_adapter import CANVAS_INSTRUCTIONS, insert_ref_images

PI_CLI = "C:/cli/pi.exe"
CODEX_CLI = "C:/cli/codex.exe"
TOOLS_PATH = "C:/canvas tools/canvas-tools.js"
MCP_URL = "http://127.0.0.1:9999/mcp?project=p&node=n"


def _params(session_dir, **overrides):
    values = dict(
        resolved_cli=PI_CLI,
        session_dir=session_dir,
        system_prompt="SYS PROMPT TEXT",
        tools_path=TOOLS_PATH,
        node_id="node-123456789",
    )
    values.update(overrides)
    return SpawnParams(**values)


def _pi_prefix(session_dir):
    return [
        PI_CLI, "--mode", "rpc", "--no-extensions", "-e", TOOLS_PATH,
        # 原生 --tools 也过滤扩展；#445 只允许共享业务命名空间和受限文件工具。
        "--no-builtin-tools", "--tools", "canvas.*,generate.*,media.*,viewport.*,agent.*,plan.*,ops.*,chat.*,read,write,edit",
        "--session-dir", session_dir, "--name", "firefly-agent-node-123",
    ]


def _codex_prefix():
    return [
        CODEX_CLI, "exec", "-s", "read-only",
        "-c", 'developer_instructions="SYS PROMPT TEXT"',
        "-c", 'mcp_servers.firefly.url="' + MCP_URL + '"',
        "-c", 'mcp_servers.firefly.default_tools_approval_mode="approve"',
    ]


class SpawnParamsCompatibilityTests(unittest.TestCase):
    def test_original_defaults_and_appended_optional_field(self):
        params = SpawnParams("pi.exe", ".", "SYS", "tools.js", "node-1")
        defaults = dict(
            model="", continue_session=False, startup_timeout_s=60.0,
            agent_cli="pi", ext_session_id=None, mcp_url=None,
            system_prompt_file="", pi_session_id="", reasoning_effort=None,
        )
        for name, value in defaults.items():
            with self.subTest(field=name):
                self.assertEqual(getattr(params, name), value)
        self.assertEqual(fields(SpawnParams)[-1].name, "reasoning_effort")
        self.assertIsNone(fields(SpawnParams)[-1].default)

    def test_all_legacy_positional_arguments_keep_their_meaning(self):
        names = (
            "resolved_cli", "session_dir", "system_prompt", "tools_path", "node_id",
            "model", "continue_session", "startup_timeout_s", "agent_cli",
            "ext_session_id", "mcp_url", "system_prompt_file", "pi_session_id",
        )
        values = (
            "pi.exe", "sessions", "SYS", "tools.js", "node-1", "provider/model",
            True, 17.0, "codex", "thread-1", MCP_URL, "system-prompt.txt", "pi-1",
        )
        self.assertEqual(tuple(f.name for f in fields(SpawnParams)), (*names, "reasoning_effort"))
        params = SpawnParams(*values)
        self.assertEqual(tuple(getattr(params, name) for name in names), values)
        self.assertIsNone(params.reasoning_effort)
        self.assertEqual(SpawnParams(*values, "high").reasoning_effort, "high")


class ArgvFixture(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="agent-effort-424-")
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)
        self.session_dir = str(self.directory)
        # Even an accidental future spawn must not reach an actual CLI.
        popen = mock.patch.object(
            agent_host.subprocess, "Popen", side_effect=AssertionError("argv tests must not spawn"))
        popen.start()
        self.addCleanup(popen.stop)

    def npm_wrapper(self, name):
        wrapper = self.directory / (name + ".cmd")
        script = self.directory / "pkg" / "bin" / (name + ".js")
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text("// inert test fixture", encoding="utf-8")
        node = self.directory / "node.exe"
        node.touch()
        wrapper.write_text(
            '@ECHO off\n"%_prog%" "%dp0%\\pkg\\bin\\' + name + '.js" %*\n',
            encoding="utf-8",
        )
        return str(wrapper), str(node), str(script)


class PiEffortArgvTests(ArgvFixture):
    def test_default_and_explicit_none_preserve_exact_argv(self):
        expected = _pi_prefix(self.session_dir) + ["--system-prompt", "SYS PROMPT TEXT"]
        for override in ({}, {"reasoning_effort": None}):
            with self.subTest(override=override):
                argv = PiAdapter().build_argv(_params(self.session_dir, **override))
                self.assertEqual(argv, expected)
                self.assertEqual(agent_host.finalize_spawn_argv(argv), expected)

    def test_effort_after_deterministic_session_and_provider_before_prompt_file(self):
        (self.directory / "previous.jsonl").touch()
        prompt_file = str(self.directory / "prompt with spaces.txt")
        # None alone means no override; adapters do not own an effort enum.
        for effort in ("high", "", "future-effort"):
            with self.subTest(effort=effort):
                params = _params(
                    self.session_dir, pi_session_id="pi-session-424", continue_session=True,
                    model="openai/gpt-5.1", reasoning_effort=effort,
                    system_prompt="first\nsecond", system_prompt_file=prompt_file,
                )
                expected = _pi_prefix(self.session_dir) + [
                    "--session-id", "pi-session-424", "--provider", "openai", "--model", "gpt-5.1",
                    "--thinking", effort, "--system-prompt", prompt_file,
                ]
                self.assertEqual(PiAdapter().build_argv(params), expected)

    def test_continue_and_bare_model_stay_before_effort_and_inline_prompt(self):
        for explicit_continue in (True, False):
            if not explicit_continue:
                (self.directory / "previous.jsonl").touch()
            with self.subTest(explicit_continue=explicit_continue):
                params = _params(
                    self.session_dir, continue_session=explicit_continue, model="model-id",
                    reasoning_effort="high", system_prompt="first\nsecond",
                )
                self.assertEqual(PiAdapter().build_argv(params), _pi_prefix(self.session_dir) + [
                    "--continue", "--model", "model-id", "--thinking", "high",
                    "--system-prompt", "first\nsecond",
                ])

    def test_windows_npm_wrapper_and_prompt_safety_unchanged(self):
        wrapper, node, script = self.npm_wrapper("pi")
        prompt_file = str(self.directory / "prompt with spaces.txt")
        for file_arg in (prompt_file, ""):
            with self.subTest(system_prompt_file=file_arg):
                params = _params(
                    self.session_dir, resolved_cli=wrapper, pi_session_id="pi-session-424",
                    model="provider/model-id", reasoning_effort="high",
                    system_prompt="first\nsecond", system_prompt_file=file_arg,
                )
                expected = [node, script, *_pi_prefix(self.session_dir)[1:]] + [
                    "--session-id", "pi-session-424", "--provider", "provider", "--model", "model-id",
                    "--thinking", "high", "--system-prompt", file_arg or "first second",
                ]
                argv = PiAdapter().build_argv(params)
                self.assertEqual(argv, expected)
                self.assertEqual(agent_host.finalize_spawn_argv(argv), expected)
                self.assertTrue(all("\n" not in arg for arg in argv))

    def test_invalid_wrapper_still_refused(self):
        wrapper = self.directory / "invalid.cmd"
        wrapper.write_text("@echo off\necho not an npm shim\n", encoding="utf-8")
        self.assertIsNone(PiAdapter().build_argv(_params(
            self.session_dir, resolved_cli=str(wrapper), reasoning_effort="high")))
        with self.assertRaisesRegex(ValueError, "refusing to launch via cmd.exe"):
            agent_host.finalize_spawn_argv([str(wrapper), "--thinking", "high"])

    def test_missing_cli_still_returns_none(self):
        self.assertIsNone(PiAdapter().build_argv(_params(
            self.session_dir, resolved_cli=None, reasoning_effort="high")))


class CodexEffortArgvTests(ArgvFixture):
    def setUp(self):
        super().setUp()
        which = mock.patch(
            "app.services.agent_adapters.codex_adapter.shutil.which", return_value=CODEX_CLI)
        self.which = which.start()
        self.addCleanup(which.stop)
        self.adapter = CodexAdapter()

    def params(self, **overrides):
        values = dict(resolved_cli=CODEX_CLI, agent_cli="codex", mcp_url=MCP_URL)
        values.update(overrides)
        return _params(self.session_dir, **values)

    def build(self, params, resume):
        if resume:
            return self.adapter.build_resume_argv(params, "thread-424")
        return self.adapter.build_argv(params)

    def tail(self, resume):
        return ["--skip-git-repo-check"] + (
            ["resume", "thread-424", "--json", "-"] if resume else ["--json", "-"])

    def test_default_and_explicit_none_preserve_first_and_resume_exact_argv(self):
        for resume in (False, True):
            for override in ({}, {"reasoning_effort": None}):
                with self.subTest(resume=resume, override=override):
                    expected = _codex_prefix() + self.tail(resume)
                    argv = self.build(self.params(**override), resume)
                    self.assertEqual(argv, expected)
                    self.assertEqual(agent_host.finalize_spawn_argv(argv), expected)

    def test_effort_config_before_exec_tail_and_resume(self):
        for resume in (False, True):
            with self.subTest(resume=resume):
                expected = _codex_prefix() + [
                    "-m", "gpt-5.1-codex", "-c", 'model_reasoning_effort="high"',
                ] + self.tail(resume)
                argv = self.build(self.params(model="gpt-5.1-codex", reasoning_effort="high"), resume)
                self.assertEqual(argv, expected)
                self.assertEqual(agent_host.finalize_spawn_argv(argv), expected)

    def test_effort_uses_existing_toml_string_escaping_without_value_validation(self):
        # Deliberately not a capability choice: test serialization, not support.
        cases = (
            ('a"b\\c\nsecond\rline', 'model_reasoning_effort="a\\"b\\\\c\\nsecondline"'),
            ("", 'model_reasoning_effort=""'),
        )
        for effort, serialized in cases:
            for resume in (False, True):
                with self.subTest(effort=effort, resume=resume):
                    self.assertEqual(
                        self.build(self.params(reasoning_effort=effort), resume),
                        _codex_prefix() + ["-c", serialized] + self.tail(resume),
                    )

    def test_empty_prompt_fallback_and_absent_mcp_unchanged(self):
        argv = self.adapter.build_argv(self.params(
            system_prompt="", mcp_url=None, reasoning_effort="high"))
        self.assertEqual(argv, [
            CODEX_CLI, "exec", "-s", "read-only",
            "-c", 'developer_instructions="' + CANVAS_INSTRUCTIONS + '"',
            "-c", 'model_reasoning_effort="high"',
        ] + self.tail(False))

    def test_reference_images_stay_before_stdin_without_mutating_input(self):
        refs = ["C:/refs/a with spaces.png", "C:/refs/b.png"]
        for resume in (False, True):
            for effort in (None, "high"):
                with self.subTest(resume=resume, effort=effort):
                    argv = self.build(self.params(reasoning_effort=effort), resume)
                    original = list(argv)
                    expected = argv[:-1] + ["-i", refs[0], "-i", refs[1], "-"]
                    images = insert_ref_images(argv, refs)
                    self.assertEqual(images, expected)
                    self.assertEqual(argv, original)
                    self.assertIsNot(images, argv)
                    self.assertEqual(agent_host.finalize_spawn_argv(images), expected)
                    self.assertEqual(insert_ref_images(argv, []), argv)
        self.assertEqual(insert_ref_images([CODEX_CLI, "exec"], refs), [CODEX_CLI, "exec"])

    def test_windows_wrapper_finalization_preserves_effort_resume_and_images(self):
        wrapper, node, script = self.npm_wrapper("codex")
        self.which.return_value = wrapper
        for resume in (False, True):
            with self.subTest(resume=resume):
                argv = self.build(self.params(reasoning_effort="high"), resume)
                expected = [wrapper, *_codex_prefix()[1:], "-c", 'model_reasoning_effort="high"']
                expected += self.tail(resume)
                self.assertEqual(argv, expected)
                images = insert_ref_images(argv, ["C:/refs/image.png"])
                self.assertEqual(agent_host.finalize_spawn_argv(images), [
                    node, script, *expected[1:-1], "-i", "C:/refs/image.png", "-",
                ])
                self.assertEqual(argv, expected)

    def test_invalid_wrapper_finalization_still_refused(self):
        wrapper = self.directory / "invalid.bat"
        wrapper.write_text("@echo off\necho not an npm shim\n", encoding="utf-8")
        self.which.return_value = str(wrapper)
        for resume in (False, True):
            with self.subTest(resume=resume):
                argv = self.build(self.params(reasoning_effort="high"), resume)
                with self.assertRaisesRegex(ValueError, "refusing to launch via cmd.exe"):
                    agent_host.finalize_spawn_argv(argv)

    def test_missing_cli_still_returns_none_for_first_and_resume(self):
        self.which.return_value = None
        for resume in (False, True):
            with self.subTest(resume=resume):
                self.assertIsNone(self.build(self.params(reasoning_effort="high"), resume))


if __name__ == "__main__":
    unittest.main()
