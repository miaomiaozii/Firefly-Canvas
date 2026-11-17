"""Native model metadata, not account-callability or runtime-setting ACKs (#421/#424).

get_capabilities(host, model='') returns exactly host/status/models/defaults/
selection/error. Model IDs are native execution IDs (Pi: provider/id). An empty
model follows the native host default; defaults always describe that host, not
the queried model. Effort options come only from the selected model's native
metadata; ultra is hidden, including defaults. Null defaults are never replaced
by an arbitrary enum member. selection.source records provenance and the
metadata-only availability boundary. Codex config/read is configured intent,
NOT evidence of effective reasoning effort.

Each call takes file-only snapshots into a fresh runtime-owned temporary home.
Only settings/models/auth/catalog files are copied, never sessions or bindings;
credentials are copied as opaque bytes, never parsed, logged or returned. The
child environment excludes inherited agent/session markers, provider secrets
and Node injection hooks. This file-only, sanitized-environment probe cannot
claim production-default equivalence: env-only authentication, proxies and
custom CAs are deliberately not inherited. Pi is offline/sessionless with discovery/tools off;
Codex issues only initialize, initialized, config/read and paginated model/list.
No prompts, threads, turns, production exec/resume, or persistent cache here.

Raw nonblocking pipes (Windows requires Python 3.12+) avoid reader/watchdog
threads and enforce one finite deadline across startup, writes and all reads.
Every exit kills the owned process tree and closes its anonymous Windows job.
"""
from __future__ import annotations

from contextlib import contextmanager
import ctypes
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tempfile
import time

from app.constants import RUNTIME_ROOT
from app.services import agent_host
from app.services.agent_adapters.codex_adapter import codex_home_dir, _toml_value
from tools.process_tree import ProcessJob, kill_process_tree


_PROBE_TIMEOUT = 30.0
_MAX_FRAME = 2 * 1024 * 1024
_MAX_OUTPUT = 8 * 1024 * 1024
_MAX_MODELS = 4096
_MAX_PAGES = 32
_MAX_COPY = 16 * 1024 * 1024
_EFFORT = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,31}\Z")
_PI_FLAGS = [
    "--mode", "rpc", "--no-session", "--offline", "--no-extensions",
    "--no-mcp", "--no-skills", "--no-prompt-templates", "--no-themes",
    "--no-context-files", "--no-tools", "--no-approve",
]


