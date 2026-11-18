"""#371：缓存戳必须跟着改动走。

两件容易混在一起的事：
  - 页面里的资源有没有带 `?v=`（静态检查，任何时候都该成立）；
  - 相对基线改了被引用的 js/css，戳就必须换过（差异检查，本文件）。
第二条才是抓「改了代码没刷戳」的那条——#377 首轮那种遗漏：戳一直在、三页一直同值，
只是没变，静态检查全绿。

基线默认取 `git merge-base HEAD main`。要手动复现某个历史时刻（证明这条抓得到东西），
用 `FIREFLY_ASSET_STAMP_BASE=<ref>` 指定基线：
  FIREFLY_ASSET_STAMP_BASE=9943907 pytest tests/test_asset_version_bump.py   # 停在 585ad36 时应为红
"""

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from html import unescape
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qsl, urlsplit

REPO_ROOT = Path(__file__).resolve().parents[1]
PAGES = ("static/smart-canvas.html", "static/project-list.html", "static/global-settings.html")
# images/ 不参与刷戳（图片换文件就是换 URL，靠文件名变化失效）。
STAMP = re.compile(r'((?:href|src)="/static/)(?!images/)([^"]+)\?v=([^"]+)')


def page_stamps(text: str) -> dict[str, str]:
    """页文本里的「资源路径 -> 缓存戳」。路径不带 /static/ 前缀。"""
    return {path: stamp for _, path, stamp in STAMP.findall(text)}


def git(*args: str) -> str | None:
    # 不能用 text=True：Windows 上它按本地代码页（这台机器是 gbk）解码，
    # 而页面是 UTF-8 带中文，直接 UnicodeDecodeError。手动按 UTF-8 解。
    result = subprocess.run(["git", "-C", str(REPO_ROOT), *args], capture_output=True)
    if result.returncode != 0:
        return None
    return result.stdout.decode("utf-8", "replace").strip()


class AssetVersionBumpTests(unittest.TestCase):
    """动了被三页引用的 js/css，三页的 ?v= 就必须换过（相对基线）。"""

    def _stamps_at(self, revision: str) -> dict[str, dict[str, str]] | None:
        if git("rev-parse", "--verify", revision) is None:
            return None
        pages = {}
        for page in PAGES:
            blob = git("show", f"{revision}:{page}")
            if blob is None:
                return None
            pages[page] = page_stamps(blob)
        return pages

    def _stamps_in_worktree(self) -> dict[str, dict[str, str]]:
        return {page: page_stamps((REPO_ROOT / page).read_text(encoding="utf-8")) for page in PAGES}

    def test_changed_assets_must_carry_a_fresh_stamp(self):
        base_ref = os.environ.get("FIREFLY_ASSET_STAMP_BASE") or git("merge-base", "HEAD", "main")
        if not base_ref:
            self.skipTest("拿不到 git（便携包无 .git 或浅克隆），差异检查跳过")
        before = self._stamps_at(base_ref)
        if before is None:
            self.skipTest(f"基线 {base_ref[:8]} 上读不到三个页面，差异检查跳过")

        diff = git("diff", "--name-only", base_ref, "--", "static") or ""
        referenced = {path for page in before.values() for path in page}
        changed = [line[len("static/"):] for line in diff.splitlines()
                   if line.startswith("static/") and line[len("static/"):] in referenced]
        if not changed:
            return  # 在 main 本身上跑：没有差异，自然通过

        now = self._stamps_in_worktree()
        for page in PAGES:
            for asset in changed:
                if asset not in before[page] or asset not in now[page]:
                    continue
                self.assertNotEqual(
                    before[page][asset], now[page][asset],
                    f"{page} 引用的 {asset} 在基线 {base_ref[:8]} 之后被改过，"
                    f"但页面里的 ?v= 还是 {before[page][asset]!r}——"
                    "浏览器会继续用缓存里的旧文件，改动到不了用户手上",
                )


class StampGuardSelfCheckTests(unittest.TestCase):
    """这条守卫本身要能被「戳没变」的改动刷红（防它被写成一直绿）。"""

    def test_same_stamp_for_a_changed_asset_is_a_failure(self):
        page = 'href="/static/js/smart-canvas.js?v=20261115.084712.3400"'
        self.assertEqual(
            page_stamps(page), {"js/smart-canvas.js": "20261115.084712.3400"})
        bumped = 'href="/static/js/smart-canvas.js?v=20261115.130123.1565"'
        self.assertNotEqual(page_stamps(page), page_stamps(bumped))

    def test_images_are_exempt(self):
        self.assertEqual(page_stamps('src="/static/images/logo.png?v=1"'), {})


