"""#369 quality-gate step: build-free type checking of static/js via tsc --noEmit.

The gate is fully offline (deny-all proxy, and the git-ls-files workspace has no
``node_modules``), so this step never invokes npm.  It integrity-checks the pinned
``packages/npm/*.tgz`` against ``package-lock.json`` (sha512), extracts them into
``node_modules/`` (gitignored, disposable) with stdlib ``tarfile``, and runs the
checked-in compiler through the trusted node executable:

    node node_modules/typescript/lib/tsc.js -p jsconfig.json --noEmit

No network, no lifecycle scripts, no npm cache, and nothing is written outside the
workspace.  ``tsc --noEmit`` is type-check only: no build output, no bundler
(#369 — “无构建情况下加类型检查”).

A repository without the JS type-check setup (no package.json / jsconfig.json /
package-lock.json, e.g. the gate's minimal fixture) has nothing to type-check: the
single test verifies the no-op condition and the step exits 0 — same convention as
the tabbit-e2e in-gate fast-skip (#295: the gate verifies wiring; product behavior
is verified where the setup exists).  (A zero-test module would exit 5, which the
gate reads as failure — hence the explicit wiring test.)
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import unittest
from pathlib import Path

REPO_ROOT = Path(os.getcwd())
PACKAGE_JSON = REPO_ROOT / "package.json"
JSCONFIG = REPO_ROOT / "jsconfig.json"
LOCK = REPO_ROOT / "package-lock.json"
VENDOR_DIR = REPO_ROOT / "packages" / "npm"
NODE_MODULES = REPO_ROOT / "node_modules"
TSC = NODE_MODULES / "typescript" / "lib" / "tsc.js"


def _setup_present() -> bool:
    """Type-check setup exists: the three pin files plus at least one vendored tarball."""
    return (
        VENDOR_DIR.is_dir()
        and PACKAGE_JSON.is_file()
        and JSCONFIG.is_file()
        and LOCK.is_file()
        and any(VENDOR_DIR.glob("*.tgz"))
    )


def _package_filter(name: str):
    """Filter that extracts one npm tarball directly into node_modules/<name>/.

    npm tarballs stage their contents under a top-level ``package/`` directory.
    Instead of renaming that staging directory after extraction (the rename of
    a just-written directory can hit ``PermissionError: [WinError 5]`` when an
    antivirus or index service still holds the fresh Temp files — observed in
    the gate's Temp workspace, #369 review), each member's name is rewritten
    up front so extraction lands at the final path directly.
    """
    prefix = "package/"

    def _filter(member: tarfile.TarInfo, dest_path: str) -> tarfile.TarInfo | bool:
        member = tarfile.data_filter(member, dest_path)
        if member is False:
            return member
        if member.name == "package" or member.name == prefix:
            return member.replace(name=name + "/")
        if member.name.startswith(prefix):
            return member.replace(name=name + "/" + member.name[len(prefix):])
        return member

    return _filter


def _verify_and_extract(name: str, version: str, integrity: str) -> None:
    tgz = VENDOR_DIR / f"{name}-{version}.tgz"
    if not tgz.is_file():
        raise RuntimeError(
            f"vendored tarball missing: packages/npm/{tgz.name} — "
            "repack with: npm pack "
            f"{name}@{version} --pack-destination packages/npm"
        )
    if not integrity.startswith("sha512-"):
        raise RuntimeError(
            f"package-lock.json entry for {name}@{version} has no sha512 integrity"
        )
    digest_part = integrity[len("sha512-"):]
    expected = base64.urlsafe_b64decode(digest_part + "=" * (-len(digest_part) % 4))
    actual = hashlib.sha512(tgz.read_bytes()).digest()
    if actual != expected:
        raise RuntimeError(
            f"integrity mismatch for packages/npm/{tgz.name}: "
            f"lockfile sha512 {integrity} does not match the file"
        )
    dest = NODE_MODULES / name
    if dest.exists():
        shutil.rmtree(dest)
    with tarfile.open(tgz, "r:gz") as tf:
        tf.extractall(NODE_MODULES, filter=_package_filter(name))


def _ensure_typescript() -> str:
    """Resolve node; extract the pinned vendored tarballs when node_modules is absent."""
    node = shutil.which("node")
    if not node:
        raise RuntimeError("node not found on PATH; the #369 type-check step requires node")
    if TSC.is_file():
        return node
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    for key, meta in lock.get("packages", {}).items():
        # Top-level packages only (node_modules/<name>); a scoped name would need
        # the "@scope+name-<version>.tgz" pack convention — fail loudly if ever added.
        if not key.startswith("node_modules/") or key.count("/") != 1:
            continue
        _verify_and_extract(key.rsplit("/", 1)[1], meta["version"], meta.get("integrity", ""))
    if not TSC.is_file():
        raise RuntimeError(
            "typescript is not in package-lock.json / packages/npm; "
            "the pinned tarballs must cover every top-level dependency"
        )
    return node


def _run_typecheck(node: str) -> subprocess.CompletedProcess:
    # 相对路径：门内守卫只放行精确的
    # `node node_modules/typescript/lib/tsc.js -p jsconfig.json --noEmit`
    # （tools/quality_gate.py 守卫策略，#369），cwd 就是仓库根。
    return subprocess.run(
        [node, "node_modules/typescript/lib/tsc.js", "-p", "jsconfig.json", "--noEmit"],
        cwd=REPO_ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=120,
        check=False,
    )


def _tail(text: str, limit: int = 4000) -> str:
    text = (text or "").strip()
    return text[-limit:] if len(text) > limit else text


class JavaScriptTypeCheckTests(unittest.TestCase):
    """The pinned offline toolchain type-checks static/js cleanly."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.setup_present = _setup_present()
        if cls.setup_present:
            cls.node = _ensure_typescript()

    def test_typecheck_passes(self) -> None:
        if not self.setup_present:
            # No type-check setup in this repository (e.g. the gate's minimal
            # fixture): wiring is what's under test — nothing to type-check.
            return
        result = _run_typecheck(self.node)
        self.assertEqual(
            result.returncode,
            0,
            "tsc --noEmit failed:\n" + _tail(result.stdout) + "\n" + _tail(result.stderr),
        )