class _Unavailable(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def _malformed() -> _Unavailable:
    return _Unavailable("malformed_metadata", "Native model metadata is malformed; update the CLI and retry.")


def _text(value, limit: int = 256) -> str:
    if not isinstance(value, str) or not value or len(value) > limit or any(ord(c) < 32 for c in value):
        raise _malformed()
    return value


def _native_model(value) -> str:
    value = _text(value, 128)
    try:
        agent_host.validate_spawn_arg("model", value, model=True)
    except ValueError:
        raise _malformed() from None
    return value


def _efforts(values) -> list[str]:
    if not isinstance(values, list) or len(values) > 64:
        raise _malformed()
    result = []
    for value in values:
        if not isinstance(value, str) or not _EFFORT.fullmatch(value):
            raise _malformed()
        if value.casefold() != "ultra" and value not in result:
            result.append(value)
    if not result:
        raise _Unavailable("efforts_unavailable", "This model has no exposed native effort options; use host defaults or select another model.")
    return result


def _default_effort(value, efforts: list[str]) -> str | None:
    if not isinstance(value, str) or not _EFFORT.fullmatch(value):
        raise _malformed()
    if value.casefold() == "ultra":
        return None
    if value not in efforts:
        raise _Unavailable("unsupported_default_effort", "A native default effort is not supported by its model; correct the native configuration and retry.")
    return value


def _copy_files(source: Path, target: Path, names: tuple[str, ...]) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for name in names:
        path = source / name
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            continue
        if size > _MAX_COPY or not path.is_file():
            raise _Unavailable("native_files_unavailable", "A native settings file is invalid or too large; check the CLI configuration and retry.")
        # No parsing (especially auth), hardlinks, recursive copies or writes to source.
        shutil.copyfile(path, target / name)


def _snapshot(host: str, root: Path) -> Path:
    target = root / "snapshot"
    if host == "pi":
        source = Path(os.environ.get("PI_CODING_AGENT_DIR") or Path.home() / ".pi" / "agent")
        _copy_files(source, target, ("settings.json", "models.json", "auth.json"))
    else:
        explicit_home = os.environ.get("CODEX_HOME")
        source = Path(explicit_home or codex_home_dir())
        _copy_files(source, target, ("config.toml", "auth.json", "models_cache.json"))
        if not explicit_home:
            # Mirror prepare_codex_home's newer global login selection without
            # writing its persistent home. An explicit CODEX_HOME never borrows
            # another home's identity, just like the production spawn boundary.
            global_home = Path.home() / ".codex"
            try:
                global_stamp = (global_home / "auth.json").stat().st_mtime_ns
            except FileNotFoundError:
                global_stamp = None
            local_auth = target / "auth.json"
            if global_stamp is not None and (not local_auth.exists() or global_stamp > (source / "auth.json").stat().st_mtime_ns):
                _copy_files(global_home, target, ("auth.json",))
    return target


def _environment(host: str, folder: Path, snapshot: Path) -> dict[str, str]:
    keep = {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "SYSTEMDRIVE", "PATHEXT", "LANG", "LC_ALL"}
    env = {key: value for key, value in os.environ.items() if key.upper() in keep}
    home, native = folder / "home", folder / "native"
    dirs = {
        "HOME": home, "USERPROFILE": home,
        "APPDATA": home / "AppData" / "Roaming", "LOCALAPPDATA": home / "AppData" / "Local",
        "XDG_CONFIG_HOME": home / "config", "XDG_CACHE_HOME": home / "cache", "XDG_DATA_HOME": home / "data",
        "TEMP": folder / "tmp", "TMP": folder / "tmp",
    }
    for key, path in dirs.items():
        path.mkdir(parents=True, exist_ok=True)
        env[key] = str(path)
    (folder / "work").mkdir()
    names = ("settings.json", "models.json", "auth.json") if host == "pi" else ("config.toml", "auth.json", "models_cache.json")
    _copy_files(snapshot, native, names)
    env.update({"NO_COLOR": "1", "TERM": "dumb", "DO_NOT_TRACK": "1", "OTEL_SDK_DISABLED": "true"})
    if host == "pi":
        env.update({"PI_CODING_AGENT_DIR": str(native), "PI_CODING_AGENT_SESSION_DIR": str(native / "sessions"),
                    "PI_OFFLINE": "1", "PI_SKIP_VERSION_CHECK": "1", "PI_TELEMETRY": "0"})
    else:
        env["CODEX_HOME"] = str(native)
    return env


class _RPC:
    def __init__(self, process, deadline: float, host: str):
        self.process, self.deadline, self.host = process, deadline, host
        self.buffer = b""
        self.total = 0
        self.sequence = 0
        os.set_blocking(process.stdin.fileno(), False)
        os.set_blocking(process.stdout.fileno(), False)

    def _check_deadline(self):
        if time.monotonic() >= self.deadline:
            raise _Unavailable("probe_timeout", "Native capability discovery timed out; check the CLI installation and retry.")

    def send(self, frame: dict) -> None:
        pending = (json.dumps(frame, separators=(",", ":")) + "\n").encode("utf-8")
        while pending:
            self._check_deadline()
            written = self.process.stdin.write(pending)
            if written:
                pending = pending[written:]
            else:
                time.sleep(0.01)

    def _frame(self) -> dict:
        while True:
            self._check_deadline()
            if b"\n" in self.buffer:
                line, self.buffer = self.buffer.split(b"\n", 1)
                if len(line) > _MAX_FRAME:
                    raise _malformed()
                try:
                    frame = json.loads(line)
                except (ValueError, UnicodeDecodeError, RecursionError):
                    raise _malformed() from None
                if not isinstance(frame, dict):
                    raise _malformed()
                return frame
            if len(self.buffer) > _MAX_FRAME:
                raise _malformed()
            chunk = self.process.stdout.read(65536)
            if chunk == b"":
                raise _Unavailable("probe_eof", "The native CLI closed before returning metadata; check its configuration and retry.")
            if chunk is None:
                if self.process.poll() is not None:
                    raise _Unavailable("probe_eof", "The native CLI exited before returning metadata; check its configuration and retry.")
                time.sleep(0.01)
                continue
            self.total += len(chunk)
            if self.total > _MAX_OUTPUT:
                raise _malformed()
            self.buffer += chunk

    def call(self, command: str, params: dict | None = None) -> dict:
        self.sequence += 1
        # String IDs avoid bool/int equality and must be echoed exactly. Never
        # accept a matching command name with a missing/stale/different ID.
        ident = f"cap-{self.sequence}"
        frame = {"id": ident, "type": command} if self.host == "pi" else {"id": ident, "method": command, "params": params or {}}
        self.send(frame)
        while True:
            response = self._frame()
            if "id" not in response:
                if self.host == "codex" and isinstance(response.get("method"), str) and "result" not in response and "error" not in response:
                    continue  # Native status notifications, not RPC responses.
                if self.host == "pi" and response.get("type") != "response" and isinstance(response.get("type"), str):
                    continue
                raise _malformed()
            if type(response["id"]) is not str or response["id"] != ident:
                raise _Unavailable("rpc_id_mismatch", "The native CLI returned an unexpected RPC response; update the CLI and retry.")
            if self.host == "pi":
                if response.get("type") != "response" or response.get("command") != command or type(response.get("success")) is not bool:
                    raise _malformed()
                if not response["success"]:
                    raise _Unavailable("native_rpc_error", "Pi rejected metadata discovery; check its model configuration and retry.")
                data = response.get("data")
            else:
                if "error" in response:
                    raise _Unavailable("native_rpc_error", "Codex rejected metadata discovery; check its native configuration and retry.")
                data = response.get("result")
            if not isinstance(data, dict):
                raise _malformed()
            return data


def _attach_spawn_handle(job, process) -> bool:
    """Windows: reuse our Popen handle when reopening it cannot attach (#424).

    A native smoke reproduced ERROR_ACCESS_DENIED with ProcessJob.attach's
    reopened handle, while the original creation handle attached successfully.
    Never respawn the process or relax the requirement to own its entire tree.
    """
    if job.handle is None:
        return False
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    kernel.AssignProcessToJobObject.restype = ctypes.c_int
    attached = bool(kernel.AssignProcessToJobObject(
        ctypes.c_void_p(job.handle), ctypes.c_void_p(int(process._handle))))
    if attached:
        job.attached += 1
    return attached


class _JobAccounting(ctypes.Structure):
    _fields_ = [(name, kind) for name, kind in (
        ("total_user_time", ctypes.c_int64), ("total_kernel_time", ctypes.c_int64),
        ("period_user_time", ctypes.c_int64), ("period_kernel_time", ctypes.c_int64),
        ("page_faults", ctypes.c_uint32), ("total_processes", ctypes.c_uint32),
        ("active_processes", ctypes.c_uint32), ("terminated_processes", ctypes.c_uint32),
    )]


def _stop_windows_job(job) -> None:
    """Wait for native descendants, not just the npm wrapper, before unlinking.

    Closing the job initiates asynchronous termination. A real Codex metadata
    probe still held SQLite files after its Node wrapper exited, so query our
    anonymous job until all descendants have released their handles (max 2s).
    """
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.TerminateJobObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel.TerminateJobObject.restype = ctypes.c_int
    kernel.QueryInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p]
    kernel.QueryInformationJobObject.restype = ctypes.c_int
    handle, accounting = ctypes.c_void_p(job.handle), _JobAccounting()
    error = "Cannot finish native process cleanup; check process permissions before retrying."
    if not kernel.TerminateJobObject(handle, 1):
        raise _Unavailable("cleanup_failed", error)
    deadline = time.monotonic() + 2.0
    while True:
        if not kernel.QueryInformationJobObject(handle, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None):
            raise _Unavailable("cleanup_failed", error)
        if accounting.active_processes == 0:
            return
        if time.monotonic() >= deadline:
            raise _Unavailable("cleanup_failed", error)
        time.sleep(0.01)


