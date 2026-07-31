"""Headless Chrome smoke acceptance for the project-list -> core-canvas flow."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import requests


ROOT = Path(__file__).resolve().parents[1]


def find_browser() -> Path:
    for candidate in (
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
    ):
        if candidate.is_file():
            return candidate
    raise RuntimeError("Chrome or Edge is required")


def wait_for_server(base: str) -> None:
    for _ in range(100):
        try:
            if requests.get(base + "/", timeout=1).status_code == 200:
                return
        except requests.RequestException:
            pass
        time.sleep(0.1)
    raise RuntimeError("isolated Firefly server did not start")


def dump_dom(browser: Path, url: str, profile: Path) -> str:
    result = subprocess.run(
        [
            str(browser), "--headless=new", "--disable-gpu", "--no-first-run",
            "--disable-extensions", "--virtual-time-budget=2500",
            f"--user-data-dir={profile}", "--dump-dom", url,
        ],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=40,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-1000:] or "Chrome dump-dom failed")
    return result.stdout.lower()


def main() -> int:
    browser = find_browser()
    port = 18767
    base = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(prefix="firefly-issue17-browser-") as root:
        env = os.environ.copy()
        env.update({
            "FIREFLY_TEST_PROJECT_ROOT": str(Path(root) / "projects"),
            "FIREFLY_TEST_PORT": str(port),
            "FIREFLY_TEST_DISABLE_CLI_PROBES": "1",
        })
        process = subprocess.Popen(
            [str(Path(os.environ.get("PYTHON", "python"))), str(ROOT / "tests" / "project_entry_server.py")],
            cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        profiles = []
        try:
            wait_for_server(base)
            project_response = requests.post(
                base + "/api/project-library", json={"name": "浏览器核心项目"},
                headers={"Origin": base}, timeout=10,
            )
            project_response.raise_for_status()
            project = project_response.json()["project"]
            project_id = project["id"]
            root_profile = Path(tempfile.mkdtemp(prefix="firefly-issue17-root-profile-")); profiles.append(root_profile)
            canvas_profile = Path(tempfile.mkdtemp(prefix="firefly-issue17-canvas-profile-")); profiles.append(canvas_profile)
            root_dom = dump_dom(browser, base + "/", root_profile)
            canvas_dom = dump_dom(
                browser,
                f"{base}/static/smart-canvas.html?id={project_id}&project={project_id}&project_mode=1",
                canvas_profile,
            )
            removed_statuses = {
                path: requests.get(base + path, timeout=10).status_code
                for path in (
                    "/static/index.html", "/static/canvas.html", "/static/canvas-list.html",
                    "/static/zimage.html", "/static/gpt-chat.html", "/static/asset-manager.html",
                )
            }
            required_root = ("你的项目", "新建项目", "项目回收站")
            required_canvas = ("浏览器核心项目", "运行", "全局设置")
            passed = (
                all(marker in root_dom for marker in required_root)
                and all(marker in canvas_dom for marker in required_canvas)
                and "<iframe" not in root_dom
                and all(status == 404 for status in removed_statuses.values())
            )
            print({"browser": str(browser), "root": required_root, "root_found": {marker: marker in root_dom for marker in required_root},
                   "canvas": required_canvas, "canvas_found": {marker: marker in canvas_dom for marker in required_canvas},
                   "iframe": "<iframe" in root_dom, "removed_statuses": removed_statuses, "pass": passed})
            return 0 if passed else 1
        finally:
            for profile in profiles:
                shutil.rmtree(profile, ignore_errors=True)
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=8)


if __name__ == "__main__":
    raise SystemExit(main())
