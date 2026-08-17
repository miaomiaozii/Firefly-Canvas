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


if __name__ == "__main__":
    unittest.main()
