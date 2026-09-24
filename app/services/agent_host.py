"""Agent host service: manages pi --mode rpc subprocesses.

Each agent node has a dedicated pi subprocess that communicates via JSONL
over stdin/stdout. This service handles spawning, event forwarding, command
sending, and crash recovery.
"""
import asyncio
import json
import logging
import os
import signal
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# Pi CLI path
PI_CLI = os.environ.get("FIREFLY_PI_CLI", "pi")

# Default system prompt for canvas agents
DEFAULT_SYSTEM_PROMPT = """你是本项目的画布自动化 Agent。你是执行者，不是编排器——不要让用户填表/选任务。

行为准则：
- 简洁、中文、边做边报（每个动作一行活动行 + 必要的解释）
- 小任务直接做；多步任务先给 3 行内计划，等用户确认或用户已明确"直接做"
- 破坏性操作（删除节点/删除媒体）必须先问用户确认
- 不确定用户意图时问，不要猜
- 动手前先 canvas.list_nodes 看现状，避免重复建节点
- 完成任务后，如果生成了新内容，用 viewport.focus 聚焦到产物

可用工具：
- canvas.list_nodes / canvas.get_node / canvas.get_selection（查询画布）
- canvas.create_node / canvas.update_node / canvas.delete_node（结构操作）
- canvas.connect / canvas.disconnect（连线操作）
- generate.trigger / generate.status / generate.wait（生成操作）
- media.upload / media.list（媒体操作）
- viewport.focus（视口聚焦）"""

# Autonomy level system prompt suffixes (appended to DEFAULT_SYSTEM_PROMPT at spawn)
# cautious: current behavior (plan-first for multi-step)
# standard: execute directly, report as you go
# autonomous: execute directly, only safety-floor operations trigger confirmation
AUTONOMY_SYSTEM_PROMPT_SUFFIXES = {
    "cautious": "",
    "standard": "\n\n自治级别：标准——多步任务直接执行，边做边报，不要先给计划等确认。",
    "autonomous": "\n\n自治级别：自主——直接执行任务，不要先给计划等确认。仅项目级删除等真正不可逆操作会触发用户确认（工具层已处理）。",
}

# Safety floor tools: always require confirmation even in autonomous mode
SAFETY_FLOOR_TOOLS = frozenset()  # Currently no project-level delete tool in canvas-tools


@dataclass
class AgentSession:
    """Represents one agent node's pi subprocess session."""
    project_id: str
    node_id: str
    process: Optional[subprocess.Popen] = None
    session_id: Optional[str] = None
    session_file: Optional[str] = None
    model: str = ""
    autonomy: str = "cautious"  # cautious | standard | autonomous
    status: str = "stopped"  # stopped, starting, running, busy, waiting_confirm, crashed
    event_handlers: List[Callable] = field(default_factory=list)
    confirm_handlers: List[Callable] = field(default_factory=list)
    _reader_task: Optional[asyncio.Task] = None
    _started_at: float = 0.0
    _last_activity: float = 0.0

    @property
    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    @property
    def session_dir(self) -> str:
        """Session files are stored within the project directory."""
        return f"agent_nodes/{self.node_id}/sessions"


