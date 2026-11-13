"""Agent host service: manages pi --mode rpc subprocesses.

Each agent node has a dedicated pi subprocess that communicates via JSONL
over stdin/stdout. This service handles spawning, event forwarding, command
sending, and crash recovery.
"""
import asyncio
import inspect
import json
import logging
import os
import shutil
import signal
import subprocess
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from firefly_usage import collect_pi_turn_usage, usage_has_data

logger = logging.getLogger(__name__)

# --- #239：子进程超时必杀 + 退出树杀（防 pi sidecar 僵尸/进程树泄漏，抄 ComfyArt 7eab251 P0） ---

# stop_session：terminate 宽限（POSIX SIGTERM）多久后升级整树杀。
STOP_GRACE_SECONDS = 3.0
# 杀进程后等待其（以及 killer 进程）真正退出的最长时间。
KILL_WAIT_SECONDS = 5.0
# spawn 就绪等待（get_state）超时：Node 冷启动慢，给足余量；超时后 run_command 必杀整树。
RPC_SPAWN_TIMEOUT_SECONDS = 60.0
# #318 票 C：节点级事件回放日志（events.jsonl）与降级重放参数（监理 03:28 裁定）。
EVENTS_LOG_MAX_LINES = 2000      # 每节点保留尾 N 行（截头不清 seq）
EVENTS_REPLAY_TURNS = 20         # 降级重建重放最近 N 轮（不足按实际全量，含 user 侧）
EVENTS_REPLAY_MAX_CHARS = 12000  # 重放文本块字符预算（保最近轮）
# 单条 RPC 命令响应超时（wait_for 硬超时 → finally 必杀）。
RPC_TIMEOUT_SECONDS = 10.0
# busy 会话空闲 reap 阈值：15 分钟无活动视为挂死（对照 ComfyArt bot.py 15min reap）。
IDLE_TIMEOUT_SECONDS = 15 * 60
# 空闲 watchdog 扫描间隔。
IDLE_WATCHDOG_INTERVAL_SECONDS = 60.0
# #286：turn 停顿提醒阈值。busy 会话超过该秒数无任何事件（如上游模型服务器
# 未启动/不可达时 pi 静默挂起）先向事件流注入 stall error（前端可见、可换
# 模型/重连），不杀进程；真正 reap 仍按 IDLE_TIMEOUT_SECONDS。
TURN_STALL_NOTIFY_SECONDS = 120.0
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

# #278：pi 可执行解析——Windows 上 npm 安装只有 pi.cmd 包装器，CreateProcess
# 不自动补 .cmd 后缀，直接 Popen(["pi", ...]) 会 WinError 2。
# FIREFLY_PI_CLI 原值缺失时回落到 PI_CLI 默认名（which 可按 PATHEXT 找到 .cmd 包装器）。
PI_CLI_FALLBACK_NAME = "pi"


def resolve_pi_cli(env: Optional[Dict[str, str]] = None) -> Optional[str]:
    """解析 pi 可执行入口（找不到返回 None）。

    优先级：FIREFLY_PI_CLI 原值（若存在）→ ``shutil.which(PI_CLI)``
    （Windows 上能解析到 pi.cmd 包装器）→ None。
    """
    source = os.environ if env is None else env
    explicit = source.get("FIREFLY_PI_CLI")
    if explicit:
        return explicit
    return shutil.which(PI_CLI or PI_CLI_FALLBACK_NAME)


def _is_batch_wrapper(path: str) -> bool:
    """是否为 .cmd/.bat 批处理包装器（Windows npm 安装形态）。"""
    name = os.path.basename(path).lower()
    return name.endswith(".cmd") or name.endswith(".bat")


def write_system_prompt_file(session_dir: str, prompt: str) -> str:
    """#334：把完整系统提示词原子落盘 <session_dir>/system-prompt.txt，返回路径。

    根因：cmd /c 包装器下含字面换行的引号参数会把后续全部参数吐掉
    （现物：宿主 argv 429B 止于提示词首行，--continue/model 全丢；且历史上
    每次 spawn 提示词只送达第一行）。pi 原生支持 `--system-prompt <path>`。
    每节点单文件（session_dir 即节点级），覆盖写不追加。内存真源仍是
    system_prompt（SpawnParams 双保险，监理钉③）。
    """
    path = os.path.join(session_dir, SYSTEM_PROMPT_FILE_NAME)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        fh.write(prompt)
    os.replace(tmp, path)
    return path


def build_pi_launch_command(resolved_cli: Optional[str], args: List[str]) -> Optional[List[str]]:
    """构造 pi 启动 argv（解析失败返回 None）。

    解析结果是 .cmd/.bat 包装器时用 ``cmd /c`` 前缀数组形式启动：直接
    Popen 多参数 .cmd 依赖 CreateProcess 的隐式处理（不可靠），显式
    cmd /c 安全且避免 shell=True 字符串拼接。包装器进程仍作为
    session.process 顶层进程，_kill_process_tree 的 taskkill /T /F 覆盖
    整棵进程树，不会产生 cmd.exe 僵尸。
    """
    if not resolved_cli:
        return None
    argv = [resolved_cli, *args]
    if _is_batch_wrapper(resolved_cli):
        return ["cmd", "/c", *argv]
    return argv