@contextmanager
def _native_rpc(argv: list[str], env: dict, cwd: Path, deadline: float, host: str):
    process = None
    job = ProcessJob()  # Anonymous job: concurrent probes never share ownership.
    try:
        flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
        process = job.spawn(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            bufsize=0, cwd=str(cwd), env=env, shell=False, **flags)
        if os.name == "nt" and not job.attached and not _attach_spawn_handle(job, process):
            raise _Unavailable("cleanup_unavailable", "Cannot safely own the native CLI process tree; check process permissions and retry.")
        yield _RPC(process, deadline, host)
    finally:
        try:
            if process is not None:
                if os.name != "nt":
                    # The root may already have exited while descendants retain
                    # the pipes. Its owned new-session group still needs killing.
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                kill_process_tree(process, grace=1.0)
        finally:
            try:
                if os.name == "nt" and job.attached:
                    _stop_windows_job(job)
            finally:
                job.close()
                if process is not None:
                    try:
                        process.wait(timeout=2.0)
                    except subprocess.TimeoutExpired:
                        raise _Unavailable("cleanup_failed", "The native CLI did not stop; check process permissions before retrying.") from None
                    finally:
                        process.stdin.close()
                        process.stdout.close()


def _pi_model(model) -> str:
    if not isinstance(model, dict):
        raise _malformed()
    if model.get("type", "chat") in ("image", "classifier"):
        raise _Unavailable("unsupported_model", "Select a native chat model, not an image or classifier model.")
    if model.get("type", "chat") != "chat":
        raise _malformed()
    return _native_model(_text(model.get("provider"), 64) + "/" + _text(model.get("id"), 128))


