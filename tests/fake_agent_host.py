"""Fake agent host for testing agent node TUI without real pi CLI.

This module provides a FakeAgentProcess that simulates a pi --mode rpc subprocess.
It accepts JSONL commands and emits scripted JSONL events, allowing deterministic
testing of event forwarding and TUI rendering in CI.

Usage:
    from tests.fake_agent_host import FakeAgentProcess

    # Create a fake process with scripted events
    fake = FakeAgentProcess(script=[
        {"type": "session", "sessionId": "test-session-1"},
        {"type": "agent_start"},
        {"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": "你好"}},
        {"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": "，我是"}},
        {"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": " Agent"}},
        {"type": "turn_end", "message": {"content": [{"type": "text", "text": "你好，我是 Agent"}]}},
        {"type": "agent_end"},
    ])

    # Use it in tests instead of real subprocess
    session.process = fake
"""
import asyncio
import json
import os
import time
from pathlib import Path
from unittest import mock
from typing import Any, Dict, List, Optional

# Inert stand-in for the pi CLI (see tests/stubs/pi_cli_stub.cmd). Pointing the
# agent host at it keeps a stray test spawn from ever launching a real agent
# subprocess, which the quality gate records as a subprocess-executable event.
STUB_PI_CLI = Path(__file__).resolve().parent / "stubs" / "pi_cli_stub.cmd"


class FakeAgentProcess:
    """Simulates a pi --mode rpc subprocess for testing.

    Accepts JSONL commands written to stdin and emits scripted JSONL events
    on stdout. Commands are collected and can be inspected in tests.
    """

    def __init__(self, script: List[Dict[str, Any]] = None, auto_state: bool = True):
        """
        Args:
            script: List of events to emit in order. If None, no events are emitted.
            auto_state: If True, automatically respond to get_state commands with
                        a default session state.
        """
        self.script = list(script or [])
        self._script_index = 0
        self._commands: List[Dict[str, Any]] = []
        self._stdout_queue: asyncio.Queue = asyncio.Queue()
        self._stdin_lines: List[str] = []
        self._closed = False
        self._returncode = None
        self._auto_state = auto_state
        self._session_id = "fake-session-" + str(int(time.time()))
        self._session_file = f"/tmp/{self._session_id}.jsonl"

        # Pre-load script into stdout queue
        for event in self.script:
            self._stdout_queue.put_nowait(json.dumps(event) + "\n")

    @property
    def stdout(self):
        """Return a fake stdout with readline() method."""
        return self

    @property
    def stdin(self):
        """Return a fake stdin with write() and flush() methods."""
        return self

    def readline(self):
        """Read next line from stdout (blocking for real process, async-safe here)."""
        if self._script_index < len(self.script):
            line = json.dumps(self.script[self._script_index]) + "\n"
            self._script_index += 1
            return line
        return ""

    async def readline_async(self):
        """Async version of readline for asyncio.to_thread compatibility."""
        return self.readline()

    def write(self, data: str):
        """Write to stdin (collect commands)."""
        if data.endswith("\n"):
            line = data.rstrip("\n")
            if line:
                try:
                    command = json.loads(line)
                    self._commands.append(command)

                    # Auto-respond to get_state
                    if self._auto_state and command.get("type") == "get_state":
                        response = {
                            "type": "response",
                            "command": "get_state",
                            "success": True,
                            "data": {
                                "sessionId": self._session_id,
                                "sessionFile": self._session_file,
                                "model": "fake-model",
                            },
                        }
                        self._stdout_queue.put_nowait(json.dumps(response) + "\n")
                except json.JSONDecodeError:
                    pass

    def flush(self):
        """Flush stdin (no-op for fake)."""
        pass

    def poll(self):
        """Check if process has exited."""
        if self._closed:
            return self._returncode
        return None

    @property
    def returncode(self):
        return self._returncode

    def terminate(self):
        """Terminate the fake process."""
        self._closed = True
        self._returncode = 0

    def kill(self):
        """Kill the fake process."""
        self._closed = True
        self._returncode = -9

    def wait(self, timeout=None):
        """Wait for process to exit."""
        if self._closed:
            return self._returncode
        # For fake, just return immediately if closed
        return self._returncode

    @property
    def commands(self):
        """Return list of commands received."""
        return list(self._commands)

    def get_command(self, cmd_type: str) -> Optional[Dict[str, Any]]:
        """Get the most recent command of a given type."""
        for cmd in reversed(self._commands):
            if cmd.get("type") == cmd_type:
                return cmd
        return None

    def reset_script(self, script: List[Dict[str, Any]]):
        """Reset the event script (for multi-turn testing)."""
        self.script = list(script)
        self._script_index = 0
        # Clear the queue and reload
        while not self._stdout_queue.empty():
            try:
                self._stdout_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        for event in self.script:
            self._stdout_queue.put_nowait(json.dumps(event) + "\n")

    def add_events(self, events: List[Dict[str, Any]]):
        """Append events to the script."""
        for event in events:
            self.script.append(event)
            self._stdout_queue.put_nowait(json.dumps(event) + "\n")


