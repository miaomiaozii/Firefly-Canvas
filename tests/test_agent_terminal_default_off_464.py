"""Terminal admission is independent of read-only file sandboxing (#464)."""
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services import agent_host
from app.services.agent_adapters import CodexAdapter, PiAdapter, SpawnParams

MCP_URL = "http://127.0.0.1:3198/api/mcp?project=p&node=n"
PI_TOOLS = {
    "canvas.*", "generate.*", "media.*", "viewport.*", "agent.*",
    "plan.*", "ops.*", "chat.*", "read", "write", "edit",
}


def _terminal_default_off_params(tmp_path, host, **overrides):
    sessions = tmp_path / "sessions"
    sessions.mkdir(exist_ok=True)
    values = dict(
        resolved_cli=str(tmp_path / f"fixture-{host}.exe"),
        session_dir=str(sessions),
        system_prompt="Use Firefly tools.",
        tools_path=str(ROOT / "app" / "agent_extensions" / "canvas-tools.ts"),
        node_id="node-464",
        agent_cli=host,
    )
    values.update(overrides)
    return SpawnParams(**values)


@pytest.mark.parametrize("thread_id", [None, "thread-464"], ids=["first", "resume"])
@pytest.mark.parametrize("mcp_url", [None, MCP_URL], ids=["no-mcp", "mcp"])
@pytest.mark.parametrize("effort", [None, "high"], ids=["default", "explicit-effort"])
def test_codex_read_only_also_disables_native_terminal(
    tmp_path, monkeypatch, thread_id, mcp_url, effort,
):
    params = _terminal_default_off_params(tmp_path, "codex", mcp_url=mcp_url, reasoning_effort=effort)
    monkeypatch.setattr(
        "app.services.agent_adapters.codex_adapter.shutil.which",
        lambda name: params.resolved_cli,
    )
    adapter = CodexAdapter()
    argv = (adapter.build_argv(params) if thread_id is None
            else adapter.build_resume_argv(params, thread_id))
    configs = [argv[index + 1] for index, value in enumerate(argv[:-1]) if value == "-c"]
    assert [value for value in configs if value.startswith("features.shell_tool=")] == [
        "features.shell_tool=false",
    ]
    assert argv[argv.index("-s") + 1] == "read-only"
    assert argv.count("-s") == 1
    assert argv[-2:] == ["--json", "-"]
    assert agent_host.finalize_spawn_argv(argv) == argv
    if thread_id is not None:
        resume = argv.index("resume")
        assert argv[resume + 1] == thread_id
        assert argv.index("features.shell_tool=false") < resume
        assert argv.index("-s") < resume
    else:
        assert "resume" not in argv
    if mcp_url is not None:
        assert 'mcp_servers.firefly.default_tools_approval_mode="approve"' in configs
    else:
        assert not any(value.startswith("mcp_servers.") for value in configs)
    if effort is None:
        assert not any(value.startswith("model_reasoning_effort=") for value in configs)
    else:
        assert 'model_reasoning_effort="high"' in configs


def test_codex_missing_cli_does_not_fall_back_to_a_shell(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.agent_adapters.codex_adapter.shutil.which", lambda _: None)
    params = _terminal_default_off_params(tmp_path, "codex")
    adapter = CodexAdapter()
    assert adapter.build_argv(params) is None
    assert adapter.build_resume_argv(params, "thread-464") is None


@pytest.mark.parametrize("binding", ["fresh", "continued", "pinned"])
def test_pi_only_allows_business_and_guarded_file_tools(tmp_path, monkeypatch, binding):
    params = _terminal_default_off_params(tmp_path, "pi")
    if binding == "continued":
        (Path(params.session_dir) / "session.jsonl").write_text("{}\n", encoding="utf-8")
    elif binding == "pinned":
        params.pi_session_id = "00000000-0000-4000-8000-000000000464"
    monkeypatch.setattr(
        agent_host, "build_pi_launch_command", lambda cli, args: [cli, *args],
    )
    argv = PiAdapter().build_argv(params)
    assert argv.count("--no-builtin-tools") == 1
    assert argv.count("--tools") == 1
    assert set(argv[argv.index("--tools") + 1].split(",")) == PI_TOOLS
    assert "--no-extensions" in argv
    assert argv[argv.index("-e") + 1] == params.tools_path
    assert not {"bash", "powershell", "PowerShell", "codemode", "exec", "shell.run", "*"} & set(argv)
    if binding == "continued":
        assert "--continue" in argv
    elif binding == "pinned":
        assert argv[argv.index("--session-id") + 1] == params.pi_session_id


def test_pi_unresolved_launcher_does_not_fall_back_to_a_shell(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_host, "build_pi_launch_command", lambda *_: None)
    assert PiAdapter().build_argv(_terminal_default_off_params(tmp_path, "pi")) is None
