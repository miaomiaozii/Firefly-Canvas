"""由刷新工具写入、由运行中的 HTML 响应读取的缓存版本。"""
import os
import re
import tempfile
from pathlib import Path
from uuid import uuid4

_REPO_ROOT = Path(__file__).resolve().parents[1]
_VALID_STAMP = re.compile(rb"[0-9a-f]{32}")


def runtime_root() -> Path:
    configured = os.environ.get("FIREFLY_RUNTIME_ROOT", "").strip()
    return _REPO_ROOT / configured if configured else _REPO_ROOT


def _marker_path() -> Path:
    return runtime_root() / "tmp/firefly-asset-cache-version"


def read_refresh_stamp() -> bytes:
    try:
        with _marker_path().open("rb") as handle:
            stamp = handle.read(33)
    except OSError:
        return b""
    return stamp if _VALID_STAMP.fullmatch(stamp) else b""


def refresh_asset_stamp() -> None:
    path = _marker_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # 响应线程不能读到半写入的版本号；同目录落盘后原子替换。
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".firefly-cache-", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        temporary.write_bytes(uuid4().hex.encode("ascii"))
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
