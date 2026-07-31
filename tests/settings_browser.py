"""Exercise the embedded global-settings surface in real headless Chrome.

The browser uses a temporary project library, runtime, provider registry, and
API env file.  Provider model requests are answered by an in-process fake so
the test never contacts an external service.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from firefly_projects import ProjectStore
from tests import conversation_browser as harness


CLIENT_SCRIPT = ROOT / "tests" / "settings_browser_client.js"
VIEWPORTS = [(1424, 805), (390, 844)]


def seed_store(store: ProjectStore) -> str:
    project = store.create_project("Settings browser project")
    store.apply_mutations(
        project["id"],
        [
            {
                "op": "upsert_node",
                "node": {
                    "id": "settings-prompt",
                    "type": "smart-prompt",
                    "x": 80,
                    "y": 120,
                    "w": 316,
                    "h": 240,
                    "text": "Temporary settings fixture",
                },
            }
        ],
        base_revision=0,
    )
    store.flush(project["id"])
    return project["id"]


class _FakeModelResponse:
    status_code = 200
    headers = {}
    text = json.dumps(
        {
            "data": [
                {"id": "chat-browser"},
                {"id": "nano-banana-pro"},
                {"id": "veo-browser"},
            ]
        }
    )

    def json(self):
        return json.loads(self.text)


class _FakeModelClient:
    def __init__(self, *args, **kwargs):
        self.requested_urls: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def get(self, url, **_kwargs):
        self.requested_urls.append(str(url))
        return _FakeModelResponse()


def load_app(root: Path, store: ProjectStore):
    app = harness.load_production_app(root, store)
    module = app.state.issue13_module

    async def cli_unavailable():
        return {"installed": False, "logged_in": False, "message": "隔离浏览器测试未启用 CLI"}

    module.codex_status = cli_unavailable
    module.gemini_cli_status = cli_unavailable
    module.jimeng_status = cli_unavailable
    module.httpx.AsyncClient = _FakeModelClient

    Path(module.API_ENV_FILE).parent.mkdir(parents=True, exist_ok=True)
    Path(module.API_ENV_FILE).write_text(
        "API_PROVIDER_SECRET_TEST_KEY=super-secret-value\n",
        encoding="utf-8",
    )
    Path(module.API_PROVIDERS_FILE).parent.mkdir(parents=True, exist_ok=True)
    Path(module.API_PROVIDERS_FILE).write_text(
        json.dumps(
            [
                {
                    "id": "secret-test",
                    "name": "Secret Browser Provider",
                    "base_url": "https://example.test/v1",
                    "protocol": "openai",
                    "image_models": ["legacy-image"],
                    "chat_models": ["legacy-chat"],
                    "video_models": ["legacy-video"],
                    "enabled": True,
                    "primary": True,
                }
            ]
        ),
        encoding="utf-8",
    )
    return app, module


def launch(browser: Path, server, viewport: tuple[int, int], project_id: str, switch_path: Path):
    import subprocess

    width, height = viewport
    profile = Path(tempfile.mkdtemp(prefix="firefly-settings-browser-profile-"))
    before = len(server.results)
    query = urllib.parse.urlencode(
        {
            "project": project_id,
            "browserTest": "1",
            "settingsProject": project_id,
            "settingsSwitchPath": str(switch_path),
        }
    )
    url = f"{server.base_url}/static/smart-canvas.html?id={urllib.parse.quote(project_id)}&{query}"
    process = subprocess.Popen(
        [
            str(browser),
            "--headless=new",
            "--disable-gpu",
            "--disable-background-networking",
            "--disable-component-update",
            "--disable-extensions",
            "--no-first-run",
            "--no-default-browser-check",
            "--hide-scrollbars",
            f"--window-size={width},{height}",
            f"--user-data-dir={profile}",
            url,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=ROOT,
    )
    try:
        return server.wait_for_result(before, timeout=90)
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        harness.remove_profile(profile)


def main():
    browser = harness.find_browser()
    harness.CLIENT_SCRIPT = CLIENT_SCRIPT
    results = []
    with tempfile.TemporaryDirectory(prefix="firefly-settings-browser-library-") as root_text:
        root = Path(root_text)
        for index, viewport in enumerate(VIEWPORTS):
            projects_root = root / f"projects-{index}"
            target_root = root / f"migrated-{index}"
            store = ProjectStore(projects_root, snapshot_delay=0)
            project_id = seed_store(store)
            app, module = load_app(root / f"runtime-{index}", store)
            server = harness.ConversationBrowserServer(store, app)
            server.start()
            try:
                result = launch(browser, server, viewport, project_id, target_root.resolve())
                result["pythonChecks"] = {
                    "newLibraryProjectCopied": (target_root / project_id / "project.json").is_file(),
                    "oldLibraryRetained": (projects_root / project_id / "project.json").is_file(),
                    "providerKeyWritten": "API_PROVIDER_SECRET_TEST_KEY=browser-key-rotated" in Path(module.API_ENV_FILE).read_text(encoding="utf-8"),
                }
                result["pass"] = result.get("pass") and all(result["pythonChecks"].values())
                if not result.get("pass"):
                    raise RuntimeError(json.dumps(result, ensure_ascii=False, indent=2))
                results.append(result)
            finally:
                server.close()
                try:
                    module.FIREFLY_PROJECT_STORE.close()
                except Exception:
                    pass
                if module.FIREFLY_PROJECT_STORE is not store:
                    store.close()
    summary = {
        "surface": "embedded-global-settings",
        "browser": str(browser),
        "viewports": VIEWPORTS,
        "runs": results,
        "pass": len(results) == len(VIEWPORTS) and all(item.get("pass") for item in results),
    }
    print(json.dumps(summary, ensure_ascii=True, indent=2))
    return 0 if summary["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