# #279：画布工具回连基址的默认端口（与 main.py uvicorn 默认监听端口一致）。
# #334：pi 系统提示词落盘文件名（session_dir 即节点级，单文件覆盖写）。
SYSTEM_PROMPT_FILE_NAME = "system-prompt.txt"
DEFAULT_AGENT_API_PORT = "3000"
DEFAULT_AGENT_API_BASE = f"http://127.0.0.1:{DEFAULT_AGENT_API_PORT}"


def resolve_agent_api_base(env: Optional[Dict[str, str]] = None) -> str:
    """计算注入 sidecar 的 FIREFLY_AGENT_API_BASE（#279）。

    优先级：显式 FIREFLY_AGENT_API_BASE → FIREFLY_PORT 端口 → 默认 3000。
    默认与 main.py 实际监听端口（uvicorn 3000）一致，消除旧兜底 8000
    （sidecar 启动成功后所有画布工具调用会打到 8000 全失败）。
    """
    source = os.environ if env is None else env
    explicit = source.get("FIREFLY_AGENT_API_BASE")
    if explicit:
        return explicit
    port = (source.get("FIREFLY_PORT") or "").strip()
    try:
        port_number = int(port)
    except ValueError:
        port_number = 0
    if 0 < port_number <= 65535:
        return f"http://127.0.0.1:{port_number}"
    return DEFAULT_AGENT_API_BASE


def ensure_agent_api_base_env(env: Optional[Dict[str, str]] = None) -> str:
    """确保 env（默认 os.environ）中有与监听端口一致的 FIREFLY_AGENT_API_BASE，返回最终值。

    服务启动处（main.py）在拉起 sidecar 前调用，使 agent_host 注入的
    回连基址跟随实际监听端口（#279）。
    """
    target = os.environ if env is None else env
    base = resolve_agent_api_base(target)
    if not target.get("FIREFLY_AGENT_API_BASE"):
        target["FIREFLY_AGENT_API_BASE"] = base
    return base

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


