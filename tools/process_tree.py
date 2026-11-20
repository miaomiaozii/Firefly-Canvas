"""子进程树的生命周期管理（#361）。

Windows 上「杀一个进程」不等于「杀一棵树」：

- 质量门用 ``taskkill /PID x /T /F`` 杀超时步骤，前提是**门自己活得足够久去执行这条命令**。
  门被 Ctrl+C、被杀、或者崩了，句柄链一断，node/python/chrome 就全变孤儿——
  2026-11-14 清出来的 26 个遗留进程就是这么来的（12 个 ``node --check`` +
  2 个 ``test-canvas-generation-tasks.mjs`` + 2 个 Playwright 脚本连同 8 个 headless 浏览器）。
- Chrome/Edge 带 ``--user-data-dir`` 启动时会拉起 GPU / renderer / crashpad 一整套子进程，
  ``Popen.terminate()`` 只杀主进程，渲染进程会留在后台。

两条手段合起来才是完整的：

1. :class:`ProcessJob` —— Windows Job Object，``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE``。
   子进程一出生就挂进作业里，**之后它自己生的子进程也自动在作业里**。作业句柄是门进程持有的，
   门无论怎么死（正常退出、崩溃、被 KillProcess），OS 关闭最后一个句柄时把整棵树端掉。
   这条不依赖任何「记得去 cleanup」，所以 Ctrl+C 和崩溃也覆盖。
2. :func:`kill_process_tree` —— 显式超时/取消路径上的主动杀树（Windows 走 ``taskkill /T /F``，
   POSIX 走进程组），配合 Job Object 用：能主动杀就别等 OS 兜底。

非 Windows 上 Job Object 没有对应物，:class:`ProcessJob` 退化成空操作；那边靠
``start_new_session=True`` + ``os.killpg``（调用方已有），语义等价。
"""
from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

__all__ = [
    "ProcessJob",
    "descendant_pids",
    "gate_leftover_processes",
    "job_object_supported",
    "kill_process_tree",
    "process_alive",
    "process_creation_time",
    "process_parent_map",
    "remove_execution_workspace",
    "select_orphan_children",
    "select_reapable_orphans",
]

_CREATE_BREAKAWAY_FROM_JOB = 0x01000000
_CREATE_NEW_PROCESS_GROUP = 0x00000200
_TH32CS_SNAPPROCESS = 0x00000002


def job_object_supported() -> bool:
    """当前平台有没有 Job Object（只有 Windows 有）。"""
    return _nt()


def _nt() -> bool:
    return os.name == "nt"


# --------------------------------------------------------------------------- Windows Job Object

