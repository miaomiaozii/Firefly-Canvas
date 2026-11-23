"""#458：用独立目录验证 PowerShell 的真实退出码和不停机的缓存刷新。"""
import json
import os
import re
import shutil
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import subprocess
import sys
from pathlib import Path

import pytest

from app.static_pages import html_response
from app.backend_source import source_digest

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
    for rel in ("app/asset_cache.py", "app/backend_source.py", "app/backend_version.py", "tools/refresh_cache.py", "tools/refresh-cache.ps1", "刷新缓存.bat"):
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / rel, target)
    state = {"loaded": source_digest(repo), "status": 200}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            assert self.path == "/api/app-info"
            current = source_digest(repo)
            payload = state.get("payload", {"backend": {
                "schema": 1, "loaded": state["loaded"], "source": current,
                "restart_required": state["loaded"] != current,
            }})
            self.send_response(state["status"])
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode("utf-8"))

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    state["url"] = f"http://127.0.0.1:{server.server_port}"
    print(f"#488 owned fixture: pid={os.getpid()} url={state['url']} repo={repo}")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield repo, runtime, projects, state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()
        print(f"#488 owned fixture closed: {state['url']}")


def run_refresh(repo, service_url):
    if not POWERSHELL:
        pytest.skip("需要 Windows PowerShell")
    return subprocess.run(
        [POWERSHELL, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(repo / "tools/refresh-cache.ps1"), "-PythonPath", sys.executable,
         "-ServiceUrl", service_url],
        cwd=repo.parent, capture_output=True, timeout=25,
    )


def test_refresh_changes_next_html_response_without_restarting_or_writing_html(isolated_refresh):
    repo, _, projects, service = isolated_refresh
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
        result = run_refresh(repo, service["url"])
        assert result.returncode == 0, (result.stdout, result.stderr)
        assert b"failures=none" in result.stdout
        assert "无需重启服务" in result.stdout.decode("utf-8")
        next_body = html_response(page).body
        assert next_body != first
        assert re.sub(rb"\?v=[^&]+", b"?v=old", next_body) == template
        assert page.read_bytes() == template
        first = next_body
    assert not repo_cache.exists()
    assert external_cache.read_bytes() == data_cache.read_bytes() == b"sentinel"


def test_marker_failure_is_nonzero_with_no_success(isolated_refresh):
    repo, runtime, _, service = isolated_refresh
    (runtime / "tmp").mkdir(parents=True)
    (runtime / "tmp/firefly-asset-cache-version").mkdir()
    result = run_refresh(repo, service["url"])
    assert result.returncode == 1
    assert b"failures=asset_version_refresh_failed" in result.stdout
    assert "无需重启服务" not in result.stdout.decode("utf-8")
    assert not list((runtime / "tmp").glob(".firefly-cache-*"))


def test_bytecode_failure_is_reported_without_losing_other_failures(isolated_refresh, monkeypatch, capsys):
    from tools import refresh_cache

    repo, runtime, _, service = isolated_refresh
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
    assert refresh_cache.refresh(repo, service["url"]) == 1
    assert "failures=pycache_remove_failed,asset_version_refresh_failed" in capsys.readouterr().out


@pytest.mark.parametrize("legacy", ["absent", "unversioned", "stale", "other-checkout"])
def test_backend_requires_manual_restart_without_false_success(isolated_refresh, legacy):
    repo, runtime, _, service = isolated_refresh
    if legacy == "absent":
        service.update(status=404, payload={"detail": "Not Found"})
    elif legacy == "unversioned":
        service["payload"] = {"name": "萤火画布", "version": "old"}
    elif legacy == "other-checkout":
        service["payload"] = {"backend": {"schema": 1, "loaded": "sha256:" + "f" * 64,
            "source": "sha256:" + "f" * 64, "restart_required": False}}
    else:
        (repo / "app/new_route.py").write_text("route = 'new'", encoding="utf-8")
    result = run_refresh(repo, service["url"])
    stdout = result.stdout.decode("utf-8")
    assert result.returncode == 1, stdout
    assert "backend_restart_required" in stdout
    assert "后端已更新，需要重启服务" in stdout
    assert "重启服务" in stdout and "run.bat" in stdout
    assert "无需重启服务" not in stdout
    assert (runtime / "tmp/firefly-asset-cache-version").is_file()
    if legacy == "stale":
        assert service["loaded"] != source_digest(repo)


