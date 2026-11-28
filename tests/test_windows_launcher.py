import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class WindowsLauncherTests(unittest.TestCase):
    def test_launcher_python_can_import_project_modules_from_run_bat_working_directory(self):
        bundled_python = ROOT / "python" / "python.exe"
        launcher_python = bundled_python if bundled_python.exists() else Path(sys.executable)
        result = subprocess.run(
            [
                str(launcher_python),
                "-c",
                "import os,runpy,sys; sys.path.insert(0, os.path.dirname(os.path.abspath('main.py'))); import firefly_projects; print(firefly_projects.__file__)",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertTrue(result.stdout.strip().endswith("firefly_projects.py"), result.stdout)

    def test_run_bat_waits_for_health_before_opening_browser(self):
        launcher = (ROOT / "run.bat").read_text(encoding="utf-8")
        helper = (ROOT / "tools" / "wait-for-local-server.ps1").read_text(encoding="utf-8")
        self.assertIn("launch-firefly.ps1", launcher)
        detached = (ROOT / "tools" / "launch-firefly.ps1").read_text(encoding="utf-8")
        self.assertIn("Start-Process", detached)
        self.assertIn("WindowStyle Hidden", detached)
        self.assertIn("runpy.run_path('main.py', run_name='__main__')", detached)
        self.assertIn("wait-for-local-server.ps1", detached)
        self.assertIn("Invoke-WebRequest", helper)
        self.assertIn("Start-Process $uri", helper)


@unittest.skipUnless(sys.platform == "win32", "Windows restart tool")
class RestartEnvironmentTests(unittest.TestCase):
    def test_development_restart_excludes_only_the_harness_variables_from_server(self):
        import tempfile

        injected = {
            key: f"fixture-{key}"
            for key in (
                "PI_PACKAGE_DIR", "PI_CODING_AGENT_DIR", "PI_SESSION_FILE", "PI_SESSION_ID",
                "PI_PROVIDER", "PI_MODEL", "PI_REASONING_LEVEL", "BILLION_CONTEXT_NATIVE",
                "BILLION_CONTEXT_PROXY",
            )
        }
        preserved = {"FIREFLY_PI_CLI": "fixture-cli", "PI_OFFLINE": "1", "BILLION_CONTEXT_OTHER": "keep"}
        with tempfile.TemporaryDirectory(prefix="firefly-restart-env-") as directory:
            root = Path(directory)
            tools = root / "tools"
            tools.mkdir()
            for name in ("restart-firefly.ps1", "firefly-boot-logs.ps1"):
                shutil.copyfile(ROOT / "tools" / name, tools / name)
            # Use real process creation and redirection, without touching the running service or opening a browser.
            (tools / "run-server.py").write_text(
                "import json, os\n"
                f"print(json.dumps({{key: os.environ[key] for key in {list(injected | preserved)!r} if key in os.environ}}), flush=True)\n",
                encoding="utf-8",
            )
            (tools / "wait-for-local-server.ps1").write_text("exit 0\n", encoding="ascii")
            (root / "check.ps1").write_text(
                "param([string]$PythonPath)\n"
                "$ErrorActionPreference = 'Stop'\n"
                "function Get-NetTCPConnection { [CmdletBinding()] param($LocalPort, $State) }\n"
                "function Get-CimInstance { [CmdletBinding()] param($ClassName, $Filter) }\n"
                "function Stop-Process { throw 'test must not stop a process' }\n"
                "$children = [System.Collections.Generic.List[System.Diagnostics.Process]]::new()\n"
                "function Start-Process {\n"
                "  param($FilePath, $ArgumentList, $WorkingDirectory, $WindowStyle,\n"
                "        $RedirectStandardOutput, $RedirectStandardError)\n"
                "  $children.Add((Microsoft.PowerShell.Management\\Start-Process @PSBoundParameters -PassThru))\n"
                "}\n"
                "try { & (Join-Path $PSScriptRoot 'tools/restart-firefly.ps1') -PythonPath $PythonPath }\n"
                "finally {\n"
                "  foreach ($child in $children) {\n"
                "    if (-not $child.WaitForExit(5000)) { $child.Kill(); $child.WaitForExit(); throw 'fixture child timed out' }\n"
                "    $child.Dispose()\n"
                "  }\n"
                "}\n"
                + "\n".join(f"if ($env:{key} -cne '{value}') {{ throw 'parent {key} changed' }}"
                            for key, value in (injected | preserved).items()),
                encoding="utf-8",
            )
            result = subprocess.run(
                ["powershell.exe", "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                 str(root / "check.ps1"), "-PythonPath", sys.executable],
                cwd=root, env={**os.environ, **injected, **preserved, "FIREFLY_RUNTIME_ROOT": str(root)},
                capture_output=True, timeout=20,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            stdout, = (root / "logs").glob("boot-*.stdout.log")
            self.assertEqual(json.loads(stdout.read_text(encoding="utf-8")), preserved)


if __name__ == "__main__":
    unittest.main()


class LauncherDependencyCheckStderrTests(unittest.TestCase):
    """launch-firefly.ps1 must judge dependencies by exit code, not by stderr noise."""

    def _run_with_fake_python(self, body: str):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "fakepy.cmd"
            fake.write_text("@echo off\r\n" + body + "\r\n", encoding="ascii")
            return subprocess.run(
                ["powershell.exe", "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-File", str(ROOT / "tools" / "launch-firefly.ps1"),
                 "-PythonPath", str(fake), "-NoPopup", "-NoBrowser", "-Port", "3199"],
                cwd=ROOT, capture_output=True, timeout=60,
            )

    @unittest.skipUnless(sys.platform == "win32", "Windows launcher")
    def test_stderr_warning_with_exit_zero_is_not_reported_as_missing_python(self):
        # The real trigger was requests' RequestsDependencyWarning on stderr; with
        # ErrorActionPreference=Stop PowerShell 5.1 turned it into "找不到可用的 Python".
        result = self._run_with_fake_python(
            "if \"%2\"==\"--names-only\" (echo RequestsDependencyWarning 1>&2 & exit /b 0)\r\nexit /b 1"
        )
        out = result.stdout.decode("utf-8", "replace") + result.stderr.decode("utf-8", "replace")
        self.assertNotIn("找不到可用的 Python", out)
        self.assertNotIn("依赖不完整", out)

    @unittest.skipUnless(sys.platform == "win32", "Windows launcher")
    def test_missing_dependency_exit_code_still_blocks_launch(self):
        result = self._run_with_fake_python(
            "if \"%2\"==\"--names-only\" (echo RequestsDependencyWarning 1>&2 & echo Pillow-PIL & exit /b 1)\r\nexit /b 1"
        )
        out = result.stdout.decode("utf-8", "replace")
        self.assertEqual(result.returncode, 1, out)
        self.assertIn("Pillow-PIL", out)
        self.assertNotIn("找不到可用的 Python", out)