"""Isolated browser-test server for the project entry surface."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import uvicorn

import main
from firefly_projects import ProjectStore


def run() -> None:
    project_root = os.environ.get("FIREFLY_TEST_PROJECT_ROOT", "").strip()
    if not project_root:
        raise RuntimeError("FIREFLY_TEST_PROJECT_ROOT is required")
    main.FIREFLY_PROJECT_STORE = ProjectStore(project_root, snapshot_delay=1.0)
    if os.environ.get("FIREFLY_TEST_GLOBAL_SETTINGS_FILE"):
        main.GLOBAL_SETTINGS_FILE = os.environ["FIREFLY_TEST_GLOBAL_SETTINGS_FILE"]
    if os.environ.get("FIREFLY_TEST_API_ENV_FILE"):
        main.API_ENV_FILE = os.environ["FIREFLY_TEST_API_ENV_FILE"]
    if os.environ.get("FIREFLY_TEST_PROVIDER_FILE"):
        main.API_PROVIDERS_FILE = os.environ["FIREFLY_TEST_PROVIDER_FILE"]
    if os.environ.get("FIREFLY_TEST_DISABLE_CLI_PROBES") == "1":
        async def unavailable_cli():
            return {"installed": False, "logged_in": False, "message": "隔离测试未启用 CLI 探测"}
        main.codex_status = unavailable_cli
        main.gemini_cli_status = unavailable_cli
        main.jimeng_status = unavailable_cli
    # Product startup also performs legacy asset migrations. They are unrelated
    # to this browser seam and must never run in an isolated project-entry test.
    main.app.router.on_startup.clear()
    port = int(os.environ.get("FIREFLY_TEST_PORT", "18765"))
    uvicorn.run(main.app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    run()
