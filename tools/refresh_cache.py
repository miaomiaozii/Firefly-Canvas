"""刷新缓存，不管理任何实例或修改静态文件。"""
import argparse
import json
import os
import re
import stat
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from app.backend_source import source_digest
from app.asset_cache import refresh_asset_stamp, runtime_root

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SOURCE_DIRS = ("app", "tools", "plan_ir", "tests")
_SKIP_DIRS = {"node_modules", "data", "assets", "output", "logs", "tmp", "venv", "browser-profiles"}


def _is_link(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def _read_app_info(service_url: str):
    url = urlsplit(service_url)
    if (url.scheme != "http" or url.hostname not in {"localhost", "127.0.0.1", "::1"}
            or url.username or url.password or url.query or url.fragment or url.path not in {"", "/"}):
        raise ValueError("ServiceUrl 必须是本机服务的 HTTP 基址")
    request = Request(service_url.rstrip("/") + "/api/app-info", headers={"Cache-Control": "no-cache"})
    # 本机诊断不经过环境代理，也不跟随重定向到其他地址。
    with build_opener(ProxyHandler({}), _NoRedirect()).open(request, timeout=3) as response:
        raw = response.read(65537)
    if len(raw) > 65536:
        raise ValueError("版本响应过大")
    return json.loads(raw)


def _backend_check(root: Path, service_url: str) -> str | None:
    legacy = False
    try:
        try:
            info = _read_app_info(service_url)
        except HTTPError as error:
            if error.code != 404:
                raise
            legacy = True
            info = {}
        if not isinstance(info, dict):
            raise ValueError("版本响应不是对象")
        legacy = legacy or "backend" not in info
        if not legacy:
            backend = info["backend"]
            valid_id = lambda value: isinstance(value, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", value)
            if (not isinstance(backend, dict) or backend.get("schema") != 1
                    or not valid_id(backend.get("loaded")) or not valid_id(backend.get("source"))
                    or type(backend.get("restart_required")) is not bool):
                raise ValueError("后端版本信息不完整")
            expected = source_digest(root)
            if backend["loaded"] == backend["source"] == expected and backend["restart_required"] is False:
                print("[BACKEND] 运行中的后端与当前源码一致。")
                return None
    except (OSError, URLError, ValueError) as error:
        print(f"[FAIL] backend_check_failed: 无法确认运行中的后端版本（{type(error).__name__}）。请确认服务已启动及 ServiceUrl 正确，再重试；未报告刷新成功。")
        return "backend_check_failed"
    reason = "当前服务未提供后端版本信息，无法验证已加载新代码。" if legacy else "运行中的后端与当前源码不一致；请同时确认 ServiceUrl 指向本安装的服务。"
    print(f"[FAIL] backend_restart_required: 后端已更新，需要重启服务。{reason}")
    print("请在萤火画布托盘菜单选择“重启服务”；若使用 run.bat 启动，请先在原服务窗口按 Ctrl+C 停止，再运行 run.bat。随后重新载入页面；仅刷新缓存不会加载新的 Python 代码。本工具不会自动重启服务。")
    return "backend_restart_required"


def refresh(root: Path, service_url: str) -> int:
    failures = []
    removed = 0
    protected = {runtime_root().resolve()}
    projects = os.environ.get("FIREFLY_PROJECTS_ROOT", "").strip()
    if projects:
        protected.add(Path(projects).resolve())

    def fail(code, path, error):
        failures.append(code)
        print(f"[FAIL] {code}: {path}: {error}")

    def clean(path: Path, *, bytecode_dir: bool = False) -> None:
        nonlocal removed
        if path in protected:
            return
        try:
            info = path.lstat()
            if not stat.S_ISDIR(info.st_mode) or _is_link(info):
                return
            entries = list(path.iterdir())
        except FileNotFoundError:
            return
        except OSError as error:
            fail("pycache_scan_failed", path, error)
            return

        for entry in entries:
            if entry in protected:
                continue
            if bytecode_dir:
                if entry.suffix not in {".pyc", ".pyo"}:
                    continue
                try:
                    info = entry.lstat()
                    if stat.S_ISREG(info.st_mode) and not _is_link(info):
                        entry.unlink()
                        removed += 1
                except FileNotFoundError:
                    continue
                except OSError as error:
                    fail("pycache_remove_failed", entry, error)
            elif entry.name == "__pycache__":
                clean(entry, bytecode_dir=True)
            elif not entry.name.startswith(".") and entry.name not in _SKIP_DIRS:
                clean(entry)

    # 不遍历仓库根的其他目录、运行数据或任何符号链接 / junction。
    clean(root / "__pycache__", bytecode_dir=True)
    for dirname in _SOURCE_DIRS:
        clean(root / dirname)

    try:
        refresh_asset_stamp()
    except OSError as error:
        fail("asset_version_refresh_failed", runtime_root(), error)

    backend_failure = _backend_check(root, service_url)
    if backend_failure:
        failures.append(backend_failure)

    print(f"[REFRESH] RESULT: pycache_removed={removed} failures={','.join(failures) or 'none'}")
    return 1 if failures else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service-url", default="http://127.0.0.1:3000")
    args = parser.parse_args()
    raise SystemExit(refresh(_REPO_ROOT, args.service_url))
