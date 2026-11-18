"""全局 JSON 配置的原子读写与损坏隔离（issue #345）。

为什么放在仓库根而不是 ``app/services/``：``firefly_projects.py``（根模块）必须用同一个
写入器，而 ``app/services/__init__.py`` 会 import ``firefly_projects`` —— 放包里就是循环
导入。本模块只依赖标准库，谁都能 import。

要治的事故（#345）：全局配置（API Key、资源库、提示词库、存储目录、共享文件夹）以前都是
``open(path, "w")`` + ``json.dump``。``"w"`` 在打开那一刻就把原文件截成 0 字节，dump 到一半
断电/崩溃就什么都不剩；读侧再 ``except Exception: return 默认值``，下一次保存就把默认值写
回去——用户整套 API Key 从此消失，而且没有任何痕迹。

两条规矩：
1. 写：先写同目录隐藏临时文件 → flush + fsync → ``os.replace`` 一次替换。中途失败只留临时
   文件（finally 清掉），原文件保持完整。
2. 读：文件读不动或解不开 → **先把坏文件改名 ``.corrupt-<UTC>`` 保住字节**，记一条诊断给
   界面提示，再返回默认值。默认值永远不会覆盖坏文件，因为坏文件已经被挪开了。
"""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)

PathLike = Union[str, os.PathLike]

CORRUPT_MARK = ".corrupt-"
DIAGNOSTIC_KIND_CORRUPT = "corrupt-json"
_DIAGNOSTIC_LIMIT = 50
_DIAGNOSTICS_LOCK = threading.Lock()
_DIAGNOSTICS: List[Dict[str, Any]] = []


class CorruptJSONFile(RuntimeError):
    """JSON 文件存在但读不出可用内容；已改名的话 ``quarantine`` 是新路径。"""

    def __init__(self, path: Path, reason: str, quarantine: Optional[Path] = None) -> None:
        self.path = path
        self.reason = reason
        self.quarantine = quarantine
        where = f"（坏文件已改名保在 {quarantine.name}）" if quarantine else "（坏文件改名失败，仍在原地）"
        super().__init__(f"{path.name} 读取失败：{reason}{where}")


# ---------------------------------------------------------------- 诊断登记（给界面提示）

