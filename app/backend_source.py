"""#488: bounded Python source identity, independent of Git and release labels.

v1 hashes root *.py and app/ + plan_ir/ recursively (*.py only). Relative names
and bytes are length-framed in sorted order, so additions/removals/renames count.
Static assets, VERSION, tools/tests, data/runtime, hidden dirs and links/reparse
points do not count. Callers must finish deployment before starting a process;
concurrent deployment cannot provide an atomic cross-file snapshot.
"""
from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

_SKIP_DIRS = {"__pycache__", "node_modules", "data", "assets", "output", "logs", "tmp", "runtime", "projects", "venv", "browser-profiles"}


def _source_is_link(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def source_digest(root: Path) -> str:
    protected = set()
    for key in ("FIREFLY_RUNTIME_ROOT", "FIREFLY_PROJECTS_ROOT"):
        value = os.environ.get(key, "").strip()
        if value:
            protected.add(Path(os.path.abspath(root / value)))
    files = []

    def visit(path: Path, *, recurse: bool) -> None:
        try:
            info = path.lstat()
        except FileNotFoundError:
            return
        # Resolving a junction can touch a foreign path before it is rejected.
        if _source_is_link(info) or Path(os.path.abspath(path)) in protected:
            return
        if stat.S_ISREG(info.st_mode):
            if path.suffix == ".py":
                files.append(path)
        elif recurse and stat.S_ISDIR(info.st_mode):
            for child in path.iterdir():
                if not child.name.startswith(".") and child.name not in _SKIP_DIRS:
                    visit(child, recurse=True)

    for child in root.iterdir():
        if child.suffix == ".py":
            visit(child, recurse=False)
    for family in ("app", "plan_ir"):
        visit(root / family, recurse=True)
    digest = hashlib.sha256(b"firefly-backend-source-v1\0")
    for path in sorted(files, key=lambda item: item.relative_to(root).as_posix()):
        name = path.relative_to(root).as_posix().encode("utf-8")
        content = path.read_bytes()
        for part in (name, content):
            digest.update(len(part).to_bytes(8, "big"))
            digest.update(part)
    return "sha256:" + digest.hexdigest()