def _pi_state(data: dict) -> tuple[str, str]:
    level = data.get("thinkingLevel")
    if not isinstance(level, str) or not _EFFORT.fullmatch(level):
        raise _malformed()
    return _pi_model(data.get("model")), level


def _pi_probe(result: dict, cli: list[str], root: Path, snapshot: Path, deadline: float, requested: str):
    env = _environment("pi", root / "default", snapshot)
    with _native_rpc([*cli, *_PI_FLAGS], env, root / "default" / "work", deadline, "pi") as rpc:
        default_model, default_effort = _pi_state(rpc.call("get_state"))
        levels = _efforts(rpc.call("get_available_thinking_levels").get("levels"))
        rows = rpc.call("get_available_models").get("models")
    if not isinstance(rows, list) or len(rows) > _MAX_MODELS:
        raise _malformed()
    models = {}
    for row in rows:
        if not isinstance(row, dict):
            raise _malformed()
        if row.get("type", "chat") in ("image", "classifier"):
            continue  # Match the existing chat-models boundary; never offer image tools.
        ident = _pi_model(row)
        if ident in models:
            raise _malformed()
        models[ident] = {"id": ident, "name": _text(row.get("name"))}
    result["models"] = list(models.values())  # [] offline is honest; state is not an auth directory.
    result["defaults"]["model"] = default_model
    result["defaults"]["effort"] = _default_effort(default_effort, levels)
    selected = requested or default_model
    result["selection"]["model"] = selected
    if selected != default_model:
        folder = root / "selected"
        env = _environment("pi", folder, snapshot)
        # Pi's resolver can fuzzy-match; a native readback MUST equal the full
        # requested execution ID before any levels are advertised for it.
        if "/" in selected:
            provider, model_id = selected.split("/", 1)
            args = ["--provider", provider, "--model", model_id]
        else:
            args = ["--model", selected]
        with _native_rpc([*cli, *_PI_FLAGS, *args], env, folder / "work", deadline, "pi") as rpc:
            actual, _ = _pi_state(rpc.call("get_state"))
            if actual != selected:
                raise _Unavailable("unsupported_model", "Pi did not resolve that exact provider/model ID; select a native directory entry and retry.")
            levels = _efforts(rpc.call("get_available_thinking_levels").get("levels"))
    result["selection"]["efforts"] = levels


