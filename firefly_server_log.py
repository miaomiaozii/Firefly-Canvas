"""issue #364：服务器文本日志（UTF-8、追加写、7 天 / 100MB 轮转）。

现场问题（票面）：``tools/launch-firefly.ps1`` 用固定文件名重定向
``data/server.stdout.log`` / ``server.stderr.log``，每次启动覆盖 → 崩溃前的日志没了；
没有轮转（MISSION.md:98 要求 7 天 / 100MB）；子进程没设 UTF-8，中文全是 GBK 乱码；
轮询 ComfyUI ``/queue`` 的 httpx INFO 一行一次，10 小时就能堆 1.37MB。

约定跟 ``firefly_diagnostics.py``（issue #37）对齐：日志落在 ``<FIREFLY_RUNTIME_ROOT>/logs/``，
只做本地文件 I/O，不联网。隔离实例（质量门 / e2e）设了 ``FIREFLY_RUNTIME_ROOT``，
日志就落进自己的沙箱，不污染真实库。

这里只装**文件** handler；控制台 handler 由 ``main.py`` 的 ``basicConfig`` 负责，
测试里 ``import main`` 也不会往仓库里写文件（调用点在 ``main.py`` 的 ``__main__`` 守卫里）。
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import List, Optional

SERVER_LOG_NAME = "server.log"
ARCHIVE_GLOB = "server-*.log"
BOOT_LOG_GLOB = "boot-*.log"

DEFAULT_MAX_BYTES = 100 * 1024 * 1024  # 与 firefly_diagnostics 同一个上限（MISSION.md:98）
DEFAULT_MAX_AGE_DAYS = 7
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

REPO_ROOT = Path(__file__).resolve().parent


def log_dir(runtime_root: Optional[os.PathLike] = None) -> Path:
    """日志目录：``<FIREFLY_RUNTIME_ROOT>/logs``，未设时用仓库根。

    与 ``firefly_diagnostics.log_dir()`` 同一个根，两类日志并排放，用户要清就清一个目录。
    """
    if runtime_root is not None:
        root = Path(runtime_root)
    else:
        value = os.environ.get("FIREFLY_RUNTIME_ROOT", "").strip()
        root = Path(value) if value else REPO_ROOT
    return root / "logs"


def server_log_path(runtime_root: Optional[os.PathLike] = None) -> Path:
    return log_dir(runtime_root) / SERVER_LOG_NAME


def _stamp(moment: float) -> str:
    return time.strftime("%Y%m%d-%H%M%S", time.localtime(moment))


class ServerLogHandler(logging.Handler):
    """追加写 + 「满 100MB 或过 7 天」轮转的文本 handler。

    不用 ``TimedRotatingFileHandler``：它按「归档数」保留，跟「保留 7 天」不是一回事，
    而且同一天内因大小触发第二次轮转时归档名会撞车、把上一份盖掉。这里自己改名归档
    （名字带时间戳，撞车就加序号），归档超龄才删——和 ``firefly_diagnostics._rotate`` 同口径。
    """

    def __init__(
        self,
        path: os.PathLike | str,
        *,
        max_bytes: int = DEFAULT_MAX_BYTES,
        max_age_days: int = DEFAULT_MAX_AGE_DAYS,
        level: int = logging.NOTSET,
    ) -> None:
        super().__init__(level=level)
        self.setFormatter(logging.Formatter(LOG_FORMAT))
        self.path = Path(path)
        self.max_bytes = int(max_bytes)
        self.max_age_days = int(max_age_days)
        self._stream = None
        self._bytes_written = 0
        # 单次会话可能跑很多天（uvicorn 的 access 行全打在启动器的 boot 日志里，
        # 那份不会自己轮转），所以写日志时顺手隔一段时间清一次 boot 日志，不用等下次启动。
        self._boot_prune_interval = 300.0
        self._next_boot_prune = 0.0
        self._open()

    # --- 打开 / 关闭 ---------------------------------------------------------

    def _open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # 追加：下一次启动接着写，不盖掉上一次的日志（票面验收①）。
        self._stream = open(self.path, "a", encoding="utf-8", errors="replace", newline="\n")
        self._bytes_written = self.path.stat().st_size

    def flush(self) -> None:
        with self.lock:
            if self._stream is not None:
                self._stream.flush()

    def close(self) -> None:
        with self.lock:
            if self._stream is not None:
                try:
                    self._stream.flush()
                except OSError:
                    pass
                try:
                    self._stream.close()
                except OSError:
                    pass
                self._stream = None
        super().close()

    # --- 轮转 ---------------------------------------------------------------

    def _purge_age(self, now: Optional[float] = None) -> None:
        moment = time.time() if now is None else now
        try:
            archives = list(self.path.parent.glob(ARCHIVE_GLOB))
        except OSError:
            return
        for path in archives:
            try:
                if moment - path.stat().st_mtime > self.max_age_days * 86400:
                    path.unlink(missing_ok=True)
            except OSError:
                continue

    def _rollover(self) -> None:
        """把活动文件改名成归档，再开一个新的活动文件。"""
        if self._stream is not None:
            try:
                self._stream.close()
            except OSError:
                pass
            self._stream = None
        stamp = _stamp(time.time())
        archive = self.path.parent / f"{self.path.stem}-{stamp}.log"
        counter = 1
        while archive.exists():
            archive = self.path.parent / f"{self.path.stem}-{stamp}.{counter}.log"
            counter += 1
        try:
            self.path.replace(archive)
        except OSError:
            # 改名失败就别把日志丢了：接着往原文件追加。
            pass
        self._open()

    # --- 写入 ---------------------------------------------------------------

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
        except Exception:  # pragma: no cover - 格式化失败由 logging.handleError 兜底
            self.handleError(record)
            return
        with self.lock:
            try:
                if self._stream is None:
                    self._open()
                payload = message + "\n"
                self._stream.write(payload)
                self._bytes_written += len(payload.encode("utf-8"))
                if self._bytes_written >= self.max_bytes:
                    self._rollover()
                self._purge_age()
                now = time.time()
                if now >= self._next_boot_prune:
                    self._next_boot_prune = now + self._boot_prune_interval
                    prune_boot_logs(self.path.parent, max_age_days=self.max_age_days)
                self._stream.flush()
            except Exception:
                self.handleError(record)


def prune_boot_logs(
    directory: Optional[os.PathLike] = None,
    *,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    max_total_bytes: int = DEFAULT_MAX_BYTES,
    now: Optional[float] = None,
) -> List[Path]:
    """清掉超龄或超出总体积上限的启动期日志（``boot-*.log``），返回被删的路径。

    启动器每次启动写一对带时间戳的 boot 日志（保住「日志配置生效之前就崩」的现场）。
    它们不像 ``server.log`` 那样自己轮转，所以 MISSION.md:98 的「7 天 / 100MB」两条都要在这里钉：
    先按龄删，再把剩下的从新到旧累加，总量超 100MB 之后的（都是更旧的）接着删。
    ``server.log`` 不在 glob 里，不会误删。
    """
    target = Path(directory) if directory is not None else log_dir()
    moment = time.time() if now is None else now
    removed: List[Path] = []
    try:
        candidates = sorted(
            (path for path in target.glob(BOOT_LOG_GLOB)),
            key=lambda path: path.stat().st_mtime,
            reverse=True,  # 新的先进：收体积时留下最近的，超限的全是更旧的
        )
    except OSError:
        return removed
    kept_bytes = 0
    for path in candidates:
        try:
            size = path.stat().st_size
            age = moment - path.stat().st_mtime
        except OSError:
            continue
        too_old = age > max_age_days * 86400
        too_much = kept_bytes + size > max_total_bytes
        if too_old or too_much:
            try:
                path.unlink(missing_ok=True)
                removed.append(path)
            except OSError:
                continue
            continue
        kept_bytes += size
    return removed


def configure_server_logging(
    logger: Optional[logging.Logger] = None,
    *,
    level: int = logging.INFO,
    path: Optional[os.PathLike] = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
) -> Optional[ServerLogHandler]:
    """给 root logger 挂一个 UTF-8 轮转文件 handler，并把 httpx 降到 WARNING。

    幂等：同一个 logger 上已经有 ``ServerLogHandler`` 就返回它，不重复挂
    （重复挂会让每行日志翻倍）。返回 ``None`` 表示建文件失败——日志本身是尽力而为，
    建不出来也不能让服务起不来。
    """
    target = logger if logger is not None else logging.getLogger()
    for existing in target.handlers:
        if isinstance(existing, ServerLogHandler):
            return existing
    try:
        handler = ServerLogHandler(
            Path(path) if path is not None else server_log_path(),
            max_bytes=max_bytes,
            max_age_days=max_age_days,
            level=level,
        )
    except OSError:
        return None
    target.addHandler(handler)
    # 每轮询一次 ComfyUI /queue 就一行 INFO：10 小时 1.37MB 里大半是它（票面）。
    # 请求真出错时 httpx 自己会升 WARNING/ERROR，静音掉 INFO 不丢诊断信息。
    logging.getLogger("httpx").setLevel(logging.WARNING)
    prune_boot_logs()
    return handler
