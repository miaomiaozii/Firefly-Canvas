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
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# --- #239：子进程超时必杀 + 退出树杀（防 pi sidecar 僵尸/进程树泄漏，抄 ComfyArt 7eab251 P0） ---

# stop_session：terminate 宽限（POSIX SIGTERM）多久后升级整树杀。
STOP_GRACE_SECONDS = 3.0
# 杀进程后等待其（以及 killer 进程）真正退出的最长时间。
KILL_WAIT_SECONDS = 5.0
# spawn 就绪等待（get_state）超时：Node 冷启动慢，给足余量；超时后 run_command 必杀整树。
RPC_SPAWN_TIMEOUT_SECONDS = 60.0
# 单条 RPC 命令响应超时（wait_for 硬超时 → finally 必杀）。
RPC_TIMEOUT_SECONDS = 10.0
# busy 会话空闲 reap 阈值：15 分钟无活动视为挂死（对照 ComfyArt bot.py 15min reap）。
IDLE_TIMEOUT_SECONDS = 15 * 60
# 空闲 watchdog 扫描间隔。
IDLE_WATCHDOG_INTERVAL_SECONDS = 60.0
# 连续自动 respawn 上限：超过说明 sidecar 处于崩溃循环，停止自愈并终态化。
MAX_CONSECUTIVE_RESPAWNS = 5
# stderr 尾截断：只留尾 800 字（对照 ComfyArt _cli_common.py STDERR_TAIL=800）。
STDERR_TAIL_CHARS = 800
STDERR_MAX_CHUNKS = 200
STDERR_CHUNK_CHARS = 512

# 错误五元组：agent CLI 结果归一为
# {status ∈ done|aborted|error, error_kind ∈ timeout_idle|timeout_hard|crash|stopped|llm_error}
# （对照 ComfyArt bot/providers.py TurnResult）。
RESULT_DONE = "done"
RESULT_ABORTED = "aborted"
RESULT_ERROR = "error"
ERROR_KIND_TIMEOUT_IDLE = "timeout_idle"
ERROR_KIND_TIMEOUT_HARD = "timeout_hard"
ERROR_KIND_CRASH = "crash"
ERROR_KIND_STOPPED = "stopped"
ERROR_KIND_LLM_ERROR = "llm_error"

# 非终态「活」状态：进程已逝但 status 仍处其一会话即孤儿，需终态化（防僵尸 running）。
LIVE_SESSION_STATUSES = frozenset({"starting", "running", "busy", "waiting_confirm"})


def normalize_result(status: str, error_kind: str = "") -> dict:
    """构造归一化结果（错误五元组），取值集合见上方常量。"""
    return {"status": status, "error_kind": error_kind}


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
- canvas.list_nodes / canvas.get_node（查询画布）
- canvas.create_node / canvas.update_node / canvas.delete_node（结构操作）
- canvas.connect / canvas.disconnect（连线操作）
- generate.trigger / generate.status / generate.wait（生成操作）
- media.upload / media.list / media.delete（媒体操作）
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
    status: str = "stopped"  # stopped, starting, running, busy, waiting_confirm, crashed, aborted
    event_handlers: List[Callable] = field(default_factory=list)
    confirm_handlers: List[Callable] = field(default_factory=list)
    _reader_task: Optional[asyncio.Task] = None
    _started_at: float = 0.0
    _last_activity: float = 0.0
    # --- #239：超时必杀 / 退出树杀 / 错误五元组 ---
    _stopping: bool = False  # 手动 stop 进行中：reader finally 不得自动 respawn
    _respawn_count: int = 0  # 连续自动 respawn 次数（spawn 就绪后清零）
    result: dict = field(default_factory=lambda: normalize_result(RESULT_DONE, ""))
    _stderr_chunks: deque = field(default_factory=lambda: deque(maxlen=STDERR_MAX_CHUNKS), repr=False)
    _stderr_thread: Optional[threading.Thread] = None

    @property
    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    @property
    def session_dir(self) -> str:
        """Session files are stored within the project directory."""
        return f"agent_nodes/{self.node_id}/sessions"

    @property
    def stderr_tail(self) -> str:
        """stderr 尾部 STDERR_TAIL_CHARS 字（有界内存，供崩溃诊断）。"""
        return "".join(self._stderr_chunks)[-STDERR_TAIL_CHARS:]