def record_data_diagnostic(kind: str, path: PathLike, *, detail: str = "") -> Dict[str, Any]:
    entry = {
        "kind": kind,
        "file": str(path),
        "detail": detail,
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    with _DIAGNOSTICS_LOCK:
        _DIAGNOSTICS.append(entry)
        del _DIAGNOSTICS[:-_DIAGNOSTIC_LIMIT]
    return entry


def recent_data_diagnostics(limit: int = _DIAGNOSTIC_LIMIT) -> List[Dict[str, Any]]:
    with _DIAGNOSTICS_LOCK:
        return [dict(entry) for entry in _DIAGNOSTICS[-max(1, int(limit)):]]


def clear_data_diagnostics() -> None:
    with _DIAGNOSTICS_LOCK:
        _DIAGNOSTICS.clear()


# ---------------------------------------------------------------- 写：tmp + fsync + replace

def _staging_path(path: Path) -> Path:
    return path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")


def _backup_name(path: Path, index: int) -> Path:
    return path.with_name(path.name + (".bak" if index == 1 else f".bak-{index}"))


def _rotate_backups(path: Path, backup_count: int) -> None:
    """轮转（issue #94 语义）：.bak 为最新，.bak-2/.bak-3 … 更旧；左移时先处理最旧端。"""
    count = max(1, int(backup_count))
    for index in range(count, 1, -1):
        previous = _backup_name(path, index - 1)
        target = _backup_name(path, index)
        if previous.exists():
            _atomic_copy(previous, target)
        elif target.exists():
            target.unlink()
    _atomic_copy(path, _backup_name(path, 1))


# Only retry the final replace, not serialization, fsync or backup rotation.
# Windows access denial can be transient while another process holds the target.
_REPLACE_RETRY_DELAYS = (0.01, 0.02, 0.04)


def _replace_staged(staging: Path, target: Path) -> None:
    for attempt in range(len(_REPLACE_RETRY_DELAYS) + 1):
        try:
            os.replace(staging, target)
            return
        except PermissionError as exc:
            if (sys.platform != "win32" or getattr(exc, "winerror", None) != 5
                    or attempt == len(_REPLACE_RETRY_DELAYS)):
                raise
            time.sleep(_REPLACE_RETRY_DELAYS[attempt])


def _atomic_copy(src: Path, dst: Path) -> None:
    staging = dst.with_name(f".{dst.name}.{uuid.uuid4().hex}.tmp")
    try:
        with src.open("rb") as source_handle, staging.open("wb") as target_handle:
            for chunk in iter(lambda: source_handle.read(1024 * 1024), b""):
                target_handle.write(chunk)
            target_handle.flush()
            os.fsync(target_handle.fileno())
        _replace_staged(staging, dst)
    finally:
        staging.unlink(missing_ok=True)


def write_json_atomic(
    path: PathLike,
    value: Any,
    *,
    keep_backup: bool = False,
    backup_count: int = 1,
    indent: int = 2,
) -> None:
    """原子写一个 JSON 文件：临时文件 → fsync → ``os.replace``。

    任何一步抛错时，目标文件保持原样（要么旧内容，要么新内容，绝不会是截断的半份）。
    ``keep_backup=True`` 时先把旧文件轮转到 ``.bak``/``.bak-2``…（项目库存元数据用）。
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = _staging_path(target)
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=indent)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        if keep_backup and target.exists():
            _rotate_backups(target, backup_count)
        _replace_staged(temporary, target)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


# ---------------------------------------------------------------- 读：坏文件先隔离

def quarantine_corrupt_json(path: PathLike) -> Optional[Path]:
    """把坏文件改名成 ``<name>.corrupt-<UTC时间戳>``，返回新路径；改名失败返回 ``None``。

    同目录改名在 Windows 上也是原子的。绝不覆盖已有隔离文件——加 ``-1``/``-2`` 后缀，
    因为隔离文件是用户唯一的救命证据。
    """
    target = Path(path)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    candidate = target.with_name(f"{target.name}{CORRUPT_MARK}{stamp}")
    index = 1
    while candidate.exists():
        candidate = target.with_name(f"{target.name}{CORRUPT_MARK}{stamp}-{index}")
        index += 1
    try:
        os.replace(target, candidate)
    except OSError as exc:  # 文件被别的进程占着等：宁可留在原地也别装作没事
        logger.error(f"损坏的 {target.name} 改名失败（仍在原地）: {exc}")
        return None
    return candidate


def read_json(
    path: PathLike,
    *,
    default: Any = None,
    encoding: str = "utf-8",
    quarantine: bool = True,
    raise_on_corrupt: bool = False,
) -> Any:
    """读一个 JSON 文件。

    - 不存在 → ``default``（首次运行的正常路径，不算损坏）。
    - 存在但读不动 / 解不开（空文件、半份 JSON、BOM 乱码、权限错误）→ 改名
      ``.corrupt-<UTC>`` 保住字节 + 记诊断 + 打日志，然后返回 ``default``。
      ``raise_on_corrupt=True`` 时改抛 :class:`CorruptJSONFile`（调用方必须自己处理，
      不许再用默认值覆盖）。
    """
    target = Path(path)
    try:
        with target.open("r", encoding=encoding) as handle:
            return json.load(handle)
    except FileNotFoundError:
        return default
    except (OSError, ValueError) as exc:  # ValueError 覆盖 json.JSONDecodeError / UnicodeDecodeError
        reason = str(exc)
        moved = quarantine_corrupt_json(target) if quarantine else None
        detail = f"{reason}；已改名 {moved.name}" if moved else f"{reason}；改名失败，文件仍在原地"
        record_data_diagnostic(DIAGNOSTIC_KIND_CORRUPT, target, detail=detail)
        logger.error(f"读取 {target} 失败，按默认值继续：{detail}")
        if raise_on_corrupt:
            raise CorruptJSONFile(target, reason, moved) from exc
        return default
