"""刷新缓存，不管理任何实例或修改静态文件。"""
import os
import stat
from pathlib import Path

from app.asset_cache import refresh_asset_stamp, runtime_root

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SOURCE_DIRS = ("app", "tools", "plan_ir", "tests")
_SKIP_DIRS = {"node_modules", "data", "assets", "output", "logs", "tmp", "venv", "browser-profiles"}


def _is_link(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def refresh(root: Path) -> int:
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

    print(f"[REFRESH] RESULT: pycache_removed={removed} failures={','.join(failures) or 'none'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(refresh(_REPO_ROOT))
