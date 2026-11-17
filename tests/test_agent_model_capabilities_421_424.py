"""#421/#424 metadata and gate-2 cache: fake native RPC, never installed CLIs."""
from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from threading import Event, Lock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# Probe launch helpers live in the runtime, whose application reference binds at
# import. Bootstrap the fixture app before importing those helpers.
import main
from app.services import agent_model_capabilities as cap


SECRET = "SYNTHETIC_NOT_A_CREDENTIAL_DO_NOT_EXPOSE"
PI_A = {"provider": "native", "id": "reasoning", "name": "Native reasoning", "apiKey": SECRET}
PI_B = {"provider": "native", "id": "simple", "name": "Native simple"}


def codex_row(model="reasoning", *, levels=("low", "high", "ultra"), default="low", is_default=True, hidden=False):
    return {"id": "directory-" + model, "model": model, "displayName": "Native " + model,
            "hidden": hidden, "isDefault": is_default, "defaultReasoningEffort": default,
            "supportedReasoningEfforts": [{"reasoningEffort": value, "description": "Native description"} for value in levels]}


class FakePipe:
    def __init__(self, process, kind):
        self.process, self.kind, self.closed = process, kind, False
        self.pending = b""

    def fileno(self):
        return 40000 + self.process.pid + (self.kind == "stdout")

    def write(self, data):
        assert not self.closed and self.kind == "stdin"
        if self.process.block_writes:
            return None
        if self.process.broken_write:
            raise BrokenPipeError(SECRET)
        size = min(len(data), self.process.write_chunk)
        self.pending += data[:size]
        while b"\n" in self.pending:
            raw, self.pending = self.pending.split(b"\n", 1)
            frame = json.loads(raw)
            self.process.frames.append(frame)
            self.process.program.respond(self.process, frame)
        return size

    def read(self, size):
        assert not self.closed and self.kind == "stdout"
        if self.process.read_error:
            raise self.process.read_error
        if self.process.output:
            size = min(size, self.process.read_chunk)
            chunk, self.process.output = self.process.output[:size], self.process.output[size:]
            return chunk
        return b"" if self.process.eof else None

    def close(self):
        self.closed = True


class FakeProcess:
    def __init__(self, program, argv, kwargs):
        self.program, self.argv, self.kwargs = program, argv, kwargs
        self.pid = 1000 + len(program.processes)
        self.returncode = None
        self.frames, self.output = [], b""
        self.eof = self.block_writes = self.broken_write = False
        self.read_error = None
        self.read_chunk, self.write_chunk = 65536, 65536
        self.stdin, self.stdout = FakePipe(self, "stdin"), FakePipe(self, "stdout")
        self.waits = []
        self.native_files = {path.name: path.read_bytes() for path in Path(kwargs["env"].get("PI_CODING_AGENT_DIR") or kwargs["env"]["CODEX_HOME"]).iterdir() if path.is_file()}

    def emit(self, value):
        self.output += (json.dumps(value) + "\n").encode()

    def poll(self):
        return self.returncode

    def wait(self, timeout):
        self.waits.append(timeout)
        if self.program.fail_wait:
            raise subprocess.TimeoutExpired("fake native", timeout)
        assert self.returncode is not None
        return self.returncode


class NativeProgram:
    def __init__(self):
        self.processes, self.jobs, self.killed, self.nonblocking = [], [], [], []
        self.pi_models = [deepcopy(PI_A), deepcopy(PI_B)]
        self.pi_default, self.pi_effort = deepcopy(PI_A), "high"
        self.pi_selected = None
        self.pi_levels = {"reasoning": ["off", "low", "high", "xhigh", "ULTRA"], "simple": ["off"]}
        self.config = {"model": "reasoning", "model_reasoning_effort": None, "auth": SECRET}
        self.pages = [{"data": [codex_row(), codex_row("simple", levels=("none",), default="none", is_default=False)], "nextCursor": None}]
        self.hook = None
        self.setup_process = None
        self.spawn_error = None
        self.fail_wait = False
        self.attach = True
        self.clock = 0.0

    def spawn(self, argv, **kwargs):
        if self.spawn_error:
            raise self.spawn_error
        assert kwargs["shell"] is False
        assert kwargs["stderr"] == subprocess.DEVNULL
        assert kwargs["bufsize"] == 0
        assert kwargs["stdin"] == kwargs["stdout"] == subprocess.PIPE
        process = FakeProcess(self, argv, kwargs)
        self.processes.append(process)
        if self.setup_process:
            self.setup_process(process)
        return process

    def kill(self, process, grace):
        assert process in self.processes and grace == 1.0
        self.killed.append(process)
        process.returncode = -9

    def sleep(self, seconds):
        self.clock += seconds

    def respond(self, process, frame):
        command = frame.get("type") or frame.get("method")
        assert command in {"get_state", "get_available_models", "get_available_thinking_levels", "initialize", "initialized", "config/read", "model/list"}
        if self.hook and self.hook(process, frame):
            return
        if "type" in frame:
            selected = self.pi_default
            if "--model" in process.argv:
                model = process.argv[process.argv.index("--model") + 1]
                selected = self.pi_selected or next((row for row in self.pi_models if row["id"] == model), self.pi_default)
            if command == "get_state":
                data = {"model": selected, "thinkingLevel": self.pi_effort if "--model" not in process.argv else "off", "sessionId": "MUST_NOT_LEAK"}
            elif command == "get_available_models":
                data = {"models": self.pi_models}
            else:
                data = {"levels": self.pi_levels[selected["id"]]}
            process.emit({"id": frame["id"], "type": "response", "command": command, "success": True, "data": data})
        elif command == "initialized":
            return
        else:
            if command == "initialize":
                data = {"userAgent": "native fake"}
            elif command == "config/read":
                data = {"config": self.config, "layers": [{"private": SECRET}]}
            else:
                cursor = frame["params"]["cursor"]
                page = 0 if cursor is None else int(cursor)
                data = self.pages[page]
            process.emit({"id": frame["id"], "result": data})


@pytest.fixture(autouse=True)
def capability_cache(monkeypatch):
    # Probe/source monkeypatches must never inherit another test's cache/flight.
    monkeypatch.setattr(cap, "_cache", OrderedDict())
    monkeypatch.setattr(cap, "_inflight", {})
    yield
    assert not cap._inflight


@contextmanager
def preserve_source(path):
    content, stamp = path.read_bytes(), path.stat()
    try:
        yield
    finally:
        path.write_bytes(content)
        os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))


