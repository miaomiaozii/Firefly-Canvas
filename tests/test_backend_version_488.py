"""#488: startup identity must not follow later source or VERSION edits."""
import importlib.util
import json
import queue
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, build_opener

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]


def load_version(path):
    spec = importlib.util.spec_from_file_location("isolated_backend_version", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_startup_snapshot_and_live_source_are_distinct(tmp_path):
    source = tmp_path / "app/backend_version.py"
    source.parent.mkdir()
    shutil.copyfile(ROOT / "app/backend_version.py", source)
    shutil.copyfile(ROOT / "app/backend_source.py", source.with_name("backend_source.py"))
    code = tmp_path / "main.py"
    code.write_text("value = 'before'\n", encoding="utf-8")
    version = load_version(source)
    loaded = version.PROCESS_BACKEND_ID
    assert version.backend_version_info() == {
        "schema": 1, "loaded": loaded, "source": loaded, "restart_required": False,
    }
    code.write_text("value = 'after'\n", encoding="utf-8")
    (tmp_path / "VERSION").write_text("new release", encoding="utf-8")
    stale = version.backend_version_info()
    assert stale["loaded"] == loaded
    assert stale["source"] != loaded
    assert stale["restart_required"] is True
    restarted = load_version(source)
    assert restarted.PROCESS_BACKEND_ID == stale["source"]
    assert restarted.backend_version_info()["restart_required"] is False


def test_digest_tracks_names_additions_removals_bytes_not_static_or_data(tmp_path):
    from app.backend_version import source_digest

    code = tmp_path / "app/service.py"
    code.parent.mkdir()
    code.write_bytes(b"first")
    before = source_digest(tmp_path)
    for rel in ("static/ui.js", "app/data/private.py", "tmp/cache.py", "tests/probe.py", "tools/test.py", "VERSION"):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"ignored")
    assert source_digest(tmp_path) == before
    code.write_bytes(b"other")
    assert source_digest(tmp_path) != before
    code.write_bytes(b"first")
    code.rename(code.with_name("renamed.py"))
    assert source_digest(tmp_path) != before
    code.with_name("renamed.py").rename(code)
    extra = tmp_path / "plan_ir/new.py"
    extra.parent.mkdir()
    extra.write_bytes(b"new")
    assert source_digest(tmp_path) != before
    extra.unlink()
    assert source_digest(tmp_path) == before


def test_digest_excludes_links_junctions_and_protected_roots(tmp_path, monkeypatch):
    from app.backend_version import source_digest

    app = tmp_path / "app"
    app.mkdir()
    (app / "real.py").write_bytes(b"real")
    before = source_digest(tmp_path)
    outside = tmp_path / "private"
    outside.mkdir()
    (outside / "secret.py").write_bytes(b"private")
    junction = app / "linked"
    if sys.platform == "win32":
        result = subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(junction), str(outside)], capture_output=True, timeout=10)
        assert result.returncode == 0, result.stderr
    else:
        junction.symlink_to(outside, target_is_directory=True)
    try:
        assert source_digest(tmp_path) == before
        (outside / "secret.py").write_bytes(b"changed")
        assert source_digest(tmp_path) == before
    finally:
        if sys.platform == "win32":
            junction.rmdir()
        else:
            junction.unlink()
    protected = app / "private_runtime"
    protected.mkdir()
    (protected / "secret.py").write_bytes(b"private")
    monkeypatch.setenv("FIREFLY_RUNTIME_ROOT", str(protected))
    assert source_digest(tmp_path) == before
    monkeypatch.delenv("FIREFLY_RUNTIME_ROOT")
    monkeypatch.setenv("FIREFLY_PROJECTS_ROOT", str(protected))
    assert source_digest(tmp_path) == before


def test_digest_never_resolves_a_link_even_when_it_is_a_protected_root(tmp_path, monkeypatch):
    from app.backend_source import source_digest

    app = tmp_path / "app"
    app.mkdir()
    (app / "real.py").write_bytes(b"real")
    before = source_digest(tmp_path)
    outside = tmp_path / "private"
    outside.mkdir()
    (outside / "secret.py").write_bytes(b"private")
    link = app / "linked"
    if sys.platform == "win32":
        result = subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True, timeout=10)
        assert result.returncode == 0, result.stderr
    else:
        link.symlink_to(outside, target_is_directory=True)
    original_resolve = Path.resolve

    def deny_link_resolution(path, *args, **kwargs):
        assert path != link, "source identity must reject a link before resolving its target"
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", deny_link_resolution)
    monkeypatch.setenv("FIREFLY_RUNTIME_ROOT", str(link))
    try:
        assert source_digest(tmp_path) == before
        monkeypatch.delenv("FIREFLY_RUNTIME_ROOT")
        assert source_digest(tmp_path) == before
    finally:
        if sys.platform == "win32":
            link.rmdir()
        else:
            link.unlink()


def test_app_info_adds_backend_contract_without_private_details(monkeypatch):
    from app.routers import api_app_info
    from app import backend_version

    app = FastAPI()
    app.include_router(api_app_info.router)
    with TestClient(app) as client:
        response = client.get("/api/app-info")
        assert response.status_code == 200
        data = response.json()
        assert {"name", "version", "repo_url", "repository"} <= data.keys()
        assert data["backend"] == backend_version.backend_version_info()
        assert response.headers["cache-control"] == "no-store"
        monkeypatch.setattr(backend_version, "source_digest", lambda root: (_ for _ in ()).throw(PermissionError("private path")))
        unknown = client.get("/api/app-info").json()["backend"]
        assert unknown["source"] is None and unknown["restart_required"] is None
        assert "private path" not in str(unknown)