def _codex_probe(result: dict, cli: list[str], root: Path, snapshot: Path, deadline: float, requested: str):
    folder = root / "default"
    env = _environment("codex", folder, snapshot)
    overrides = ["check_for_update_on_startup=false", "analytics.enabled=false", "feedback.enabled=false",
                 "features.remote_control=false", "features.remote_plugin=false", "features.memories=false",
                 "mcp_servers={}", "project_doc_max_bytes=0", "allow_login_shell=false",
                 "sqlite_home=" + _toml_value(str(folder / "sqlite")), "log_dir=" + _toml_value(str(folder / "logs"))]
    argv = [*cli, *[arg for value in overrides for arg in ("-c", value)], "app-server", "--stdio"]
    rows = []
    with _native_rpc(argv, env, folder / "work", deadline, "codex") as rpc:
        rpc.call("initialize", {"clientInfo": {"name": "firefly-capabilities", "version": "1"}})
        rpc.send({"method": "initialized", "params": {}})
        config = rpc.call("config/read", {"includeLayers": False}).get("config")
        if not isinstance(config, dict):
            raise _malformed()
        cursor, seen = None, set()
        for _ in range(_MAX_PAGES):
            page = rpc.call("model/list", {"includeHidden": False, "limit": 100, "cursor": cursor})
            data = page.get("data")
            if not isinstance(data, list) or "nextCursor" not in page:
                raise _malformed()
            rows.extend(data)
            if len(rows) > _MAX_MODELS:
                raise _malformed()
            cursor = page["nextCursor"]
            if cursor is None:
                break
            if not isinstance(cursor, str) or not cursor or len(cursor) > 1024 or cursor in seen:
                raise _malformed()
            seen.add(cursor)
        else:
            raise _Unavailable("pagination_limit", "The native model directory exceeded the discovery limit; update the CLI and retry.")
    directory = {}
    for row in rows:
        if not isinstance(row, dict) or type(row.get("hidden")) is not bool or type(row.get("isDefault")) is not bool:
            raise _malformed()
        if row["hidden"]:
            continue
        ident = _native_model(row.get("model"))
        _text(row.get("id"), 128)
        if ident in directory:
            raise _malformed()
        supported = row.get("supportedReasoningEfforts")
        if not isinstance(supported, list) or any(not isinstance(item, dict) for item in supported):
            raise _malformed()
        levels = _efforts([item.get("reasoningEffort") for item in supported])
        if "defaultReasoningEffort" not in row:
            raise _malformed()
        directory[ident] = {"name": _text(row.get("displayName")), "efforts": levels,
                            "default": _default_effort(row["defaultReasoningEffort"], levels), "is_default": row["isDefault"]}
    result["models"] = [{"id": ident, "name": row["name"]} for ident, row in directory.items()]
    if not directory:
        raise _Unavailable("models_unavailable", "Codex returned no visible models; check its model provider configuration and retry.")
    if "model" not in config or "model_reasoning_effort" not in config:
        raise _malformed()
    default_model = config["model"]
    if default_model is None:
        native_defaults = [ident for ident, row in directory.items() if row["is_default"]]
        if len(native_defaults) != 1:
            raise _Unavailable("default_model_unknown", "Codex did not identify one default model; configure its native default and retry.")
        default_model = native_defaults[0]
        result["selection"]["source"]["defaults"] += "+model/list.isDefault"
    else:
        default_model = _native_model(default_model)
    result["defaults"]["model"] = default_model
    if default_model not in directory:
        raise _Unavailable("unsupported_default_model", "The Codex configured default is not in its visible native directory; correct its configuration and retry.")
    default = directory[default_model]
    configured_effort = config["model_reasoning_effort"]
    result["defaults"]["effort"] = default["default"] if configured_effort is None else _default_effort(configured_effort, default["efforts"])
    if configured_effort is None:
        result["selection"]["source"]["defaults"] += "+model/list.defaultReasoningEffort"
    selected = requested or default_model
    result["selection"]["model"] = selected
    if selected not in directory:
        raise _Unavailable("unsupported_model", "That model is not in the Codex native directory; select a listed model and retry.")
    result["selection"]["efforts"] = directory[selected]["efforts"]


