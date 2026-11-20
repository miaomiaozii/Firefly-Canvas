"""#458：用独立目录验证 PowerShell 的真实退出码和不停机的缓存刷新。"""
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from app.static_pages import html_response

ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which("powershell.exe")


def test_refresh_launcher_does_not_manage_server_processes():
    script = (ROOT / "tools/refresh-cache.ps1").read_text(encoding="utf-8-sig")
    assert not re.search(r"Get-NetTCPConnection|Get-CimInstance|Stop-Process|Start-Process|run-server\.py", script)


@pytest.fixture
def isolated_refresh(tmp_path, monkeypatch):
    repo = tmp_path / "fixture checkout"
    runtime = tmp_path / "runtime"
    projects = tmp_path / "projects"
    monkeypatch.setenv("FIREFLY_RUNTIME_ROOT", str(runtime))
    monkeypatch.setenv("FIREFLY_PROJECTS_ROOT", str(projects))
    for rel in ("app/asset_cache.py", "tools/refresh_cache.py", "tools/refresh-cache.ps1", "刷新缓存.bat"):
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / rel, target)
    return repo, runtime, projects


def run_refresh(repo):
    if not POWERSHELL:
        pytest.skip("需要 Windows PowerShell")
    return subprocess.run(
        [POWERSHELL, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(repo / "tools/refresh-cache.ps1"), "-PythonPath", sys.executable],
        cwd=repo.parent, capture_output=True, timeout=25,
    )


def test_refresh_changes_next_html_response_without_restarting_or_writing_html(isolated_refresh):
    repo, _, projects = isolated_refresh
    page = repo / "static/probe.html"
    page.parent.mkdir()
    template = b'<script src="/static/probe.js?v=old&amp;agent=403#anchor"></script>'
    page.write_bytes(template)
    repo_cache = repo / "app/__pycache__/demo.pyc"
    external_cache = projects / "__pycache__/precious.pyc"
    data_cache = repo / "app/data/__pycache__/keep.pyc"
    for path in (repo_cache, external_cache, data_cache):
        path.parent.mkdir(parents=True)
        path.write_bytes(b"sentinel")
    first = html_response(page).body
    for _ in range(2):
        result = run_refresh(repo)
        assert result.returncode == 0, (result.stdout, result.stderr)
        assert b"failures=none" in result.stdout
        next_body = html_response(page).body
        assert next_body != first
        assert re.sub(rb"\?v=[^&]+", b"?v=old", next_body) == template
        assert page.read_bytes() == template
        first = next_body
    assert not repo_cache.exists()
    assert external_cache.read_bytes() == data_cache.read_bytes() == b"sentinel"


def test_marker_failure_is_nonzero_with_no_success(isolated_refresh):
    repo, runtime, _ = isolated_refresh
    (runtime / "tmp").mkdir(parents=True)
    (runtime / "tmp/firefly-asset-cache-version").mkdir()
    result = run_refresh(repo)
    assert result.returncode == 1
    assert b"failures=asset_version_refresh_failed" in result.stdout
    assert not list((runtime / "tmp").glob(".firefly-cache-*"))


def test_bytecode_failure_is_reported_without_losing_other_failures(isolated_refresh, monkeypatch, capsys):
    from tools import refresh_cache

    repo, runtime, _ = isolated_refresh
    cache = repo / "__pycache__"
    cache.mkdir()
    (cache / "demo.pyc").write_bytes(b"sentinel")
    runtime.mkdir()
    (runtime / "tmp").write_bytes(b"not a directory")
    original = Path.unlink

    def denied(path, *args, **kwargs):
        if path == cache / "demo.pyc":
            raise PermissionError("test bytecode is locked")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", denied)
    assert refresh_cache.refresh(repo) == 1
    assert "failures=pycache_remove_failed,asset_version_refresh_failed" in capsys.readouterr().out