@pytest.fixture
def native(monkeypatch, tmp_path):
    monkeypatch.setattr(cap.agent_host, "_app_main", main)
    program = NativeProgram()
    home, pi_home, codex_home = tmp_path / "user", tmp_path / "pi-source", tmp_path / "codex-source"
    pi_home.mkdir()
    codex_home.mkdir()
    (home / ".codex").mkdir(parents=True)
    # These are synthetic bytes only. Native files are copied, not parsed.
    for path, content in {
        pi_home / "settings.json": '{"defaultModel":"reasoning","defaultThinkingLevel":"high"}',
        pi_home / "models.json": '{"providers":{}}', pi_home / "auth.json": SECRET,
        pi_home / "sessions.jsonl": "BINDINGS_MUST_NOT_COPY", pi_home / "extensions.js": "MUST_NOT_RUN",
        codex_home / "config.toml": 'model="reasoning"', codex_home / "auth.json": SECRET,
        codex_home / "models_cache.json": '{"synthetic":true}', codex_home / "history.jsonl": "BINDINGS_MUST_NOT_COPY",
        home / ".codex" / "auth.json": "SYNTHETIC_GLOBAL_LOGIN",
    }.items():
        path.write_text(content, encoding="utf-8")
    originals = {path: (path.read_bytes(), path.stat().st_mtime_ns) for root in (pi_home, codex_home, home) for path in root.rglob("*") if path.is_file()}
    monkeypatch.setattr(cap, "RUNTIME_ROOT", str(tmp_path / "runtime"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(pi_home))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setattr(cap.agent_host, "resolve_pi_cli", lambda: "C:/fake/pi.exe")
    real_which = cap.shutil.which
    monkeypatch.setattr(cap.shutil, "which", lambda name: "C:/fake/codex.exe" if name == "codex" else real_which(name))
    monkeypatch.setattr(cap.subprocess, "Popen", program.spawn)
    monkeypatch.setattr(cap, "kill_process_tree", program.kill)
    monkeypatch.setattr(cap, "_stop_windows_job", lambda job: None)
    monkeypatch.setattr(cap.os, "set_blocking", lambda fd, blocking: program.nonblocking.append((fd, blocking)))
    if os.name != "nt":
        monkeypatch.setattr(cap.os, "killpg", lambda pid, sig: None)
    monkeypatch.setattr(cap.time, "monotonic", lambda: program.clock)
    monkeypatch.setattr(cap.time, "sleep", program.sleep)
    monkeypatch.setattr(cap, "_PROBE_TIMEOUT", 0.2)

    class FakeJob:
        def __init__(self):
            self.closed, self.attached = False, 0
            self.handle = None
            program.jobs.append(self)

        def spawn(self, argv, **kwargs):
            process = cap.subprocess.Popen(argv, **kwargs)
            self.attached += int(program.attach)
            return process

        def close(self):
            self.closed = True

    monkeypatch.setattr(cap, "ProcessJob", FakeJob)
    program.pi_home, program.codex_home, program.home = pi_home, codex_home, home
    yield program
    assert all(job.closed for job in program.jobs)
    assert program.killed == program.processes
    assert all(process.stdin.closed and process.stdout.closed for process in program.processes)
    assert all(path.read_bytes() == content and path.stat().st_mtime_ns == stamp for path, (content, stamp) in originals.items() if path.exists())
    assert not list((tmp_path / "runtime" / "agent-capabilities").glob("probe-*"))


def assert_shape(result, host, *, available=True):
    assert result.keys() == {"host", "status", "models", "defaults", "selection", "error"}
    assert result["host"] == host
    assert result["status"] == ("available" if available else "unavailable")
    assert result["defaults"].keys() == {"model", "effort"}
    assert result["selection"].keys() == {"model", "efforts", "source"}
    assert all(row.keys() == {"id", "name"} for row in result["models"])
    assert "account-callability-unverified" in result["selection"]["source"]["scope"]
    assert SECRET not in json.dumps(result)
    assert "MUST_NOT_LEAK" not in json.dumps(result)
    assert all(value.casefold() != "ultra" for value in result["selection"]["efforts"])
    assert result["defaults"]["effort"] is None or result["defaults"]["effort"].casefold() != "ultra"
    if available:
        assert result["error"] is None
    else:
        assert result["error"].keys() == {"code", "message"}
        assert result["error"]["code"]
        assert result["error"]["message"]
        assert result["selection"]["efforts"] == []


def test_pi_native_shape_defaults_and_no_fabricated_auth(native):
    result = cap.get_capabilities("pi")
    assert_shape(result, "pi")
    assert result["defaults"] == {"model": "native/reasoning", "effort": "high"}
    assert result["models"] == [{"id": "native/reasoning", "name": "Native reasoning"}, {"id": "native/simple", "name": "Native simple"}]
    assert result["selection"]["model"] == "native/reasoning"
    assert result["selection"]["efforts"] == ["off", "low", "high", "xhigh"]
    assert result["selection"]["source"]["efforts"] == "pi.rpc.get_available_thinking_levels"
    assert len(native.processes) == 1
    assert [frame["type"] for frame in native.processes[0].frames] == ["get_state", "get_available_thinking_levels", "get_available_models"]


@pytest.mark.parametrize("kind", ["image", "classifier"])
def test_pi_non_chat_models_never_enter_directory_or_selection(native, kind):
    native.pi_models[1]["type"] = kind
    result = cap.get_capabilities("pi")
    assert_shape(result, "pi")
    assert result["models"] == [{"id": "native/reasoning", "name": "Native reasoning"}]
    selected = cap.get_capabilities("pi", "native/simple")
    assert_shape(selected, "pi", available=False)
    assert selected["error"]["code"] == "unsupported_model"


def test_pi_offline_empty_directory_but_selected_state_known(native):
    native.pi_models = []
    result = cap.get_capabilities("pi")
    assert_shape(result, "pi")
    assert result["models"] == []
    assert result["selection"]["model"] == "native/reasoning"
    assert result["selection"]["efforts"] == ["off", "low", "high", "xhigh"]


def test_pi_selected_native_query_preserves_host_default(native):
    result = cap.get_capabilities("pi", "native/simple")
    assert_shape(result, "pi")
    assert result["defaults"] == {"model": "native/reasoning", "effort": "high"}
    assert result["selection"]["model"] == "native/simple"
    assert result["selection"]["efforts"] == ["off"]
    assert len(native.processes) == 2
    assert "--model" not in native.processes[0].argv
    assert native.processes[1].argv[-4:] == ["--provider", "native", "--model", "simple"]
    assert "--thinking" not in native.processes[1].argv
    assert native.processes[0].kwargs["env"]["HOME"] != native.processes[1].kwargs["env"]["HOME"]


def test_pi_offline_selected_exact_native_model_without_directory(native):
    native.pi_models = []
    native.pi_selected = deepcopy(PI_B)
    result = cap.get_capabilities("pi", "native/simple")
    assert_shape(result, "pi")
    assert result["models"] == []
    assert result["selection"]["efforts"] == ["off"]


@pytest.mark.parametrize("requested", ["simple", "native/alias", "native/simple:high", "other/simple"])
def test_pi_never_accepts_fuzzy_resolution_or_provider_alias(native, requested):
    native.pi_selected = deepcopy(PI_B)
    result = cap.get_capabilities("pi", requested)
    assert_shape(result, "pi", available=False)
    assert result["error"]["code"] == "unsupported_model"
    assert result["defaults"] == {"model": "native/reasoning", "effort": "high"}
    assert len(native.processes[-1].frames) == 1


def test_pi_only_actual_supported_values_not_global_enum(native):
    native.pi_levels["reasoning"] = ["off", "minimal", "low", "medium", "high", "model_specific", "ultra"]
    result = cap.get_capabilities("pi")
    assert result["selection"]["efforts"] == ["off", "minimal", "low", "medium", "high", "model_specific"]
    assert "xhigh" not in result["selection"]["efforts"]


def test_codex_native_directory_not_config_echo_and_pagination(native):
    native.pages = [{"data": [codex_row()], "nextCursor": "1"},
                    {"data": [codex_row("simple", levels=("none",), default="none", is_default=False), codex_row("hidden", hidden=True)], "nextCursor": None}]
    result = cap.get_capabilities("codex", "simple")
    assert_shape(result, "codex")
    assert result["models"] == [{"id": "reasoning", "name": "Native reasoning"}, {"id": "simple", "name": "Native simple"}]
    assert result["defaults"] == {"model": "reasoning", "effort": "low"}
    assert result["selection"]["model"] == "simple"
    assert result["selection"]["efforts"] == ["none"]
    assert "configured, not effective" in result["selection"]["source"]["defaults"]
    frames = native.processes[0].frames
    assert [frame["method"] for frame in frames] == ["initialize", "initialized", "config/read", "model/list", "model/list"]
    assert frames[-2]["params"] == {"includeHidden": False, "limit": 100, "cursor": None}
    assert frames[-1]["params"]["cursor"] == "1"
    assert "--model" not in native.processes[0].argv
    assert "model_reasoning_effort=" not in " ".join(native.processes[0].argv)


@pytest.mark.parametrize("configured_model,configured_effort,expected_effort", [(None, None, "low"), ("reasoning", "high", "high"), ("reasoning", "ULtRa", None)])
def test_codex_empty_selection_follows_native_host_defaults(native, configured_model, configured_effort, expected_effort):
    native.config.update(model=configured_model, model_reasoning_effort=configured_effort)
    result = cap.get_capabilities("codex")
    assert_shape(result, "codex")
    assert result["defaults"] == {"model": "reasoning", "effort": expected_effort}
    assert result["selection"]["model"] == "reasoning"
    assert result["selection"]["efforts"] == ["low", "high"]


@pytest.mark.parametrize("host", ["pi", "codex"])
def test_ultra_default_hidden_never_replaced_with_first_option(native, host):
    if host == "pi":
        native.pi_effort = "uLtRa"
    else:
        native.pages[0]["data"][0]["defaultReasoningEffort"] = "uLtRa"
    result = cap.get_capabilities(host)
    assert_shape(result, host)
    assert result["defaults"]["effort"] is None


@pytest.mark.parametrize("requested", ["unknown", "directory-reasoning", "hidden"])
def test_codex_unknown_or_directory_id_alias_rejected(native, requested):
    result = cap.get_capabilities("codex", requested)
    assert_shape(result, "codex", available=False)
    assert result["error"]["code"] == "unsupported_model"
    assert result["defaults"] == {"model": "reasoning", "effort": "low"}


@pytest.mark.parametrize("host", ["pi", "codex"])
def test_config_or_state_echo_cannot_create_supported_effort(native, host):
    if host == "pi":
        native.pi_effort = "not_supported"
    else:
        native.config["model_reasoning_effort"] = "not_supported"
    result = cap.get_capabilities(host)
    assert_shape(result, host, available=False)
    assert result["defaults"]["effort"] is None
    assert result["error"]["code"] == "unsupported_default_effort"


@pytest.mark.parametrize("host,model", [(None, ""), ("PI", ""), ("other", ""), ([], ""), ("pi", None), ("pi", []), ("codex", "--help"), ("pi", "a\n"), ("codex", "../model"), ("pi", "a & echo"), ("pi", "a" * 129)])
def test_invalid_client_input_raises_value_error_before_any_process(native, host, model):
    with pytest.raises(ValueError):
        cap.get_capabilities(host, model)
    assert native.processes == native.jobs == []


@pytest.mark.parametrize("host", ["pi", "codex"])
def test_missing_cli_is_explicit_unavailable(native, monkeypatch, host):
    monkeypatch.setattr(cap.agent_host, "resolve_pi_cli", lambda: None)
    monkeypatch.setattr(cap.shutil, "which", lambda name: None)
    result = cap.get_capabilities(host)
    assert_shape(result, host, available=False)
    assert result["error"]["code"] == "cli_missing"
    assert native.jobs == []


def test_reuses_safe_npm_shim_resolution_never_shell(native, monkeypatch, tmp_path):
    wrapper = tmp_path / "native.cmd"
    script, node = tmp_path / "entry.js", tmp_path / "node.exe"
    script.write_text("// synthetic, never executed", encoding="utf-8")
    node.write_bytes(b"synthetic")
    wrapper.write_text('"%_prog%" "%dp0%\\entry.js" %*', encoding="utf-8")
    monkeypatch.setattr(cap.agent_host, "resolve_pi_cli", lambda: str(wrapper))
    result = cap.get_capabilities("pi")
    assert_shape(result, "pi")
    assert native.processes[0].argv[:2] == [str(node), str(script)]
    assert all("cmd.exe" not in part.lower() for part in native.processes[0].argv)


def test_unsafe_npm_wrapper_refused_without_process(native, monkeypatch, tmp_path):
    wrapper = tmp_path / "native.cmd"
    wrapper.write_text("echo arbitrary shell wrapper", encoding="utf-8")
    monkeypatch.setattr(cap.agent_host, "resolve_pi_cli", lambda: str(wrapper))
    result = cap.get_capabilities("pi")
    assert_shape(result, "pi", available=False)
    assert result["error"]["code"] == "cli_wrapper_unsupported"
    assert native.processes == []


@pytest.mark.parametrize("host", ["pi", "codex"])
def test_fresh_home_only_required_files_no_bindings_no_secret_env(native, monkeypatch, host):
    for key in ("OPENAI_API_KEY", "NODE_OPTIONS", "PI_SESSION_ID", "PI_MODEL", "CODEX_THREAD_ID", "HTTP_PROXY"):
        monkeypatch.setenv(key, SECRET)
    result = cap.get_capabilities(host)
    assert_shape(result, host)
    process = native.processes[0]
    assert set(process.native_files) == ({"settings.json", "models.json", "auth.json"} if host == "pi" else {"config.toml", "auth.json", "models_cache.json"})
    assert process.native_files["auth.json"].decode() == SECRET
    env = process.kwargs["env"]
    assert SECRET not in json.dumps(env)
    assert Path(env["HOME"]) != native.home
    assert Path(process.kwargs["cwd"]) != Path.cwd()
    assert env["HOME"] == env["USERPROFILE"]
    if host == "pi":
        assert all(flag in process.argv for flag in cap._PI_FLAGS)
        assert env["PI_OFFLINE"] == env["PI_SKIP_VERSION_CHECK"] == "1"
        assert "--continue" not in process.argv and "--session-id" not in process.argv
    else:
        for setting in ("check_for_update_on_startup=false", "mcp_servers={}", "project_doc_max_bytes=0", "features.remote_plugin=false", "features.remote_control=false"):
            assert setting in process.argv
        assert any(arg.startswith("sqlite_home=") for arg in process.argv)
        assert any(arg.startswith("log_dir=") for arg in process.argv)
    assert len(native.nonblocking) == 2
    assert all(blocking is False for _, blocking in native.nonblocking)


def test_no_default_pi_dir_override_uses_native_user_directory(native, monkeypatch):
    monkeypatch.delenv("PI_CODING_AGENT_DIR")
    source = native.home / ".pi" / "agent"
    source.mkdir(parents=True)
    (source / "settings.json").write_text("SYNTHETIC_DEFAULT_SETTINGS", encoding="utf-8")
    cap.get_capabilities("pi")
    assert native.processes[0].native_files == {"settings.json": b"SYNTHETIC_DEFAULT_SETTINGS"}


def test_codex_runtime_home_resolution_without_writing_it(native, monkeypatch):
    monkeypatch.delenv("CODEX_HOME")
    monkeypatch.setattr(cap, "codex_home_dir", lambda: str(native.codex_home))
    result = cap.get_capabilities("codex")
    assert_shape(result, "codex")
    assert native.processes[0].native_files["config.toml"] == b'model="reasoning"'


def test_codex_file_login_fallback_is_opaque_copy_only(native, monkeypatch):
    monkeypatch.delenv("CODEX_HOME")
    monkeypatch.setattr(cap, "codex_home_dir", lambda: str(native.codex_home))
    (native.codex_home / "auth.json").unlink()
    result = cap.get_capabilities("codex")
    assert_shape(result, "codex")
    assert native.processes[0].native_files["auth.json"] == b"SYNTHETIC_GLOBAL_LOGIN"
    assert not (native.codex_home / "auth.json").exists()


def test_explicit_codex_home_does_not_borrow_global_identity(native):
    (native.codex_home / "auth.json").unlink()
    result = cap.get_capabilities("codex")
    assert_shape(result, "codex")
    assert "auth.json" not in native.processes[0].native_files


@pytest.mark.parametrize("newer_global", [True, False])
def test_codex_default_home_mirrors_newer_login_without_sync_writes(native, monkeypatch, newer_global):
    monkeypatch.delenv("CODEX_HOME")
    monkeypatch.setattr(cap, "codex_home_dir", lambda: str(native.codex_home))
    local = native.codex_home / "auth.json"
    global_file = native.home / ".codex" / "auth.json"
    # Adjust only this test's synthetic source, then restore its fixture stamp.
    original = global_file.stat()
    stamp = local.stat().st_mtime_ns + (1_000_000_000 if newer_global else -1_000_000_000)
    os.utime(global_file, ns=(original.st_atime_ns, stamp))
    try:
        result = cap.get_capabilities("codex")
        assert_shape(result, "codex")
        expected = b"SYNTHETIC_GLOBAL_LOGIN" if newer_global else SECRET.encode()
        assert native.processes[0].native_files["auth.json"] == expected
    finally:
        os.utime(global_file, ns=(original.st_atime_ns, original.st_mtime_ns))


@pytest.mark.parametrize("host", ["pi", "codex"])
def test_rpc_notifications_and_fragmented_io(native, host):
    def setup(process):
        process.read_chunk = process.write_chunk = 7
        process.emit({"type": "status", "private": SECRET} if host == "pi" else {"method": "remoteControl/status/changed", "params": {"private": SECRET}})
    native.setup_process = setup
    assert_shape(cap.get_capabilities(host), host)


@pytest.mark.parametrize("host", ["pi", "codex"])
@pytest.mark.parametrize("wrong_id", [None, 1, True, "old-request", "cap-2"])
def test_rpc_strict_ids_not_command_matching(native, host, wrong_id):
    def hook(process, frame):
        if "id" not in frame:
            return False
        if host == "pi":
            process.emit({"id": wrong_id, "type": "response", "command": frame["type"], "success": True, "data": {}})
        else:
            process.emit({"id": wrong_id, "result": {}})
        return True
    native.hook = hook
    result = cap.get_capabilities(host)
    assert_shape(result, host, available=False)
    assert result["error"]["code"] == "rpc_id_mismatch"
    assert len(native.processes[0].frames) == 1


@pytest.mark.parametrize("host", ["pi", "codex"])
def test_missing_rpc_id_is_malformed(native, host):
    def hook(process, frame):
        process.emit({"type": "response", "command": frame.get("type"), "success": True, "data": {}} if host == "pi" else {"result": {}})
        return True
    native.hook = hook
    result = cap.get_capabilities(host)
    assert_shape(result, host, available=False)
    assert result["error"]["code"] == "malformed_metadata"


@pytest.mark.parametrize("bad", [{"type": "response", "command": "other", "success": True, "data": {}},
                                 {"type": "response", "command": "get_state", "success": 1, "data": {}},
                                 {"type": "response", "command": "get_state", "success": True, "data": None}])
def test_pi_wrong_command_success_type_or_data_rejected(native, bad):
    native.hook = lambda process, frame: process.emit({**bad, "id": frame["id"]}) or True
    result = cap.get_capabilities("pi")
    assert_shape(result, "pi", available=False)
    assert result["error"]["code"] == "malformed_metadata"


@pytest.mark.parametrize("host", ["pi", "codex"])
def test_native_error_is_sanitized_and_tree_cleaned(native, host):
    def hook(process, frame):
        if host == "pi":
            process.emit({"id": frame["id"], "type": "response", "command": frame["type"], "success": False, "error": SECRET})
        else:
            process.emit({"id": frame["id"], "error": {"code": -123, "message": SECRET, "config": SECRET}})
        return True
    native.hook = hook
    result = cap.get_capabilities(host)
    assert_shape(result, host, available=False)
    assert result["error"]["code"] == "native_rpc_error"


@pytest.mark.parametrize("host", ["pi", "codex"])
@pytest.mark.parametrize("raw", [b"not json SECRET\n", b"[]\n", b"null\n", b"\xff\n", b"{\"unterminated\":\n"])
def test_malformed_json_and_non_object_clean_up(native, host, raw):
    def hook(process, frame):
        process.output += raw
        return True
    native.hook = hook
    result = cap.get_capabilities(host)
    assert_shape(result, host, available=False)
    assert result["error"]["code"] == "malformed_metadata"


@pytest.mark.parametrize("host", ["pi", "codex"])
@pytest.mark.parametrize("kind", ["eof", "exit", "timeout", "partial", "blocked-write", "broken-write"])
def test_eof_timeout_and_io_failure_always_clean_up(native, host, kind):
    def setup(process):
        if kind == "blocked-write":
            process.block_writes = True
        elif kind == "broken-write":
            process.broken_write = True
    def hook(process, frame):
        if kind == "eof":
            process.eof = True
        elif kind == "exit":
            process.returncode = 1
        elif kind == "partial":
            process.output += b'{"id":'
            process.eof = True
        return True
    native.setup_process, native.hook = setup, hook
    result = cap.get_capabilities(host)
    assert_shape(result, host, available=False)
    expected = "native_io_error" if kind == "broken-write" else "probe_timeout" if kind in ("timeout", "blocked-write") else "probe_eof"
    assert result["error"]["code"] == expected
    assert native.clock <= cap._PROBE_TIMEOUT + 0.02


@pytest.mark.parametrize("host", ["pi", "codex"])
def test_cancel_base_exception_propagates_after_cleanup(native, host):
    native.setup_process = lambda process: setattr(process, "read_error", KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        cap.get_capabilities(host)
    assert native.killed == native.processes
    assert all(process.stdin.closed and process.stdout.closed for process in native.processes)


@pytest.mark.parametrize("host", ["pi", "codex"])
def test_spawn_failure_closes_job_and_sanitizes_paths(native, host):
    native.spawn_error = OSError(SECRET)
    result = cap.get_capabilities(host)
    assert_shape(result, host, available=False)
    assert result["error"]["code"] == "native_io_error"
    assert len(native.jobs) == 1 and native.jobs[0].closed


@pytest.mark.skipif(os.name != "nt", reason="Windows job ownership guard")
def test_refuse_probe_when_windows_job_cannot_own_tree(native):
    native.attach = False
    result = cap.get_capabilities("pi")
    assert_shape(result, "pi", available=False)
    assert result["error"]["code"] == "cleanup_unavailable"
    assert native.processes[0].frames == []


@pytest.mark.skipif(os.name != "nt", reason="Windows creation handle")
@pytest.mark.parametrize("success", [False, True])
def test_windows_attach_uses_original_creation_handle(monkeypatch, success):
    calls = []

    class Assign:
        def __call__(self, job, process):
            calls.append((job.value, process.value))
            return int(success)

    class Kernel:
        AssignProcessToJobObject = Assign()

    monkeypatch.setattr(cap.ctypes, "WinDLL", lambda *args, **kwargs: Kernel())
    job = type("Job", (), {"handle": 123, "attached": 0})()
    process = type("Process", (), {"_handle": 456})()
    assert cap._attach_spawn_handle(job, process) is success
    assert calls == [(123, 456)]
    assert job.attached == int(success)


@pytest.mark.skipif(os.name != "nt", reason="Windows creation handle")
def test_windows_retry_attach_never_spawns_twice(native, monkeypatch):
    native.attach = False
    calls = []

    def attach(job, process):
        calls.append(process)
        job.attached += 1
        return True

    monkeypatch.setattr(cap, "_attach_spawn_handle", attach)
    result = cap.get_capabilities("pi")
    assert_shape(result, "pi")
    assert len(native.processes) == 1
    assert calls == native.processes


@pytest.mark.skipif(os.name != "nt", reason="Windows native job teardown")
@pytest.mark.parametrize("failure", [None, "terminate", "query", "timeout"])
def test_windows_job_waits_for_native_descendants(monkeypatch, failure):
    clock, events, active = [0.0], [], [2, 1, 0]

    class Call:
        def __init__(self, fn):
            self.fn = fn

        def __call__(self, *args):
            return self.fn(*args)

    def terminate(handle, code):
        events.append("terminate")
        assert handle.value == 123
        return int(failure != "terminate")

    def query(handle, info_class, buffer, size, returned):
        events.append("query")
        assert handle.value == 123 and info_class == 1
        account = cap.ctypes.cast(buffer, cap.ctypes.POINTER(cap._JobAccounting)).contents
        account.active_processes = 1 if failure == "timeout" else active.pop(0)
        return int(failure != "query")

    kernel = type("Kernel", (), {"TerminateJobObject": Call(terminate), "QueryInformationJobObject": Call(query)})()
    monkeypatch.setattr(cap.ctypes, "WinDLL", lambda *args, **kwargs: kernel)
    monkeypatch.setattr(cap.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(cap.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + 0.75))
    job = type("Job", (), {"handle": 123})()
    if failure:
        with pytest.raises(cap._Unavailable) as error:
            cap._stop_windows_job(job)
        assert error.value.code == "cleanup_failed"
    else:
        cap._stop_windows_job(job)
        assert events == ["terminate", "query", "query", "query"]
        assert not active


@pytest.mark.skipif(os.name != "nt", reason="Windows sharing violations")
@pytest.mark.parametrize("winerror", [32, 5])
def test_windows_temporary_cleanup_retries_only_sharing_violation(native, monkeypatch, winerror):
    original, temporaries, calls = cap.tempfile.TemporaryDirectory, [], []

    class SharingTemp(original):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            temporaries.append(self)

        def cleanup(self):
            calls.append(self.name)
            if len(calls) == 1:
                error = PermissionError("synthetic own temporary file")
                error.winerror = winerror
                raise error
            return super().cleanup()

    monkeypatch.setattr(cap.tempfile, "TemporaryDirectory", SharingTemp)
    try:
        result = cap.get_capabilities("codex")
        assert_shape(result, "codex", available=winerror == 32)
        assert len(calls) == (2 if winerror == 32 else 1)
    finally:
        for temporary in temporaries:
            temporary.cleanup()


def test_cleanup_wait_failure_explicit_unavailable(native):
    native.fail_wait = True
    result = cap.get_capabilities("pi")
    assert_shape(result, "pi", available=False)
    assert result["error"]["code"] == "cleanup_failed"


@pytest.mark.parametrize("field,value", [("model", []), ("model_reasoning_effort", []), ("model_reasoning_effort", ""), ("model", "not-listed")])
def test_codex_invalid_default_explicit_unavailable(native, field, value):
    native.config[field] = value
    result = cap.get_capabilities("codex")
    assert_shape(result, "codex", available=False)
    assert result["defaults"]["effort"] is None


@pytest.mark.parametrize("field", ["model", "model_reasoning_effort"])
def test_codex_missing_config_default_fields_not_guessed(native, field):
    del native.config[field]
    result = cap.get_capabilities("codex")
    assert_shape(result, "codex", available=False)
    assert result["error"]["code"] == "malformed_metadata"


@pytest.mark.parametrize("defaults", [[], [codex_row(is_default=False)], [codex_row(), codex_row("another")]])
def test_codex_ambiguous_or_absent_default_never_chooses_first(native, defaults):
    native.config["model"] = None
    native.pages = [{"data": defaults, "nextCursor": None}]
    result = cap.get_capabilities("codex")
    assert_shape(result, "codex", available=False)
    assert result["defaults"] == {"model": "", "effort": None}
    assert result["error"]["code"] in ("default_model_unknown", "models_unavailable")


@pytest.mark.parametrize("levels", [None, "high", [], ["ultra", "ULTRA"], ["high", 1], ["high", " bad"], ["high", ""], ["high", "bad\n"]])
@pytest.mark.parametrize("host", ["pi", "codex"])
def test_unknown_or_malformed_effort_options_not_fabricated(native, host, levels):
    if host == "pi":
        native.pi_levels["reasoning"] = levels
    else:
        native.pages[0]["data"][0]["supportedReasoningEfforts"] = levels if not isinstance(levels, list) else [{"reasoningEffort": value} for value in levels]
    result = cap.get_capabilities(host)
    assert_shape(result, host, available=False)
    assert result["error"]["code"] in ("malformed_metadata", "efforts_unavailable")


@pytest.mark.parametrize("bad_state", [{}, {"model": None, "thinkingLevel": "off"}, {"model": PI_A}, {"model": PI_A, "thinkingLevel": None}])
def test_pi_unknown_native_state_not_silently_defaulted(native, bad_state):
    native.hook = lambda process, frame: process.emit({"id": frame["id"], "type": "response", "command": frame["type"], "success": True, "data": bad_state}) or True
    result = cap.get_capabilities("pi")
    assert_shape(result, "pi", available=False)
    assert result["error"]["code"] == "malformed_metadata"


@pytest.mark.parametrize("rows", [None, {}, [PI_A, PI_A], [None], [{"id": "model", "name": "no provider"}]])
def test_pi_malformed_directory_unavailable(native, rows):
    native.pi_models = rows
    result = cap.get_capabilities("pi")
    assert_shape(result, "pi", available=False)
    assert result["error"]["code"] == "malformed_metadata"


@pytest.mark.parametrize("page", [{}, {"data": {}}, {"data": [], "nextCursor": 1}, {"data": [], "nextCursor": ""}, {"data": [None], "nextCursor": None}, {"data": [codex_row(), codex_row()], "nextCursor": None}])
def test_codex_malformed_directory_or_pagination_unavailable(native, page):
    native.pages = [page]
    result = cap.get_capabilities("codex")
    assert_shape(result, "codex", available=False)
    assert result["error"]["code"] == "malformed_metadata"


def test_codex_repeated_cursor_rejected_without_unbounded_loop(native):
    native.pages = [{"data": [], "nextCursor": "1"}, {"data": [], "nextCursor": "1"}]
    result = cap.get_capabilities("codex")
    assert_shape(result, "codex", available=False)
    assert result["error"]["code"] == "malformed_metadata"
    assert len([frame for frame in native.processes[0].frames if frame["method"] == "model/list"]) == 2


def test_codex_pagination_has_finite_page_budget(native, monkeypatch):
    monkeypatch.setattr(cap, "_MAX_PAGES", 2)
    native.pages = [{"data": [], "nextCursor": "1"}, {"data": [], "nextCursor": "2"}]
    result = cap.get_capabilities("codex")
    assert_shape(result, "codex", available=False)
    assert result["error"]["code"] == "pagination_limit"


@pytest.mark.parametrize("host", ["pi", "codex"])
@pytest.mark.parametrize("bound", ["_MAX_FRAME", "_MAX_OUTPUT"])
def test_output_frame_and_total_output_are_bounded(native, monkeypatch, host, bound):
    monkeypatch.setattr(cap, bound, 20)
    result = cap.get_capabilities(host)
    assert_shape(result, host, available=False)
    assert result["error"]["code"] == "malformed_metadata"


def test_one_deadline_covers_both_pi_probes(native):
    def hook(process, frame):
        native.clock += 0.05
        return False
    native.hook = hook
    result = cap.get_capabilities("pi", "native/simple")
    assert_shape(result, "pi", available=False)
    assert result["error"]["code"] == "probe_timeout"
    assert result["defaults"] == {"model": "native/reasoning", "effort": "high"}
    assert len(native.processes) == 2


@pytest.mark.parametrize("field,value", [("defaultReasoningEffort", None), ("supportedReasoningEfforts", [{}]), ("hidden", 0), ("isDefault", "true"), ("model", "bad\n"), ("displayName", None)])
def test_codex_malformed_native_model_fields_not_echoed(native, field, value):
    native.pages[0]["data"][0][field] = value
    result = cap.get_capabilities("codex")
    assert_shape(result, "codex", available=False)
    assert result["error"]["code"] == "malformed_metadata"


def test_native_copy_errors_actionable_not_swallowed_or_leaked(native, monkeypatch):
    monkeypatch.setattr(cap.shutil, "copyfile", lambda *args: (_ for _ in ()).throw(PermissionError(SECRET)))
    result = cap.get_capabilities("pi")
    assert_shape(result, "pi", available=False)
    assert result["error"]["code"] == "native_io_error"
    assert native.jobs == []


def test_native_copy_size_is_bounded(native, monkeypatch):
    monkeypatch.setattr(cap, "_MAX_COPY", 1)
    result = cap.get_capabilities("pi")
    assert_shape(result, "pi", available=False)
    assert result["error"]["code"] == "native_files_unavailable"
    assert native.jobs == []


@pytest.mark.parametrize("host", ["pi", "codex"])
def test_cache_hits_are_deep_copies_without_new_snapshot_or_spawn(native, monkeypatch, host):
    first = cap.get_capabilities(host)
    expected = deepcopy(first)
    first["models"][0]["name"] = "fake"
    first["selection"]["efforts"].append("fake")
    first["selection"]["source"].clear()
    first["defaults"]["model"] = "fake"
    snapshots = []
    original = cap._snapshot

    def snapshot(*args):
        snapshots.append(args)
        return original(*args)

    monkeypatch.setattr(cap, "_snapshot", snapshot)
    second = cap.get_capabilities(host)
    assert second == expected
    second["models"].clear()
    assert cap.get_capabilities(host) == expected
    assert len(native.processes) == 1
    assert snapshots == []
    # Expiry makes a new isolated home, never revives the deleted snapshot.
    native.clock += cap._CACHE_TTL
    assert cap.get_capabilities(host) == expected
    assert len(native.processes) == 2 and len(snapshots) == 1
    assert native.processes[0].kwargs["env"]["HOME"] != native.processes[1].kwargs["env"]["HOME"]


@pytest.mark.parametrize("host,model", [("pi", ""), ("codex", ""), ("pi", "native/simple")])
@pytest.mark.parametrize("outcome", ["available", "rpc_error", "cancel"])
def test_same_key_simultaneous_threads_share_probe_and_cleanup(native, monkeypatch, host, model, outcome):
    started, release, waiting = Event(), Event(), Event()
    waiters, lock = [], Lock()

    class ObservedFlight(Future):
        def result(self, timeout=None):
            with lock:
                waiters.append(self)
                if len(waiters) == 5:
                    waiting.set()
            return super().result(timeout)

    def setup(process):
        if outcome == "cancel":
            process.read_error = KeyboardInterrupt()
        started.set()
        assert release.wait(5), "test did not release the fake native spawn"

    if outcome == "rpc_error":
        def hook(process, frame):
            process.emit({"id": frame["id"], "type": "response", "command": frame.get("type"), "success": False, "error": SECRET})
            return True
        native.hook = hook
    native.setup_process = setup
    monkeypatch.setattr(cap, "Future", ObservedFlight)
    with ThreadPoolExecutor(max_workers=6) as pool:
        calls = [pool.submit(cap.get_capabilities, host, model)]
        try:
            assert started.wait(5), "fake native probe did not start"
            calls.extend(pool.submit(cap.get_capabilities, host, model) for _ in range(5))
            assert waiting.wait(5), "not all concurrent callers joined the flight"
        finally:
            release.set()
        if outcome == "cancel":
            for call in calls:
                with pytest.raises(KeyboardInterrupt):
                    call.result(timeout=5)
        else:
            results = [call.result(timeout=5) for call in calls]
            expected = deepcopy(results[0])
            for result in results:
                assert_shape(result, host, available=outcome == "available")
                assert result == expected
            results[0]["models"].clear()
            results[0]["selection"]["source"].clear()
            if outcome == "rpc_error":
                results[0]["error"]["message"] = "changed by caller"
            assert all(result == expected for result in results[1:])
            assert cap.get_capabilities(host, model) == expected
    # Pi's selected-model contract needs its default and selected RPC sessions;
    # single-flight shares that entire discovery, not just its first subprocess.
    expected_spawns = 2 if model and outcome == "available" else 1
    assert len(native.processes) == len(native.jobs) == expected_spawns
    assert not cap._inflight
    assert native.killed == native.processes
    assert all(job.closed for job in native.jobs)
    assert all(process.stdin.closed and process.stdout.closed for process in native.processes)
    if outcome == "cancel":
        assert not cap._cache
        native.setup_process = None
        assert_shape(cap.get_capabilities(host, model), host)
        assert len(native.processes) == (3 if model else 2)


@pytest.mark.parametrize("host,name", [("pi", "settings.json"), ("pi", "models.json"), ("pi", "auth.json"),
                                       ("codex", "config.toml"), ("codex", "models_cache.json"), ("codex", "auth.json")])
@pytest.mark.parametrize("change", ["mtime", "size", "absent"])
def test_each_native_source_mtime_size_and_absence_invalidate(native, host, name, change):
    source = (native.pi_home if host == "pi" else native.codex_home) / name
    with preserve_source(source):
        cap.get_capabilities(host)
        stamp = source.stat()
        if change == "mtime":
            os.utime(source, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 100_000))
            assert source.stat().st_size == stamp.st_size
            assert source.stat().st_mtime_ns != stamp.st_mtime_ns
        elif change == "size":
            source.write_bytes(source.read_bytes() + b" ")
            os.utime(source, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
            assert source.stat().st_mtime_ns == stamp.st_mtime_ns
            assert source.stat().st_size != stamp.st_size
        else:
            source.unlink()
        assert_shape(cap.get_capabilities(host), host)
        assert_shape(cap.get_capabilities(host), host)
        assert len(native.processes) == 2
        # Recreating a previously absent file also changes the key.
        if change == "absent":
            source.write_bytes(b"SYNTHETIC_NEW_FILE")
            assert_shape(cap.get_capabilities(host), host)
            assert len(native.processes) == 3


@pytest.mark.parametrize("host,env", [("pi", "PI_CODING_AGENT_DIR"), ("codex", "CODEX_HOME")])
def test_native_source_path_changes_invalidate_even_with_identical_file_stats(native, monkeypatch, tmp_path, host, env):
    home = native.pi_home if host == "pi" else native.codex_home
    alternative = tmp_path / "alternative-source"
    shutil.copytree(home, alternative, copy_function=shutil.copy2)
    for name in cap._NATIVE_FILES[host]:
        assert (alternative / name).stat().st_mtime_ns == (home / name).stat().st_mtime_ns
        assert (alternative / name).stat().st_size == (home / name).stat().st_size
    first = cap.get_capabilities(host)
    monkeypatch.setenv(env, str(alternative))
    assert cap.get_capabilities(host) == first
    assert cap.get_capabilities(host) == first
    assert len(native.processes) == 2


def test_host_and_requested_model_are_separate_cache_keys(native, monkeypatch):
    # Isolate these two key dimensions from the independently tested file key.
    monkeypatch.setattr(cap, "_source_fingerprint", lambda host, sources: ())
    for host, models in (("pi", ("", "native/reasoning")), ("codex", ("", "reasoning"))):
        for model in models:
            assert_shape(cap.get_capabilities(host, model), host)
            assert_shape(cap.get_capabilities(host, model), host)
    assert len(native.processes) == 4
    assert len(cap._cache) == 4


@pytest.mark.parametrize("change", ["mtime", "size", "absent", "path"])
def test_codex_used_global_auth_source_is_part_of_key(native, monkeypatch, tmp_path, change):
    monkeypatch.delenv("CODEX_HOME")
    monkeypatch.setattr(cap, "codex_home_dir", lambda: str(native.codex_home))
    local, fallback = native.codex_home / "auth.json", native.home / ".codex" / "auth.json"
    with preserve_source(local), preserve_source(fallback):
        local.unlink()
        cap.get_capabilities("codex")
        assert native.processes[0].native_files["auth.json"] == fallback.read_bytes()
        stamp = fallback.stat()
        if change == "mtime":
            os.utime(fallback, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 100_000))
        elif change == "size":
            fallback.write_bytes(fallback.read_bytes() + b" ")
            os.utime(fallback, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        elif change == "absent":
            fallback.unlink()
        else:
            new_home = tmp_path / "another-user"
            (new_home / ".codex").mkdir(parents=True)
            shutil.copy2(fallback, new_home / ".codex" / "auth.json")
            monkeypatch.setattr(Path, "home", classmethod(lambda cls: new_home))
        assert_shape(cap.get_capabilities("codex"), "codex")
        assert_shape(cap.get_capabilities("codex"), "codex")
        assert len(native.processes) == 2
        if change == "absent":
            assert "auth.json" not in native.processes[-1].native_files
        elif change == "size":
            assert native.processes[-1].native_files["auth.json"] == fallback.read_bytes()


def test_codex_previously_unused_or_missing_global_login_can_become_selected(native, monkeypatch):
    monkeypatch.delenv("CODEX_HOME")
    monkeypatch.setattr(cap, "codex_home_dir", lambda: str(native.codex_home))
    local, fallback = native.codex_home / "auth.json", native.home / ".codex" / "auth.json"
    with preserve_source(fallback):
        stamp = fallback.stat()
        os.utime(fallback, ns=(stamp.st_atime_ns, local.stat().st_mtime_ns - 1_000_000))
        cap.get_capabilities("codex")
        assert native.processes[-1].native_files["auth.json"] == local.read_bytes()
        os.utime(fallback, ns=(stamp.st_atime_ns, local.stat().st_mtime_ns + 1_000_000))
        cap.get_capabilities("codex")
        assert native.processes[-1].native_files["auth.json"] == fallback.read_bytes()
        fallback.unlink()
        cap.get_capabilities("codex")
        assert native.processes[-1].native_files["auth.json"] == local.read_bytes()
        fallback.write_bytes(b"SYNTHETIC_REPLACEMENT_LOGIN")
        cap.get_capabilities("codex")
        assert len(native.processes) == 4


def test_explicit_codex_home_cache_ignores_unrelated_global_login(native):
    fallback = native.home / ".codex" / "auth.json"
    first = cap.get_capabilities("codex")
    with preserve_source(fallback):
        fallback.write_bytes(b"SYNTHETIC_CHANGED_GLOBAL_LOGIN")
        assert cap.get_capabilities("codex") == first
        assert len(native.processes) == 1
        assert native.processes[0].native_files["auth.json"] == SECRET.encode()


@pytest.mark.parametrize("host", ["pi", "codex"])
@pytest.mark.parametrize("failure", ["spawn", "rpc", "cleanup"])
def test_unavailable_cache_has_short_expiry_then_recovers(native, host, failure):
    if failure == "spawn":
        native.spawn_error = OSError(SECRET)
    elif failure == "cleanup":
        native.fail_wait = True
    elif host == "pi":
        native.pi_effort = "unknown"
    else:
        native.config["model_reasoning_effort"] = "unknown"
    first = cap.get_capabilities(host)
    assert_shape(first, host, available=False)
    expected = deepcopy(first)
    first["error"]["message"] = "caller mutation"
    first["selection"]["source"].clear()
    assert len(native.jobs) == 1 and native.jobs[0].closed
    native.spawn_error, native.fail_wait, native.pi_effort = None, False, "high"
    native.config["model_reasoning_effort"] = None
    assert cap._CACHE_FAILURE_TTL < cap._CACHE_TTL
    native.clock += cap._CACHE_FAILURE_TTL - 0.001
    assert cap.get_capabilities(host) == expected
    assert len(native.jobs) == 1
    native.clock += 0.001
    assert_shape(cap.get_capabilities(host), host)
    assert len(native.jobs) == 2


def test_missing_cli_result_expires_without_native_file_changes(native, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(cap.agent_host, "resolve_pi_cli", lambda: None)
        assert cap.get_capabilities("pi")["error"]["code"] == "cli_missing"
    assert cap.get_capabilities("pi")["error"]["code"] == "cli_missing"
    assert native.jobs == []
    native.clock += cap._CACHE_FAILURE_TTL
    assert_shape(cap.get_capabilities("pi"), "pi")
    assert len(native.processes) == 1


def test_snapshot_copy_failure_is_short_cached_after_temporary_cleanup(native, monkeypatch):
    with monkeypatch.context() as patch:
        def denied(*args):
            raise PermissionError(SECRET)
        patch.setattr(cap.shutil, "copyfile", denied)
        result = cap.get_capabilities("pi")
        assert_shape(result, "pi", available=False)
        assert result["error"]["code"] == "native_io_error"
    assert not list((Path(cap.RUNTIME_ROOT) / "agent-capabilities").glob("probe-*"))
    assert cap.get_capabilities("pi") == result
    assert native.jobs == []
    native.clock += cap._CACHE_FAILURE_TTL
    assert_shape(cap.get_capabilities("pi"), "pi")
    assert len(native.processes) == 1


def test_cache_is_bounded_and_evicts_least_recently_used(native, monkeypatch):
    monkeypatch.setattr(cap, "_CACHE_MAX_ENTRIES", 2)
    for model in ("", "reasoning", "", "simple"):
        assert_shape(cap.get_capabilities("codex", model), "codex")
        assert len(cap._cache) <= 2
    assert len(native.processes) == 3
    assert {key[1] for key in cap._cache} == {"", "simple"}
    assert_shape(cap.get_capabilities("codex", "reasoning"), "codex")
    assert len(native.processes) == 4
    assert {key[1] for key in cap._cache} == {"reasoning", "simple"}


@pytest.mark.parametrize("host", ["pi", "codex"])
def test_source_stat_failure_is_explicit_uncached_and_never_spawns(native, monkeypatch, host):
    source = native.pi_home / "models.json" if host == "pi" else native.codex_home / "config.toml"
    original = Path.stat

    def stat(path, *args, **kwargs):
        if path == source:
            raise PermissionError(SECRET)
        return original(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "stat", stat)
        result = cap.get_capabilities(host)
        assert_shape(result, host, available=False)
        assert result["error"]["code"] == "native_io_error"
        assert native.jobs == []
        assert not cap._cache and not cap._inflight
    assert_shape(cap.get_capabilities(host), host)