def install_stub_pi_cli() -> Dict[str, Optional[str]]:
    """Point the agent host's pi CLI path at the inert stub executable.

    ``app.services.agent_host.PI_CLI`` is read from ``FIREFLY_PI_CLI`` at import
    time and defaults to a bare ``"pi"``, which Windows resolves through PATH to
    ``pi.cmd``. Call this from ``setUpModule`` and pass the return value to
    ``restore_stub_pi_cli`` in ``tearDownModule`` so no test in the module can
    start the real CLI, even if it reaches ``spawn_session`` by accident.
    """
    from app.services import agent_host

    previous = {
        "module": agent_host.PI_CLI,
        "env": os.environ.get("FIREFLY_PI_CLI"),
    }
    agent_host.PI_CLI = str(STUB_PI_CLI)
    os.environ["FIREFLY_PI_CLI"] = str(STUB_PI_CLI)
    return previous


def restore_stub_pi_cli(previous: Dict[str, Optional[str]]) -> None:
    """Undo :func:`install_stub_pi_cli`."""
    from app.services import agent_host

    agent_host.PI_CLI = previous["module"]
    if previous["env"] is None:
        os.environ.pop("FIREFLY_PI_CLI", None)
    else:
        os.environ["FIREFLY_PI_CLI"] = previous["env"]


def install_fake_spawn(
    manager,
    script: Optional[List[Dict[str, Any]]] = None,
):
    """Replace ``manager.spawn_session`` with a subprocess-free fake.

    ``AgentHostManager._read_events`` auto-respawns a crashed session, so any
    test that drives a crash through the reader task also drives the spawn path.
    The fake attaches a :class:`FakeAgentProcess` instead of calling
    ``subprocess.Popen`` and records every call for assertions.

    Returns ``(patcher, calls)``; call ``patcher.stop()`` in ``tearDown``.
    """
    calls: List[Dict[str, Any]] = []

    async def fake_spawn(session, model: str = "", autonomy: str = "", continue_session: bool = False) -> bool:
        calls.append({
            "session": session,
            "model": model,
            "autonomy": autonomy,
            "continue_session": continue_session,
        })
        if session.process is None or session.process.poll() is not None:
            session.process = FakeAgentProcess(script=list(script or []))
        session.status = "running"
        return True

    patcher = mock.patch.object(manager, "spawn_session", side_effect=fake_spawn)
    patcher.start()
    return patcher, calls


def make_streaming_response_script(text: str, chunk_size: int = 2) -> List[Dict[str, Any]]:
    """Create a script that simulates streaming text response.

    Args:
        text: The full response text.
        chunk_size: Number of characters per delta event.
    """
    events = [
        {"type": "agent_start"},
    ]
    for i in range(0, len(text), chunk_size):
        chunk = text[i:i + chunk_size]
        events.append({
            "type": "message_update",
            "assistantMessageEvent": {"type": "text_delta", "delta": chunk},
        })
    events.append({
        "type": "turn_end",
        "message": {"content": [{"type": "text", "text": text}]},
    })
    events.append({"type": "agent_end"})
    return events


def make_tool_call_script(tool_name: str, tool_args: dict, result: Any) -> List[Dict[str, Any]]:
    """Create a script that simulates a tool call."""
    tool_call_id = f"call-{int(time.time() * 1000)}"
    return [
        {"type": "agent_start"},
        {"type": "tool_execution_start", "toolName": tool_name, "toolCallId": tool_call_id, "args": tool_args},
        {"type": "tool_execution_end", "toolCallId": tool_call_id, "result": result, "isError": False},
        {"type": "agent_end"},
    ]


def make_create_node_script(node_id: str, node_type: str, title: str = "") -> List[Dict[str, Any]]:
    """Create a script for canvas.create_node tool call."""
    return make_tool_call_script(
        "canvas.create_node",
        {"type": node_type, "title": title},
        {"success": True, "node_id": node_id},
    )