@pytest.mark.parametrize("status,payload", [(503, {}), (200, []), (200, {"backend": {"schema": 1}})])
def test_unknown_backend_check_is_nonzero_not_proven_mismatch(isolated_refresh, status, payload):
    repo, _, _, service = isolated_refresh
    service.update(status=status, payload=payload)
    result = run_refresh(repo, service["url"])
    stdout = result.stdout.decode("utf-8")
    assert result.returncode == 1, stdout
    assert "backend_check_failed" in stdout
    assert "无法确认" in stdout
    assert "后端已更新，需要重启服务" not in stdout
    assert "无需重启服务" not in stdout


def test_unreachable_backend_is_nonzero_without_claiming_mismatch(isolated_refresh, monkeypatch, capsys):
    from tools import refresh_cache
    from urllib.error import URLError

    repo, _, _, service = isolated_refresh
    def unreachable(*_args, **_kwargs):
        raise URLError("controlled unreachable endpoint")
    monkeypatch.setattr(refresh_cache, "_read_app_info", unreachable)
    assert refresh_cache.refresh(repo, service["url"]) == 1
    stdout = capsys.readouterr().out
    assert "backend_check_failed" in stdout
    assert "后端已更新，需要重启服务" not in stdout


def test_static_only_refresh_and_all_failures_remain_independent(isolated_refresh, capsys):
    from tools import refresh_cache

    repo, runtime, _, service = isolated_refresh
    (repo / "static").mkdir()
    (repo / "static/new.js").write_bytes(b"new static source")
    assert refresh_cache.refresh(repo, service["url"]) == 0
    (repo / "app/new.py").write_bytes(b"new backend source")
    (runtime / "tmp/firefly-asset-cache-version").unlink()
    (runtime / "tmp/firefly-asset-cache-version").mkdir()
    assert refresh_cache.refresh(repo, service["url"]) == 1
    stdout = capsys.readouterr().out
    assert "failures=asset_version_refresh_failed,backend_restart_required" in stdout


def test_source_read_error_is_unverified_and_preserves_other_failures(isolated_refresh, monkeypatch, capsys):
    from tools import refresh_cache

    repo, runtime, _, service = isolated_refresh
    (runtime / "tmp").mkdir(parents=True)
    (runtime / "tmp/firefly-asset-cache-version").mkdir()

    def unreadable(_root):
        raise PermissionError("private source path")

    monkeypatch.setattr(refresh_cache, "source_digest", unreadable)
    assert refresh_cache.refresh(repo, service["url"]) == 1
    stdout = capsys.readouterr().out
    assert "failures=asset_version_refresh_failed,backend_check_failed" in stdout
    assert "无法确认" in stdout
    assert "private source path" not in stdout
    assert "后端已更新，需要重启服务" not in stdout


def test_refresh_never_follows_junction_or_embedded_project_root(isolated_refresh, monkeypatch):
    repo, _, projects, service = isolated_refresh
    external = projects / "__pycache__/valuable.pyc"
    external.parent.mkdir(parents=True)
    external.write_bytes(b"preserve external bytecode")
    linked = repo / "app/__pycache__"
    embedded = repo / "tools/private_projects/__pycache__/valuable.pyc"
    embedded.parent.mkdir(parents=True)
    embedded.write_bytes(b"preserve project bytecode")
    monkeypatch.setenv("FIREFLY_PROJECTS_ROOT", str(embedded.parents[1]))
    if sys.platform == "win32":
        result = subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(linked), str(external.parent)], capture_output=True, timeout=10)
        assert result.returncode == 0, result.stderr
    else:
        linked.symlink_to(external.parent, target_is_directory=True)
    try:
        result = run_refresh(repo, service["url"])
        assert result.returncode == 0, (result.stdout, result.stderr)
        assert external.read_bytes() == b"preserve external bytecode"
        assert embedded.read_bytes() == b"preserve project bytecode"
    finally:
        if sys.platform == "win32":
            linked.rmdir()
        else:
            linked.unlink()
