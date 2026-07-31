"""Real Chrome smoke check for the standalone PS connector shell."""
from __future__ import annotations

import http.server
import subprocess
import tempfile
import threading
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONNECTOR = ROOT / "tools" / "photoshop-asset-connector"


def find_browser() -> Path:
    for candidate in (
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
    ):
        if candidate.is_file():
            return candidate
    raise RuntimeError("Chrome or Edge is required")


def main() -> int:
    browser = find_browser()
    handler = lambda *args, **kwargs: http.server.SimpleHTTPRequestHandler(*args, directory=str(CONNECTOR), **kwargs)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    profile = Path(tempfile.mkdtemp(prefix="firefly-issue16-connector-browser-"))
    try:
        url = f"http://127.0.0.1:{server.server_port}/index.html"
        result = subprocess.run(
            [str(browser), "--headless=new", "--disable-gpu", "--no-first-run", "--disable-extensions",
             f"--user-data-dir={profile}", "--dump-dom", url],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
        )
        dom = result.stdout.lower()
        required = ("ps 连接器", "发送到 photoshop", "从 photoshop 返回", "当前选区", "刷新状态")
        passed = result.returncode == 0 and all(item in dom for item in required)
        print({"browser": str(browser), "returncode": result.returncode, "required": required, "pass": passed})
        return 0 if passed else 1
    finally:
        server.shutdown(); server.server_close()
        import shutil
        shutil.rmtree(profile, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