_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JobObjectExtendedLimitInformation = 9
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_TERMINATE = 0x0002


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class ProcessJob:
    """一个 KILL_ON_JOB_CLOSE 作业。把子进程挂进来，进程树一锅端。

    用法（质量门）::

        job = ProcessJob("firefly-quality-gate")
        process = job.spawn([sys.executable, "tools/something.py"])
        ...
        job.close()   # 不 close 也行：进程退出时 OS 会关句柄并杀完整棵树

    作业建不起来、或者挂不上（受限环境、被别的管理器管着）只记录进 :attr:`failures`
    并返回未挂作业的子进程——门禁不能因为回收手段不可用而变红，但会把它说出来，
    因为那时只剩 ``kill_process_tree`` 一条腿，Ctrl+C / 崩溃这条路就没兜底了。
    """

    def __init__(self, name: str = "") -> None:
        self.handle = None
        self.name = name
        self.attached = 0
        self.last_error = 0
        self.failures: list[str] = []
        if not _nt():
            return
        try:
            kernel32 = _kernel32()
            handle = kernel32.CreateJobObjectW(None, name or None)
            if not handle:
                self.failures.append(f"CreateJobObjectW 返回 0（GetLastError={ctypes.get_last_error()}）")
                return
            info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            kernel32.SetInformationJobObject.argtypes = [
                ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32
            ]
            ok = kernel32.SetInformationJobObject(
                handle,
                _JobObjectExtendedLimitInformation,
                ctypes.byref(info),
                ctypes.sizeof(info),
            )
            if not ok:
                self.failures.append(f"SetInformationJobObject 失败（GetLastError={ctypes.get_last_error()}）")
                kernel32.CloseHandle(ctypes.c_void_p(handle))
                return
            self.handle = handle
        except (OSError, AttributeError) as error:  # 非 Windows 或缺 kernel32
            self.failures.append(f"Job Object 不可用：{error}")

    def attach(self, process) -> bool:
        """把 ``subprocess.Popen``（或任何带 ``.pid`` 的对象）挂进作业。

        失败最常见的原因是**这个进程已经属于另一个作业**（Windows 上进程只能有一个直接作业）——
        跑在别的启动器/服务包装里就是这样。那种情形用 :meth:`spawn` 带
        ``CREATE_BREAKAWAY_FROM_JOB`` 重生一次。
        """
        if self.handle is None:
            return False
        pid = getattr(process, "pid", process)
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            return False
        if pid <= 0:
            return False
        kernel32 = _kernel32()
        handle = kernel32.OpenProcess(
            _PROCESS_SET_QUOTA | _PROCESS_TERMINATE | _PROCESS_QUERY_LIMITED_INFORMATION, False, pid
        )
        if not handle:
            self.last_error = ctypes.get_last_error()
            self.failures.append(f"拿不到 pid {pid} 的进程句柄（GetLastError={self.last_error}）")
            return False
        ok = bool(kernel32.AssignProcessToJobObject(ctypes.c_void_p(self.handle), ctypes.c_void_p(handle)))
        # 错误码必须紧跟失败调用读：CloseHandle 会把 last error 顶掉，
        # 否则报告里那个 ``GetLastError=`` 是个陈码，排查时会骗人。
        assign_error = ctypes.get_last_error()
        kernel32.CloseHandle(ctypes.c_void_p(handle))
        if not ok:
            self.last_error = assign_error
            self.failures.append(f"AssignProcessToJobObject 失败：pid {pid}（GetLastError={self.last_error}）")
            return False
        self.attached += 1
        return True

    def spawn(self, command, **kwargs):
        """起子进程并挂进作业（质量门唯一推荐的起子进程方式）。

        带 ``CREATE_BREAKAWAY_FROM_JOB`` 起：Windows 上一个进程只能属于一个作业，门自己
        如果被别的启动器拉起来（已经在某个作业里），子进程会继承那个作业、挂不上我们的。
        breakaway 让它脱离继承的作业；父作业不允许 breakaway 时（CreateProcess 报错）退回普通启动。

        **进程只能起一遍。** 已经跑起来的子进程就算挂不上作业也必须原样返回——步骤可能已经
        写了东西，再起一遍就是重复副作用（2026-11-15 门红：browser 步骤被起两遍，第二个报
        「artifact directory already exists」）。挂不上时记一条原因，回收退到 :func:`kill_process_tree`。
        """
        flags = int(kwargs.get("creationflags", 0)) | _CREATE_NEW_PROCESS_GROUP
        if not _nt():
            return subprocess.Popen(command, **kwargs)
        if self.handle is not None:
            attempt = dict(kwargs)
            attempt["creationflags"] = flags | _CREATE_BREAKAWAY_FROM_JOB
            try:
                process = subprocess.Popen(command, **attempt)
            except OSError as error:  # 父作业不允许 breakaway：进程还没起，可以换方式重起
                self.failures.append(f"breakaway 启动失败，改普通启动：{error}")
            else:
                if not self.attach(process):
                    self.failures.append(
                        f"pid {process.pid} 挂不上作业（GetLastError={self.last_error}），回收退到主动杀树"
                    )
                return process
        attempt = dict(kwargs)
        attempt["creationflags"] = flags
        return subprocess.Popen(command, **attempt)

    def status(self) -> str:
        """给质量门横幅用的一句话。"""
        if not _nt():
            return "POSIX 进程组（killpg）"
        if self.handle is None:
            return f"Job Object 不可用（{'; '.join(self.failures) or '未知原因'}），仅主动杀树生效"
        if self.attached == 0:
            return (
                "Job Object 已建但挂不上子进程"
                f"（GetLastError={self.last_error}），本轮仅主动杀树生效：{'; '.join(self.failures) or '无记录'}"
            )
        return f"Job Object 生效（已挂 {self.attached} 个子进程树）"

    def close(self) -> None:
        """关闭作业句柄。最后一个句柄一关，作业里所有进程立即被 OS 杀掉。"""
        if self.handle is None:
            return
        try:
            _kernel32().CloseHandle(ctypes.c_void_p(self.handle))
        except (OSError, AttributeError):
            pass
        self.handle = None

    def __enter__(self) -> "ProcessJob":
        return self

    def __exit__(self, *_exc) -> bool:
        self.close()
        return False


