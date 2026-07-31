import json
import asyncio
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main
from firefly_projects import ProjectStore, ProjectStoreError


class FakeProcess:
    def __init__(self, returncode, stdout=b"", stderr=b""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr

    async def communicate(self):
        return self.stdout, self.stderr


class FakeUpstreamResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload, ensure_ascii=False)
        self.headers = {}

    def json(self):
        return self._payload


class FakeUpstreamClient:
    def __init__(self, response):
        self.response = response
        self.request = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, **kwargs):
        self.request = {"url": url, **kwargs}
        return self.response


class ProjectLibraryMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        self.source = self.base / "source"
        self.store = ProjectStore(self.source, snapshot_delay=0)

    def tearDown(self):
        self.store.close()
        self.temp_dir.cleanup()

    def test_copy_library_verifies_every_file_before_new_store_can_open(self):
        project = self.store.create_project("迁移项目")
        media = self.store.import_media(project["id"], b"verified-media", "source.png", "image/png")
        target = self.base / "target"

        result = self.store.copy_library_to(target)

        self.assertGreaterEqual(result["file_count"], 5)
        self.assertGreater(result["total_bytes"], 0)
        self.assertEqual(result["source"], str(self.source.resolve()))
        self.assertEqual(result["target"], str(target.resolve()))
        self.assertTrue((self.source / project["id"] / "project.json").is_file())
        migrated = ProjectStore(target, snapshot_delay=0)
        try:
            opened = migrated.open_project(project["id"])
            copied_media = migrated.media_record(project["id"], media["asset_id"])
            self.assertEqual(opened["project"]["name"], "迁移项目")
            self.assertEqual(Path(copied_media["path"]).read_bytes(), b"verified-media")
        finally:
            migrated.close()

    def test_copy_library_rejects_nonempty_target_without_touching_source(self):
        project = self.store.create_project("不能丢")
        target = self.base / "occupied"
        target.mkdir()
        (target / "foreign.txt").write_text("keep", encoding="utf-8")

        with self.assertRaisesRegex(ProjectStoreError, "目标项目库必须为空"):
            self.store.copy_library_to(target)

        self.assertEqual(self.store.open_project(project["id"])["project"]["name"], "不能丢")
        self.assertEqual((target / "foreign.txt").read_text(encoding="utf-8"), "keep")


class GlobalSettingsApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        self.previous_store = main.FIREFLY_PROJECT_STORE
        self.previous_env_file = main.API_ENV_FILE
        self.previous_provider_file = main.API_PROVIDERS_FILE
        self.previous_settings_file = main.GLOBAL_SETTINGS_FILE
        self.previous_root_env = os.environ.get("FIREFLY_PROJECTS_ROOT")
        self.store = ProjectStore(self.base / "projects", snapshot_delay=0)
        main.FIREFLY_PROJECT_STORE = self.store
        main.API_ENV_FILE = str(self.base / "API" / ".env")
        main.API_PROVIDERS_FILE = str(self.base / "providers.json")
        main.GLOBAL_SETTINGS_FILE = str(self.base / "global_settings.json")
        Path(main.API_ENV_FILE).parent.mkdir(parents=True)
        Path(main.API_ENV_FILE).write_text("API_PROVIDER_SECRET_TEST_KEY=super-secret-value\n", encoding="utf-8")
        Path(main.API_PROVIDERS_FILE).write_text(json.dumps([{
            "id": "secret-test", "name": "Secret Test", "base_url": "https://example.test/v1",
            "protocol": "openai", "image_models": ["image-one"], "chat_models": ["chat-one"],
            "video_models": ["video-one"], "enabled": True,
        }]), encoding="utf-8")
        os.environ.pop("API_PROVIDER_SECRET_TEST_KEY", None)
        for index in range(12):
            os.environ.pop(f"FIREFLY_CONCURRENT_TEST_{index}", None)
        self.client = TestClient(main.app)

    def tearDown(self):
        self.client.close()
        if main.FIREFLY_PROJECT_STORE is not self.store:
            main.FIREFLY_PROJECT_STORE.close()
        self.store.close()
        main.FIREFLY_PROJECT_STORE = self.previous_store
        main.API_ENV_FILE = self.previous_env_file
        main.API_PROVIDERS_FILE = self.previous_provider_file
        main.GLOBAL_SETTINGS_FILE = self.previous_settings_file
        os.environ.pop("API_PROVIDER_SECRET_TEST_KEY", None)
        if self.previous_root_env is None:
            os.environ.pop("FIREFLY_PROJECTS_ROOT", None)
        else:
            os.environ["FIREFLY_PROJECTS_ROOT"] = self.previous_root_env
        self.temp_dir.cleanup()

    def test_global_settings_aggregates_masked_providers_cli_and_library(self):
        with (
            patch.object(main, "codex_status", AsyncMock(return_value={"installed": True, "logged_in": None})),
            patch.object(main, "gemini_cli_status", AsyncMock(return_value={"installed": False, "logged_in": False})),
            patch.object(main, "jimeng_status", AsyncMock(return_value={"installed": True, "logged_in": True})),
        ):
            response = self.client.get("/api/global-settings")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        serialized = json.dumps(payload, ensure_ascii=False)
        provider = next(item for item in payload["providers"] if item["id"] == "secret-test")
        self.assertNotIn("super-secret-value", serialized)
        self.assertTrue(provider["has_key"])
        self.assertTrue(provider["key_preview"].endswith("alue"))
        self.assertEqual(provider["image_models"], ["image-one"])
        self.assertEqual(payload["cli"]["jimeng"]["logged_in"], True)
        self.assertEqual(payload["project_library"]["path"], str(self.store.root))
        self.assertEqual(payload["photoshop"]["connected"], False)

    def test_legacy_provider_entries_are_filtered_from_the_global_registry(self):
        Path(main.API_PROVIDERS_FILE).write_text(json.dumps([
            {"id": "modelscope", "name": "ModelScope", "protocol": "openai"},
            {"id": "runninghub", "name": "RunningHub", "protocol": "runninghub"},
            {"id": "new-provider", "name": "新服务商", "protocol": "openai"},
        ]), encoding="utf-8")

        providers = main.load_api_providers()

        self.assertEqual([item["id"] for item in providers], ["new-provider"])

    def test_unsaved_provider_fetch_returns_grouped_models_and_recommendations(self):
        upstream = FakeUpstreamClient(FakeUpstreamResponse({
            "data": [
                {"id": "chat-model"},
                {"id": "nano-banana-pro"},
                {"id": "veo-3"},
            ],
        }))
        with patch.object(main.httpx, "AsyncClient", return_value=upstream):
            response = self.client.post("/api/providers/fetch-models", json={
                "provider_id": "new-provider",
                "base_url": "https://upstream.test/v1",
                "api_key": "temporary-key",
                "protocol": "openai",
            })

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["image_models"], ["nano-banana-pro"])
        self.assertEqual(payload["chat_models"], ["chat-model"])
        self.assertEqual(payload["video_models"], ["veo-3"])
        self.assertEqual(payload["recommended_models"]["image"], "nano-banana-pro")
        self.assertEqual(upstream.request["url"], "https://upstream.test/v1/models")

    def test_recommendations_prefer_chatgpt_5_4_and_nano_banana_pro(self):
        grouped = {
            "image": ["gpt-image-2", "nano-banana-pro"],
            "chat": ["gpt-5.5", "gpt-5.4"],
            "video": ["veo-3"],
        }
        self.assertEqual(main.recommend_upstream_models(grouped), {
            "image": "nano-banana-pro",
            "chat": "gpt-5.4",
            "video": "veo-3",
        })

    def test_global_settings_survives_cli_status_failure(self):
        with (
            patch.object(main, "codex_status", AsyncMock(side_effect=RuntimeError("codex probe failed"))),
            patch.object(main, "gemini_cli_status", AsyncMock(side_effect=RuntimeError("gemini probe failed"))),
            patch.object(main, "jimeng_status", AsyncMock(side_effect=RuntimeError("jimeng probe failed"))),
        ):
            response = self.client.get("/api/global-settings")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertIn("probe failed", payload["cli"]["codex"]["message"])

    def test_provider_key_is_written_to_global_env_and_only_masked_on_response(self):
        response = self.client.put("/api/providers", json=[{
            "id": "secret-test", "name": "Secret Test", "base_url": "https://example.test/v1",
            "protocol": "openai", "image_models": ["image-two"], "chat_models": ["chat-two"],
            "video_models": [], "enabled": True, "api_key": "replacement-secret",
        }])

        self.assertEqual(response.status_code, 200, response.text)
        serialized = json.dumps(response.json(), ensure_ascii=False)
        self.assertNotIn("replacement-secret", serialized)
        self.assertIn("API_PROVIDER_SECRET_TEST_KEY=replacement-secret", Path(main.API_ENV_FILE).read_text(encoding="utf-8"))
        provider = next(item for item in response.json()["providers"] if item["id"] == "secret-test")
        self.assertEqual(provider["key_preview"], "••••••••cret")
        self.assertEqual(main.mask_secret("abc"), "••••••••")

    def test_concurrent_global_env_updates_preserve_every_key(self):
        barrier = threading.Barrier(13)
        threads = []
        for index in range(12):
            def write_key(item=index):
                barrier.wait()
                main.update_env_values({f"FIREFLY_CONCURRENT_TEST_{item}": f"value-{item}"})
            threads.append(threading.Thread(target=write_key))
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(5)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        content = Path(main.API_ENV_FILE).read_text(encoding="utf-8")
        for index in range(12):
            self.assertIn(f"FIREFLY_CONCURRENT_TEST_{index}=value-{index}", content)

    def test_nonempty_library_requires_explicit_move_confirmation(self):
        self.store.create_project("需确认")

        response = self.client.post("/api/global-settings/project-library", json={
            "path": str(self.base / "not-confirmed"), "move_existing": False,
        })

        self.assertEqual(response.status_code, 409)
        self.assertIn("显式选择移动", response.json()["detail"])
        self.assertIs(main.FIREFLY_PROJECT_STORE, self.store)

    def test_preferences_are_shared_by_the_global_settings_api(self):
        saved = self.client.patch("/api/global-settings/preferences", json={"theme": "light", "language": "en"})

        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json()["preferences"], {"theme": "light", "language": "en", "autosave_interval_minutes": 20})
        with (
            patch.object(main, "codex_status", AsyncMock(return_value={})),
            patch.object(main, "gemini_cli_status", AsyncMock(return_value={})),
            patch.object(main, "jimeng_status", AsyncMock(return_value={})),
        ):
            loaded = self.client.get("/api/global-settings")
        self.assertEqual(loaded.json()["preferences"], {"theme": "light", "language": "en", "autosave_interval_minutes": 20})

    def test_autosave_interval_is_persisted_and_validated(self):
        saved = self.client.patch("/api/global-settings/preferences", json={
            "theme": "dark", "language": "zh", "autosave_interval_minutes": 45,
        })

        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json()["preferences"]["autosave_interval_minutes"], 45)
        self.assertEqual(self.client.get("/api/global-settings/preferences").json()["preferences"]["autosave_interval_minutes"], 45)

        invalid = self.client.patch("/api/global-settings/preferences", json={
            "theme": "dark", "language": "zh", "autosave_interval_minutes": 0,
        })
        self.assertEqual(invalid.status_code, 422)

    def test_photoshop_connector_websocket_is_reported_as_connected(self):
        marker = object()
        main.manager.connection_clients[marker] = "ps_connector_test"
        try:
            status = main.photoshop_connection_status()
        finally:
            main.manager.connection_clients.pop(marker, None)
        self.assertEqual(status["connected"], True)
        self.assertEqual(status["connection_count"], 1)

    def test_explicit_migration_switches_only_after_verified_copy(self):
        project = self.store.create_project("要移动")
        target = self.base / "migrated"

        response = self.client.post("/api/global-settings/project-library", json={
            "path": str(target), "move_existing": True,
        })

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(main.FIREFLY_PROJECT_STORE.root, target.resolve())
        self.assertEqual(main.FIREFLY_PROJECT_STORE.open_project(project["id"])["project"]["name"], "要移动")
        self.assertTrue((self.store.root / project["id"] / "project.json").is_file())
        self.assertIn(f"FIREFLY_PROJECTS_ROOT={target.resolve()}", Path(main.API_ENV_FILE).read_text(encoding="utf-8"))

    def test_failed_migration_rolls_back_active_library_and_env(self):
        project = self.store.create_project("原库")
        target = self.base / "occupied"
        target.mkdir()
        (target / "foreign.txt").write_text("do not overwrite", encoding="utf-8")
        before_env = Path(main.API_ENV_FILE).read_text(encoding="utf-8")

        response = self.client.post("/api/global-settings/project-library", json={
            "path": str(target), "move_existing": True,
        })

        self.assertEqual(response.status_code, 409)
        self.assertIs(main.FIREFLY_PROJECT_STORE, self.store)
        self.assertEqual(self.store.open_project(project["id"])["project"]["name"], "原库")
        self.assertEqual(Path(main.API_ENV_FILE).read_text(encoding="utf-8"), before_env)
        self.assertEqual((target / "foreign.txt").read_text(encoding="utf-8"), "do not overwrite")

    def test_persistence_failure_removes_verified_copy_and_keeps_active_library(self):
        project = self.store.create_project("持久化失败")
        target = self.base / "copy-before-env-failure"
        target.mkdir()
        before_env = Path(main.API_ENV_FILE).read_text(encoding="utf-8")

        with patch.object(main, "update_env_values", side_effect=OSError("disk full")):
            response = self.client.post("/api/global-settings/project-library", json={
                "path": str(target), "move_existing": True,
            })

        self.assertEqual(response.status_code, 500)
        self.assertIs(main.FIREFLY_PROJECT_STORE, self.store)
        self.assertEqual(self.store.open_project(project["id"])["project"]["name"], "持久化失败")
        self.assertTrue(target.is_dir())
        self.assertEqual(list(target.iterdir()), [])
        self.assertEqual(Path(main.API_ENV_FILE).read_text(encoding="utf-8"), before_env)

    def test_migration_waits_for_inflight_project_write_and_copies_it(self):
        project = self.store.create_project("并发写入")
        entered = threading.Event()
        release = threading.Event()
        original_apply = self.store.apply_mutations
        results = {}

        def slow_apply(*args, **kwargs):
            entered.set()
            release.wait(5)
            return original_apply(*args, **kwargs)

        def write_project():
            results["write"] = main.project_store_call(
                "apply_mutations", project["id"],
                [{"op": "upsert_node", "node": {"id": "kept-during-move", "type": "prompt"}}],
                base_revision=0,
            )

        target = self.base / "after-concurrent-write"
        with patch.object(self.store, "apply_mutations", side_effect=slow_apply):
            writer = threading.Thread(target=write_project)
            writer.start()
            self.assertTrue(entered.wait(2))
            mover = threading.Thread(target=lambda: results.setdefault(
                "move", main.switch_project_library(str(target), True)
            ))
            mover.start()
            time.sleep(0.05)
            self.assertTrue(mover.is_alive())
            release.set()
            writer.join(5)
            mover.join(5)

        self.assertFalse(writer.is_alive())
        self.assertFalse(mover.is_alive())
        opened = main.FIREFLY_PROJECT_STORE.open_project(project["id"])
        self.assertEqual([node["id"] for node in opened["canvas"]["nodes"]], ["kept-during-move"])

    def test_compatibility_read_and_library_status_share_the_switch_lock(self):
        project = self.store.create_project("锁内读取")
        entered = threading.Event()
        release = threading.Event()
        original_canvas = self.store.canvas
        results = {}

        def slow_canvas(*args, **kwargs):
            entered.set()
            release.wait(5)
            return original_canvas(*args, **kwargs)

        with patch.object(self.store, "canvas", side_effect=slow_canvas):
            reader = threading.Thread(target=lambda: results.setdefault("canvas", main.load_canvas(project["id"])))
            reader.start()
            self.assertTrue(entered.wait(2))
            target = self.base / "after-compatibility-read"
            mover = threading.Thread(target=lambda: results.setdefault("move", main.switch_project_library(str(target), True)))
            mover.start()
            time.sleep(0.05)
            self.assertTrue(mover.is_alive())
            release.set()
            reader.join(5)
            mover.join(5)

        self.assertFalse(reader.is_alive())
        self.assertFalse(mover.is_alive())
        self.assertEqual(results["canvas"]["id"], project["id"])
        status = main.project_library_status()
        self.assertEqual(status["path"], str(main.FIREFLY_PROJECT_STORE.root))
        self.assertEqual(status["project_count"], 1)

    def test_cli_status_reports_login_checks_instead_of_unknown(self):
        codex_processes = [
            FakeProcess(0, b"codex-cli 1.0"),
            FakeProcess(0, b"Logged in using ChatGPT"),
        ]
        with (
            patch.object(main, "codex_cli_executable", return_value="codex"),
            patch.object(main, "gpt_image_2_skill_executable", return_value="helper"),
            patch.object(main.asyncio, "create_subprocess_exec", AsyncMock(side_effect=codex_processes)),
        ):
            codex = asyncio.run(main.codex_status())
        self.assertEqual(codex["installed"], True)
        self.assertEqual(codex["logged_in"], True)

        agy_processes = [
            FakeProcess(0, b"agy 1.0"),
            FakeProcess(0, b"auto"),
        ]
        with (
            patch.object(main, "gemini_cli_executable", return_value="agy.exe"),
            patch.object(main.asyncio, "create_subprocess_exec", AsyncMock(side_effect=agy_processes)),
        ):
            antigravity = asyncio.run(main.gemini_cli_status())
        self.assertEqual(antigravity["installed"], True)
        self.assertEqual(antigravity["logged_in"], True)