class AssetRefreshCompatibilityTests(unittest.TestCase):
    """#371: execute only each launcher's bump function, never stop/start servers."""

    def test_restart_and_refresh_preserve_scoped_revisions_and_other_query_parameters(self):
        from tests import test_canvas_frontend_sources as frontend

        shell = shutil.which("powershell") or shutil.which("pwsh")
        if shell is None:
            self.skipTest("Windows launcher integration requires PowerShell")
        for script, function in (("refresh-cache.ps1", "Bump-AllAssetVersions"),
                                 ("restart-firefly.ps1", "Bump-AssetVersions")):
            with self.subTest(script=script), tempfile.TemporaryDirectory(prefix="firefly-cache-stamp-") as temp:
                root = Path(temp)
                static = root / "static"
                static.mkdir()
                for page in PAGES:
                    shutil.copyfile(REPO_ROOT / page, root / page)
                # Also protect non-Agent query parameters, both HTML-escaped and raw.
                extra = ('\n<script src="/static/js/probe.js?v=1&amp;mode=preview"></script>'
                         '\n<script src="/static/js/probe2.js?v=1&mode=preview"></script>\n')
                with (static / "project-list.html").open("a", encoding="utf-8", newline="") as target:
                    target.write(extra)
                before = {page: (root / page).read_bytes() for page in PAGES}
                environment = dict(os.environ, FIREFLY_CACHE_TEST_ROOT=str(root),
                                   FIREFLY_CACHE_TEST_SCRIPT=str(REPO_ROOT / "tools" / script),
                                   FIREFLY_CACHE_TEST_FUNCTION=function)
                # AST extraction defines only the named function. Dot-sourcing the
                # whole script would kill port 3000: explicitly never do that here.
                command = r"""
$ErrorActionPreference = 'Stop'
$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($env:FIREFLY_CACHE_TEST_SCRIPT, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'cache script parse failed' }
$definition = $ast.Find({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $env:FIREFLY_CACHE_TEST_FUNCTION }, $true)
if (-not $definition) { throw 'cache bump function missing' }
$root = $env:FIREFLY_CACHE_TEST_ROOT
. ([scriptblock]::Create($definition.Extent.Text))
& $env:FIREFLY_CACHE_TEST_FUNCTION
"""
                result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                         "-Command", command], env=environment, capture_output=True,
                                        encoding="utf-8", errors="replace", timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                # Re-run the exact existing contract on the bumped temporary pages.
                # Preserve its five r3 revisions and navigation-before-TUI order.
                with patch.object(frontend, "ROOT", root):
                    contract = frontend.CanvasFrontendSourceListTests()
                    contract.test_agent_r3_assets_share_a_scoped_cache_revision_and_navigation_loads_first()
                    contract.test_local_assets_on_product_pages_all_carry_a_cache_stamp()
                    contract.test_canvas_assets_share_one_cache_buster()
                    contract.test_product_pages_share_one_stamp_across_the_repo()
                versions = set()
                for page in PAGES:
                    after = (root / page).read_bytes()
                    self.assertEqual(after.count(b"\r\n"), before[page].count(b"\r\n"))
                    old_urls = re.findall(r'(?:href|src)="(/static/[^\"]+\?v=[^\"]+)"', before[page].decode("utf-8"))
                    new_urls = re.findall(r'(?:href|src)="(/static/[^\"]+\?v=[^\"]+)"', after.decode("utf-8"))
                    self.assertEqual(len(old_urls), len(new_urls))
                    for old, new in zip(old_urls, new_urls):
                        old_url, new_url = urlsplit(unescape(old)), urlsplit(unescape(new))
                        self.assertEqual(old_url.path, new_url.path)
                        old_params, new_params = dict(parse_qsl(old_url.query)), dict(parse_qsl(new_url.query))
                        old_version = old_params.pop("v")
                        new_version = new_params.pop("v")
                        self.assertNotEqual(old_version, new_version)
                        self.assertEqual(old_params, new_params, f"{script}: {new}")
                        versions.add(new_version)
                self.assertEqual(len(versions), 1, "all three pages must share the new release stamp")


if __name__ == "__main__":
    unittest.main()