# --------------------------------------------------------------------------- 显式杀树


_KERNEL32 = None


def _kernel32():
    """取 kernel32（``use_last_error=True``，这样 ``ctypes.get_last_error()`` 拿得到真错误码）。

    ``ctypes.windll`` 默认不记录 last error，拿不到失败原因就没法区分
    「环境不允许作业」和「我们自己写错了」。
    """
    global _KERNEL32
    if _KERNEL32 is None:
        dll = ctypes.WinDLL("kernel32", use_last_error=True)
        dll.CreateJobObjectW.restype = ctypes.c_void_p
        dll.OpenProcess.restype = ctypes.c_void_p
        dll.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        dll.SetInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
        dll.IsProcessInJob.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_bool)]
        _KERNEL32 = dll
    return _KERNEL32


# --------------------------------------------------------------------------- 上一轮留下的孤儿

# 只在命令行里出现这些标记的进程才会被当成「上一轮门/端到端留下的垃圾」。
# 故意不包含 ``main.py`` 或 ``chrome.exe`` 这种本身常见的名字——用户的真实应用在跑，
# 误杀比泄漏更糟。2026-11-14 那 26 个遗留全部带临时目录前缀。
# （以前这里还写了裸 ``smart-canvas.js``：那等于「命令行里提到应用文件名的都杀」，
# 任何加载过这个文件的工具进程都能被 ``taskkill /T /F``，跟上面的原则直接矛盾——拿掉。）
GATE_LEFTOVER_MARKERS = (
    "firefly-quality-gate-run-",
    "firefly-quality-gate-evidence",
    "firefly-e2e-profile-",
    "test-canvas-generation-tasks.mjs",
)


def select_reapable_orphans(
    processes,
    *,
    now: float,
    min_age_seconds: float = 1800.0,
    keep: str = "",
    self_pid: int | None = None,
) -> list[int]:
    """从一份进程快照里挑出「上一轮门/端到端没收尾、现在没人管」的进程 pid。

    三个条件都得满足，缺一个就不杀：

    1. 命令行里带 :data:`GATE_LEFTOVER_MARKERS` 之一的临时目录/脚本标记——用户的真实应用、
       真实浏览器、别的 node 服务都不会匹配。
    2. **家长已经不在了**：快照里找不到它，或者家长自己已经被判为垃圾（chrome 主进程带临时
       profile 标记、它的 ``--type=utility`` 子进程也一起死）。还在跟一个活着的门跑的进程不能动。
    3. 存活超过 ``min_age_seconds``（默认 30 分钟）：正常一轮不会把子进程丢这么久，
       刚起来的子进程也不能因为快照竞态被误杀。

    ``keep`` 是本轮自己的临时目录名：本轮子进程带同样的前缀，必须排除。
    """
    pids = {int(item["pid"]) for item in processes}
    by_pid = {int(item["pid"]): item for item in processes}
    keep_text = (keep or "").lower()
    reaped: set[int] = set()
    # 垃圾是一条链：chrome 的主进程被杀了，它的 --type=utility 子进程也得跟着杀。
    # 所以反复扫到不再新增为止——已判定为垃圾的父进程不再当作「还活着的家长」。
    while True:
        added = False
        for item in processes:
            pid = int(item["pid"])
            if pid in reaped or pid == (self_pid or os.getpid()):
                continue
            command_line = str(item.get("command_line") or "")
            if not command_line:
                continue
            lowered = command_line.lower()
            if keep_text and keep_text in lowered:
                continue
            if not any(marker in lowered for marker in GATE_LEFTOVER_MARKERS):
                continue
            parent_pid = int(item.get("parent_pid") or 0)
            parent = by_pid.get(parent_pid)
            if parent is not None and parent_pid not in reaped:
                continue  # 家长还活着、而且不是同一条垃圾链上的：那是当下另一个活的运行
            created_at = item.get("created_at")
            if created_at is None or float(now) - float(created_at) < min_age_seconds:
                continue
            reaped.add(pid)
            added = True
        if not added:
            break
    return sorted(reaped)


