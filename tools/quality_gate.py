"""Run Firefly Canvas quality checks inside an isolated, offline envelope."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import http.server
import json
import os
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

from PIL import Image, UnidentifiedImageError

if __package__:
    from tools.complete_generation_lifecycle_evidence import (
        failed_complete_generation_lifecycle_evidence,
        validate_complete_generation_lifecycle_artifacts,
    )
    from tools.composer_flow_evidence import (
        failed_composer_flow_evidence,
        validate_composer_flow_artifacts,
    )
    from tools.edge_compatibility_evidence import (
        failed_edge_compatibility_evidence,
        validate_edge_compatibility_artifacts,
    )
    from tools.generation_lifecycle_evidence import (
        failed_generation_lifecycle_evidence,
        validate_generation_lifecycle_artifacts,
    )
    from tools.long_content_evidence import (
        failed_long_content_evidence,
        validate_long_content_artifacts,
    )
    from tools.native_input_evidence import has_trusted_selector_owner_click
    from tools.project_linked_generation_evidence import (
        failed_project_linked_generation_evidence,
        validate_project_linked_generation_artifacts,
    )
    from tools.ui_integrity_evidence import (
        failed_ui_integrity_evidence,
        validate_ui_integrity_artifacts,
    )
else:
    from complete_generation_lifecycle_evidence import (
        failed_complete_generation_lifecycle_evidence,
        validate_complete_generation_lifecycle_artifacts,
    )
    from composer_flow_evidence import (
        failed_composer_flow_evidence,
        validate_composer_flow_artifacts,
    )
    from edge_compatibility_evidence import (
        failed_edge_compatibility_evidence,
        validate_edge_compatibility_artifacts,
    )
    from generation_lifecycle_evidence import (
        failed_generation_lifecycle_evidence,
        validate_generation_lifecycle_artifacts,
    )
    from long_content_evidence import (
        failed_long_content_evidence,
        validate_long_content_artifacts,
    )
    from native_input_evidence import has_trusted_selector_owner_click
    from project_linked_generation_evidence import (
        failed_project_linked_generation_evidence,
        validate_project_linked_generation_artifacts,
    )
    from ui_integrity_evidence import (
        failed_ui_integrity_evidence,
        validate_ui_integrity_artifacts,
    )


# Declared budgets from MISSION.md and issue #21: 30s / 2min / 5min / 15min.
MODE_BUDGETS = {
    "fast": 30,
    "domain": 120,
    "trial": 300,
    "release": 900,
}

STEP_DEFINITIONS = {
    "javascript-syntax": lambda python: [
        trusted_node_executable(),
        "--check",
        "static/js/smart-canvas.js",
    ],
    "surface-contract": lambda python: [python, "-m", "unittest", "tests.test_product_surface_behavior"],
    "project-contract": lambda python: [
        python,
        "-m",
        "unittest",
        "tests.test_project_store",
        "tests.test_history_deletion_isolation",
        "tests.test_project_history",
        "tests.test_project_video_generation",
        "tests.test_project_image_generation",
        "tests.test_issue40_count8_concurrency",
        "tests.test_issue41_task_state_machine",
        "tests.test_diagnostics",
    ],
    "provider-contract": lambda python: [
        python,
        "-m",
        "unittest",
        "tests.test_provider_contract_registry",
        "tests.test_provider_contract_families",
        "tests.test_provider_contract_template",
    ],
    "native-browser-evidence": lambda python: [python, "tests/core_surface_browser.py"],
    "project-linked-generation-evidence": lambda python: [python, "tests/project_linked_generation_native_browser.py"],
    "generation-lifecycle-evidence": lambda python: [python, "tests/generation_lifecycle_browser.py"],
    "complete-generation-lifecycle-evidence": lambda python: [python, "tests/generation_task_native_browser.py"],
    "composer-flow-evidence": lambda python: [python, "tests/composer_flow_native_browser.py"],
    "long-content-evidence": lambda python: [python, "tests/long_content_native_browser.py"],
    "ui-integrity-evidence": lambda python: [python, "tests/ui_integrity_native_browser.py"],
    "edge-compatibility": lambda python: [python, "tests/edge_compatibility_browser.py"],
    "browser-tracer": lambda python: [python, "tests/conversation_browser.py"],
    "browser-harness-contract": lambda python: [python, "-m", "unittest", "tests.test_conversation_browser_harness"],
}

MODE_STEPS = {
    "fast": ("javascript-syntax", "surface-contract"),
    "domain": (
        "javascript-syntax",
        "surface-contract",
        "project-contract",
        "provider-contract",
        "native-browser-evidence",
        "generation-lifecycle-evidence",
    ),
    "trial": (
        "javascript-syntax",
        "surface-contract",
        "project-contract",
        "provider-contract",
        "native-browser-evidence",
        "project-linked-generation-evidence",
        "generation-lifecycle-evidence",
        "complete-generation-lifecycle-evidence",
        "composer-flow-evidence",
        "long-content-evidence",
        "ui-integrity-evidence",
        "browser-tracer",
    ),
    "release": (
        "javascript-syntax",
        "surface-contract",
        "project-contract",
        "provider-contract",
        "native-browser-evidence",
        "project-linked-generation-evidence",
        "generation-lifecycle-evidence",
        "complete-generation-lifecycle-evidence",
        "composer-flow-evidence",
        "long-content-evidence",
        "ui-integrity-evidence",
        "edge-compatibility",
        "browser-tracer",
        "browser-harness-contract",
    ),
}

NATIVE_BROWSER_VIEWPORTS = ((1920, 1080), (1440, 900), (1280, 720), (1024, 768), (800, 600))
NATIVE_BROWSER_FONTS = ("Inter", "JetBrains Mono", "Space Grotesk")

# Browser evidence journeys produce their own artifact roots, take roughly a
# minute each, and are independent of one another, so the gate runs them as one
# parallel group (see _execution_groups).  Every other step stays sequential.
BROWSER_EVIDENCE_STEP_NAMES = frozenset(
    (
        "native-browser-evidence",
        "project-linked-generation-evidence",
        "generation-lifecycle-evidence",
        "complete-generation-lifecycle-evidence",
        "composer-flow-evidence",
        "long-content-evidence",
        "ui-integrity-evidence",
        "edge-compatibility",
    )
)

# Issue #39 severity policy: a failed critical release journey is always an S1
# blocker; every other failed step is S2 (blocks release by default, recorded
# in trial), guard violations are S0, evidence retries are S1, and
# non-blocking observations (e.g. browser background networking) are S3.
CRITICAL_JOURNEY_STEPS = frozenset(
    (
        "native-browser-evidence",
        "provider-contract",
        "project-linked-generation-evidence",
        "generation-lifecycle-evidence",
        "complete-generation-lifecycle-evidence",
    )
)

SENSITIVE_ENV_MARKERS = (
    "API_KEY",
    "ACCESS_KEY",
    "CREDENTIAL",
    "PASSWORD",
    "PASSWD",
    "PRIVATE_KEY",
    "SECRET",
    "TOKEN",
)

PROVIDER_EXECUTABLE_ENV_NAMES = (
    "ANTIGRAVITY_BIN",
    "AGY_BIN",
    "CODEX_BIN",
    "DREAMINA_BIN",
    "GEMINI_BIN",
    "GPT_IMAGE_2_SKILL_BIN",
    "JIMENG_BIN",
)

PASSTHROUGH_ENV_NAMES = {
    "CI",
    "COMSPEC",
    "LANG",
    "LC_ALL",
    "NO_COLOR",
    "NUMBER_OF_PROCESSORS",
    "OS",
    "PATH",
    "PATHEXT",
    "PROCESSOR_ARCHITECTURE",
    "PROCESSOR_IDENTIFIER",
    "SYSTEMDRIVE",
    "SYSTEMROOT",
    "TERM",
    "WINDIR",
}

# Stock Chrome probes these control-plane hosts even with background services disabled.
# The loopback proxy still denies and records them; only product-visible targets fail the gate.
BROWSER_CONTROL_PLANE_TARGETS = frozenset(
    {
        "accounts.google.com",
        "android.clients.google.com",
        "clients2.google.com",
        "content-autofill.googleapis.com",
        "optimizationguide-pa.googleapis.com",
        "redirector.gvt1.com",
        "www.google.com",
        "www.gstatic.com",
    }
)

# Microsoft Edge opens Microsoft/Bing surfaces on startup (NTP, first-run, update
# checks).  They are browser control-plane traffic too, not product calls, so they
# are recorded as S3 observations instead of blocking S0 guard events.  Suffix
# matching keeps look-alike hosts (e.g. evil-microsoft.com) out of the allow list.
BROWSER_CONTROL_PLANE_SUFFIXES = (
    "bing.com",
    "microsoft.com",
    "msn.com",
)


def is_browser_control_plane_target(target: str) -> bool:
    normalized = (target or "").strip("[]").lower().rstrip(".")
    if not normalized:
        return False
    if normalized in BROWSER_CONTROL_PLANE_TARGETS:
        return True
    return any(
        normalized == suffix or normalized.endswith(f".{suffix}")
        for suffix in BROWSER_CONTROL_PLANE_SUFFIXES
    )

GUARD_SOURCE = r'''
import asyncio
import ipaddress
import json
import os
import socket
import subprocess
import sys

_event_file = os.environ.get("FIREFLY_QUALITY_GATE_GUARD_EVENTS", "")
_child_event_file = os.environ.get("FIREFLY_QUALITY_GATE_CHILD_EVENTS", "")
_blocked_paths = {
    os.path.normcase(os.path.realpath(path))
    for path in json.loads(os.environ.get("FIREFLY_QUALITY_GATE_BLOCKED_EXECUTABLES", "[]"))
    if path
}
_blocked_names = {
    "agy", "agy.exe", "antigravity", "antigravity.exe",
    "codex", "codex.cmd", "codex.exe",
    "dreamina", "dreamina.cmd", "dreamina.exe",
    "gemini", "gemini.cmd", "gemini.exe",
    "gpt-image-2-skill", "gpt-image-2-skill.cmd", "gpt-image-2-skill.exe",
    "jimeng", "jimeng.cmd", "jimeng.exe", "wsl", "wsl.exe",
}
_allowed_paths = {
    os.path.normcase(os.path.realpath(path))
    for path in json.loads(os.environ.get("FIREFLY_QUALITY_GATE_ALLOWED_EXECUTABLES", "[]"))
    if path
}
_python_paths = {os.path.normcase(os.path.realpath(sys.executable))}
_browser_paths = _allowed_paths - _python_paths
_guard_environment_names = {
    "FIREFLY_PROJECTS_ROOT",
    "FIREFLY_QUALITY_GATE",
    "FIREFLY_QUALITY_GATE_ALLOWED_EXECUTABLES",
    "FIREFLY_QUALITY_GATE_BLOCKED_EXECUTABLES",
    "FIREFLY_QUALITY_GATE_BROWSER_PROXY",
    "FIREFLY_QUALITY_GATE_CHILD_EVENTS",
    "FIREFLY_QUALITY_GATE_GUARD_EVENTS",
    "FIREFLY_QUALITY_GATE_RETRY_EVENTS",
    "FIREFLY_RUNTIME_ROOT",
    "FIREFLY_TEST_DISABLE_CLI_PROBES",
    "FIREFLY_TEST_MODE",
    "PYTHONPATH",
    "PYTHONPYCACHEPREFIX",
}


def _record(kind, target):
    if not _event_file:
        return
    event = json.dumps({"kind": kind, "target": str(target)}, ensure_ascii=True) + "\n"
    descriptor = os.open(_event_file, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, event.encode("utf-8"))
    finally:
        os.close(descriptor)


def _record_child(pid, target):
    if not _child_event_file:
        return
    event = json.dumps({"pid": pid, "target": str(target)}, ensure_ascii=True) + "\n"
    descriptor = os.open(_child_event_file, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, event.encode("utf-8"))
    finally:
        os.close(descriptor)


def _argument_text(argument):
    try:
        return os.fsdecode(os.fspath(argument))
    except TypeError:
        return str(argument)


def _is_loopback(host):
    if host is None:
        return True
    text = str(host).strip().strip("[]").lower()
    if text == "localhost" or text.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(text).is_loopback
    except ValueError:
        return False


def _deny_non_loopback(address):
    host = address[0] if isinstance(address, tuple) and address else None
    if not _is_loopback(host):
        _record("network", host)
        raise PermissionError(f"quality gate denied non-localhost network target: {host}")


_real_getaddrinfo = socket.getaddrinfo
def _guarded_getaddrinfo(host, *args, **kwargs):
    if not _is_loopback(host):
        _record("network", host)
        raise PermissionError(f"quality gate denied non-localhost network target: {host}")
    return _real_getaddrinfo(host, *args, **kwargs)
socket.getaddrinfo = _guarded_getaddrinfo


_real_gethostbyname = socket.gethostbyname
def _guarded_gethostbyname(host):
    _deny_non_loopback((host, None))
    return _real_gethostbyname(host)
socket.gethostbyname = _guarded_gethostbyname


_real_gethostbyname_ex = socket.gethostbyname_ex
def _guarded_gethostbyname_ex(host):
    _deny_non_loopback((host, None))
    return _real_gethostbyname_ex(host)
socket.gethostbyname_ex = _guarded_gethostbyname_ex


_real_gethostbyaddr = socket.gethostbyaddr
def _guarded_gethostbyaddr(host):
    _deny_non_loopback((host, None))
    return _real_gethostbyaddr(host)
socket.gethostbyaddr = _guarded_gethostbyaddr


_real_getnameinfo = socket.getnameinfo
def _guarded_getnameinfo(sockaddr, flags):
    _deny_non_loopback(sockaddr)
    return _real_getnameinfo(sockaddr, flags)
socket.getnameinfo = _guarded_getnameinfo


_real_socket_connect = socket.socket.connect
def _guarded_socket_connect(instance, address):
    _deny_non_loopback(address)
    return _real_socket_connect(instance, address)
socket.socket.connect = _guarded_socket_connect


_real_socket_connect_ex = socket.socket.connect_ex
def _guarded_socket_connect_ex(instance, address):
    _deny_non_loopback(address)
    return _real_socket_connect_ex(instance, address)
socket.socket.connect_ex = _guarded_socket_connect_ex


_real_socket_sendto = socket.socket.sendto
def _guarded_socket_sendto(instance, data, *args):
    _deny_non_loopback(args[-1] if args else None)
    return _real_socket_sendto(instance, data, *args)
socket.socket.sendto = _guarded_socket_sendto


if hasattr(socket.socket, "sendmsg"):
    _real_socket_sendmsg = socket.socket.sendmsg
    def _guarded_socket_sendmsg(instance, buffers, *args):
        _deny_non_loopback(args[2] if len(args) >= 3 else None)
        return _real_socket_sendmsg(instance, buffers, *args)
    socket.socket.sendmsg = _guarded_socket_sendmsg


_real_asyncio_create_connection = asyncio.BaseEventLoop.create_connection
async def _guarded_asyncio_create_connection(
    instance, protocol_factory, host=None, port=None, *args, **kwargs
):
    if host is not None:
        _deny_non_loopback((host, port))
    return await _real_asyncio_create_connection(
        instance, protocol_factory, host, port, *args, **kwargs
    )
asyncio.BaseEventLoop.create_connection = _guarded_asyncio_create_connection


_real_asyncio_create_datagram_endpoint = asyncio.BaseEventLoop.create_datagram_endpoint
async def _guarded_asyncio_create_datagram_endpoint(
    instance, protocol_factory, local_addr=None, remote_addr=None, *args, **kwargs
):
    if remote_addr is not None:
        _deny_non_loopback(remote_addr)
    return await _real_asyncio_create_datagram_endpoint(
        instance,
        protocol_factory,
        local_addr=local_addr,
        remote_addr=remote_addr,
        *args,
        **kwargs,
    )
asyncio.BaseEventLoop.create_datagram_endpoint = _guarded_asyncio_create_datagram_endpoint


def _guarded_child_environment(environment):
    child_environment = dict(os.environ if environment is None else environment)
    child_environment.pop("NODE_OPTIONS", None)
    for name in _guard_environment_names:
        value = os.environ.get(name)
        if value is None:
            child_environment.pop(name, None)
        else:
            child_environment[name] = value
    return child_environment


_real_popen = subprocess.Popen
class _GuardedPopen(_real_popen):
    def __init__(self, args, *popen_args, **popen_kwargs):
        command = args if isinstance(args, (list, tuple)) else [args]
        executable = os.fsdecode(
            os.fspath(popen_kwargs.get("executable") or (command[0] if command else ""))
        )
        normalized = os.path.normcase(os.path.realpath(executable)) if executable else ""
        basename = os.path.basename(executable).lower()
        if normalized in _blocked_paths or basename in _blocked_names:
            _record("provider-executable", basename or executable)
            raise PermissionError(f"quality gate denied provider executable: {basename or executable}")
        if popen_kwargs.get("shell") or normalized not in _allowed_paths:
            _record("subprocess-executable", basename or executable)
            raise PermissionError(f"quality gate denied subprocess executable: {basename or executable}")
        if normalized in _python_paths:
            unsafe_flags = {"S", "I", "E"}
            if any(
                _argument_text(argument).startswith("-")
                and not _argument_text(argument).startswith("--")
                and unsafe_flags.intersection(_argument_text(argument)[1:])
                for argument in command[1:]
            ):
                _record("subprocess-executable", basename)
                raise PermissionError(f"quality gate denied unguarded Python executable: {basename}")
        if normalized in _browser_paths:
            if not isinstance(args, (list, tuple)):
                _record("subprocess-executable", basename)
                raise PermissionError(f"quality gate requires structured browser arguments: {basename}")
            command = list(args)
            browser_proxy = os.environ.get("FIREFLY_QUALITY_GATE_BROWSER_PROXY", "http://127.0.0.1:9")
            conflicting_prefixes = (
                "--host-resolver-rules",
                "--no-proxy-server",
                "--proxy-auto-detect",
                "--proxy-bypass-list",
                "--proxy-pac-url",
                "--proxy-server",
            )
            command = [
                command[0],
                *(
                    argument
                    for argument in command[1:]
                    if not _argument_text(argument).lower().startswith(conflicting_prefixes)
                ),
            ]
            command = [_argument_text(argument) for argument in command]
            required_flags = (
                "--disable-background-mode",
                "--disable-client-side-phishing-detection",
                "--disable-component-extensions-with-background-pages",
                "--disable-default-apps",
                "--disable-domain-reliability",
                "--disable-features=AutofillServerCommunication,CertificateTransparencyComponentUpdater,MediaRouter,OptimizationHints,Translate",
                "--disable-quic",
                "--disable-search-engine-choice-screen",
                "--disable-sync",
                "--host-resolver-rules=MAP * 127.0.0.1, EXCLUDE localhost, EXCLUDE 127.0.0.1, EXCLUDE [::1]",
                "--metrics-recording-only",
                "--no-pings",
                f"--proxy-server={browser_proxy}",
                "--proxy-bypass-list=localhost;127.0.0.1;[::1]",
                "--safebrowsing-disable-auto-update",
                "--variations-insecure-server-url=",
                "--variations-server-url=",
            )
            command[1:1] = [flag for flag in required_flags if flag not in command]
            args = command
        popen_kwargs["env"] = _guarded_child_environment(popen_kwargs.get("env"))
        super().__init__(args, *popen_args, **popen_kwargs)
        _record_child(self.pid, basename or executable)
subprocess.Popen = _GuardedPopen


def _deny_os_process(*args, **kwargs):
    target = args[0] if args else "os process API"
    _record("subprocess-executable", target)
    raise PermissionError(f"quality gate denied os process API: {target}")


for _process_api in (
    "system", "spawnl", "spawnle", "spawnlp", "spawnlpe",
    "spawnv", "spawnve", "spawnvp", "spawnvpe",
    "execl", "execle", "execlp", "execlpe",
    "execv", "execve", "execvp", "execvpe", "startfile",
):
    if hasattr(os, _process_api):
        setattr(os, _process_api, _deny_os_process)
'''


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    budget_help = ", ".join(
        f"{mode} ({MODE_BUDGETS[mode]} s)" for mode in MODE_BUDGETS
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "mode",
        choices=tuple(MODE_BUDGETS),
        help=f"quality gate mode and its declared budget: {budget_help}",
    )
    parser.add_argument("--evidence-dir", type=Path)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    return parser.parse_args(argv)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def command_text(command: list[str]) -> str:
    return subprocess.list2cmdline(command) if os.name == "nt" else shlex.join(command)


def sensitive_environment_values() -> list[str]:
    values = {
        value
        for name, value in os.environ.items()
        if value and any(marker in name.upper() for marker in SENSITIVE_ENV_MARKERS)
    }
    return sorted(values, key=len, reverse=True)


def redact_text(value: str, sensitive_values: list[str]) -> str:
    for sensitive_value in sensitive_values:
        if sensitive_value not in value:
            continue
        if len(sensitive_value) < 4:
            return "[REDACTED: text contained a short sensitive value]"
        value = value.replace(sensitive_value, "[REDACTED]")
    return value


def redact_data(value, sensitive_values: list[str]):
    if isinstance(value, str):
        return redact_text(value, sensitive_values)
    if isinstance(value, list):
        return [redact_data(item, sensitive_values) for item in value]
    if isinstance(value, dict):
        return {
            redact_text(key, sensitive_values) if isinstance(key, str) else key: redact_data(
                item,
                sensitive_values,
            )
            for key, item in value.items()
        }
    return value


def append_event(path: Path, kind: str, target: str) -> None:
    record = json.dumps({"kind": kind, "target": target}, ensure_ascii=True) + "\n"
    descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, record.encode("utf-8"))
    finally:
        os.close(descriptor)


class BrowserDenyProxy:
    def __init__(self, event_file: Path):
        self.event_file = event_file
        event_file_path = event_file
        event_lock = threading.Lock()

        class Handler(http.server.BaseHTTPRequestHandler):
            def deny(self):
                if self.command == "CONNECT":
                    target = self.path.rsplit(":", 1)[0].strip("[]")
                else:
                    parsed = urlsplit(self.path)
                    target = parsed.hostname or self.headers.get("Host", "").rsplit(":", 1)[0]
                kind = (
                    "browser-background-network"
                    if is_browser_control_plane_target(target)
                    else "network"
                )
                with event_lock:
                    append_event(event_file_path, kind, target or "unknown browser target")
                self.send_response(403)
                self.send_header("Content-Length", "0")
                self.end_headers()

            do_CONNECT = deny
            do_DELETE = deny
            do_GET = deny
            do_HEAD = deny
            do_OPTIONS = deny
            do_PATCH = deny
            do_POST = deny
            do_PUT = deny

            def __getattr__(self, name):
                if name.startswith("do_"):
                    return self.deny
                raise AttributeError(name)

            def log_message(self, format, *args):
                return

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def file_digest(path: Path) -> str:
    if path.is_symlink():
        return "symlink:" + os.readlink(path)
    if not path.exists():
        return "missing"
    digest = hashlib.sha256()
    try:
        with path.open("rb") as file:
            for chunk in iter(lambda: file.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        return f"error:{type(error).__name__}:{error}"
    return digest.hexdigest()


def trusted_executable_paths() -> list[str]:
    candidates = [Path(sys.executable)]
    candidates.extend(
        (
            Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
            Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
            Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
            Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        )
    )
    return sorted({str(path.resolve()) for path in candidates if path.is_file()})


def trusted_node_executable() -> str:
    candidates = (
        (
            Path(r"C:\Program Files\nodejs\node.exe"),
            Path(r"C:\Program Files (x86)\nodejs\node.exe"),
        )
        if os.name == "nt"
        else (Path("/usr/bin/node"), Path("/usr/local/bin/node"))
    )
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate.resolve())
    return str(candidates[0])


def git_state(repo_root: Path) -> dict:
    try:
        process = subprocess.run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            cwd=repo_root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"available": False, "dirty": None, "entries": [], "error": str(error)}
    if process.returncode != 0:
        return {
            "available": False,
            "dirty": None,
            "entries": [],
            "error": process.stderr.strip() or f"git exited {process.returncode}",
        }
    entries = [line for line in process.stdout.splitlines() if line]
    try:
        changed_files = subprocess.run(
            ["git", "ls-files", "--modified", "--deleted", "--others", "--exclude-standard", "-z"],
            cwd=repo_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"available": False, "dirty": bool(entries), "entries": entries, "error": str(error)}
    if changed_files.returncode != 0:
        return {
            "available": False,
            "dirty": bool(entries),
            "entries": entries,
            "error": changed_files.stderr.decode(errors="replace").strip()
            or f"git ls-files exited {changed_files.returncode}",
        }
    relative_paths = [
        item.decode("utf-8", errors="surrogateescape")
        for item in changed_files.stdout.split(b"\0")
        if item
    ]
    fingerprints = {relative: file_digest(repo_root / relative) for relative in relative_paths}
    return {
        "available": True,
        "dirty": bool(entries),
        "entries": entries,
        "fingerprints": fingerprints,
    }


def create_execution_workspace(repo_root: Path, workspace_root: Path) -> int:
    try:
        process = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=repo_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(f"could not enumerate repository files: {error}") from error
    if process.returncode != 0:
        message = process.stderr.decode(errors="replace").strip()
        raise RuntimeError(message or f"git ls-files exited {process.returncode}")

    workspace_root.mkdir(parents=True, exist_ok=False)
    copied_files = 0
    for item in process.stdout.split(b"\0"):
        if not item:
            continue
        relative = item.decode("utf-8", errors="surrogateescape")
        relative_path = Path(relative)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise RuntimeError(f"repository returned an unsafe path: {relative}")
        source = repo_root / relative_path
        if not source.is_file():
            continue
        destination = workspace_root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        copied_files += 1
    return copied_files


def isolated_environment(
    run_root: Path,
    repo_root: Path,
    browser_proxy_url: str,
) -> dict[str, str]:
    blocked_executables = [
        value
        for name in PROVIDER_EXECUTABLE_ENV_NAMES
        if (value := os.environ.get(name, "").strip())
    ]
    environment = {
        key: value for key, value in os.environ.items() if key.upper() in PASSTHROUGH_ENV_NAMES
    }
    runtime_root = run_root / "runtime"
    projects_root = run_root / "projects"
    home_root = run_root / "home"
    temp_root = run_root / "temp"
    pycache_root = run_root / "pycache"
    guard_root = run_root / "guard"
    guard_events = run_root / "guard-events.jsonl"
    retry_events = run_root / "retry-events.jsonl"
    child_events = run_root / "child-events.jsonl"
    for path in (runtime_root, projects_root, home_root, temp_root, pycache_root, guard_root):
        path.mkdir(parents=True, exist_ok=True)
    (guard_root / "sitecustomize.py").write_text(
        textwrap.dedent(GUARD_SOURCE).lstrip(),
        encoding="utf-8",
    )
    environment.update(
        {
            "FIREFLY_TEST_MODE": "1",
            "FIREFLY_TEST_DISABLE_CLI_PROBES": "1",
            "FIREFLY_QUALITY_GATE": "1",
            "FIREFLY_QUALITY_GATE_ALLOWED_EXECUTABLES": json.dumps(
                trusted_executable_paths()
            ),
            "FIREFLY_QUALITY_GATE_BLOCKED_EXECUTABLES": json.dumps(blocked_executables),
            "FIREFLY_QUALITY_GATE_BROWSER_PROXY": browser_proxy_url,
            # Chrome 151 rejects remote debugging when USERPROFILE is synthetic,
            # even with an explicit temporary user-data-dir. Only the browser
            # subprocess receives this host identity; all writable roots stay temporary.
            "FIREFLY_QUALITY_GATE_BROWSER_USERPROFILE": os.environ.get("USERPROFILE", ""),
            "FIREFLY_QUALITY_GATE_CHILD_EVENTS": str(child_events),
            "FIREFLY_QUALITY_GATE_GUARD_EVENTS": str(guard_events),
            "FIREFLY_QUALITY_GATE_RETRY_EVENTS": str(retry_events),
            "FIREFLY_RUNTIME_ROOT": str(runtime_root),
            "FIREFLY_PROJECTS_ROOT": str(projects_root),
            "HOME": str(home_root),
            "USERPROFILE": str(home_root),
            "APPDATA": str(home_root / "AppData" / "Roaming"),
            "LOCALAPPDATA": str(home_root / "AppData" / "Local"),
            "TEMP": str(temp_root),
            "TMP": str(temp_root),
            "PYTHONPYCACHEPREFIX": str(pycache_root),
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
            "PYTHON": sys.executable,
        }
    )
    environment["PYTHONPATH"] = os.pathsep.join(
        (str(guard_root), str(repo_root))
    )
    return environment


def read_events(
    path: Path,
    sensitive_values: list[str],
    invalid_event: dict,
) -> list[dict]:
    if not path.is_file():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            event = invalid_event
        if isinstance(event, dict):
            events.append(redact_data(event, sensitive_values))
    return events


def read_guard_events(path: Path, sensitive_values: list[str]) -> list[dict]:
    return read_events(
        path,
        sensitive_values,
        {"kind": "guard-log-error", "target": "invalid event record"},
    )


def read_retry_events(path: Path, sensitive_values: list[str]) -> list[dict]:
    return read_events(
        path,
        sensitive_values,
        {"kind": "retry-log-error", "reason": "invalid event record"},
    )


def read_child_events(path: Path, sensitive_values: list[str]) -> list[dict]:
    return read_events(
        path,
        sensitive_values,
        {"pid": None, "target": "invalid child event record"},
    )


def blocking_guard_events(events: list[dict]) -> list[dict]:
    return [event for event in events if event.get("kind") != "browser-background-network"]


# Children whose shutdown tail the gate waits on for longer than ordinary
# subprocesses (see stop_leaked_children).
BROWSER_CHILD_TARGETS = frozenset(
    {
        "brave.exe",
        "chrome.exe",
        "chromium.exe",
        "firefox.exe",
        "msedge.exe",
    }
)


def process_is_running(pid: int) -> bool:
    if os.name == "nt":
        import ctypes

        synchronize = 0x00100000
        wait_timeout = 0x00000102
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
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


def wait_for_process_exit(
    pid: int,
    *,
    timeout: float = 1.0,
    poll_interval: float = 0.05,
) -> bool:
    deadline = time.monotonic() + max(0.0, timeout)
    while process_is_running(pid):
        now = time.monotonic()
        if now >= deadline:
            return False
        time.sleep(max(0.0, min(poll_interval, deadline - now)))
    return True


def stop_process_id(pid: int) -> None:
    if os.name == "nt":
        system_root = Path(os.environ.get("SYSTEMROOT", r"C:\Windows"))
        taskkill = system_root / "System32" / "taskkill.exe"
        try:
            subprocess.run(
                [str(taskkill), "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except (OSError, ProcessLookupError):
        return
    deadline = time.monotonic() + 2
    while process_is_running(pid) and time.monotonic() < deadline:
        time.sleep(0.02)
    if process_is_running(pid):
        try:
            os.kill(pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass


def stop_leaked_children(events: list[dict], guard_events_file: Path) -> None:
    for event in events:
        try:
            pid = int(event.get("pid"))
        except (TypeError, ValueError):
            continue
        target = str(event.get("target") or f"pid {pid}")
        # Windows browsers can take several seconds to finish shutting down
        # after their harness already terminated them; give them a longer
        # grace so a natural shutdown tail is not misreported as a leak.
        grace = 8.0 if target.lower() in BROWSER_CHILD_TARGETS else 1.0
        if pid <= 0 or wait_for_process_exit(pid, timeout=grace):
            continue
        stop_process_id(pid)
        append_event(guard_events_file, "subprocess-leak", target)


def stop_process_tree(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=5,
            )
        except subprocess.TimeoutExpired:
            pass
    else:
        try:
            os.killpg(process.pid, 9)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        process.kill()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def unittest_summary(command: list[str], stdout: str, stderr: str) -> dict | None:
    if "unittest" not in command or "-m" not in command:
        return None
    output = f"{stdout}\n{stderr}"

    def count(label: str) -> int:
        match = re.search(rf"{re.escape(label)}=(\d+)", output)
        return int(match.group(1)) if match else 0

    return {
        "skipped": count("skipped"),
        "expected_failures": count("expected failures"),
        "unexpected_successes": count("unexpected successes"),
    }


def output_reports_skip(stdout: str, stderr: str) -> bool:
    output = f"{stdout}\n{stderr}"
    return bool(
        re.search(r"(?im)^\s*skip(?:ped)?(?:\b|:)", output)
        or re.search(r'"(?:skip|skipped)"\s*:\s*true', output, re.IGNORECASE)
    )


def run_step(
    name: str,
    command: list[str],
    repo_root: Path,
    environment: dict[str, str],
    timeout: float,
    sensitive_values: list[str],
) -> dict:
    started_at = utc_now()
    started = time.monotonic()
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    try:
        process = subprocess.Popen(
            command,
            cwd=repo_root,
            env=environment,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=creationflags,
            start_new_session=os.name != "nt",
        )
    except OSError as error:
        return {
            "name": name,
            "command": command,
            "command_text": command_text(command),
            "started_at": started_at,
            "duration_seconds": round(time.monotonic() - started, 3),
            "status": "precondition-failed",
            "exit_code": None,
            "stdout_tail": "",
            "stderr_tail": redact_text(str(error), sensitive_values),
        }
    try:
        stdout, stderr = process.communicate(timeout=max(timeout, 0.01))
        status = "passed" if process.returncode == 0 else "failed"
        exit_code = process.returncode
    except subprocess.TimeoutExpired:
        stop_process_tree(process)
        try:
            stdout, stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
            stdout, stderr = "", "process output did not close after timeout"
        status = "timed-out"
        exit_code = 124
    summary = unittest_summary(command, stdout, stderr)
    reported_skip = output_reports_skip(stdout, stderr)
    if summary and any(summary.values()) and status == "passed":
        status = "failed"
        exit_code = 1
    if reported_skip and status == "passed":
        status = "failed"
        exit_code = 1
    result = {
        "name": name,
        "command": command,
        "command_text": command_text(command),
        "started_at": started_at,
        "duration_seconds": round(time.monotonic() - started, 3),
        "status": status,
        "exit_code": exit_code,
        "stdout_tail": redact_text(stdout[-4000:], sensitive_values),
        "stderr_tail": redact_text(stderr[-4000:], sensitive_values),
        "reported_skip": reported_skip,
    }
    if summary is not None:
        result["unittest"] = summary
    return result


def write_report(evidence_dir: Path, report: dict) -> None:
    evidence_dir.mkdir(parents=True, exist_ok=True)
    target = evidence_dir / "report.json"
    temporary = evidence_dir / ".report.json.tmp"
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, target)


def default_evidence_dir(mode: str) -> Path:
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    return Path(tempfile.gettempdir()) / "firefly-quality-gate" / f"{stamp}-{mode}-{os.getpid()}"


def effective_budget(declared_budget: int) -> float:
    if os.environ.get("FIREFLY_TEST_MODE") != "1":
        return float(declared_budget)
    raw_value = os.environ.get("FIREFLY_QUALITY_GATE_TEST_BUDGET_SECONDS", "").strip()
    if not raw_value:
        return float(declared_budget)
    try:
        return max(0.1, min(float(raw_value), float(declared_budget)))
    except ValueError:
        return float(declared_budget)


def path_is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def native_browser_step_command(python: str, evidence_dir: Path) -> tuple[list[str], Path]:
    artifact_root = evidence_dir / "native-browser-core"
    command = STEP_DEFINITIONS["native-browser-evidence"](python)
    command.extend(["--artifacts-dir", str(artifact_root)])
    return command, artifact_root


def project_linked_generation_step_command(python: str, evidence_dir: Path) -> tuple[list[str], Path]:
    artifact_root = evidence_dir / "project-linked-generation"
    command = STEP_DEFINITIONS["project-linked-generation-evidence"](python)
    command.extend(["--artifacts-dir", str(artifact_root)])
    return command, artifact_root


def generation_lifecycle_step_command(python: str, evidence_dir: Path) -> tuple[list[str], Path]:
    artifact_root = evidence_dir / "generation-lifecycle"
    command = STEP_DEFINITIONS["generation-lifecycle-evidence"](python)
    command.extend(["--artifacts-dir", str(artifact_root)])
    return command, artifact_root


def complete_generation_lifecycle_step_command(python: str, evidence_dir: Path) -> tuple[list[str], Path]:
    artifact_root = evidence_dir / "complete-generation-lifecycle"
    command = STEP_DEFINITIONS["complete-generation-lifecycle-evidence"](python)
    command.extend(["--artifacts-dir", str(artifact_root)])
    return command, artifact_root


def composer_flow_step_command(python: str, evidence_dir: Path) -> tuple[list[str], Path]:
    artifact_root = evidence_dir / "composer-flow"
    command = STEP_DEFINITIONS["composer-flow-evidence"](python)
    command.extend(["--artifacts-dir", str(artifact_root)])
    return command, artifact_root


def long_content_step_command(python: str, evidence_dir: Path) -> tuple[list[str], Path]:
    artifact_root = evidence_dir / "long-content"
    command = STEP_DEFINITIONS["long-content-evidence"](python)
    command.extend(["--artifacts-dir", str(artifact_root)])
    return command, artifact_root


def ui_integrity_step_command(python: str, evidence_dir: Path) -> tuple[list[str], Path]:
    artifact_root = evidence_dir / "ui-integrity"
    command = STEP_DEFINITIONS["ui-integrity-evidence"](python)
    command.extend(["--artifacts-dir", str(artifact_root)])
    return command, artifact_root


def edge_compatibility_step_command(python: str, evidence_dir: Path) -> tuple[list[str], Path]:
    artifact_root = evidence_dir / "edge-compatibility"
    command = STEP_DEFINITIONS["edge-compatibility"](python)
    command.extend(["--artifacts-dir", str(artifact_root)])
    return command, artifact_root


def browser_step_spec(
    name: str,
    python: str,
    evidence_dir: Path,
) -> tuple[list[str], Path, object, object, str] | None:
    """Resolve a browser evidence step to (command, artifact_root, validator,
    failed-evidence reader, display name); None for non-browser steps."""
    if name == "native-browser-evidence":
        command, artifact_root = native_browser_step_command(python, evidence_dir)
        return (
            command,
            artifact_root,
            validate_native_browser_artifacts,
            failed_native_browser_evidence,
            "native browser",
        )
    if name == "project-linked-generation-evidence":
        command, artifact_root = project_linked_generation_step_command(python, evidence_dir)
        return (
            command,
            artifact_root,
            validate_project_linked_generation_artifacts,
            failed_project_linked_generation_evidence,
            "project linked generation",
        )
    if name == "generation-lifecycle-evidence":
        command, artifact_root = generation_lifecycle_step_command(python, evidence_dir)
        return (
            command,
            artifact_root,
            validate_generation_lifecycle_artifacts,
            failed_generation_lifecycle_evidence,
            "generation lifecycle",
        )
    if name == "complete-generation-lifecycle-evidence":
        command, artifact_root = complete_generation_lifecycle_step_command(python, evidence_dir)
        return (
            command,
            artifact_root,
            validate_complete_generation_lifecycle_artifacts,
            failed_complete_generation_lifecycle_evidence,
            "complete generation lifecycle",
        )
    if name == "composer-flow-evidence":
        command, artifact_root = composer_flow_step_command(python, evidence_dir)
        return (
            command,
            artifact_root,
            validate_composer_flow_artifacts,
            failed_composer_flow_evidence,
            "Composer flow",
        )
    if name == "long-content-evidence":
        command, artifact_root = long_content_step_command(python, evidence_dir)
        return (
            command,
            artifact_root,
            validate_long_content_artifacts,
            failed_long_content_evidence,
            "long content",
        )
    if name == "ui-integrity-evidence":
        command, artifact_root = ui_integrity_step_command(python, evidence_dir)
        return (
            command,
            artifact_root,
            validate_ui_integrity_artifacts,
            failed_ui_integrity_evidence,
            "desktop UI integrity",
        )
    if name == "edge-compatibility":
        command, artifact_root = edge_compatibility_step_command(python, evidence_dir)
        return (
            command,
            artifact_root,
            validate_edge_compatibility_artifacts,
            failed_edge_compatibility_evidence,
            "Edge compatibility",
        )
    return None


def execution_groups(names: tuple[str, ...]) -> list[tuple[str, list[str]]]:
    """Split a mode's step list into ("sequential", [name]) singletons and one
    ("parallel", [...]) group per contiguous run of browser evidence steps."""
    groups: list[tuple[str, list[str]]] = []
    for name in names:
        if name in BROWSER_EVIDENCE_STEP_NAMES:
            if groups and groups[-1][0] == "parallel":
                groups[-1][1].append(name)
            else:
                groups.append(("parallel", [name]))
        else:
            groups.append(("sequential", [name]))
    return groups


# Issue #39 severity policy.  Frequencies F4/F3/F2/F1/FU are recorded per
# finding (default FU with the observation count) and never modify severity.
SEVERITY_POLICY = {
    "S0": {
        "blocks": ["fast", "domain", "trial", "release"],
        "source": "isolated environment guard violations",
    },
    "S1": {
        "blocks": ["fast", "domain", "trial", "release"],
        "source": "evidence retries and failed critical release journeys",
    },
    "S2": {
        "blocks": ["release"],
        "accepted_by": "FIREFLY_QUALITY_GATE_ACCEPT_S2=1",
        "source": "failed non-critical steps (recorded in trial)",
    },
    "S3": {
        "blocks": [],
        "source": "recorded observations (e.g. browser background networking)",
    },
}

SUPPORT_MATRIX = {
    "windows": {
        "chrome": {
            "stable": {
                "modes": ["fast", "domain", "trial", "release"],
                "viewports": [list(viewport) for viewport in NATIVE_BROWSER_VIEWPORTS],
            }
        },
        "edge": {
            "stable": {
                "modes": ["release"],
                "viewports": [[1440, 900]],
            }
        },
    },
    "phone": "out",
    "touch": "out",
    "firefox": "out",
    "safari": "out",
    "non_windows": "out",
}

RETRY_POLICY = {
    "allowed_evidence_retries": 0,
    "first_failure_preserved": True,
    "policy": (
        "any retry evidence fails its owning step and the gate never converts a "
        "failure into success (stricter than the one-retry allowance)"
    ),
}


def build_severity_findings(
    steps: list[dict],
    guard_events: list[dict],
    retry_events: list[dict],
) -> list[dict]:
    """Classify everything the run observed into S0/S1/S2/S3 findings.  Findings
    never change an exit code that honest step execution already produced; they
    make the auditable severity record explicit."""
    findings: list[dict] = []
    for event in guard_events:
        if blocking_guard_events([event]):
            findings.append(
                {
                    "severity": "S0",
                    "source": "guard",
                    "frequency": "FU",
                    "observations": 1,
                    "message": (
                        "isolated environment guard violation: "
                        f"{event.get('kind')} {event.get('target') or ''}".strip()
                    ),
                }
            )
        else:
            findings.append(
                {
                    "severity": "S3",
                    "source": "guard-background",
                    "frequency": "FU",
                    "observations": 1,
                    "message": "recorded browser background networking observation",
                }
            )
    for event in retry_events:
        findings.append(
            {
                "severity": "S1",
                "source": "retry",
                "frequency": "FU",
                "observations": 1,
                "message": "evidence retry recorded; retries never convert failures into success",
            }
        )
    for step in steps:
        status = step.get("status")
        if status == "passed":
            continue
        severity = "S1" if step.get("name") in CRITICAL_JOURNEY_STEPS else "S2"
        findings.append(
            {
                "severity": severity,
                "source": "step",
                "step": step.get("name"),
                "status": status,
                "frequency": "FU",
                "observations": 1,
                "message": step.get("stderr_tail") or f"step {step.get('name')} {status}",
            }
        )
    return findings


def verdict_reasons(
    mode: str,
    final_exit_code: int,
    accept_s2: bool,
    s2_findings: list[dict],
) -> list[str]:
    reasons = [
        "fully offline dry run; no real provider traffic is possible inside the isolated envelope",
        "support matrix: Windows stable Chrome for all modes, Windows stable Edge for release only; phone/touch/Firefox/Safari/non-Windows are out of scope",
        "S0/S1 always block; S2 blocks release by default; S3 is recorded only",
        "evidence retry policy: zero retries allowed, first failure always preserved",
        f"budget {MODE_BUDGETS[mode]}s ({mode} mode)",
    ]
    if final_exit_code != 0:
        reasons.append(f"gate failed with exit code {final_exit_code}")
    if s2_findings:
        if accept_s2:
            reasons.append(
                f"{len(s2_findings)} S2 finding(s) explicitly accepted via "
                "FIREFLY_QUALITY_GATE_ACCEPT_S2=1 (failure evidence preserved, verdict unchanged)"
            )
        else:
            reasons.append(
                f"{len(s2_findings)} S2 finding(s) block release by default; set "
                "FIREFLY_QUALITY_GATE_ACCEPT_S2=1 to accept them explicitly"
            )
    return reasons


def provider_contract_summary(repo_root: Path) -> dict:
    """Summarize the provider contract fixtures the gate exercises (issue #39
    evidence contract: provider contract summaries must be part of the audit)."""
    fixtures_dir = repo_root / "tests" / "fixtures" / "provider_contracts"
    fixture_files = (
        sorted(path.name for path in fixtures_dir.glob("*.json"))
        if fixtures_dir.is_dir()
        else []
    )
    declared_families = 0
    for name in fixture_files:
        try:
            data = json.loads((fixtures_dir / name).read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            family_keys = data.get("family_keys")
            contracts = data.get("contracts")
            if isinstance(family_keys, list):
                declared_families += len(family_keys)
            elif isinstance(contracts, list):
                declared_families += len(contracts)
    return {
        "fixture_files": fixture_files,
        "declared_families": declared_families,
        "contract_test_modules": [
            "tests.test_provider_contract_template",
            "tests.test_provider_contract_registry",
            "tests.test_provider_contract_families",
        ],
        "hard_deny_guard": (
            "sitecustomize guard denies non-loopback networking and blocks provider executables"
        ),
    }


def native_browser_artifact(root: Path, relative: object, label: str) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError(f"{label} must be a non-empty relative path")
    candidate = (root / relative).resolve()
    if not path_is_within(candidate, root) or not candidate.is_file():
        raise ValueError(f"{label} is missing or outside the evidence root: {relative!r}")
    return candidate


def read_native_browser_json(path: Path, label: str) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is not valid UTF-8 JSON: {error}") from error


def validate_project_export_package(path: Path, roundtrip: dict) -> None:
    def read_json(archive: zipfile.ZipFile, name: str) -> object:
        try:
            return json.loads(archive.read(name).decode("utf-8"))
        except (KeyError, UnicodeError, json.JSONDecodeError) as error:
            raise ValueError(f"export package {name} is not valid UTF-8 JSON: {error}") from error

    try:
        with zipfile.ZipFile(path, "r") as archive:
            members = archive.namelist()
            if len(members) != len(set(members)):
                raise ValueError("export package contains duplicate members")
            required = {"manifest.json", "project.json", "canvas.json", "history.json", "media/index.json"}
            if not required.issubset(members):
                raise ValueError("export package is missing required project files")
            if any(Path(name).is_absolute() or ".." in Path(name).parts for name in members):
                raise ValueError("export package contains an unsafe member path")

            manifest = read_json(archive, "manifest.json")
            if not isinstance(manifest, dict) or (
                manifest.get("format") != "firefly-package-v1"
                or manifest.get("project_id") != roundtrip.get("project_id")
            ):
                raise ValueError("export package manifest identity is invalid")
            declared = manifest.get("files")
            if not isinstance(declared, list) or not all(isinstance(item, dict) for item in declared):
                raise ValueError("export package manifest files are invalid")
            declared_by_path = {item.get("path"): item for item in declared}
            if (
                len(declared_by_path) != len(declared)
                or set(declared_by_path) != set(members) - {"manifest.json"}
            ):
                raise ValueError("export package members do not match its manifest")
            for name, item in declared_by_path.items():
                if not isinstance(name, str):
                    raise ValueError("export package manifest contains an invalid path")
                payload = archive.read(name)
                if item.get("size") != len(payload) or item.get("sha256") != hashlib.sha256(payload).hexdigest():
                    raise ValueError(f"export package member hash is invalid: {name}")

            project = read_json(archive, "project.json")
            canvas = read_json(archive, "canvas.json")
            history = read_json(archive, "history.json")
            media_index = read_json(archive, "media/index.json")
            project_id = roundtrip.get("project_id")
            asset_ids = roundtrip.get("media_asset_ids")
            if (
                not isinstance(project, dict)
                or project.get("id") != project_id
                or not isinstance(canvas, dict)
                or canvas.get("id") != project_id
                or canvas.get("project_id") != project_id
                or len(canvas.get("nodes") or []) != roundtrip.get("node_count")
                or len(canvas.get("connections") or []) != roundtrip.get("connection_count")
                or not isinstance(history, dict)
                or not isinstance(history.get("records"), list)
                or roundtrip.get("generated_asset_id")
                not in {
                    record.get("output_asset_id")
                    for record in history["records"]
                    if isinstance(record, dict)
                }
                or not isinstance(media_index, dict)
                or not isinstance(media_index.get("items"), dict)
                or set(media_index["items"]) != set(asset_ids or [])
            ):
                raise ValueError("export package project scope is invalid")
            for asset_id, record in media_index["items"].items():
                if not isinstance(record, dict):
                    raise ValueError("export package media index is invalid")
                filename = record.get("filename")
                member = f"media/{filename}" if isinstance(filename, str) else ""
                if member not in declared_by_path:
                    raise ValueError(f"export package media file is missing: {asset_id}")
                payload = archive.read(member)
                if (
                    record.get("asset_id") != asset_id
                    or record.get("size") != len(payload)
                    or record.get("sha256") != hashlib.sha256(payload).hexdigest()
                ):
                    raise ValueError(f"export package media identity is invalid: {asset_id}")
    except (OSError, zipfile.BadZipFile) as error:
        raise ValueError(f"export package is not a valid .fly archive: {error}") from error


def numeric_coordinate(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def browser_target_tag(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    return re.split(r"[.#]", value.strip(), maxsplit=1)[0].lower()


def browser_points_match(first: object, second: object) -> bool:
    if not isinstance(first, dict) or not isinstance(second, dict):
        return False
    if not all(numeric_coordinate(point.get(axis)) for point in (first, second) for axis in ("x", "y")):
        return False
    return abs(first["x"] - second["x"]) <= 1 and abs(first["y"] - second["y"]) <= 1


def expected_native_focus(operation: dict) -> bool:
    focus = operation.get("focus_after")
    if not isinstance(focus, str) or not focus:
        return False
    if operation.get("action") == "click":
        selector = operation.get("selector")
        if isinstance(selector, str) and ".project-open" in selector:
            return focus.startswith("button.project-open")
        if isinstance(selector, str) and ".project-duplicate" in selector:
            return focus.startswith("button.") and "project-duplicate" in focus.split(".")[1:]
        if isinstance(selector, str) and ".project-cleanup-preview" in selector:
            return focus.startswith("button.") and "project-cleanup-preview" in focus.split(".")[1:]
        if isinstance(selector, str) and ".prompt-node-text" in selector:
            return focus.startswith("textarea.prompt-node-text")
        project_controls = {
            "#showCreate": "button#showCreate",
            "#projectName": "input#projectName",
            '#createForm button[type="submit"]': "button.button.primary",
            '#createMenu [data-create-type="prompt"]': "button.create-card",
            '#createMenu [data-create-type="image"]': "button.create-card",
            "#canvasEmptyImport": "button#canvasEmptyImport",
            "#closeCleanupPreview": "button#closeCleanupPreview",
            "#confirmCleanup": "button#confirmCleanup",
            ".smart-back": "button.smart-back",
        }
        expected = project_controls.get(selector)
        if expected:
            return focus.startswith(expected)
        return focus.startswith("body")
    if operation.get("action") == "drag" and operation.get("selector") == "#shell":
        return focus.startswith("div#shell")
    if operation.get("action") == "keyboard":
        observed_target = operation.get("observed_target")
        if isinstance(observed_target, str) and observed_target.startswith("input#projectName"):
            return focus.startswith("input#projectName")
        if isinstance(observed_target, str) and observed_target.startswith("textarea.prompt-node-text"):
            return focus.startswith("textarea.prompt-node-text")
        if isinstance(observed_target, str) and observed_target.startswith("body"):
            return focus.startswith("body")
        if isinstance(observed_target, str) and observed_target.startswith("div#shell"):
            return focus.startswith("div#shell")
        return False
    return focus.startswith("body")


def validate_image_organization(
    root: Path,
    organization: object,
    screenshot_references: list,
) -> None:
    if not isinstance(organization, dict) or organization.get("status") != "passed":
        raise ValueError("image organization evidence is missing or failed")
    project_id = organization.get("project_id")
    if not isinstance(project_id, str) or not project_id or organization.get("reopened_project_id") != project_id:
        raise ValueError("image organization project identity is invalid")

    operations_path = native_browser_artifact(
        root,
        organization.get("operations"),
        "image organization operations",
    )
    operations = []
    for line_number, line in enumerate(operations_path.read_text(encoding="utf-8").splitlines(), start=1):
        try:
            operation = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(
                f"native image organization operation {line_number} is not valid JSON: {error}"
            ) from error
        if not isinstance(operation, dict):
            raise ValueError(f"native image organization operation {line_number} is invalid")
        operations.append(operation)

    expected_steps = [
        ("click", "empty-import", (), None),
        ("file-upload", "dynamic-input", (), None),
        ("drag", "image-node", (), None),
        ("click", "shell", (), None),
        ("click", "create-image", (), None),
        ("click", "image-drop", (), None),
        ("file-upload", "dynamic-input", (), None),
        ("drag", "image-node", (), None),
        ("drag", "shell", ("Control",), None),
        ("keyboard", "body", ("Control",), "g"),
        ("click", "group-node", (), None),
        ("drag", "group-node", (), None),
        ("drag", "group-resize", (), None),
        ("keyboard", "body", ("Control", "Shift"), "g"),
        ("keyboard", "body", (), "Escape"),
        ("click", "save", (), None),
        ("click", "back", (), None),
        ("click", "project-open", (), None),
    ]
    if len(operations) != len(expected_steps):
        raise ValueError("native image organization journey is incomplete")
    def selector_matches(role: str, selector: object) -> bool:
        if not isinstance(selector, str):
            return False
        exact = {
            "empty-import": "#canvasEmptyImport",
            "dynamic-input": 'body > input[type="file"]:not(#fileInput)',
            "shell": "#shell",
            "create-image": '#createMenu [data-create-type="image"]',
            "save": "#smartSaveButton",
            "back": ".smart-back",
        }
        if role in exact:
            return selector == exact[role]
        if role == "project-open":
            return selector == ".project-open" or selector.endswith(" .project-open")
        if role == "image-node":
            return selector.startswith(".image-node[data-id=") and selector.endswith("]")
        if role == "image-drop":
            return selector.startswith(".image-node[data-id=") and selector.endswith("] .node-drop")
        if role == "group-resize":
            return selector.startswith(".image-node.smart-group-node[data-id=") and selector.endswith("] .node-resize-handle")
        if role == "group-node":
            return selector.startswith(".image-node.smart-group-node[data-id=") and selector.endswith("]")
        return role == "body"

    upload_batches: list[list[dict[str, object]]] = []
    for sequence, (operation, expected) in enumerate(zip(operations, expected_steps), start=1):
        action, selector_role, modifiers, expected_key = expected
        if operation.get("sequence") != sequence or operation.get("action") != action:
            raise ValueError("native image organization journey order is invalid")
        if operation.get("trusted") is not True:
            raise ValueError("native image organization journey contains an untrusted event")
        if action == "keyboard":
            if (
                operation.get("key") != expected_key
                or operation.get("observed_key") != expected_key
                or operation.get("observed_type") != "keyup"
                or operation.get("modifiers") != list(modifiers)
                or operation.get("observed_modifiers") != list(modifiers)
            ):
                raise ValueError("native image organization keyboard evidence is invalid")
            continue
        if not selector_matches(selector_role, operation.get("selector")):
            raise ValueError("native image organization selector evidence is invalid")
        if action == "file-upload":
            files = operation.get("files")
            if (
                operation.get("observed_type") != "change"
                or operation.get("observed_modifiers") != []
                or operation.get("observed_target") != operation.get("focus_after")
                or not str(operation.get("observed_target") or "").startswith("input")
                or not isinstance(files, list)
                or not files
                or not all(isinstance(item, dict) for item in files)
            ):
                raise ValueError("native image organization upload focus evidence is invalid")
            upload_batches.append(files)
            continue
        expected_button = 2 if operation.get("button") == "right" else 0
        if operation.get("observed_button") != expected_button:
            raise ValueError("native image organization mouse button evidence is invalid")
        point = operation.get("point") if action == "click" else operation.get("end")
        expected_observed_type = (
            "contextmenu" if action == "click" and expected_button == 2
            else "click" if action == "click"
            else "mouseup"
        )
        if (
            operation.get("observed_type") != expected_observed_type
            or (action == "click" and operation.get("observed_release_type") != "mouseup")
            or not browser_points_match(point, operation.get("observed_point"))
            or not browser_target_tag(operation.get("hit"))
            or not browser_target_tag(operation.get("observed_target"))
            or not expected_native_focus(operation)
        ):
            raise ValueError("native image organization pointer evidence is invalid")
        if action == "click" and (
            operation.get("unobscured") is not True
            or not has_trusted_selector_owner_click(
                operation,
                button="right" if expected_button == 2 else "left",
            )
        ):
            raise ValueError("native image organization click evidence is obscured or changed owner")
        if action == "drag" and (
            operation.get("observed_within_selector") is not True
            or operation.get("modifiers") != list(modifiers)
            or operation.get("observed_modifiers") != list(modifiers)
        ):
            raise ValueError("native image organization drag evidence is invalid")
        if action == "drag":
            expected_release_selector = (
                "#shell" if selector_role in {"shell", "group-resize"} else operation.get("selector")
            )
            if operation.get("release_selector") != expected_release_selector:
                raise ValueError("native image organization drag release evidence is invalid")

    if len(upload_batches) != 2:
        raise ValueError("image organization upload batches are invalid")
    uploaded_files = [item for batch in upload_batches for item in batch]
    if [item.get("name") for item in uploaded_files] != [
        "landscape.png",
        "portrait.png",
        "square.png",
    ]:
        raise ValueError("image organization upload order is invalid")

    assets = organization.get("assets")
    if not isinstance(assets, list) or len(assets) != 3 or not all(isinstance(item, dict) for item in assets):
        raise ValueError("image organization original media evidence is invalid")
    assets_by_id = {item.get("asset_id"): item for item in assets}
    if len(assets_by_id) != 3 or not all(isinstance(asset_id, str) and asset_id for asset_id in assets_by_id):
        raise ValueError("image organization original media identity is invalid")
    files_by_name = {item.get("name"): item for item in uploaded_files}
    if len(files_by_name) != 3:
        raise ValueError("image organization upload identities are incomplete")
    asset_names = [item.get("name") for item in assets]
    if len(asset_names) != len(set(asset_names)) or set(asset_names) != set(files_by_name):
        raise ValueError("image organization uploaded asset identities are invalid")
    for asset in assets:
        source_width = asset.get("source_width")
        source_height = asset.get("source_height")
        uploaded = files_by_name.get(asset.get("name"))
        if (
            not numeric_coordinate(source_width)
            or not numeric_coordinate(source_height)
            or source_width <= 0
            or source_height <= 0
            or not isinstance(uploaded, dict)
            or uploaded.get("size") != asset.get("size")
            or uploaded.get("sha256") != asset.get("sha256")
        ):
            raise ValueError("image organization original media identity is invalid")
    original_hashes = {asset_id: item.get("sha256") for asset_id, item in assets_by_id.items()}
    if (
        organization.get("media_before") != original_hashes
        or organization.get("media_after") != original_hashes
    ):
        raise ValueError("image organization original media changed during organization")
    if organization.get("reopened_media_hashes") != original_hashes:
        raise ValueError("image organization reopened media hashes are invalid")

    def validate_ratios(value: object, *, expected_ids: set[str], label: str) -> None:
        if (
            not isinstance(value, list)
            or len(value) != len(expected_ids)
            or not all(isinstance(item, dict) for item in value)
        ):
            raise ValueError(f"image organization {label} ratios are missing")
        observed_ids = [item.get("asset_id") for item in value]
        if len(observed_ids) != len(set(observed_ids)) or set(observed_ids) != expected_ids:
            raise ValueError(f"image organization {label} ratio identities are invalid")
        for item in value:
            asset = assets_by_id.get(item.get("asset_id"))
            display_ratio = item.get("display_ratio")
            expected_ratio = asset["source_width"] / asset["source_height"] if asset else 0
            if (
                not numeric_coordinate(display_ratio)
                or display_ratio <= 0
                or abs(display_ratio - expected_ratio) > 0.03
            ):
                raise ValueError(f"image organization {label} aspect ratio changed")

    ordinary_ids = {
        asset_id
        for asset_id, asset in assets_by_id.items()
        if asset.get("name") in {"landscape.png", "portrait.png"}
    }
    all_asset_ids = set(assets_by_id)
    if len(ordinary_ids) != 2:
        raise ValueError("image organization ordinary ratio identities are invalid")
    validate_ratios(organization.get("ordinary_ratios"), expected_ids=ordinary_ids, label="ordinary")
    validate_ratios(organization.get("group_ratios"), expected_ids=all_asset_ids, label="group")
    validate_ratios(organization.get("ungroup_ratios"), expected_ids=all_asset_ids, label="ungroup")
    ungrouped = organization.get("ungrouped")
    validate_ratios(ungrouped, expected_ids=all_asset_ids, label="ungrouped")
    if not isinstance(ungrouped, list):
        raise ValueError("image organization ungroup layout is invalid")
    rectangles = []
    for item in ungrouped:
        rect = item.get("rect")
        if not isinstance(rect, dict):
            raise ValueError("image organization ungroup geometry is invalid")
        values = tuple(rect.get(key) for key in ("x", "y", "width", "height"))
        if not all(numeric_coordinate(value) for value in values) or values[2] <= 0 or values[3] <= 0:
            raise ValueError("image organization ungroup geometry is invalid")
        if item.get("clipped") is not False:
            raise ValueError("image organization ungrouped media is clipped")
        rectangles.append(values)
    for index, (x1, y1, width1, height1) in enumerate(rectangles):
        for x2, y2, width2, height2 in rectangles[index + 1 :]:
            if x1 < x2 + width2 and x2 < x1 + width1 and y1 < y2 + height2 and y2 < y1 + height1:
                raise ValueError("image organization ungrouped media overlaps")

    before = organization.get("group_size_before")
    after = organization.get("group_size_after")
    if not all(
        isinstance(size, dict)
        and all(numeric_coordinate(size.get(axis)) and size[axis] > 0 for axis in ("width", "height"))
        for size in (before, after)
    ) or before == after:
        raise ValueError("image organization group resize evidence is invalid")
    if (
        organization.get("controls_reachable") is not True
        or organization.get("page_horizontal_overflow") is not False
        or organization.get("reopened_preserved") is not True
    ):
        raise ValueError("image organization viewport or reopen evidence is invalid")
    screenshots = {
        organization.get("organized_screenshot"),
        organization.get("ungrouped_screenshot"),
    }
    if None in screenshots or len(screenshots) != 2 or not screenshots.issubset(set(screenshot_references)):
        raise ValueError("image organization screenshots are invalid")


def validate_native_browser_screenshot(path: Path, viewport: tuple[int, int]) -> dict:
    try:
        with Image.open(path) as image:
            image.load()
            if image.format != "PNG":
                raise ValueError(f"screenshot is not a PNG: {path.name!r}")
            if image.size != viewport:
                raise ValueError(
                    f"screenshot dimensions {image.size!r} do not match viewport {viewport!r}: {path.name!r}"
                )
            extrema = image.convert("RGB").getextrema()
    except (OSError, UnidentifiedImageError) as error:
        raise ValueError(f"screenshot is not a decodable PNG: {path.name!r}: {error}") from error
    if not any(low != high for low, high in extrema):
        raise ValueError(f"screenshot has no visible variation: {path.name!r}")
    return {
        "path": str(path),
        "width": viewport[0],
        "height": viewport[1],
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def validate_native_browser_artifacts(artifact_root: Path) -> dict:
    root = artifact_root.resolve()
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError("manifest.json is missing")
    manifest = read_native_browser_json(manifest_path, "manifest.json")
    if not isinstance(manifest, dict):
        raise ValueError("manifest.json must contain an object")
    if manifest.get("schema_version") != 1 or manifest.get("journey") != "core-canvas":
        raise ValueError("manifest identity must be schema 1 for the core-canvas journey")
    if manifest.get("status") != "passed" or manifest.get("first_failure") is not None:
        raise ValueError("manifest must be passed with no first failure")
    if manifest.get("retries") not in (None, [], 0):
        raise ValueError("native browser evidence must not contain retries")

    environment = manifest.get("environment")
    if not isinstance(environment, dict):
        raise ValueError("manifest environment is missing")
    browser = environment.get("browser")
    if not isinstance(browser, dict):
        raise ValueError("manifest browser identity is missing")
    if (
        environment.get("os") != "Windows"
        or environment.get("dpr") != 1
        or environment.get("color_scheme") != "light"
        or environment.get("fonts") != list(NATIVE_BROWSER_FONTS)
        or browser.get("name") != "Google Chrome"
        or browser.get("channel") != "stable"
        or not isinstance(browser.get("version"), str)
        or not browser["version"].strip()
    ):
        raise ValueError("manifest environment is not the fixed Windows Chrome stable profile")

    attempts = manifest.get("attempts")
    if not isinstance(attempts, list) or [attempt.get("viewport") for attempt in attempts if isinstance(attempt, dict)] != [
        list(viewport) for viewport in NATIVE_BROWSER_VIEWPORTS
    ]:
        raise ValueError("attempts must cover the five canonical viewports exactly once and in order")

    validated_screenshots = []
    screenshot_evidence = []
    for attempt, viewport in zip(attempts, NATIVE_BROWSER_VIEWPORTS, strict=True):
        if not isinstance(attempt, dict) or attempt.get("status") != "passed":
            raise ValueError(f"viewport {viewport[0]}x{viewport[1]} did not pass")
        if attempt.get("retry") not in (None, False, 0) or attempt.get("retries") not in (None, [], 0):
            raise ValueError(f"viewport {viewport[0]}x{viewport[1]} contains retry evidence")
        observed = attempt.get("observed")
        expected_fonts = {font: True for font in NATIVE_BROWSER_FONTS}
        if not isinstance(observed, dict) or (
            observed.get("width") != viewport[0]
            or observed.get("height") != viewport[1]
            or observed.get("dpr") != 1
            or observed.get("color_scheme") != "light"
            or observed.get("theme") != "light"
            or observed.get("fonts") != expected_fonts
        ):
            raise ValueError(f"viewport {viewport[0]}x{viewport[1]} environment observation is invalid")
        if attempt.get("unexpected_console_errors") != 0:
            raise ValueError(f"viewport {viewport[0]}x{viewport[1]} has unexpected console errors")

        roundtrip = attempt.get("project_roundtrip")
        if not isinstance(roundtrip, dict) or roundtrip.get("status") != "passed":
            raise ValueError(f"viewport {viewport[0]}x{viewport[1]} has no passed project roundtrip")
        project_id = roundtrip.get("project_id")
        canvas_id = roundtrip.get("canvas_id")
        node_id = roundtrip.get("node_id")
        before_position = roundtrip.get("position_before")
        after_position = roundtrip.get("position_after")
        generated_position_before = roundtrip.get("generated_position_before")
        generated_position_after = roundtrip.get("generated_position_after")
        revision_after_save = roundtrip.get("revision_after_save")
        revision_after_reopen = roundtrip.get("revision_after_reopen")
        media_asset_ids = roundtrip.get("media_asset_ids")
        duplicate_media_asset_ids = roundtrip.get("duplicate_media_asset_ids")
        export_media_asset_ids = roundtrip.get("export_media_asset_ids")
        referenced_asset_id = roundtrip.get("referenced_asset_id")
        generated_asset_id = roundtrip.get("generated_asset_id")
        unreferenced_asset_id = roundtrip.get("unreferenced_asset_id")
        duplicate_project_id = roundtrip.get("duplicate_project_id")
        valid_asset_ids = (
            isinstance(media_asset_ids, list)
            and len(media_asset_ids) == 3
            and len(set(media_asset_ids)) == 3
            and all(isinstance(asset_id, str) and asset_id for asset_id in media_asset_ids)
            and set(media_asset_ids) == {referenced_asset_id, generated_asset_id, unreferenced_asset_id}
        )
        if (
            not isinstance(project_id, str)
            or not project_id
            or roundtrip.get("reopened_project_id") != project_id
            or not isinstance(canvas_id, str)
            or not canvas_id
            or canvas_id != project_id
            or not isinstance(node_id, str)
            or not node_id
            or not isinstance(roundtrip.get("node_count"), int)
            or roundtrip["node_count"] < 3
            or not isinstance(roundtrip.get("connection_count"), int)
            or roundtrip["connection_count"] < 1
            or roundtrip.get("save_status") != "saved"
            or not isinstance(revision_after_save, int)
            or revision_after_save < 1
            or revision_after_reopen != revision_after_save
            or not all(
                isinstance(position, dict)
                and numeric_coordinate(position.get("x"))
                and numeric_coordinate(position.get("y"))
                for position in (before_position, after_position)
            )
            or before_position == after_position
            or roundtrip.get("browser_augmented_save") is not True
            or not all(
                isinstance(position, dict)
                and numeric_coordinate(position.get("x"))
                and numeric_coordinate(position.get("y"))
                for position in (generated_position_before, generated_position_after)
            )
            or generated_position_before == generated_position_after
            or not valid_asset_ids
            or roundtrip.get("history_output_asset_id") != generated_asset_id
            or roundtrip.get("duplicate_via_native_ui") is not True
            or not isinstance(duplicate_project_id, str)
            or not duplicate_project_id
            or duplicate_project_id == project_id
            or roundtrip.get("duplicate_canvas_id") != duplicate_project_id
            or roundtrip.get("duplicate_node_count") != roundtrip.get("node_count")
            or roundtrip.get("duplicate_connection_count") != roundtrip.get("connection_count")
            or duplicate_media_asset_ids != media_asset_ids
            or roundtrip.get("duplicate_provenance_preserved") is not True
            or roundtrip.get("duplicate_paths_independent") is not True
            or roundtrip.get("export_project_id") != project_id
            or export_media_asset_ids != media_asset_ids
            or roundtrip.get("trashed_project_id") != duplicate_project_id
            or roundtrip.get("restored_project_id") != duplicate_project_id
            or roundtrip.get("source_project_preserved") is not True
            or roundtrip.get("restored_duplicate_preserved") is not True
            or roundtrip.get("export_manifest_verified") is not True
            or roundtrip.get("export_media_bytes_preserved") is not True
            or roundtrip.get("export_history_preserved") is not True
            or roundtrip.get("reopened_canvas_preserved") is not True
            or roundtrip.get("reopened_history_preserved") is not True
            or roundtrip.get("reopened_media_preserved") is not True
        ):
            raise ValueError(f"viewport {viewport[0]}x{viewport[1]} project roundtrip evidence is invalid")

        operations_path = native_browser_artifact(root, attempt.get("operations"), "operations")
        operations = []
        try:
            lines = operations_path.read_text(encoding="utf-8").splitlines()
            operations = [json.loads(line) for line in lines if line.strip()]
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ValueError(f"operations are not valid UTF-8 JSON Lines: {error}") from error
        if not operations or not all(isinstance(operation, dict) for operation in operations):
            raise ValueError(f"viewport {viewport[0]}x{viewport[1]} has no operation evidence")
        if [operation.get("sequence") for operation in operations] != list(range(1, len(operations) + 1)):
            raise ValueError(f"viewport {viewport[0]}x{viewport[1]} operation sequence is not contiguous")
        if any(operation.get("trusted") is not True for operation in operations):
            raise ValueError(f"viewport {viewport[0]}x{viewport[1]} contains untrusted input")
        action_names = {operation.get("action") for operation in operations}
        if not {"click", "keyboard", "drag"}.issubset(action_names):
            raise ValueError(f"viewport {viewport[0]}x{viewport[1]} lacks click, keyboard, or drag evidence")
        for operation in operations:
            action = operation.get("action")
            if action == "click":
                point = operation.get("point")
                observed_button = operation.get("observed_button")
                expected_button = 2 if operation.get("button") == "right" else 0
                if (
                    not isinstance(observed_button, int)
                    or isinstance(observed_button, bool)
                    or observed_button != expected_button
                ):
                    raise ValueError(
                        f"viewport {viewport[0]}x{viewport[1]} mouse button evidence is invalid"
                    )
                if (
                    operation.get("unobscured") is not True
                    or not has_trusted_selector_owner_click(
                        operation,
                        button="right" if operation.get("button") == "right" else "left",
                    )
                    or not isinstance(point, dict)
                    or not numeric_coordinate(point.get("x"))
                    or not numeric_coordinate(point.get("y"))
                    or not browser_points_match(point, operation.get("observed_point"))
                    or operation.get("observed_type")
                    != ("contextmenu" if operation.get("button") == "right" else "click")
                    or operation.get("observed_release_type") != "mouseup"
                    or not browser_target_tag(operation.get("hit"))
                    or browser_target_tag(operation.get("hit"))
                    != browser_target_tag(operation.get("observed_target"))
                    or not expected_native_focus(operation)
                ):
                    raise ValueError(f"viewport {viewport[0]}x{viewport[1]} click evidence is invalid")
                if not (0 <= point["x"] < viewport[0] and 0 <= point["y"] < viewport[1]):
                    raise ValueError(f"viewport {viewport[0]}x{viewport[1]} click is outside the viewport")
            if action == "keyboard":
                observed_key = operation.get("observed_key")
                if (
                    operation.get("observed_type") != "keyup"
                    or not browser_target_tag(operation.get("observed_target"))
                    or not expected_native_focus(operation)
                    or (operation.get("key") is not None and observed_key != operation.get("key"))
                    or (operation.get("text_length") and not isinstance(observed_key, str))
                ):
                    raise ValueError(f"viewport {viewport[0]}x{viewport[1]} keyboard evidence is invalid")
            if action == "drag":
                start = operation.get("start")
                end = operation.get("end")
                observed_button = operation.get("observed_button")
                if (
                    not isinstance(observed_button, int)
                    or isinstance(observed_button, bool)
                    or observed_button != 0
                ):
                    raise ValueError(
                        f"viewport {viewport[0]}x{viewport[1]} mouse button evidence is invalid"
                    )
                if operation.get("observed_within_selector") is not True:
                    raise ValueError(
                        f"viewport {viewport[0]}x{viewport[1]} drag ended outside the selected element"
                    )
                if (
                    operation.get("unobscured") is not True
                    or not all(
                        isinstance(point, dict)
                        and numeric_coordinate(point.get("x"))
                        and numeric_coordinate(point.get("y"))
                        for point in (start, end)
                    )
                    or not browser_points_match(end, operation.get("observed_point"))
                    or operation.get("observed_type") != "mouseup"
                    or not browser_target_tag(operation.get("hit"))
                    or not browser_target_tag(operation.get("observed_target"))
                    or not expected_native_focus(operation)
                ):
                    raise ValueError(f"viewport {viewport[0]}x{viewport[1]} drag evidence is invalid")
                if not all(0 <= point["x"] < viewport[0] and 0 <= point["y"] < viewport[1] for point in (start, end)):
                    raise ValueError(f"viewport {viewport[0]}x{viewport[1]} drag is outside the viewport")

        required_project_operations = [
            ("click", "#showCreate"),
            ("click", "#projectName"),
            ("keyboard", "input#projectName"),
            ("click", '#createForm button[type="submit"]'),
            ("right-click", "#shell"),
            ("click", '#createMenu [data-create-type="prompt"]'),
            ("click", ".prompt-node-text"),
            ("keyboard", "textarea.prompt-node-text"),
            ("drag", ".image-node"),
            ("click", "#smartSaveButton"),
            ("click", ".smart-back"),
            ("click", ".project-open"),
            ("drag", ".image-node"),
            ("click", "#smartSaveButton"),
            ("click", ".smart-back"),
            ("click", ".project-duplicate"),
            ("click", ".project-open"),
        ]

        def project_operation_matches(operation: dict, required: tuple[str, str]) -> bool:
            action, target = required
            if action == "keyboard":
                observed_target = operation.get("observed_target")
                return operation.get("action") == "keyboard" and isinstance(observed_target, str) and observed_target.startswith(target)
            selector = operation.get("selector")
            if not isinstance(selector, str):
                return False
            if action == "right-click":
                return operation.get("action") == "click" and operation.get("button") == "right" and selector == target
            selector_matches = target in selector if target.startswith(".") else selector == target
            return operation.get("action") == action and selector_matches

        required_index = 0
        for operation in operations:
            if project_operation_matches(operation, required_project_operations[required_index]):
                required_index += 1
                if required_index == len(required_project_operations):
                    break
        if required_index != len(required_project_operations):
            missing = required_project_operations[required_index]
            raise ValueError(
                f"viewport {viewport[0]}x{viewport[1]} native project journey is missing {missing[0]} {missing[1]}"
            )

        export_package = native_browser_artifact(root, roundtrip.get("export_package"), "export package")
        if export_package.suffix.lower() != ".fly" or export_package.parent != operations_path.parent:
            raise ValueError(f"viewport {viewport[0]}x{viewport[1]} export package is outside its attempt root")
        validate_project_export_package(export_package, roundtrip)

        console_path = native_browser_artifact(root, attempt.get("console_errors"), "console errors")
        requests_path = native_browser_artifact(root, attempt.get("requests"), "requests")
        if not isinstance(read_native_browser_json(console_path, "console errors"), list):
            raise ValueError("console error evidence must contain a JSON list")
        if not isinstance(read_native_browser_json(requests_path, "requests"), list):
            raise ValueError("request evidence must contain a JSON list")

        screenshots = attempt.get("screenshots")
        if not isinstance(screenshots, list) or len(screenshots) < 2:
            raise ValueError(f"viewport {viewport[0]}x{viewport[1]} needs at least two screenshots")
        try:
            validate_image_organization(root, attempt.get("image_organization"), screenshots)
        except ValueError as error:
            raise ValueError(f"viewport {viewport[0]}x{viewport[1]} {error}") from error
        for relative in screenshots:
            screenshot = native_browser_artifact(root, relative, "screenshot")
            validated_screenshots.append(str(screenshot))
            screenshot_evidence.append(validate_native_browser_screenshot(screenshot, viewport))

    return {
        "status": "validated",
        "root": str(root),
        "manifest": str(manifest_path.resolve()),
        "viewport_count": len(attempts),
        "screenshots": validated_screenshots,
        "screenshot_evidence": screenshot_evidence,
        "first_failure": None,
    }


def failed_native_browser_evidence(artifact_root: Path) -> dict:
    root = artifact_root.resolve()
    manifest_path = root / "manifest.json"
    summary = {"status": "unvalidated", "root": str(root), "manifest": str(manifest_path)}
    if not manifest_path.is_file():
        summary["first_failure"] = None
        return summary
    try:
        manifest = read_native_browser_json(manifest_path, "manifest.json")
    except ValueError as error:
        summary["manifest_error"] = str(error)
        summary["first_failure"] = None
        return summary
    summary["first_failure"] = manifest.get("first_failure") if isinstance(manifest, dict) else None
    try:
        if not isinstance(manifest, dict) or (
            manifest.get("schema_version") != 1
            or manifest.get("journey") != "core-canvas"
            or manifest.get("status") != "failed"
        ):
            raise ValueError("failed manifest identity is invalid")
        failure = manifest.get("first_failure")
        if not isinstance(failure, dict) or not isinstance(failure.get("reason"), str):
            raise ValueError("first failure is missing its reason")
        viewport_value = failure.get("viewport")
        if not isinstance(viewport_value, list) or tuple(viewport_value) not in NATIVE_BROWSER_VIEWPORTS:
            raise ValueError("first failure has a non-canonical viewport")
        viewport = tuple(viewport_value)
        operations_path = native_browser_artifact(root, failure.get("operations"), "failure operations")
        try:
            operation_lines = operations_path.read_text(encoding="utf-8").splitlines()
            operations = [json.loads(line) for line in operation_lines if line.strip()]
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ValueError(f"failure operations are not valid UTF-8 JSON Lines: {error}") from error
        if not all(isinstance(operation, dict) for operation in operations):
            raise ValueError("failure operations must contain JSON objects")
        console_path = native_browser_artifact(root, failure.get("console_errors"), "failure console errors")
        requests_path = native_browser_artifact(root, failure.get("requests"), "failure requests")
        if not isinstance(read_native_browser_json(console_path, "failure console errors"), list):
            raise ValueError("failure console errors must contain a JSON list")
        if not isinstance(read_native_browser_json(requests_path, "failure requests"), list):
            raise ValueError("failure requests must contain a JSON list")
        screenshot_path = None
        if failure.get("failure_screenshot") is not None:
            screenshot_path = native_browser_artifact(
                root,
                failure.get("failure_screenshot"),
                "failure screenshot",
            )
            validate_native_browser_screenshot(screenshot_path, viewport)
        summary.update(
            {
                "status": "captured",
                "artifacts": {
                    "operations": str(operations_path),
                    "console_errors": str(console_path),
                    "requests": str(requests_path),
                    "failure_screenshot": str(screenshot_path) if screenshot_path else None,
                },
            }
        )
    except ValueError as error:
        summary["status"] = "invalid"
        summary["artifact_error"] = str(error)
    return summary


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    repo_root = args.repo_root.resolve()
    evidence_dir = (args.evidence_dir or default_evidence_dir(args.mode)).resolve()
    if path_is_within(evidence_dir, repo_root):
        print(
            "quality gate evidence directory must be outside the repository: "
            f"{evidence_dir}",
            file=sys.stderr,
        )
        return 2
    evidence_dir.mkdir(parents=True, exist_ok=True)
    budget = MODE_BUDGETS[args.mode]
    run_budget = effective_budget(budget)
    started_at = utc_now()
    started = time.monotonic()
    before = git_state(repo_root)
    steps = []
    final_exit_code = 0
    sensitive_values = sensitive_environment_values()
    guard_events = []
    retry_events = []
    child_events = []
    boundary_events_failure = None
    workspace_info = {"isolated": True, "copied_files": 0}
    findings: list[dict] = []
    accept_s2 = False
    s2_findings: list[dict] = []

    with tempfile.TemporaryDirectory(prefix="firefly-quality-gate-run-") as temporary:
        run_root = Path(temporary)
        workspace_root = run_root / "workspace"
        try:
            workspace_info["copied_files"] = create_execution_workspace(repo_root, workspace_root)
        except RuntimeError as error:
            steps.append(
                {
                    "name": "execution-workspace",
                    "command": [],
                    "command_text": "",
                    "started_at": utc_now(),
                    "duration_seconds": round(time.monotonic() - started, 3),
                    "status": "precondition-failed",
                    "exit_code": None,
                    "stdout_tail": "",
                    "stderr_tail": redact_text(str(error), sensitive_values),
                    "guard_events": [],
                }
            )
            final_exit_code = 2
        else:
            guard_events_file = run_root / "guard-events.jsonl"
            with BrowserDenyProxy(guard_events_file) as browser_proxy:
                environment = isolated_environment(
                    run_root,
                    workspace_root,
                    browser_proxy.url,
                )
                retry_events_file = Path(environment["FIREFLY_QUALITY_GATE_RETRY_EVENTS"])
                child_events_file = Path(environment["FIREFLY_QUALITY_GATE_CHILD_EVENTS"])
                parallel_workers = max(
                    1,
                    int(os.environ.get("FIREFLY_QUALITY_GATE_PARALLEL_WORKERS", "4")),
                )

                def resolve_step_command(name: str):
                    spec = browser_step_spec(name, sys.executable, evidence_dir)
                    if spec is not None:
                        return spec[0], spec
                    return STEP_DEFINITIONS[name](sys.executable), None

                for group_kind, group_names in execution_groups(MODE_STEPS[args.mode]):
                    remaining = run_budget - (time.monotonic() - started)
                    if remaining <= 0:
                        for name in group_names:
                            command, _spec = resolve_step_command(name)
                            steps.append(
                                {
                                    "name": name,
                                    "command": command,
                                    "command_text": command_text(command),
                                    "started_at": utc_now(),
                                    "duration_seconds": 0,
                                    "status": "timed-out",
                                    "exit_code": 124,
                                    "stdout_tail": "",
                                    "stderr_tail": "mode budget exhausted before step started",
                                    "guard_events": [],
                                    "retry_events": [],
                                }
                            )
                        final_exit_code = 124
                        break
                    if group_kind == "sequential":
                        name = group_names[0]
                        command, spec = resolve_step_command(name)
                        previous_guard_count = len(
                            read_guard_events(guard_events_file, sensitive_values)
                        )
                        previous_retry_count = len(
                            read_retry_events(retry_events_file, sensitive_values)
                        )
                        previous_child_count = len(
                            read_child_events(child_events_file, sensitive_values)
                        )
                        step = run_step(
                            name,
                            command,
                            workspace_root,
                            environment,
                            remaining,
                            sensitive_values,
                        )
                        step_child_events = read_child_events(
                            child_events_file,
                            sensitive_values,
                        )[previous_child_count:]
                        stop_leaked_children(step_child_events, guard_events_file)
                        step_guard_events = read_guard_events(
                            guard_events_file,
                            sensitive_values,
                        )[previous_guard_count:]
                        step_retry_events = read_retry_events(
                            retry_events_file,
                            sensitive_values,
                        )[previous_retry_count:]
                        step["guard_events"] = step_guard_events
                        step["retry_events"] = step_retry_events
                        step["child_events"] = step_child_events
                        if (
                            blocking_guard_events(step_guard_events) or step_retry_events
                        ) and step["status"] == "passed":
                            step["status"] = "failed"
                            step["exit_code"] = 1
                        if spec is not None:
                            _command, artifact_root, browser_validator, failed_browser_evidence, browser_evidence_name = spec
                            if step["status"] == "passed":
                                try:
                                    step["browser_evidence"] = browser_validator(artifact_root)
                                except (OSError, ValueError) as error:
                                    step["status"] = "failed"
                                    step["exit_code"] = 1
                                    step["stderr_tail"] = redact_text(
                                        f"{browser_evidence_name} evidence invalid: {error}",
                                        sensitive_values,
                                    )
                                    step["browser_evidence"] = failed_browser_evidence(artifact_root)
                            else:
                                step["browser_evidence"] = failed_browser_evidence(artifact_root)
                        steps.append(step)
                        if step["status"] == "timed-out":
                            final_exit_code = 124
                            break
                        if step["status"] == "precondition-failed":
                            final_exit_code = 2
                            break
                        if step["status"] != "passed":
                            final_exit_code = 1
                            break
                        continue
                    # Parallel group: every browser evidence step starts with the
                    # same group remaining budget; the mode budget pays for the
                    # group wall clock.  Guard/retry/child events cannot be
                    # attributed to a single member, so they are conservatively
                    # attached to every step in the group.
                    previous_guard_count = len(
                        read_guard_events(guard_events_file, sensitive_values)
                    )
                    previous_retry_count = len(
                        read_retry_events(retry_events_file, sensitive_values)
                    )
                    previous_child_count = len(
                        read_child_events(child_events_file, sensitive_values)
                    )
                    specs = {}
                    commands = {}
                    for name in group_names:
                        spec = browser_step_spec(name, sys.executable, evidence_dir)
                        if spec is None:
                            raise RuntimeError(
                                f"parallel group member is not a browser evidence step: {name}"
                            )
                        specs[name] = spec
                        commands[name] = spec[0]

                    def collect_group_step(name, future):
                        try:
                            return future.result()
                        except Exception as error:  # noqa: BLE001 — never lose a group member
                            return {
                                "name": name,
                                "command": commands[name],
                                "command_text": command_text(commands[name]),
                                "started_at": utc_now(),
                                "duration_seconds": 0,
                                "status": "precondition-failed",
                                "exit_code": None,
                                "stdout_tail": "",
                                "stderr_tail": redact_text(str(error), sensitive_values),
                            }

                    with ThreadPoolExecutor(max_workers=parallel_workers) as pool:
                        futures = {
                            name: pool.submit(
                                run_step,
                                name,
                                commands[name],
                                workspace_root,
                                environment,
                                remaining,
                                sensitive_values,
                            )
                            for name in group_names
                        }
                        group_steps = {
                            name: collect_group_step(name, future)
                            for name, future in futures.items()
                        }
                    group_child_events = read_child_events(
                        child_events_file,
                        sensitive_values,
                    )[previous_child_count:]
                    stop_leaked_children(group_child_events, guard_events_file)
                    group_guard_events = read_guard_events(
                        guard_events_file,
                        sensitive_values,
                    )[previous_guard_count:]
                    group_retry_events = read_retry_events(
                        retry_events_file,
                        sensitive_values,
                    )[previous_retry_count:]
                    group_failed = False
                    for name in group_names:
                        step = group_steps[name]
                        _command, artifact_root, browser_validator, failed_browser_evidence, browser_evidence_name = specs[name]
                        step["guard_events"] = group_guard_events
                        step["retry_events"] = group_retry_events
                        step["child_events"] = group_child_events
                        step["parallel_group"] = True
                        if (
                            blocking_guard_events(group_guard_events) or group_retry_events
                        ) and step["status"] == "passed":
                            step["status"] = "failed"
                            step["exit_code"] = 1
                        if step["status"] == "passed":
                            try:
                                step["browser_evidence"] = browser_validator(artifact_root)
                            except (OSError, ValueError) as error:
                                step["status"] = "failed"
                                step["exit_code"] = 1
                                step["stderr_tail"] = redact_text(
                                    f"{browser_evidence_name} evidence invalid: {error}",
                                    sensitive_values,
                                )
                                step["browser_evidence"] = failed_browser_evidence(artifact_root)
                        else:
                            step["browser_evidence"] = failed_browser_evidence(artifact_root)
                        steps.append(step)
                        if step["status"] == "timed-out":
                            final_exit_code = 124
                            group_failed = True
                        elif step["status"] == "precondition-failed":
                            final_exit_code = 2
                            group_failed = True
                        elif step["status"] != "passed":
                            final_exit_code = 1
                            group_failed = True
                    if group_failed:
                        break

            guard_events = read_guard_events(guard_events_file, sensitive_values)
            retry_events = read_retry_events(retry_events_file, sensitive_values)
            child_events = read_child_events(child_events_file, sensitive_values)
            if final_exit_code == 0 and (
                blocking_guard_events(guard_events) or retry_events
            ):
                final_exit_code = 1
                boundary_events_failure = {
                    "name": "boundary-events",
                    "status": "failed",
                    "exit_code": 1,
                    "message": "guard or retry evidence arrived after step evaluation",
                }
            findings = build_severity_findings(steps, guard_events, retry_events)
            accept_s2 = os.environ.get("FIREFLY_QUALITY_GATE_ACCEPT_S2") == "1"
            s2_findings = [finding for finding in findings if finding["severity"] == "S2"]

    after = git_state(repo_root)
    changed_during_run = before != after
    if changed_during_run and final_exit_code == 0:
        final_exit_code = 1
    first_failure = next((step for step in steps if step["status"] != "passed"), None)
    if first_failure is None and boundary_events_failure is not None:
        first_failure = boundary_events_failure
    if changed_during_run and first_failure is None:
        first_failure = {
            "name": "worktree-preservation",
            "status": "failed",
            "exit_code": 1,
            "message": "worktree contents changed while the quality gate was running",
        }
    if time.monotonic() - started > run_budget and final_exit_code == 0:
        final_exit_code = 124
        first_failure = {
            "name": "mode-budget",
            "status": "timed-out",
            "exit_code": 124,
            "message": "mode budget was exceeded during quality gate finalization",
        }
    report = {
        "schema_version": 1,
        "mode": args.mode,
        "budget_seconds": budget,
        "effective_budget_seconds": run_budget,
        "started_at": started_at,
        "ended_at": utc_now(),
        "duration_seconds": round(time.monotonic() - started, 3),
        "verdict": "passed" if final_exit_code == 0 else "failed",
        "final_exit_code": final_exit_code,
        "evidence_dir": str(evidence_dir),
        "steps": steps,
        "browser_evidence": next(
            (step.get("browser_evidence") for step in steps if step.get("browser_evidence") is not None),
            None,
        ),
        "guard_events": guard_events,
        "retry_events": retry_events,
        "child_events": child_events,
        "first_failure": first_failure,
        "execution_workspace": workspace_info,
        "dirty_worktree": {
            "before": before,
            "after": after,
            "changed_during_run": changed_during_run,
        },
        "support_matrix": SUPPORT_MATRIX,
        "offline_dry_run": True,
        "verdict_reasons": verdict_reasons(args.mode, final_exit_code, accept_s2, s2_findings),
        "severity_frequency_findings": findings,
        "severity_policy": SEVERITY_POLICY,
        "s2_accepted": accept_s2 and bool(s2_findings),
        "retry_policy": RETRY_POLICY,
        "provider_contract_summary": provider_contract_summary(repo_root),
    }
    report = redact_data(report, sensitive_values)
    write_report(evidence_dir, report)
    if time.monotonic() - started > run_budget and final_exit_code == 0:
        final_exit_code = 124
        report["ended_at"] = utc_now()
        report["duration_seconds"] = round(time.monotonic() - started, 3)
        report["verdict"] = "failed"
        report["final_exit_code"] = final_exit_code
        report["first_failure"] = {
            "name": "mode-budget",
            "status": "timed-out",
            "exit_code": 124,
            "message": "mode budget was exceeded while writing quality gate evidence",
        }
        write_report(evidence_dir, report)
    print(
        json.dumps(
            {
                "mode": report["mode"],
                "verdict": report["verdict"],
                "final_exit_code": report["final_exit_code"],
                "duration_seconds": report["duration_seconds"],
                "evidence_dir": report["evidence_dir"],
            },
            ensure_ascii=False,
        )
    )
    return final_exit_code


if __name__ == "__main__":
    raise SystemExit(main())