class AgentHostManager:
    """Manages all agent host subprocesses."""

    def __init__(self):
        self.sessions: Dict[str, AgentSession] = {}  # key: f"{project_id}:{node_id}"
        self._lock = asyncio.Lock()
        self._idle_watchdog_task: Optional[asyncio.Task] = None

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
            session._stopping = False
            session.status = "starting"
            session._started_at = time.time()
            session._last_activity = time.time()

            # #239: stderr 由后台线程排空（防管道背压挂死子进程，只留尾 800 字）
            session._stderr_thread = threading.Thread(
                target=self._drain_stderr,
                args=(session,),
                name=f"agent-stderr-{session.node_id[:8]}",
                daemon=True,
            )
            session._stderr_thread.start()

            # Start reader task
            session._reader_task = asyncio.create_task(self._read_events(session))

            # Wait for session to be ready (get_state)。Node 冷启动慢给足余量；
            # #239: 超时后 run_command 的 finally 必杀整棵进程树，不留僵尸 sidecar。
            state = await self.get_session_state(session, timeout=RPC_SPAWN_TIMEOUT_SECONDS)
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
            # #239: spawn 失败路径同样要树杀幸存进程，不留僵尸
            if session.process is not None and session.process.poll() is None:
                await self._kill_process_tree(session, reason="spawn failure")
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
            process = session.process
            if process is None:
                return
            exit_code = process.poll()
            if exit_code is None:
                # #239 退出路径：returncode is None —— reader 退出（手动取消/读异常）
                # 但进程仍活着：必杀整棵进程树并等待退出，不许泄漏。
                await self._kill_process_tree(session, reason="reader exited with live process")
                exit_code = process.poll()
            if exit_code is None:
                # 理论上不可达：杀后等待超时仍未退出，记异常告警
                logger.error(f"Agent process {session.node_id} (pid={process.pid}) still alive after kill; giving up")
                return
            if session._stopping:
                # stop_session 是终态写入方（aborted/stopped），此处不覆盖 result
                session.status = "stopped"
            else:
                session.status = "crashed" if exit_code != 0 else "stopped"
                if session.result.get("error_kind") not in (ERROR_KIND_TIMEOUT_HARD, ERROR_KIND_TIMEOUT_IDLE):
                    session.result = normalize_result(
                        RESULT_DONE if exit_code == 0 else RESULT_ERROR,
                        "" if exit_code == 0 else ERROR_KIND_CRASH,
                    )
            logger.warning(f"Agent process {session.node_id} exited with code {exit_code}")
            # Notify handlers
            for handler in session.event_handlers:
                try:
                    await handler(session, {"type": "process_exited", "exit_code": exit_code, "result": dict(session.result)})
                except Exception:
                    pass

            # Auto-respawn if it crashed (non-zero exit) or was running (not manually stopped)
            if not session._stopping and session.status != "stopped":
                if session._respawn_count >= MAX_CONSECUTIVE_RESPAWNS:
                    logger.error(f"Agent {session.node_id} crashed {session._respawn_count}x in a row; stopping auto-respawn")
                else:
                    session._respawn_count += 1
                    logger.info(f"Auto-respawning agent session {session.node_id} (attempt {session._respawn_count})")
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

        # #239 错误五元组：LLM 错误事件归一为 (error, llm_error)
        if event_type in ("error", "llm_error"):
            session.result = normalize_result(RESULT_ERROR, ERROR_KIND_LLM_ERROR)
        # #239: 完成一个真实回合即视为恢复健康，清零连续崩溃计数（崩溃循环永不清零）
        if event_type == "agent_end":
            session._respawn_count = 0

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

    async def run_command(self, session: AgentSession, command: dict, timeout: float = RPC_TIMEOUT_SECONDS, *, kill_on_timeout: bool = True) -> Optional[dict]:
        """#239 run_command 语义：发一条 RPC 命令并等待响应（wait_for 硬超时）。

        到点未响应即认定 sidecar 挂死：``finally`` 中必杀整棵进程树（await killer +
        等待退出），根因结果记为 ``timeout_hard``；reader 路径随后带 --continue
        自动 respawn（ADR-0019 崩溃自愈）。返回响应事件；超时返回 None。
        """
        if not session.is_running:
            raise RuntimeError(f"Agent session {session.node_id} is not running")
        command = dict(command)
        command_id = command.get("id") or f"cmd-{uuid.uuid4().hex[:8]}"
        command["id"] = command_id
        command_type = command.get("type")
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        response = None
        timed_out = False

        def capture_response(sess, event):
            nonlocal response
            if response is not None:
                return
            if event.get("type") == "response" and (
                event.get("command") == command_type or event.get("id") == command_id
            ):
                response = event

        session.event_handlers.append(capture_response)
        try:
            await self.send_command(session, command)
            while loop.time() < deadline and response is None:
                process = session.process
                if process is not None and process.poll() is not None:
                    break  # 进程已退出，不必等满超时
                await asyncio.sleep(0.1)
            if response is None and loop.time() >= deadline:
                timed_out = True
        finally:
            if capture_response in session.event_handlers:
                session.event_handlers.remove(capture_response)
            if timed_out and kill_on_timeout:
                session.result = normalize_result(RESULT_ERROR, ERROR_KIND_TIMEOUT_HARD)
                logger.error(f"Agent {session.node_id}: RPC '{command_type}' timed out after {timeout}s — killing process tree")
                await self._kill_process_tree(session, reason=f"rpc '{command_type}' timeout")
        return response

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

    async def get_fork_messages(self, session: AgentSession, timeout: float = RPC_TIMEOUT_SECONDS) -> List[dict]:
        """Get the list of user messages available for forking.

        Returns a list of {entryId, text} dicts.
        """
        command_id = f"fork-msgs-{uuid.uuid4().hex[:8]}"
        # #239: wait_for 硬超时；超时后 run_command 的 finally 必杀整树并自愈 respawn
        response = await self.run_command(session, {"id": command_id, "type": "get_fork_messages"}, timeout=timeout)

        if response and response.get("success"):
            data = response.get("data", {})
            return data.get("messages", [])
        return []

    async def fork(self, session: AgentSession, entry_id: str, timeout: float = 15.0) -> dict:
        """Fork the conversation from a specific user message.

        Sends the fork RPC command and waits for the response. The session_start
        event only carries previousSessionFile (the OLD file), not the new one,
        so after the fork response we query get_state to capture the new session file.

        Returns {session_id, session_file, cancelled}.
        """
        command_id = f"fork-{uuid.uuid4().hex[:8]}"
        # #239: wait_for 硬超时；超时后 run_command 的 finally 必杀整树并自愈 respawn
        response = await self.run_command(session, {"id": command_id, "type": "fork", "entryId": entry_id}, timeout=timeout)

        if response is None:
            raise TimeoutError(f"Fork did not complete within {timeout}s for agent {session.node_id}")
        if not response.get("success"):
            raise RuntimeError(f"Fork failed: {response.get('error')}")

        data = response.get("data", {})
        if data.get("cancelled"):
            return {"cancelled": True, "session_id": None, "session_file": None}

        # The session has switched to the new fork; query get_state for the new file
        state = await self.get_session_state(session, timeout=timeout) or {}
        session_id = state.get("sessionId")
        session_file = state.get("sessionFile")
        if not session_file:
            raise RuntimeError(f"Could not determine new session file after fork for agent {session.node_id}")
        if session_id:
            session.session_id = session_id
        session.session_file = session_file
        return {"cancelled": False, "session_id": session_id, "session_file": session_file}

    async def switch_branch(self, session: AgentSession, session_file: str, timeout: float = 10.0) -> None:
        """Switch to a different session file (branch).

        Sends the switch_session RPC command (uses sessionPath per pi RPC).
        """
        command_id = f"switch-{uuid.uuid4().hex[:8]}"
        # #239: wait_for 硬超时；超时后 run_command 的 finally 必杀整树并自愈 respawn
        response = await self.run_command(session, {
            "id": command_id,
            "type": "switch_session",
            "sessionPath": session_file,
        }, timeout=timeout)

        if response is None:
            raise TimeoutError(f"switch_session did not complete within {timeout}s for agent {session.node_id}")
        if not response.get("success"):
            raise RuntimeError(f"switch_session failed: {response.get('error')}")
        if response.get("data", {}).get("cancelled"):
            raise RuntimeError("switch_session was cancelled by an extension")

        # Update session state
        if session_file:
            session.session_file = session_file

    async def get_session_state(self, session: AgentSession, timeout: float = RPC_TIMEOUT_SECONDS) -> Optional[dict]:
        """Query the session state."""
        if not session.is_running:
            return None

        command_id = f"st-{uuid.uuid4().hex[:8]}"
        response = await self.run_command(session, {"id": command_id, "type": "get_state"}, timeout=timeout)

        if response and response.get("success"):
            return response.get("data", {})
        return None

    def _drain_stderr(self, session: AgentSession) -> None:
        """#239: 后台线程排空 sidecar stderr（防管道背压挂死子进程，只留尾部）."""
        process = session.process
        if process is None or process.stderr is None:
            return
        try:
            for line in process.stderr:
                chunk = line if len(line) <= STDERR_CHUNK_CHARS else line[-STDERR_CHUNK_CHARS:]
                session._stderr_chunks.append(chunk)
        except (ValueError, OSError):
            pass

    async def _kill_process_tree(self, session: AgentSession, reason: str = "") -> None:
        """#239: 必杀 sidecar 及其整棵任务进程树，并等待退出（killer 与被杀者都 await）。

        Windows 用 ``taskkill /PID <pid> /T /F`` 杀整棵进程树（pi 是 .cmd 包装器，
        下挂 node 及子孙进程，只杀最顶层 Popen 会泄漏整棵树；与 #230 进程树归属门
        及 tools/quality_gate.py stop_process_tree 同一口径）；POSIX 优先 psutil
        （如已安装）杀树，回退直杀顶层。
        """
        process = session.process
        if process is None or process.poll() is not None:
            return
        # fake 测试进程可能没有真实 pid——此时回退直杀顶层（kill()）
        pid = getattr(process, "pid", None)
        try:
            if os.name == "nt" and pid is not None:
                # killer 进程（taskkill）本身需要 await——不等待就无法保证树已死
                await asyncio.to_thread(
                    subprocess.run,
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=KILL_WAIT_SECONDS,
                    check=False,
                )
            else:
                try:
                    import psutil  # type: ignore
                    parent = psutil.Process(pid)
                    for child in parent.children(recursive=True):
                        try:
                            child.kill()
                        except Exception:
                            pass
                except Exception:
                    pass
                try:
                    process.kill()
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"Failed to kill process tree for agent {session.node_id} (pid={pid}): {e}")
            try:
                process.kill()
            except Exception:
                pass
        # 等待顶层进程退出，落定 returncode（收尸，防僵尸）
        try:
            await asyncio.wait_for(asyncio.to_thread(process.wait), timeout=KILL_WAIT_SECONDS)
        except Exception:
            if process.poll() is None:
                logger.error(f"Agent process {session.node_id} (pid={pid}) still alive {KILL_WAIT_SECONDS}s after kill")
        suffix = f" ({reason})" if reason else ""
        logger.info(f"Killed process tree of agent {session.node_id} (pid={pid}, returncode={process.poll()}){suffix}")

    async def stop_session(self, session: AgentSession) -> None:
        """Stop an agent session.

        #239: 先 terminate（POSIX SIGTERM 宽限），超时则必杀整棵进程树
        （Windows taskkill /T /F，await killer + 等待退出），最后清场，无僵尸/树泄漏。
        """
        session._stopping = True

        if session._reader_task:
            reader_task = session._reader_task
            session._reader_task = None
            reader_task.cancel()
            try:
                await reader_task
            except (asyncio.CancelledError, Exception):
                pass

        process = session.process
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except Exception:
                pass
            try:
                await asyncio.wait_for(asyncio.to_thread(process.wait), timeout=STOP_GRACE_SECONDS)
            except asyncio.TimeoutError:
                await self._kill_process_tree(session, reason="stop grace expired")

        # 收尾 stderr drain 线程（管道关闭后其 for 循环自然结束）
        stderr_thread = session._stderr_thread
        if stderr_thread is not None and stderr_thread.is_alive():
            stderr_thread.join(timeout=1.0)
        session._stderr_thread = None

        session.process = None
        session.status = "stopped"
        session.result = normalize_result(RESULT_ABORTED, ERROR_KIND_STOPPED)
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

    async def reap_orphan_sessions(self) -> int:
        """#239 重启孤儿终态化 pass。

        批量把进程已逝但 status 仍为非终态活状态（starting/running/busy/
        waiting_confirm）的会话终态化为 ``aborted``——即 ComfyArt
        chat_store.abort_unfinished + 15min reap 在本代码库的对应物：
        防「僵尸 running」挂在前端状态位上。幂等，可重复调用。
        返回终态化的会话数。
        """
        reaped = 0
        for session in list(self.sessions.values()):
            if session.status not in LIVE_SESSION_STATUSES:
                continue
            process = session.process
            if process is not None and process.poll() is None:
                continue  # 进程仍活着：reader / 空闲 watchdog 负责
            session.status = "aborted"
            session.result = normalize_result(RESULT_ABORTED, ERROR_KIND_STOPPED)
            reaped += 1
            logger.warning(f"Agent session {session.node_id} orphaned (process gone); finalised to aborted")
        return reaped

    async def start_idle_watchdog(self, interval: float = IDLE_WATCHDOG_INTERVAL_SECONDS) -> None:
        """#239 空闲 reap watchdog（启动时调用）。

        busy/waiting_confirm 会话超过 IDLE_TIMEOUT_SECONDS 无活动视为挂死：
        必杀整棵进程树并记 ``timeout_idle`` 根因，reader 路径随后 --continue 自愈 respawn。
        """
        if self._idle_watchdog_task is not None and not self._idle_watchdog_task.done():
            return
        self._idle_watchdog_task = asyncio.get_event_loop().create_task(self._idle_watchdog_loop(interval))

    async def stop_idle_watchdog(self) -> None:
        task = self._idle_watchdog_task
        self._idle_watchdog_task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    async def _idle_watchdog_loop(self, interval: float) -> None:
        while True:
            await asyncio.sleep(interval)
            now = time.time()
            for session in list(self.sessions.values()):
                if session.status not in ("busy", "waiting_confirm"):
                    continue
                idle = now - session._last_activity
                if idle <= IDLE_TIMEOUT_SECONDS:
                    continue
                logger.error(f"Agent {session.node_id} idle for {idle:.0f}s while busy — killing process tree")
                session.result = normalize_result(RESULT_ERROR, ERROR_KIND_TIMEOUT_IDLE)
                await self._kill_process_tree(session, reason="idle timeout")

    async def cleanup_all(self):
        """Stop all sessions (called on shutdown)."""
        await self.stop_idle_watchdog()
        for session in list(self.sessions.values()):
            await self.stop_session(session)
        self.sessions.clear()


# Resolve the main module without re-importing it (isolated-module test design).
import sys as _sys
_app_main = _sys.modules.get(os.environ.get('FIREFLY_MAIN_MODULE', '')) or _sys.modules.get('main') or _sys.modules['__main__']

# Global singleton
agent_host_manager = AgentHostManager()