class AgentHostManager:
    """Manages all agent host subprocesses."""

    def __init__(self):
        self.sessions: Dict[str, AgentSession] = {}  # key: f"{project_id}:{node_id}"
        self._lock = asyncio.Lock()

    def _session_key(self, project_id: str, node_id: str) -> str:
        return f"{project_id}:{node_id}"

    async def get_or_create_session(self, project_id: str, node_id: str, model: str = "", autonomy: str = "cautious") -> AgentSession:
        """Get existing session or create a new one."""
        key = self._session_key(project_id, node_id)
        async with self._lock:
            session = self.sessions.get(key)
            if session and session.is_running:
                return session

            # Create new session
            session = AgentSession(project_id=project_id, node_id=node_id, model=model, autonomy=autonomy)
            self.sessions[key] = session
            return session

    async def spawn_session(self, session: AgentSession, model: str = "", autonomy: str = "", continue_session: bool = False) -> bool:
        """Spawn a pi --mode rpc subprocess for the session.

        Args:
            session: The agent session to spawn.
            model: Optional model override.
            autonomy: Optional autonomy level override (cautious/standard/autonomous).
            continue_session: If True, use --continue to restore previous session.
        """
        if session.is_running:
            return True

        try:
            # Determine session directory
            project_dir = self._get_project_dir(session.project_id)
            session_dir = os.path.join(project_dir, session.session_dir)
            os.makedirs(session_dir, exist_ok=True)

            # Determine effective autonomy level
            effective_autonomy = autonomy or session.autonomy
            if effective_autonomy not in AUTONOMY_SYSTEM_PROMPT_SUFFIXES:
                effective_autonomy = "cautious"

            # Build system prompt with autonomy suffix
            system_prompt = DEFAULT_SYSTEM_PROMPT + AUTONOMY_SYSTEM_PROMPT_SUFFIXES.get(effective_autonomy, "")

            # Build command
            cmd = [
                PI_CLI, "--mode", "rpc",
                "--no-extensions",
                "-e", self._get_canvas_tools_path(),
                "--no-builtin-tools",
                "--tools", "canvas.*,generate.*,media.*,viewport.*",
                "--session-dir", session_dir,
                "--name", f"firefly-agent-{session.node_id[:8]}",
                "--system-prompt", system_prompt,
            ]

            # If continuing an existing session, add --continue flag
            # Auto-detect: if session_dir has existing session files, continue
            if continue_session:
                cmd.append("--continue")
            elif os.listdir(session_dir):
                # Existing session found, continue it
                cmd.append("--continue")

            # Add model if specified
            if model or session.model:
                use_model = model or session.model
                if "/" in use_model:
                    provider, model_id = use_model.split("/", 1)
                    cmd.extend(["--provider", provider, "--model", model_id])
                else:
                    cmd.extend(["--model", use_model])

            # Set environment for the canvas-tools extension
            env = os.environ.copy()
            env["FIREFLY_AGENT_API_BASE"] = os.environ.get("FIREFLY_AGENT_API_BASE", "http://127.0.0.1:8000")
            env["FIREFLY_AGENT_PROJECT_ID"] = session.project_id
            env["FIREFLY_AGENT_NODE_ID"] = session.node_id

            logger.info(f"Spawning pi subprocess for agent {session.node_id}: {' '.join(cmd[:6])}...")

            session.process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                env=env,
            )
            session.status = "starting"
            session._started_at = time.time()
            session._last_activity = time.time()

            # Start reader task
            session._reader_task = asyncio.create_task(self._read_events(session))

            # Wait for session to be ready (get_state)
            state = await self.get_session_state(session)
            if state:
                session.session_id = state.get("sessionId")
                session.session_file = state.get("sessionFile")
                session.status = "running"
                logger.info(f"Agent session ready: {session.session_id}")
                return True
            else:
                logger.warning(f"Agent session did not report state for {session.node_id}")
                return False

        except Exception as e:
            logger.error(f"Failed to spawn agent session {session.node_id}: {e}")
            session.status = "crashed"
            return False

    async def _read_events(self, session: AgentSession):
        """Read JSONL events from the pi subprocess and dispatch them."""
        assert session.process is not None
        try:
            while True:
                line = await asyncio.to_thread(session.process.stdout.readline)
                if not line:
                    break  # EOF - process exited

                line = line.strip()
                if not line:
                    continue

                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    logger.debug(f"Non-JSON output from agent {session.node_id}: {line[:100]}")
                    continue

                session._last_activity = time.time()
                await self._dispatch_event(session, event)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error reading events from agent {session.node_id}: {e}")
        finally:
            # Check if process exited
            exit_code = session.process.poll()
            if exit_code is not None:
                session.status = "crashed" if exit_code != 0 else "stopped"
                logger.warning(f"Agent process {session.node_id} exited with code {exit_code}")
                # Notify handlers
                for handler in session.event_handlers:
                    try:
                        await handler(session, {"type": "process_exited", "exit_code": exit_code})
                    except Exception:
                        pass

                # Auto-respawn if it crashed (non-zero exit) or was running (not manually stopped)
                if session.status != "stopped":
                    logger.info(f"Auto-respawning agent session {session.node_id}")
                    # Use --continue to restore session state
                    await self.spawn_session(session, continue_session=True)

    async def _dispatch_event(self, session: AgentSession, event: dict):
        """Dispatch an event to all registered handlers."""
        event_type = event.get("type", "")

        # Update session status based on event
        if event_type == "agent_start":
            session.status = "busy"
        elif event_type == "agent_end":
            session.status = "running"
        elif event_type == "tool_execution_start":
            session.status = "busy"

        for handler in session.event_handlers:
            try:
                await handler(session, event)
            except Exception as e:
                logger.error(f"Error in event handler for agent {session.node_id}: {e}")

    async def send_command(self, session: AgentSession, command: dict) -> None:
        """Send a JSONL command to the pi subprocess."""
        if not session.is_running:
            raise RuntimeError(f"Agent session {session.node_id} is not running")

        line = json.dumps(command) + "\n"
        session.process.stdin.write(line)
        session.process.stdin.flush()
        session._last_activity = time.time()

    async def send_prompt(self, session: AgentSession, message: str, images: list = None) -> None:
        """Send a prompt to the agent."""
        command = {"type": "prompt", "message": message}
        if images:
            command["images"] = images
        await self.send_command(session, command)
        session.status = "busy"

    async def send_steer(self, session: AgentSession, message: str) -> None:
        """Send a steer command (redirect current turn)."""
        await self.send_command(session, {"type": "steer", "message": message})

    async def send_follow_up(self, session: AgentSession, message: str) -> None:
        """Send a follow-up (queue for after current turn)."""
        await self.send_command(session, {"type": "follow_up", "message": message})

    async def send_abort(self, session: AgentSession) -> None:
        """Abort the current turn."""
        await self.send_command(session, {"type": "abort"})

    async def set_model(self, session: AgentSession, model: str) -> None:
        """Switch the model for a running session via pi RPC set_model command.

        Args:
            session: The agent session.
            model: Model identifier (e.g., "vllm-remote/qwen3.8-27b-w8a16-mtp2" or "gpt-4").
        """
        if "/" in model:
            provider, model_id = model.split("/", 1)
            command = {"type": "set_model", "provider": provider, "modelId": model_id}
        else:
            command = {"type": "set_model", "modelId": model}
        await self.send_command(session, command)
        session.model = model

    async def get_session_state(self, session: AgentSession, timeout: float = 10.0) -> Optional[dict]:
        """Query the session state."""
        if not session.is_running:
            return None

        command_id = f"st-{uuid.uuid4().hex[:8]}"
        await self.send_command(session, {"id": command_id, "type": "get_state"})

        # Wait for response
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        response = None

        # Register a temporary handler to capture the response
        def capture_response(sess, event):
            nonlocal response
            if event.get("type") == "response" and event.get("command") == "get_state":
                response = event

        session.event_handlers.append(capture_response)
        try:
            while loop.time() < deadline and response is None:
                await asyncio.sleep(0.1)
        finally:
            if capture_response in session.event_handlers:
                session.event_handlers.remove(capture_response)

        if response and response.get("success"):
            return response.get("data", {})
        return None

    async def stop_session(self, session: AgentSession) -> None:
        """Stop an agent session."""
        if session._reader_task:
            session._reader_task.cancel()
            try:
                await session._reader_task
            except asyncio.CancelledError:
                pass

        if session.process and session.process.poll() is None:
            try:
                session.process.terminate()
                await asyncio.wait_for(asyncio.to_thread(session.process.wait), timeout=5.0)
            except (asyncio.TimeoutError, subprocess.TimeoutExpired):
                session.process.kill()
            except Exception:
                pass

        session.process = None
        session.status = "stopped"
        logger.info(f"Agent session {session.node_id} stopped")

    async def respawn_session(self, session: AgentSession) -> bool:
        """Respawn a crashed session with --continue."""
        logger.info(f"Respawning crashed agent session {session.node_id}")
        return await self.spawn_session(session)

    def on_event(self, session: AgentSession, handler: Callable):
        """Register an event handler for a session."""
        session.event_handlers.append(handler)

    def on_confirm(self, session: AgentSession, handler: Callable):
        """Register a confirmation handler for a session."""
        session.confirm_handlers.append(handler)

    def _get_project_dir(self, project_id: str) -> str:
        """Get the project directory path."""
        store = _app_main.FIREFLY_PROJECT_STORE
        if store is None:
            raise RuntimeError("Project store not initialized")
        # ProjectStore has a root attribute
        return str(store.root / project_id)

    def _get_canvas_tools_path(self) -> str:
        """Get the path to the canvas-tools.ts extension."""
        return os.path.join(os.path.dirname(os.path.dirname(__file__)), "agent_extensions", "canvas-tools.ts")

    async def restore_sessions(self, project_id: str, node_ids: List[str]) -> int:
        """Restore sessions for existing agent nodes after server restart.

        For each node_id, checks if there's an existing session directory and
        respawns the pi subprocess with --continue to restore the conversation.

        Returns the number of sessions restored.
        """
        restored = 0
        for node_id in node_ids:
            try:
                session = await self.get_or_create_session(project_id, node_id)
                # Check if there's an existing session to continue
                project_dir = self._get_project_dir(project_id)
                session_dir = os.path.join(project_dir, session.session_dir)
                if os.path.isdir(session_dir) and os.listdir(session_dir):
                    # Has existing session, continue it
                    if await self.spawn_session(session, continue_session=True):
                        restored += 1
                        logger.info(f"Restored agent session {node_id}")
                else:
                    # No existing session, start fresh
                    if await self.spawn_session(session):
                        restored += 1
                        logger.info(f"Started new agent session {node_id}")
            except Exception as e:
                logger.error(f"Failed to restore agent session {node_id}: {e}")
        return restored

    async def cleanup_project(self, project_id: str):
        """Stop all agent sessions for a project (called on project trash/purge).

        Kills all running pi subprocesses for the project and removes their
        session records from the manager. Safe to call multiple times (idempotent).
        """
        key_prefix = f"{project_id}:"
        async with self._lock:
            # Collect sessions to stop and their keys
            keys_to_remove = [
                k for k in self.sessions.keys()
                if k.startswith(key_prefix)
            ]
            sessions_to_stop = [self.sessions[k] for k in keys_to_remove]
            # Remove from tracking immediately so concurrent get_or_create
            # doesn't race with cleanup
            for k in keys_to_remove:
                del self.sessions[k]
        # Stop outside the lock to avoid holding it during process I/O
        for session in sessions_to_stop:
            await self.stop_session(session)
        if sessions_to_stop:
            logger.info(f"Cleaned up {len(sessions_to_stop)} agent session(s) for project {project_id}")

    async def cleanup_all(self):
        """Stop all sessions (called on shutdown)."""
        for session in list(self.sessions.values()):
            await self.stop_session(session)
        self.sessions.clear()


# Resolve the main module without re-importing it (isolated-module test design).
import sys as _sys
_app_main = _sys.modules.get(os.environ.get('FIREFLY_MAIN_MODULE', '')) or _sys.modules.get('main') or _sys.modules['__main__']

# Global singleton
agent_host_manager = AgentHostManager()