@contextmanager
def _temporary_snapshot(parent: Path):
    temporary = tempfile.TemporaryDirectory(prefix="probe-", dir=parent)
    try:
        yield Path(temporary.name)
    finally:
        # Windows can briefly retain SQLite handles after job termination.
        # Retry only sharing violations in this probe's own tree, never access
        # denied or other failures, and never report success if cleanup fails.
        deadline = time.monotonic() + 2.0
        while True:
            try:
                temporary.cleanup()
                break
            except OSError as error:
                if os.name != "nt" or getattr(error, "winerror", None) != 32 or time.monotonic() >= deadline:
                    raise
                time.sleep(0.01)


def get_capabilities(host: str, model: str = "") -> dict:
    """Return fresh native metadata or an actionable, sanitized unavailable result.

    Invalid client host/model raises ValueError (router 400). Native failures
    return unavailable, never a guessed model/effort enum. No input effort is
    accepted: None effort means follow the host, not a synthetic option value.
    This synchronous bounded probe should be called off the async router loop.
    """
    if not isinstance(host, str) or host not in ("pi", "codex"):
        raise ValueError("host must be pi or codex")
    if not isinstance(model, str):
        raise ValueError("model must be a string")
    agent_host.validate_spawn_arg("model", model, model=True)
    if any(character.isspace() for character in model):
        raise ValueError("model must not contain whitespace")
    source = {"scope": "native-metadata; file-only; sanitized-environment; account-callability-unverified"}
    if host == "pi":
        source.update({"models": "pi.rpc.get_available_models", "defaults": "pi.rpc.get_state",
                       "efforts": "pi.rpc.get_available_thinking_levels"})
    else:
        source.update({"models": "codex.app-server.model/list", "defaults": "codex.app-server.config/read (configured, not effective)",
                       "efforts": "codex.app-server.model/list.supportedReasoningEfforts"})
    result = {"host": host, "status": "unavailable", "models": [], "defaults": {"model": "", "effort": None},
              "selection": {"model": model, "efforts": [], "source": source}, "error": None}
    try:
        executable = agent_host.resolve_pi_cli() if host == "pi" else shutil.which("codex")
        if not executable:
            raise _Unavailable("cli_missing", "Native CLI not found; install it or correct the server PATH and retry.")
        try:
            cli = agent_host.finalize_spawn_argv([executable])
        except ValueError:
            raise _Unavailable("cli_wrapper_unsupported", "Native CLI wrapper cannot be launched safely; install a standard npm wrapper or executable.") from None
        parent = Path(RUNTIME_ROOT) / "agent-capabilities"
        parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + _PROBE_TIMEOUT
        with _temporary_snapshot(parent) as root:
            snapshot = _snapshot(host, root)
            if host == "pi":
                _pi_probe(result, cli, root, snapshot, deadline, model)
            else:
                _codex_probe(result, cli, root, snapshot, deadline, model)
        result["status"] = "available"
    except _Unavailable as error:
        result["selection"]["efforts"] = []
        result["error"] = {"code": error.code, "message": error.message}
    except OSError:
        # Never expose paths, file contents, auth, raw protocol errors or stderr.
        result["selection"]["efforts"] = []
        result["error"] = {"code": "native_io_error", "message": "Cannot read native metadata or start/clean up the CLI; check installation and file permissions, then retry."}
    return result