class GlobalSettingsUiContractTests(unittest.TestCase):
    def test_both_product_surfaces_open_the_same_global_settings_page(self):
        project_page = (ROOT / "static" / "project-list.html").read_text(encoding="utf-8")
        canvas_page = (ROOT / "static" / "smart-canvas.html").read_text(encoding="utf-8")
        settings_page = (ROOT / "static" / "global-settings.html").read_text(encoding="utf-8")
        settings_script = (ROOT / "static" / "js" / "global-settings.js").read_text(encoding="utf-8")

        self.assertIn('href="/static/global-settings.html"', project_page)
        self.assertIn('id="smartSettingsLink"', canvas_page)
        self.assertIn('id="smartSettingsModal"', canvas_page)
        self.assertIn('id="smartSettingsFrame"', canvas_page)
        canvas_script = (ROOT / "static" / "js" / "smart-canvas.js").read_text(encoding="utf-8")
        self.assertIn("/static/global-settings.html?embedded=1", canvas_script)
        self.assertIn("function openSmartSettings", canvas_script)
        self.assertIn("function closeSmartSettings", canvas_script)
        self.assertIn('/static/js/global-preferences.js', project_page)
        self.assertIn('/static/js/global-preferences.js', canvas_page)
        self.assertIn('/static/js/theme.js', project_page)
        self.assertIn('/static/js/i18n.js', project_page)
        self.assertIn('/static/js/i18n.js', settings_page)
        self.assertIn('data-i18n="globalSettings.heading"', settings_page)
        self.assertIn('data-i18n="projectList.newHeading"', project_page)
        self.assertIn("/api/global-settings", settings_script)
        self.assertIn("/api/providers", settings_script)
        self.assertIn("/api/providers/test-connection", settings_script)
        self.assertIn("/api/providers/fetch-models", settings_script)
        self.assertIn("data-provider-action", settings_script)
        self.assertNotIn('value="runninghub"', settings_script)
        self.assertNotIn("ModelScope", settings_script)
        self.assertIn("/api/global-settings/project-library", settings_script)
        self.assertIn("在线服务商与模型", settings_page)
        self.assertIn("CLI 状态", settings_page)
        self.assertIn("项目库", settings_page)
        self.assertIn("Photoshop", settings_page)
        self.assertIn('id="autosaveIntervalMinutes"', settings_page)
        self.assertIn('/static/js/global-preferences.js?v=', settings_page)
        self.assertIn("自动保存间隔", settings_page)
        self.assertIn("autosave_interval_minutes", settings_script)
        self.assertNotIn("comfyui", (settings_page + settings_script).lower())
        self.assertNotIn("comfyui", (project_page + canvas_page).lower())
        self.assertFalse((ROOT / "static" / "comfyui-settings.html").exists())
        self.assertIn("clean.engine = normalizeSmartOnlineEngine(clean.engine)", settings_script := (ROOT / "static" / "js" / "smart-canvas.js").read_text(encoding="utf-8"))
        self.assertIn("node.runSettings.engine = normalizeSmartOnlineEngine", settings_script)

    def test_canvas_settings_is_an_overlay_without_reloading_canvas(self):
        canvas_script = (ROOT / "static" / "js" / "smart-canvas.js").read_text(encoding="utf-8")
        canvas_page = (ROOT / "static" / "smart-canvas.html").read_text(encoding="utf-8")
        settings_script = (ROOT / "static" / "js" / "global-settings.js").read_text(encoding="utf-8")
        self.assertIn('id="smartSettingsLink"', canvas_page)
        self.assertNotIn('target="_blank"', canvas_page)
        self.assertIn('id="smartSettingsModal"', canvas_page)
        self.assertIn('id="smartSettingsFrame"', canvas_page)
        self.assertIn("function openSmartSettings", canvas_script)
        self.assertIn("function closeSmartSettings", canvas_script)
        self.assertIn("smartSettingsModal", canvas_script)
        self.assertIn("embedded", settings_script)
        self.assertIn("notifyCanvas('close-settings'", settings_script)
        self.assertIn("BroadcastChannel('studio-api')", settings_script)
        self.assertIn("notifyCanvas('preferences-changed'", settings_script)
        self.assertIn("notifyCanvas('providers-changed'", settings_script)
        self.assertIn("settings-prepare-project-library", settings_script)
        self.assertIn("settings-project-library-ready", canvas_script)
        self.assertNotIn("applySettingsToNode(node)", canvas_script)


if __name__ == "__main__":
    unittest.main()