def _epoch_from_cim(text) -> float | None:
    """把 CIM 的 CreationDate 算成 epoch 秒。

    ``ConvertTo-Json`` 把它序列化成 ``"/Date(1794302730490)/"``（毫秒）；别的 PowerShell
    版本也可能给 ``"2026-11-10T17:25:30.038+08:00"``。两种都要接：解不出时间就当年龄未知，
    宁可不扫也不能误杀。
    """
    raw = str(text or "").strip()
    if not raw:
        return None
    marker = raw.find("Date(")
    if marker != -1:
        digits = ""
        for char in raw[marker + 5:]:
            if char.isdigit():
                digits += char
            else:
                break
        if digits:
            return float(digits) / 1000.0 if len(digits) > 11 else float(digits)
    normalized = raw.split(".")[0].replace("T", " ")[:19]
    try:
        return time.mktime(time.strptime(normalized, "%Y-%m-%d %H:%M:%S"))
    except ValueError:
        return None


def gate_leftover_processes() -> list[dict]:
    """用 CIM 拿一份进程快照（pid / parent / 命令行 / 启动时间）。

    拿不到就返空列表——清扫是锦上添花，不能因为它把门卡住。
    """
    script = (
        "Get-CimInstance Win32_Process | "
        "Select-Object ProcessId,ParentProcessId,CommandLine,CreationDate | "
        "ConvertTo-Json -Compress -Depth 3"
    )
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if completed.returncode != 0 or not completed.stdout.strip():
        return []
    try:
        rows = json.loads(completed.stdout)
    except ValueError:
        return []
    if isinstance(rows, dict):
        rows = [rows]
    snapshot: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        snapshot.append(
            {
                "pid": int(row.get("ProcessId") or 0),
                "parent_pid": int(row.get("ParentProcessId") or 0),
                "command_line": row.get("CommandLine") or "",
                "created_at": _epoch_from_cim(row.get("CreationDate")),
            }
        )
    return [row for row in snapshot if row["pid"]]


def _taskkill(pid: int, tree: bool = True) -> None:
    system_root = Path(os.environ.get("SYSTEMROOT", r"C:\Windows"))
    executable = system_root / "System32" / "taskkill.exe"
    command = [str(executable if executable.is_file() else "taskkill"), "/PID", str(pid), "/F"]
    if tree:
        command.insert(3, "/T")
    try:
        subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        pass


def process_alive(pid: int) -> bool:
    if _nt():
        try:
            kernel32 = _kernel32()
            handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return False
            still = ctypes.c_uint32()
            ok = bool(kernel32.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(still)))
            kernel32.CloseHandle(ctypes.c_void_p(handle))
            return ok and still.value == 259  # STILL_ACTIVE
        except (OSError, AttributeError):
            return False
    try:
        os.kill(pid, 0)  # noqa: F821  (POSIX)
        return True
    except OSError:
        return False


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", ctypes.c_uint32),
        ("cntUsage", ctypes.c_uint32),
        ("th32ProcessID", ctypes.c_uint32),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", ctypes.c_uint32),
        ("cntThreads", ctypes.c_uint32),
        ("th32ParentProcessID", ctypes.c_uint32),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", ctypes.c_uint32),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