# The full status vocabulary for AgentSession.status (stable, ordered).
AGENT_STATUSES = ("stopped", "starting", "running", "busy", "waiting_confirm", "crashed")


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
    _turn_started_at: float = 0.0  # #258：当前 turn 起点（agent_start → agent_end 时长）
    _stall_notified: bool = False  # #286：本 turn 是否已发过停顿提醒
    # --- #239：超时必杀 / 退出树杀 / 错误五元组 ---
    _stopping: bool = False  # 手动 stop 进行中：reader finally 不得自动 respawn
    # #318 票 B：codex 单轮态旗。busy 判定用它而非 process——agent_end 已发但进程
    # 退出收尾未完成的窗口内，新 prompt 必须可接（E2E 实抓竞态）。
    _turn_open: bool = False
    # #318 票 C：事件回放日志 seq 计数（-1=未初始化，首用时从文件尾恢复）
    _event_seq: int = -1
    _events_log_count: int = 0
    _respawn_count: int = 0  # 连续自动 respawn 次数（spawn 就绪后清零）
    result: dict = field(default_factory=lambda: normalize_result(RESULT_DONE, ""))
    # #318 票 A：宿主选择（默认 pi，行为不变）；ext_session_id 为宿主自有会话 id
    # （codex thread_id 等），票 B 接线；票 A 仅落字段。
    agent_cli: str = "pi"
    ext_session_id: Optional[str] = None
    # #278：最近一次可操作错误（如 pi 未找到），供会话状态可见（#280 使用）
    last_error: str = ""
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
        # #318 票 A：宿主 adapter 注册表（默认只有 pi；行为零变化）
        from .agent_adapters import CodexAdapter, PiAdapter
        self._adapters = {"pi": PiAdapter(), "codex": CodexAdapter()}

    def _adapter_for(self, session: AgentSession):
        """按 session.agent_cli 选 adapter；未知宿主回退 pi（票 A 保守）。"""
        return self._adapters.get(session.agent_cli, self._adapters["pi"])

    def _session_key(self, project_id: str, node_id: str) -> str:
        return f"{project_id}:{node_id}"

    async def get_or_create_session(self, project_id: str, node_id: str, model: str = "", autonomy: str = "cautious") -> AgentSession:
        """Get existing session or create a new one."""
        key = self._session_key(project_id, node_id)
        async with self._lock:
            session = self.sessions.get(key)
            if session and session.is_running:
                return session
            if session:
                # #317：空闲对象复用不替换——否则 stream 连接（不拉起）与后续 /prompt spawn 各自拿到
                # 不同对象，WS 监听器挂在旧对象上会永久丢事件；复用后同对象晚 spawn 也能广播。
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

        # #318 票 B：codex = per-turn 进程模型（ADR 0024）——「spawn」仅验证 CLI
        # 可用性并标为宿主会话；进程由 send_prompt 每轮拉起、轮末退出。
        # 无常驻进程、无 pi RPC get_state；不污染 pi 常驻路径一行。
        if session.agent_cli == "codex":
            return self._codex_validate(session)

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

            # Build command（#278：先解析可执行入口——Windows 上 pi 只有 pi.cmd
            # 包装器，直接 Popen(["pi", ...]) 会 WinError 2；解析失败记可操作错误，不启动进程）
            # #318 票 A：argv 拼法迁入 PiAdapter.build_argv（flag 列表/--continue 探测/
            # 模型 flag 逐行等价）；resolve_pi_cli/build_pi_launch_command/PI_CLI 仍驻
            # 本模块（测试 monkey-patch 面不变），PiAdapter 运行时延迟导入本模块取用。
            from .agent_adapters import SpawnParams
            adapter = self._adapter_for(session)
            # #318 票 B：codex 宿主经 MCP 端点操作画布（ADR 0022/0024）；
            # 端口/基址动态取（resolve_agent_api_base，L-1 禁硬编码），&node= 只改归属。
            api_base = resolve_agent_api_base(os.environ)
            mcp_url = (
                f"{api_base}/api/mcp?project={session.project_id}"
                f"&node={session.node_id}"
            )
            params = SpawnParams(
                resolved_cli=resolve_pi_cli(),
                session_dir=session_dir,
                system_prompt=system_prompt,
                # #334：pi 宿主提示词走文件（cmd /c 下内联多行必丢参）；codex 不经 cmd 包装器维持内联。
                system_prompt_file=(
                    write_system_prompt_file(session_dir, system_prompt)
                    if session.agent_cli == "pi" else ""
                ),
                tools_path=self._get_canvas_tools_path(),
                node_id=session.node_id,
                model=model or session.model,
                continue_session=continue_session,
                agent_cli=session.agent_cli,
                ext_session_id=session.ext_session_id,
                mcp_url=mcp_url,
            )
            cmd = adapter.build_argv(params)
            if cmd is None:
                if session.agent_cli != "pi":
                    error = (
                        f"未找到 {session.agent_cli} 可执行文件：PATH 中没有 '{adapter.cli}'。"
                        f"请先安装 {session.agent_cli} CLI"
                    )
                else:
                    error = (
                        "未找到 pi 可执行文件：PATH 中没有 'pi'，且未设置 FIREFLY_PI_CLI。"
                        "请先安装 pi（npm i -g @earendil-works/pi）或设置 FIREFLY_PI_CLI "
                        "环境变量指向 pi 可执行文件"
                    )
                logger.error(f"Failed to spawn agent session {session.node_id}: {error}")
                session.status = "crashed"
                session.last_error = error
                return False

            # （原「--continue 探测」与「model flag」两段已迁入 PiAdapter.build_argv，#318 票 A）

            # Set environment for the canvas-tools extension
            env = os.environ.copy()
            # #279：回连基址跟随实际监听端口（显式值 → FIREFLY_PORT → 默认 3000）
            env["FIREFLY_AGENT_API_BASE"] = resolve_agent_api_base(env)
            env["FIREFLY_AGENT_PROJECT_ID"] = session.project_id
            env["FIREFLY_AGENT_NODE_ID"] = session.node_id

            logger.info(f"Spawning pi subprocess for agent {session.node_id}: {' '.join(cmd[:6])}...")

            session.last_error = ""
            session.process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                # #292：Windows 中文环境下 text=True 默认 GBK；pi 的 UTF-8 JSON 含中文即解码爆炸，
                # reader 退出→误杀活进程→5 连crash。显式 UTF-8 + replace，杜绝编码误杀。
                encoding="utf-8",
                errors="replace",
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
            state = await self.get_session_state(session, timeout=adapter.startup_timeout_s)
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
            session.last_error = str(e) or e.__class__.__name__
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
            exit_event = {"type": "process_exited", "exit_code": exit_code, "result": dict(session.result)}
            self._persist_event(session, exit_event)  # #318 票 C：崩溃/退出也入回放日志
            for handler in session.event_handlers:
                try:
                    out = handler(session, exit_event)
                    if inspect.isawaitable(out):  # #281：同步处理器（capture_response 等）直接调用
                        await out
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
        # #322 票 D：工具耗时——start 记 monotonic 时锟，end 就地注入 duration_ms，
        # 在 _persist_event 之前 ⇒ live WS 与 events.jsonl 回放都带耗时（单一注入点）。
        _etype = event.get("type", "")
        if _etype == "tool_execution_start":
            session._tool_ts = getattr(session, "_tool_ts", {})
            session._tool_ts[event.get("toolCallId", "")] = time.monotonic()
        elif _etype == "tool_execution_end":
            _ts_map = getattr(session, "_tool_ts", {})
            _t0 = _ts_map.pop(event.get("toolCallId", ""), None)
            if _t0 is not None:
                event["duration_ms"] = round((time.monotonic() - _t0) * 1000)
        # #318 票 C：单一 fan-out——先持久化（就地注入 seq，WS 广播 payload 自动带
        # seq，前端去重/续拉按 seq），再分发。best-effort，永不阻断实时流。
        self._persist_event(session, event)
        event_type = event.get("type", "")

        # Update session status based on event
        if event_type == "agent_start":
            session.status = "busy"
            session._turn_started_at = time.time()  # #258：turn 起点（用量时长用）
            session._stall_notified = False  # #286：新 turn 重置停顿提醒
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
            await self._record_turn_usage(session, event)  # #258：pi sidecar 采集点（best-effort）

        for handler in session.event_handlers:
            try:
                out = handler(session, event)
                if inspect.isawaitable(out):  # #281：同步处理器直接调用，异步才 await（修 NoneType await 错误风暴）
                    await out
            except Exception as e:
                logger.error(f"Error in event handler for agent {session.node_id}: {e}")

    async def _record_turn_usage(self, session: AgentSession, event: dict) -> None:
        """#258：agent_end 后把 turn 用量写入 ops.db（pi sidecar 采集点）。

        遥测 best-effort：任何异常仅记日志，绝不影响事件流/主流程。
        provider/model 取自 session.model（"provider/model" 斜杠形式；无斜杠记 pi）。
        """
        try:
            usage = collect_pi_turn_usage(event.get("messages"))
            if not usage_has_data(usage):
                return
            duration = time.time() - session._turn_started_at if session._turn_started_at else None
            model = str(session.model or "")
            provider = model.split("/", 1)[0] if "/" in model else "pi"
            store_call = getattr(_app_main, "project_store_call", None)
            if store_call is None:
                return
            await asyncio.to_thread(
                store_call,
                "record_usage_turn",
                session.project_id,
                provider=provider,
                model=model,
                node_id=session.node_id,
                usage=usage,
                duration_seconds=duration,
            )
        except Exception as e:
            logger.warning(f"Failed to record usage for agent {session.node_id}: {e}")

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
        # Refs #323 票 E（reference frame）：每轮发送前解析 agent.refFrameId 素材，
        # 注入当前 CLI（pi=RPC images，codex=`exec -i` 实测语法）。护栏（监理 token 钉）：
        # ≤REF_MAX_IMAGES 张、单图 >REF_MAX_IMAGE_BYTES 跳过、路径解析失败跳过不炸整轮
        #（监理钉③）；视频本轮不注入（codex -i 只吃图）。注入不进落盘正文（防 jsonl
        # 爆炸），但监理钉①：user_prompt 事件带轻量 refs 元数据 {node_id,name}+跳过清单，
        # 回放 UI 可区分带素材/不带素材轮次，决策可审计。
        ref_images, ref_skipped = self._collect_reference_images(session)
        user_payload = {"type": "user_prompt", "text": message}
        if ref_images or ref_skipped:
            user_payload["refs"] = [{"node_id": r["node_id"], "name": r["name"]} for r in ref_images]
            if ref_skipped:
                user_payload["refs_skipped"] = ref_skipped
        # #318 票 C：user 侧入回放日志（仅落盘不广播——前端实时侧用本地回声，
        # 避免双气泡；降级重放/刷新回放读此数据）。
        self._persist_event(session, user_payload)
        inject_text = message
        if ref_images or ref_skipped:
            names = [r["name"] for r in ref_images] + [s["name"] for s in ref_skipped]
            inject_text = f"{message}\n\n【参考框素材清单】{'、'.join(names)}（图片已随本消息注入；视频/超限项见跳过原因）"
        # #318 票 B：codex 宿主不经 pi RPC——每轮一个 `codex exec --json` 进程。
        if session.agent_cli == "codex":
            await self._codex_turn(session, inject_text,
                                   ref_paths=[r["path"] for r in ref_images])
            return
        command = {"type": "prompt", "message": inject_text}
        rpc_images = list(images or [])
        for r in ref_images:
            payload = self._pi_image_payload(r)
            if payload is not None:
                rpc_images.append(payload)
        if rpc_images:
            command["images"] = rpc_images
        await self.send_command(session, command)
        session.status = "busy"

    def _collect_reference_images(self, session: AgentSession) -> tuple[list, list]:
        """agent.refFrameId → smart-group 成员 → 可注入图片清单（纯读，失败全降级）。

        返回 (images:[{node_id,name,path,mime}], skipped:[{name,reason}])。
        Refs #323 票 E；护栏常数在 app/services/agent_frame.py。
        """
        from .agent_frame import REF_MAX_IMAGES, REF_MAX_IMAGE_BYTES
        images: list = []
        skipped: list = []
        store = _app_main.FIREFLY_PROJECT_STORE
        if store is None or not getattr(session, "node_id", ""):
            return images, skipped
        try:
            canvas = store.canvas(session.project_id)
        except Exception:
            return images, skipped
        nodes = {str(n.get("id")): n for n in (canvas.get("nodes") or [])
                 if isinstance(n, dict) and n.get("id")}
        agent = nodes.get(str(session.node_id)) or {}
        frame = nodes.get(str(agent.get("refFrameId") or ""))
        if not isinstance(frame, dict) or frame.get("type") != "smart-group":
            return images, skipped
        for mid in (frame.get("items") or []):
            member = nodes.get(str(mid))
            if not isinstance(member, dict):
                skipped.append({"name": str(mid), "reason": "节点不存在"})
                continue
            name = str(member.get("title") or member.get("name") or mid)
            member_images = [img for img in (member.get("images") or []) if isinstance(img, dict)]
            target = next((img for img in member_images if str(img.get("kind") or "") == "image"), None)
            if target is None:
                if any(str(img.get("kind") or "") == "video" for img in member_images):
                    skipped.append({"name": name, "reason": "视频本轮不注入"})
                continue
            if len(images) >= REF_MAX_IMAGES:
                skipped.append({"name": name, "reason": f"超单轮 {REF_MAX_IMAGES} 张上限"})
                continue
            try:
                record = store.media_record(session.project_id, str(target.get("asset_id") or ""))
                path = str(record.get("path") or "")
                size = os.path.getsize(path)
            except Exception:
                # 监理钉③：路径/文件解析失败→跳过该素材不炸整轮（未来换宿主/容器化即此路径）。
                skipped.append({"name": name, "reason": "路径解析失败"})
                continue
            if size > REF_MAX_IMAGE_BYTES:
                skipped.append({"name": name, "reason": f"单图超 {REF_MAX_IMAGE_BYTES // 1_000_000}MB"})
                continue
            images.append({"node_id": str(mid), "name": name, "path": path,
                           "mime": self._image_mime(path)})
        return images, skipped

    @staticmethod
    def _image_mime(path: str) -> str:
        ext = os.path.splitext(path)[1].lower()
        return {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".webp": "image/webp", ".gif": "image/gif"}.get(ext, "image/png")

    def _pi_image_payload(self, ref: dict):
        """pi RPC images 元素（rpc-commands.md:ImageContent{type,data,mimeType}，base64）。"""
        import base64
        try:
            with open(ref["path"], "rb") as f:
                data = base64.b64encode(f.read()).decode("ascii")
        except OSError:
            return None
        return {"type": "image", "data": data, "mimeType": ref["mime"]}

    async def send_steer(self, session: AgentSession, message: str) -> None:
        """Send a steer command (redirect current turn)."""
        await self.send_command(session, {"type": "steer", "message": message})

    async def send_follow_up(self, session: AgentSession, message: str) -> None:
        """Send a follow-up (queue for after current turn)."""
        await self.send_command(session, {"type": "follow_up", "message": message})

    # ------------------------------------------------------------------
    # #318 票 B：codex 宿主——per-turn 进程模型（ADR 0024）。
    # 会话态（ext_session_id/busy）驻 AgentSession；adapter 无状态只产 argv/事件。
    # ------------------------------------------------------------------

    def _codex_validate(self, session: AgentSession) -> bool:
        """codex 的「启动」= 仅验 CLI 可用性（无进程、无 RPC get_state）。"""
        adapter = self._adapters.get("codex")
        if adapter is None or shutil.which(adapter.cli) is None:
            error = "未找到 codex 可执行文件：PATH 中没有 'codex'。请先安装（npm i -g @openai/codex）"
            logger.error(f"Failed to validate codex session {session.node_id}: {error}")
            session.status = "crashed"
            session.last_error = error
            return False
        session.last_error = ""
        session.status = "running"  # idle_stored 语义：会话可用、当前无进程
        return True

    async def _codex_turn(
        self,
        session: AgentSession,
        message: str,
        allow_resume: bool = True,
        degrade_budget: bool = True,
        ref_paths: list | None = None,
    ) -> None:
        """一轮 = 一个 `codex exec --json` 进程（ADR 0024 状态机）。

        首轮无 resume；有 ext_session_id 则 `resume <thread_id>`（实测 options 必在
        子命令前，已由 build_resume_argv 保证）。prompt 走 stdin（实测坑：参数位不可靠）。
        resume 失败降级：error 事件 UI 可见 + 新会话重发一次（重放上下文属票 C，票 B
        只保证「不静默死」）。
        """
        if session._turn_open:
            raise RuntimeError(f"Agent {session.node_id} busy：codex 拒收并发 prompt（ADR 0024 不变量）")
        from .agent_adapters import SpawnParams
        from .agent_adapters.codex_adapter import CodexAdapter, codex_spawn_env, write_home_mcp_config

        adapter = self._adapters.get("codex") or CodexAdapter()
        effective_autonomy = session.autonomy or "cautious"
        if effective_autonomy not in AUTONOMY_SYSTEM_PROMPT_SUFFIXES:
            effective_autonomy = "cautious"
        system_prompt = DEFAULT_SYSTEM_PROMPT + AUTONOMY_SYSTEM_PROMPT_SUFFIXES.get(effective_autonomy, "")
        project_dir = self._get_project_dir(session.project_id)
        session_dir = os.path.join(project_dir, session.session_dir)
        os.makedirs(session_dir, exist_ok=True)
        mcp_url = (
            f"{resolve_agent_api_base(os.environ)}/api/mcp?project={session.project_id}"
            f"&node={session.node_id}"
        )
        # #318 隔离尾单：隔离 CODEX_HOME + 写入 firefly MCP 条目（件③ mcp list 可见）
        spawn_env = codex_spawn_env()
        write_home_mcp_config(spawn_env["CODEX_HOME"], mcp_url)
        params = SpawnParams(
            resolved_cli=shutil.which("codex"),
            session_dir=session_dir,
            system_prompt=system_prompt,
            tools_path=self._get_canvas_tools_path(),
            node_id=session.node_id,
            model=session.model,
            continue_session=False,
            agent_cli="codex",
            ext_session_id=session.ext_session_id if allow_resume else None,
            mcp_url=mcp_url,
        )
        resumed = bool(allow_resume and session.ext_session_id)
        if resumed:
            cmd = adapter.build_resume_argv(params, session.ext_session_id)
        else:
            cmd = adapter.build_argv(params)
        if cmd is None:
            raise RuntimeError("未找到 codex 可执行文件：PATH 中没有 'codex'")
        # Refs #323 票 E：参考框素材注入——codex 0.162 实测 `exec -i <FILE>...`（多图重复
        # -i），插在 stdin 占位 `-` 之前；路径解析失败已在收集层降级，这里不再炸。
        from .agent_adapters.codex_adapter import insert_ref_images
        cmd = insert_ref_images(cmd, ref_paths or [])

        session.last_error = ""
        session._turn_started_at = time.time()
        session._turn_open = True
        try:
            process = await asyncio.to_thread(
                subprocess.Popen,
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                # #292 同源教训：显式 UTF-8，杜绝 GBK 环境编码误杀。
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                # 沙盒即边界（审批实测）：cwd = per-node 临时目录，-s read-only 下
                # 写画布一律经 MCP 端点确认闸。
                cwd=session_dir,
                # #318 隔离尾单：CODEX_HOME=RUNTIME根/codex-home（sessions 不再写用户全局 ~/.codex）
                env=spawn_env,
            )
        except OSError as e:
            session._turn_open = False
            raise RuntimeError(f"codex 拉起失败：{e}")
        session.process = process
        session._stopping = False
        session._started_at = time.time()
        session._last_activity = time.time()
        session._stderr_thread = threading.Thread(
            target=self._drain_stderr,
            args=(session,),
            name=f"agent-stderr-{session.node_id[:8]}",
            daemon=True,
        )
        session._stderr_thread.start()

        def _feed_stdin() -> None:
            try:
                process.stdin.write(message)
                process.stdin.close()
            except (BrokenPipeError, OSError):
                pass

        await asyncio.to_thread(_feed_stdin)
        session._reader_task = asyncio.create_task(
            self._read_codex_turn(session, process, resumed, message, degrade_budget)
        )

    async def _read_codex_turn(
        self,
        session: AgentSession,
        process: subprocess.Popen,
        resumed: bool,
        message: str,
        degrade_budget: bool,
    ) -> None:
        """codex 单轮 reader：JSONL → 内部词汇（ADR 0024 §1），轮末清进程槽。"""
        from .agent_adapters.codex_adapter import map_codex_event

        saw_thread_id = False
        saw_turn_completed = False
        try:
            while True:
                line = await asyncio.to_thread(process.stdout.readline)
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning(f"codex {session.node_id}: non-JSONL line: {line[:120]!r}")
                    continue
                if raw.get("type") == "thread.started":
                    saw_thread_id = True
                    thread_id = str(raw.get("thread_id") or "")
                    if thread_id and thread_id != session.ext_session_id:
                        session.ext_session_id = thread_id
                        self._persist_ext_session_id(session)
                if raw.get("type") == "turn.completed":
                    saw_turn_completed = True
                    session._turn_open = False  # 轮已逻辑结束：新 prompt 可排队下一轮
                if raw.get("type") == "turn_aborted":
                    session._turn_open = False
                for event in map_codex_event(raw):
                    etype = event.get("type")
                    if etype == "raw_item":
                        # 监理裁定：未知 item 取证仅入库，不广播 live（回放消费方忽略）。
                        self._persist_event(session, event)
                        continue
                    if etype == "usage" and not event.get("cumulative"):
                        await self._record_codex_usage(session, event.get("usage") or {})
                    await self._dispatch_event(session, event)
        except Exception as e:
            logger.error(f"codex reader died for {session.node_id}: {e}")
        finally:
            session._turn_open = False
            try:
                await asyncio.to_thread(process.wait)
            except Exception:
                pass
            if session.process is process:
                session.process = None
            if session._reader_task is asyncio.current_task():
                session._reader_task = None
            exit_code = process.returncode
            session._last_activity = time.time()
            if resumed and not saw_thread_id and exit_code not in (0, None) and degrade_budget:
                # ADR 0024 §2 降级：resume 认不出旧会话 → UI 可见 + 新会话重发一次。
                # 票 C：重放上下文 = 回放日志最近 EVENTS_REPLAY_TURNS 轮（含 user 侧，
                # 不足按实际全量）前置注入。
                session.ext_session_id = None
                context_block, turns_used = self._build_replay_context(session)
                await self._dispatch_event(session, {
                    "type": "error",
                    "message": (f"会话续接失败，已降级重建（已重放最近 {turns_used} 轮上下文）"
                                if turns_used else "会话续接失败，已降级重建（无可重放历史）"),
                })
                retry_message = (
                    f"{context_block}\n\n（以上是画布历史重放，请接着继续）\n{message}"
                    if context_block else message)
                try:
                    await self._codex_turn(session, retry_message, allow_resume=False, degrade_budget=False)
                except Exception as e:
                    logger.error(f"codex degrade retry failed for {session.node_id}: {e}")
                return
            if exit_code not in (0, None) and not saw_turn_completed:
                # 实测坑：codex 出错也可能 exit=0——真非零且无 turn.completed 才是硬崩。
                session.last_error = f"codex 进程异常退出（exit={exit_code}）"
                await self._dispatch_event(session, {
                    "type": "error", "message": session.last_error})
            # idle_stored：会话仍可用（下轮 resume），不标 crashed（除非上面已 crashed）。
            if session.status != "crashed":
                session.status = "running"

    def _persist_ext_session_id(self, session: AgentSession) -> None:
        """ext_session_id → 节点属性 extSessionId（重启后 resume 链存活；
        ADR 0024 不变量：落盘先于下次 spawn）。best-effort，失败只记日志。"""
        try:
            store_call = getattr(_app_main, "project_store_call", None)
            if store_call is None or not session.ext_session_id:
                return
            opened = store_call("open_project", session.project_id)
            canvas = opened.get("canvas") or {}
            nodes = canvas.get("nodes") or []
            node = next(
                (n for n in nodes if str(n.get("id") or "") == session.node_id), None)
            if not isinstance(node, dict):
                return
            updated = dict(node)
            updated["extSessionId"] = session.ext_session_id
            store_call(
                "apply_mutations",
                session.project_id,
                [{"op": "upsert_node", "node": updated}],
                base_revision=int(canvas.get("revision") or 0),
            )
            store_call("flush", session.project_id)
        except Exception as e:
            logger.warning(f"Failed to persist extSessionId for {session.node_id}: {e}")

    async def _record_codex_usage(self, session: AgentSession, usage: dict) -> None:
        """codex turn.completed usage → ops.db（与 #258 pi 采集点同表同语义；
        键归一交给 firefly_usage 别名表）。遥测 best-effort。"""
        try:
            if not usage_has_data(usage):
                return
            duration = time.time() - session._turn_started_at if session._turn_started_at else None
            model = str(session.model or "")
            store_call = getattr(_app_main, "project_store_call", None)
            if store_call is None:
                return
            await asyncio.to_thread(
                store_call,
                "record_usage_turn",
                session.project_id,
                provider="codex",
                model=model,
                node_id=session.node_id,
                usage=usage,
                duration_seconds=duration,
            )
        except Exception as e:
            logger.warning(f"Failed to record codex usage for {session.node_id}: {e}")

    async def send_abort(self, session: AgentSession) -> None:
        """Abort the current turn."""
        if session.agent_cli == "codex":
            # ADR 0024：abort=SIGINT 单轮（实测 rollout 实时落盘、可 resume）。
            # Windows 无 SIGINT：terminate() 终止当前轮进程（不毁 sessionId）。
            proc = session.process
            if proc is not None and proc.poll() is None:
                try:
                    proc.terminate()
                except Exception:
                    pass
            return
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
        if session.agent_cli == "codex":
            # codex 无 RPC：新模型经下轮 `-m` argv 生效（ADR 0024 per-turn）。
            session.model = model
            return
        await self.send_command(session, command)
        session.model = model

    async def get_fork_messages(self, session: AgentSession, timeout: float = RPC_TIMEOUT_SECONDS) -> List[dict]:
        """Get the list of user messages available for forking.

        Returns a list of {entryId, text} dicts.
        """
        if session.agent_cli == "codex":
            # #318 票 B：codex 无 pi fork 语义（续接靠 resume，ADR 0024）；
            # 防御性空列表，前端禁用面属票 D。
            return []
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
        if session.agent_cli == "codex":
            raise RuntimeError("codex 宿主不支持 pi fork 语义（会话续接靠 resume，见 ADR 0024）")
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

    # ------------------------------------------------------------------
    # #318 票 C：节点级事件回放日志 events.jsonl（归一化事件，双宿主同词汇；
    # codex 未知 item 以 raw_item 入库=取证，回放消费方忽略；不广播 live WS）。
    # 路径：<project_dir>/agent-events/<node_id>.jsonl（跟项目走）。
    # 行：{seq, ts, host, event:{…归一化事件（已注入 seq）}}
    # ------------------------------------------------------------------

    def _events_log_path(self, session: AgentSession) -> str:
        return os.path.join(
            self._get_project_dir(session.project_id),
            "agent-events", f"{session.node_id}.jsonl")

    def _init_event_seq(self, session: AgentSession, path: str) -> None:
        """从文件尾恢复 seq 与行数（跨重启/跨会话单调）。"""
        if session._event_seq >= 0:
            return
        seq, count = 0, 0
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    count += 1
                    try:
                        s = int(json.loads(line).get("seq") or 0)
                        if s > seq:
                            seq = s
                    except (ValueError, json.JSONDecodeError):
                        pass
        except FileNotFoundError:
            pass
        except OSError as e:
            logger.warning(f"events log init {session.node_id}: {e}")
        session._event_seq = seq
        session._events_log_count = count

    def _persist_event(self, session: AgentSession, event: dict) -> None:
        """追加归一化事件（就地注入 seq）。永不抛——失败只降级为无回放。"""
        try:
            path = self._events_log_path(session)
            self._init_event_seq(session, path)
            session._event_seq += 1
            session._events_log_count += 1
            event["seq"] = session._event_seq
            record = {
                "seq": session._event_seq,
                "ts": time.time(),
                "host": session.agent_cli,
                "project_id": session.project_id,
                "node_id": session.node_id,
                "event": event,
            }
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            if session._events_log_count > EVENTS_LOG_MAX_LINES:
                self._truncate_events_log(session, path)
        except Exception as e:
            logger.warning(f"events log persist failed {session.node_id}: {e}")

    def _truncate_events_log(self, session: AgentSession, path: str) -> None:
        """截头保尾 N 行；seq 单调跨截断（不改写幸存行）。temp+replace 原子。"""
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            tail = lines[-EVENTS_LOG_MAX_LINES:]
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.writelines(tail)
            os.replace(tmp, path)
            session._events_log_count = len(tail)
        except OSError as e:
            logger.warning(f"events log truncate failed {session.node_id}: {e}")

    def load_events(self, project_id: str, node_id: str, since: int = 0) -> dict:
        """GET events：返回 seq>since 行（升序）。文件不存在=空。"""
        path = os.path.join(
            self._get_project_dir(project_id), "agent-events", f"{node_id}.jsonl")
        out = []
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if int(rec.get("seq") or 0) > since:
                        out.append(rec)
        except FileNotFoundError:
            return {"events": [], "last_seq": since}
        # OSError 上抛——HTTP 语义归路由层（service 层不依赖 fastapi）
        last_seq = out[-1]["seq"] if out else since
        return {"events": out, "last_seq": last_seq}

    def _build_replay_context(self, session: AgentSession) -> tuple:
        """从回放日志派生最近 EVENTS_REPLAY_TURNS 轮文本（含 user 侧，host 无关：
        归一化词汇）。返回 (text_block, turns_used)。不足 N 轮按实际全量（监理裁定）。
        轮界定：user_prompt 开轮，agent_end 收轮（未收尾尾轮也收）。"""
        records = []
        path = self._events_log_path(session)
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            records.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
        except FileNotFoundError:
            return "", 0
        turns = []  # [{"user": str, "assistant": str}]
        cur = None
        for rec in records:
            ev = rec.get("event") or {}
            et = ev.get("type")
            if et == "user_prompt":
                cur = {"user": str(ev.get("text") or ""), "assistant": ""}
                turns.append(cur)
            elif et == "agent_end":
                cur = None
            elif et == "message_update":
                ame = ev.get("assistantMessageEvent") or {}
                if ame.get("type") == "text_delta" and cur is not None:
                    cur["assistant"] += str(ame.get("delta") or "")
        recent = turns[-EVENTS_REPLAY_TURNS:]
        # 预算内保留最近轮（从尾向前积）
        keep = []
        budget = EVENTS_REPLAY_MAX_CHARS
        for t in reversed(recent):
            block = f"user: {t['user']}\nassistant: {t['assistant']}\n"
            if budget - len(block) < 0 and keep:
                break
            keep.append(block)
            budget -= len(block)
        keep.reverse()
        if not keep:
            return "", 0
        body = "<canvas-history-replay host-session-lost rebuilt-by-firefly>\n" + "".join(keep) + "</canvas-history-replay>"
        return body, len(keep)

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
            await self._idle_watchdog_pass(time.time())

    async def _idle_watchdog_pass(self, now: float) -> None:
        """#286：单轮扫描。先短阈值发 stall 提醒（可操作），后长阈值 reap。"""
        for session in list(self.sessions.values()):
            if session.status not in ("busy", "waiting_confirm"):
                continue
            idle = now - session._last_activity
            if idle > TURN_STALL_NOTIFY_SECONDS and not session._stall_notified:
                session._stall_notified = True
                logger.warning(f"Agent {session.node_id} turn stalled: no events for {idle:.0f}s while busy (#286)")
                _tail = session.stderr_tail.strip()[-300:]
                await self._dispatch_event(session, {
                    "type": "error",
                    "stall": True,
                    "message": (
                        f"模型已 {idle:.0f} 秒无响应（可能上游不可达或模型服务器未启动）。"
                        "请在模型下拉换一个模型重发；仍无响应可点重试连接。"
                        + (f"\npi 诊断（stderr 尾部）：{_tail}" if _tail else "")
                    ),
                })
            if idle <= IDLE_TIMEOUT_SECONDS:
                continue
            logger.error(f"Agent {session.node_id} idle for {idle:.0f}s while busy — killing process tree")
            session.result = normalize_result(RESULT_ERROR, ERROR_KIND_TIMEOUT_IDLE)
            await self._kill_process_tree(session, reason="idle timeout")
    async def snapshot_sessions(self) -> List[dict]:
        """Read-only projection of live agent session state for /api/healthz.

        Never mutates session state; safe for concurrent health probes.
        """
        async with self._lock:
            return [
                {
                    "project_id": session.project_id,
                    "node_id": session.node_id,
                    "status": session.status,
                    "model": session.model,
                    "autonomy": session.autonomy,
                    "alive": session.is_running,
                    "last_error": session.last_error,  # #278：可操作错误可见
                }
                for session in self.sessions.values()
            ]

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