def test_product_pages_load_service_notice_after_localization():
    for name in ("project-list.html", "smart-canvas.html", "global-settings.html"):
        html = (ROOT / "static" / name).read_text(encoding="utf-8")
        assert html.count('/static/js/service-version.js?v=') == 1
        assert html.index('/static/js/i18n.js?v=') < html.index('/static/js/service-version.js?v=')
    styles = (ROOT / "static/css/studio-foundation.css").read_text(encoding="utf-8")
    assert ".service-version-notice" in styles
    assert (ROOT / "tools/refresh-cache.ps1").read_bytes().startswith(b"\xef\xbb\xbf")


def test_main_freezes_identity_before_importing_backend_dependencies():
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    assert source.index("from app.backend_version import PROCESS_BACKEND_ID") < source.index("from app.services.system_trust")
    assert source.index("from app.backend_version import PROCESS_BACKEND_ID") < source.index("from app.core import")


def test_real_running_python_keeps_old_route_and_identity_after_cache_refresh(tmp_path):
    """Owned tiny FastAPI service, real router/digest/PowerShell; no production lifespan."""
    repo = tmp_path / "owned checkout"
    for rel in ("app/backend_source.py", "app/backend_version.py", "app/asset_cache.py", "app/constants.py", "app/routers/api_app_info.py", "tools/refresh_cache.py", "tools/refresh-cache.ps1"):
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / rel, target)
    initial = (
        "from app.backend_version import PROCESS_BACKEND_ID\n"
        "from fastapi import FastAPI\n"
        "from app.routers.api_app_info import router\n"
        "app = FastAPI()\napp.include_router(router)\n"
        "@app.get('/behavior')\ndef behavior(): return {'value': 'v1'}\n"
    )
    (repo / "main.py").write_text(initial, encoding="utf-8")
    bootstrap = (
        "import main,socket,json,uvicorn\n"
        "sock=socket.socket(); sock.bind(('127.0.0.1',0)); sock.listen(128)\n"
        "print(json.dumps({'port':sock.getsockname()[1]}),flush=True)\n"
        "uvicorn.Server(uvicorn.Config(main.app,log_level='error',lifespan='off')).run(sockets=[sock])\n"
    )
    command = [sys.executable, "-B", "-u", "-c", bootstrap]
    powershell = shutil.which("powershell.exe")
    assert powershell, "Windows PowerShell required for real launcher exit test"
    opener = build_opener(ProxyHandler({}))

    def start():
        child = subprocess.Popen(command, cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8")
        lines = queue.Queue()
        reader = threading.Thread(target=lambda: [lines.put(line) for line in child.stdout], daemon=True)
        reader.start()
        try:
            ready = json.loads(lines.get(timeout=20))
            url = f"http://127.0.0.1:{ready['port']}"
            print(f"#488 owned uvicorn: pid={child.pid} argv={command!r} cwd={repo} url={url}")
            deadline = time.monotonic() + 15
            while True:
                try:
                    with opener.open(url + "/api/app-info", timeout=1) as response:
                        info = json.load(response)
                    return child, reader, url, info
                except (OSError, URLError):
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(.05)
        except BaseException:
            child.terminate()
            child.wait(timeout=10)
            reader.join(timeout=5)
            child.stdout.close()
            raise

    def stop(child, reader):
        child.terminate()
        child.wait(timeout=10)
        reader.join(timeout=5)
        child.stdout.close()
        assert not reader.is_alive()
        print(f"#488 owned uvicorn stopped: pid={child.pid} exit={child.returncode}")

    def refresh(url):
        return subprocess.run([powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", str(repo / "tools/refresh-cache.ps1"), "-PythonPath", sys.executable, "-ServiceUrl", url],
            cwd=repo, capture_output=True, timeout=25)

    child, reader, url, fresh = start()
    try:
        assert fresh["backend"]["loaded"] == fresh["backend"]["source"]
        assert refresh(url).returncode == 0
        (repo / "static").mkdir()
        (repo / "static/new.js").write_bytes(b"static-only")
        assert refresh(url).returncode == 0
        updated = initial.replace("'v1'", "'v2'") + "@app.get('/new-route')\ndef new_route(): return {'new': True}\n"
        (repo / "main.py").write_text(updated, encoding="utf-8")
        result = refresh(url)
        assert result.returncode == 1
        assert "后端已更新，需要重启服务" in result.stdout.decode("utf-8")
        assert "无需重启服务" not in result.stdout.decode("utf-8")
        assert child.poll() is None
        with opener.open(url + "/behavior", timeout=3) as response:
            assert json.load(response) == {"value": "v1"}
        with opener.open(url + "/api/app-info", timeout=3) as response:
            stale = json.load(response)["backend"]
        assert stale["loaded"] == fresh["backend"]["loaded"]
        assert stale["source"] != stale["loaded"] and stale["restart_required"] is True
        try:
            opener.open(url + "/new-route", timeout=3)
        except HTTPError as error:
            assert error.code == 404
        else:
            raise AssertionError("cache refresh must not hot reload Python routes")
    finally:
        stop(child, reader)
    child, reader, url, restarted = start()
    try:
        assert restarted["backend"]["loaded"] == stale["source"]
        assert restarted["backend"]["restart_required"] is False
        assert refresh(url).returncode == 0
        with opener.open(url + "/behavior", timeout=3) as response:
            assert json.load(response) == {"value": "v2"}
        with opener.open(url + "/new-route", timeout=3) as response:
            assert json.load(response) == {"new": True}
    finally:
        stop(child, reader)