def process_parent_map() -> dict[int, int]:
    """进程快照：``pid -> ppid``（Toolhelp32，毫秒级）。

    Windows 的 ppid 是**建进程那一刻**记下的，家长退出后不会改写——所以「家长已经死了」
    的孤儿（2026-11-14 那 12 个 ``node --check``）照样能顺着 ppid 找回来。
    拿不到快照（非 Windows、句柄失效）返回空字典，调用方按「查不到」处理。
    """
    if not _nt():
        return {}
    kernel32 = _kernel32()
    kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    kernel32.CreateToolhelp32Snapshot.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
    snapshot = kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if not snapshot or ctypes.c_void_p(snapshot).value in (None, ctypes.c_void_p(-1).value):
        return {}
    entry = _PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
    pairs: dict[int, int] = {}
    try:
        first = kernel32.Process32FirstW(ctypes.c_void_p(snapshot), ctypes.byref(entry))
        while first:
            pairs[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
            first = kernel32.Process32NextW(ctypes.c_void_p(snapshot), ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(ctypes.c_void_p(snapshot))
    return pairs


def descendant_pids(root_pid: int) -> list[int]:
    """:func:`process_parent_map` 里 ``root_pid`` 的全部后代（不含自己）。"""
    pairs = process_parent_map()
    if not pairs:
        return []
    children: dict[int, list[int]] = {}
    for pid, parent in pairs.items():
        children.setdefault(parent, []).append(pid)
    root = int(root_pid)
    found: list[int] = []
    pending = list(children.get(root, []))
    while pending and len(found) < 5000:  # 上限防快照异常时死循环
        pid = pending.pop()
        if pid == root or pid in found or pid in (0, 4):
            continue
        found.append(pid)
        pending.extend(children.get(pid, []))
    return found


def process_creation_time(pid: int) -> float | None:
    """进程创建时间（epoch 秒）。Windows 走 ``GetProcessTimes``，读不到返回 ``None``。"""
    if not _nt():
        return None
    kernel32 = _kernel32()
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return None
    try:
        started, _ = _process_lifetime(handle)
        return started
    finally:
        kernel32.CloseHandle(ctypes.c_void_p(handle))


def _process_lifetime(handle: int) -> tuple[float | None, float | None]:
    kernel32 = _kernel32()
    kernel32.GetProcessTimes.argtypes = [ctypes.c_void_p, *([ctypes.POINTER(ctypes.c_uint64)] * 4)]
    creation = ctypes.c_uint64()
    exited = ctypes.c_uint64()
    kernel_time = ctypes.c_uint64()
    user_time = ctypes.c_uint64()
    ok = bool(
        kernel32.GetProcessTimes(
            ctypes.c_void_p(handle),
            ctypes.byref(creation),
            ctypes.byref(exited),
            ctypes.byref(kernel_time),
            ctypes.byref(user_time),
        )
    )
    if not ok or not creation.value:
        return None, None

    def seconds(timestamp: int) -> float:
        # 将 FILETIME 的 100ns 计数换算为 Unix 时间。
        return (timestamp - 116_444_736_000_000_000) / 10_000_000

    return seconds(creation.value), seconds(exited.value) if exited.value else None


def select_orphan_children(
    children, root_started_at: float | None, root_exited_at: float | None,
) -> list[int]:
    """只接受根存活期间创建的候选；缺少任一时间时宁可不回收。"""
    if root_started_at is None or root_exited_at is None:
        return []
    # GetProcessTimes 用同一时钟，不能以容差接纳生存窗口以外的 PID。
    return [int(pid) for pid, created_at in children
            if created_at is not None and root_started_at <= created_at <= root_exited_at]


def _reap_orphan_children(root_pid: int, root_started_at: float, root_exited_at: float) -> None:
    """仅回收有身份依据的直接子进程，后代交给 taskkill /T。"""
    kernel32 = _kernel32()
    for pid, parent in process_parent_map().items():
        if parent != root_pid or pid in (0, 4, root_pid):
            continue
        handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            continue
        try:
            created_at, exited_at = _process_lifetime(handle)
            if exited_at is None and select_orphan_children([(pid, created_at)], root_started_at, root_exited_at):
                # 持有进程对象的引用，避免核对后到 taskkill 前被复用 PID。
                _kill_one(pid, process=None)
        finally:
            kernel32.CloseHandle(ctypes.c_void_p(handle))


def remove_execution_workspace(root) -> list[str]:
    """删掉本轮临时目录；删不动的路径列出来（不抛异常）。

    删不干净说明泄漏还在——还有进程钉着目录里的文件。这不该把门的 verdict 翻红（多半是
    刚杀完进程的收尾竞态），但也不能静默吞掉：那是最该被看见的信号。
    """
    path = Path(root)
    try:
        shutil.rmtree(path)
    except OSError:
        pass
    if not path.exists():
        return []
    leftovers: list[str] = []
    for entry in sorted(path.rglob("*"), key=lambda item: str(item), reverse=True):
        try:
            if entry.is_file() or entry.is_symlink():
                entry.unlink()
            else:
                entry.rmdir()
        except OSError:
            leftovers.append(str(entry))
    if path.exists():
        leftovers.append(str(path))
    return leftovers[:20]


def kill_process_tree(process, grace: float = 2.0) -> None:
    """杀掉一个进程连同它的全部后代。``process`` 可以是 ``Popen``，也可以裸传 pid。

    **先杀后代，再杀根——但只在根已经死了的时候。** ``taskkill /T`` 的树是从**活着的**根往下
    数的：根还活着时一条 ``taskkill /PID 根 /T /F`` 就把整树带走（不要一个个杀，
    那是每个后代一条 taskkill，chrome 那种三十个子进程的夹具会把几分钟耗在杀进程上）。
    根已经退出时 ``/T`` 数不到，而「父进程先退、node/chrome 留在原地钉着临时目录」
    正是 2026-11-14 泄漏的形状——这种情形才用 :func:`process_parent_map` 自己数一遍：
    ppid 是建进程时记下的，不随家长退出改写，所以只需要杀掉家长的**直接孩子**，
    它们的子树交给 ``/T``。

    非 Windows 走进程组 ``killpg``（调用方须用 ``start_new_session=True`` 起进程），
    拿不到进程组就退回 ``Popen.kill()``。

    **不能拿 PID 复用去赌。** Windows 上已退出的 ``Popen`` 用原始句柄读取生存窗口，
    只回收在此期间创建且父 PID 匹配的进程。没有进程身份依据时不扫描孤儿。

    ``grace`` 是等根进程真正消失的秒数：``taskkill /F`` 返回时句柄可能还没释放完，
    紧接着删临时目录会撞 WinError 32（目录还被死进程的工作目录钉着）。
    """
    pid = int(getattr(process, "pid", process))
    poll = getattr(process, "poll", None)
    if poll is not None:
        # Popen 对自己的子进程是权威的：句柄在手上，只有它真退出并被回收才会非 None。
        # 这里不用 process_alive(pid)——PID 会被复用，数字活着不等于「我们那个根还活着」。
        root_alive = poll() is None
    else:
        # 裸 pid：只能按 PID 判断。调用方（门内的泄漏守卫）已经核对过命令行与身份。
        root_alive = process_alive(pid)

    if not root_alive:
        # 不对已退出根的 PID 发 taskkill，也不按可能复用的 PID 重新打开根。
        handle = getattr(process, "_handle", None)
        if _nt() and isinstance(process, subprocess.Popen) and handle is not None:
            started_at, exited_at = _process_lifetime(int(handle))
            if started_at is not None and exited_at is not None:
                _reap_orphan_children(pid, started_at, exited_at)
        return

    _kill_one(pid, process=process)
    deadline = time.monotonic() + max(grace, 0.0)
    waiter = getattr(process, "wait", None)
    if waiter is not None:
        while time.monotonic() < deadline:
            try:
                waiter(timeout=0.05)
                break
            except subprocess.TimeoutExpired:
                continue
            except ValueError:
                break
    else:
        while process_alive(pid) and time.monotonic() < deadline:
            time.sleep(0.05)
    # taskkill 不存在或被拒时，至少把根进程本身掐了。
    if process_alive(pid):
        hard_kill = getattr(process, "kill", None)
        if hard_kill is not None:
            try:
                hard_kill()
            except OSError:
                pass


def _kill_one(pid: int, process) -> None:
    """杀单个 pid 的树：Windows 用 ``taskkill /T /F``，POSIX 用进程组，最后退回 ``Popen.kill()``。"""
    if os.name == "nt":
        _taskkill(pid, tree=True)
        return
    try:
        os.killpg(os.getpgid(pid), 9)
    except (ProcessLookupError, PermissionError, OSError):
        hard_kill = getattr(process, "kill", None)
        if hard_kill is not None:
            try:
                hard_kill()
            except OSError:
                pass
