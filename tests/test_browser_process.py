"""Browser process settings and static launch guard; never starts a browser."""
import ast
import os
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tools import browser_process

ROOT = Path(__file__).resolve().parents[1]
SPAWN_NAMES = {"Popen", "run", "call", "check_call", "check_output"}


def unguarded_launches(source, *, browser_runner=False):
    """Find direct subprocess launches in headless scripts or with browser argv.

    The headless check also catches argv assembled in a separate variable. The
    argument check catches Chrome/Edge launches without a headless flag. This is
    deliberately conservative, not a general Python data-flow analyzer.
    """
    tree = ast.parse(source)
    subprocess_modules = {"subprocess"}
    subprocess_functions = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            subprocess_modules.update(alias.asname or alias.name for alias in node.names if alias.name == "subprocess")
        elif isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            subprocess_functions.update(alias.asname or alias.name for alias in node.names if alias.name in SPAWN_NAMES)
    headless_script = any(
        isinstance(node, ast.Constant) and isinstance(node.value, str)
        and node.value.startswith("--headless") for node in ast.walk(tree)
    )
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        direct = (
            isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
            and func.value.id in subprocess_modules and func.attr in SPAWN_NAMES
        ) or (isinstance(func, ast.Name) and func.id in subprocess_functions)
        if not direct:
            continue
        arguments = list(node.args[:1]) + [kw.value for kw in node.keywords if kw.arg in {"args", "executable"}]
        browser_argv = any(
            (isinstance(part, ast.Name) and any(word in part.id.lower() for word in ("browser", "chrome", "msedge", "chromium")))
            or (isinstance(part, ast.Constant) and isinstance(part.value, str)
                and any(word in part.value.lower() for word in ("chrome.exe", "msedge.exe", "chromium.exe")))
            for arg in arguments for part in ast.walk(arg)
        )
        if browser_runner or headless_script or browser_argv:
            found.append(node.lineno)
    return found


class BrowserProcessTests(unittest.TestCase):
    def test_windows_flags_and_startupinfo_without_launch(self):
        startup = SimpleNamespace(dwFlags=0x20, wShowWindow=None)
        with mock.patch.object(browser_process.os, "name", "nt"), \
             mock.patch.object(browser_process.subprocess, "STARTUPINFO", return_value=startup, create=True), \
             mock.patch.object(browser_process.subprocess, "STARTF_USESHOWWINDOW", 1, create=True), \
             mock.patch.object(browser_process.subprocess, "SW_HIDE", 0, create=True), \
             mock.patch.object(browser_process.subprocess, "CREATE_NO_WINDOW", 0x08000000, create=True), \
             mock.patch.object(browser_process.subprocess, "Popen") as popen, \
             mock.patch.object(browser_process.subprocess, "run") as run:
            browser_process.browser_popen(["browser"], cwd="fixture")
            popen.assert_called_once_with(["browser"], cwd="fixture", creationflags=0x08000000, startupinfo=startup)
            self.assertEqual(startup.dwFlags, 0x21)
            self.assertEqual(startup.wShowWindow, 0)
            browser_process.browser_run(["browser"], timeout=1)
            run.assert_called_once_with(["browser"], timeout=1, creationflags=0x08000000, startupinfo=startup)

    @unittest.skipUnless(os.name == "nt", "Windows subprocess constants are unavailable")
    def test_real_windows_startupinfo_without_launch(self):
        kwargs = browser_process.hidden_browser_kwargs()
        self.assertEqual(kwargs["creationflags"], subprocess.CREATE_NO_WINDOW)
        self.assertIsInstance(kwargs["startupinfo"], subprocess.STARTUPINFO)
        self.assertTrue(kwargs["startupinfo"].dwFlags & subprocess.STARTF_USESHOWWINDOW)
        self.assertEqual(kwargs["startupinfo"].wShowWindow, subprocess.SW_HIDE)

    def test_non_windows_preserves_kwargs_without_launch(self):
        with mock.patch.object(browser_process.os, "name", "posix"), \
             mock.patch.object(browser_process.subprocess, "Popen") as popen, \
             mock.patch.object(browser_process.subprocess, "run") as run:
            browser_process.browser_popen(["browser"], cwd="fixture")
            browser_process.browser_run(["browser"], timeout=1)
            popen.assert_called_once_with(["browser"], cwd="fixture")
            run.assert_called_once_with(["browser"], timeout=1)

    def test_guard_detects_direct_launch_variants(self):
        samples = (
            'import subprocess\nsubprocess.Popen([str(browser), "--headless=new"])',
            'import subprocess as sp\ncmd = [str(executable), "--headless=new"]\nsp.run(cmd)',
            'from subprocess import Popen as spawn\nspawn(["msedge.exe"])',
            'import subprocess\nsubprocess.run([browser_path])',
        )
        for sample in samples:
            with self.subTest(sample=sample):
                self.assertTrue(unguarded_launches(sample))
        self.assertTrue(unguarded_launches('import subprocess\nsubprocess.run(command)', browser_runner=True))
        self.assertFalse(unguarded_launches('browser_popen([browser, "--headless=new"])'))

    def test_repository_browser_launches_use_helper(self):
        paths = subprocess.check_output(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z", "--", "*.py"], cwd=ROOT
        ).decode("utf-8").split("\0")
        violations = []
        for name in filter(None, paths):
            if name in {"tools/browser_process.py", "tests/test_browser_process.py"}:
                continue  # The helper wraps subprocess; this test contains synthetic examples.
            path = ROOT / name
            browser_runner = path.name.endswith("_browser.py") and not path.name.startswith("test_")
            for line in unguarded_launches(path.read_text(encoding="utf-8"), browser_runner=browser_runner):
                violations.append(f"{name}:{line}")
        self.assertFalse(violations, "direct browser subprocess launches: " + ", ".join(violations))


if __name__ == "__main__":
    unittest.main()
