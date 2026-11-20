"""Windows owner/shortcut integration, with no real Startup or Desktop writes."""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows desktop launcher")


def run_powershell(tmp_path, script):
    ps = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    path = tmp_path / "verify.ps1"
    path.write_text("$ErrorActionPreference = 'Stop'\n" + textwrap.dedent(script), encoding="utf-8-sig")
    result = subprocess.run(
        [str(ps), "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass", "-File", str(path)],
        env=dict(os.environ, TEST_REPO=str(ROOT), TEST_ROOT=str(tmp_path), TEST_PYTHON=sys.executable),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=45,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_owned_child_graceful_stop_and_descendant_cleanup(tmp_path):
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "run-server.py").write_text(textwrap.dedent("""\
        import os
        import subprocess
        import sys
        from pathlib import Path

        assert sys.stdin.buffer.readline() == b"start\\n"
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
        print("启动完成", flush=True)
        Path(os.environ["FIREFLY_DESKTOP_READY_FILE"]).write_text(str(child.pid))
        assert os.read(sys.stdin.fileno(), 1) == b""
        print("收到退出", file=sys.stderr, flush=True)
        # Deliberately leave the descendant alive; the owner must close its Job.
        """), encoding="utf-8")
    run_powershell(tmp_path, r"""
        Add-Type -Path "$env:TEST_REPO/tools/FireflyDesktopProcess.cs"
        $out = "$env:TEST_ROOT/boot.stdout"
        $err = "$env:TEST_ROOT/boot.stderr"
        $ready = "$env:TEST_ROOT/ready"
        $owner = [FireflyDesktop.FireflyDesktopProcess]::new($env:TEST_PYTHON, $env:TEST_ROOT, $out, $err, $ready)
        try {
            $until = [DateTime]::UtcNow.AddSeconds(8)
            while ((-not (Test-Path $ready)) -and -not $owner.HasExited -and [DateTime]::UtcNow -lt $until) {
                Start-Sleep -Milliseconds 30
            }
            $ownChild = Get-Process -Id ([int](Get-Content $ready))
            $owner.RequestStop()
            $until = [DateTime]::UtcNow.AddSeconds(8)
            while ((-not $owner.HasExited) -and [DateTime]::UtcNow -lt $until) { Start-Sleep -Milliseconds 30 }
            if (-not $owner.HasExited) { throw 'The owner did not deliver EOF' }
            if ($ownChild.HasExited) { throw 'Fixture did not leave a descendant' }
        } finally { $owner.Dispose() }
        if (-not $ownChild.WaitForExit(4000)) { throw 'Job did not clean its descendant' }
    """)
    assert (tmp_path / "boot.stdout").read_text(encoding="utf-8").strip() == "启动完成"
    assert (tmp_path / "boot.stderr").read_text(encoding="utf-8").strip() == "收到退出"


def test_shortcut_preserves_other_install_and_autostart_is_opt_in(tmp_path):
    run_powershell(tmp_path, r"""
        . "$env:TEST_REPO/tools/firefly-desktop-links.ps1"
        $desktop = "$env:TEST_ROOT/desktop"
        $programs = "$env:TEST_ROOT/programs"
        $startup = "$env:TEST_ROOT/startup"
        & "$env:TEST_REPO/tools/install-firefly-shortcuts.ps1" -DesktopFolder $desktop -ProgramsFolder $programs -NoPopup
        $shell = New-Object -ComObject WScript.Shell
        $path = Get-FireflyDesktopLinkPath -Folder $desktop
        foreach ($folder in @($desktop, $programs)) {
            $file = Get-FireflyDesktopLinkPath -Folder $folder
            if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw 'Missing app shortcut' }
            $link = $shell.CreateShortcut($file)
            if ($link.TargetPath -notlike '*\wscript.exe' -or $link.Arguments -notlike '*open-firefly-desktop.vbs"') {
                throw 'Unexpected shortcut target'
            }
        }
        $foreign = $shell.CreateShortcut($path)
        $foreign.Arguments = 'unrelated'
        $foreign.Save()
        $rejected = $false
        try { New-FireflyDesktopLink -RepoRoot $env:TEST_REPO -Folder $desktop }
        catch { $rejected = $true }
        if (-not $rejected -or $shell.CreateShortcut($path).Arguments -ne 'unrelated') { throw 'Replaced unrelated shortcut' }
        if (Test-FireflyAutoStart -RepoRoot $env:TEST_REPO -Folder $startup) { throw 'Unexpected autostart' }
        Set-FireflyAutoStart -RepoRoot $env:TEST_REPO -Folder $startup -Enabled $true
        if (-not (Test-FireflyAutoStart -RepoRoot $env:TEST_REPO -Folder $startup)) { throw 'Autostart not enabled' }
        Set-FireflyAutoStart -RepoRoot $env:TEST_REPO -Folder $startup -Enabled $false
        if (Test-Path -LiteralPath (Get-FireflyDesktopLinkPath -Folder $startup)) { throw 'Autostart was not removed' }
    """)
