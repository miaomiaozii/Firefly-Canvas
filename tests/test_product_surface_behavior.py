import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

import main
from firefly_projects import ProjectStore


class ProductSurfaceBehaviorTests(unittest.TestCase):
    """Exercise the public surface instead of inspecting implementation strings."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_store = main.FIREFLY_PROJECT_STORE
        main.FIREFLY_PROJECT_STORE = ProjectStore(
            Path(self.temp_dir.name) / "projects", snapshot_delay=0
        )
        self.client = TestClient(
            main.app,
            base_url="http://127.0.0.1",
            headers={"origin": "http://127.0.0.1"},
        )

    def tearDown(self):
        self.client.close()
        main.FIREFLY_PROJECT_STORE.close()
        main.FIREFLY_PROJECT_STORE = self.previous_store
        self.temp_dir.cleanup()

    def test_project_entrypoints_are_the_only_browser_surfaces(self):
        for path in ("/", "/static/project-list.html", "/static/smart-canvas.html", "/static/global-settings.html"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)

        removed_pages = (
            "/static/index.html",
            "/static/canvas.html",
            "/static/canvas-list.html",
            "/static/angle.html",
            "/static/enhance.html",
            "/static/gpt-chat.html",
            "/static/klein.html",
            "/static/online.html",
            "/static/zimage.html",
            "/static/asset-manager.html",
            "/static/api-settings.html",
        )
        for path in removed_pages:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)

    def test_legacy_runtime_and_update_requests_are_unavailable(self):
        removed_requests = (
            ("get", "/api/comfyui/config"),
            ("get", "/api/comfyui/instances"),
            ("get", "/api/workflows"),
            ("post", "/api/workflows/import"),
            ("get", "/api/update/check"),
            ("post", "/api/update/apply"),
            ("get", "/api/runninghub/workflows"),
        )
        for method, path in removed_requests:
            with self.subTest(method=method, path=path):
                response = getattr(self.client, method)(path)
                self.assertIn(response.status_code, {404, 405, 410})

        for path in (
            "/static/update-notes.json",
            "/static/runninghub/thumbnails/workflow-2058541134623891458.jpg",
            "/workflows/Z-Image.json",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)

    def test_app_identity_and_online_provider_contract_are_public(self):
        info = self.client.get("/api/app-info")
        self.assertEqual(info.status_code, 200)
        self.assertEqual(info.json(), {
            "name": "萤火画布",
            "version": main.current_app_version(),
            "repo_url": "https://github.com/miaomiaozii/Firefly-Canvas",
            "repository": {
                "label": "萤火画布",
                "url": "https://github.com/miaomiaozii/Firefly-Canvas",
            },
        })

        config = self.client.get("/api/config")
        self.assertEqual(config.status_code, 200)
        providers = {item["id"]: item for item in config.json()["api_providers"]}
        self.assertIn("volcengine", providers)
        self.assertNotIn("modelscope", providers)
        self.assertNotIn("runninghub", providers)

    def test_online_runninghub_app_boundary_remains_distinct_from_removed_workflows(self):
        self.assertEqual(self.client.get("/api/runninghub/app-info").status_code, 400)
        self.assertEqual(self.client.get("/api/runninghub/query").status_code, 400)
        self.assertEqual(self.client.post("/api/runninghub/submit", json={}).status_code, 400)
        self.assertEqual(self.client.post("/api/runninghub/upload-asset", json={}).status_code, 400)

    def test_project_save_reopen_keeps_one_core_canvas(self):
        created = self.client.post("/api/project-library", json={"name": "集成验收"})
        self.assertEqual(created.status_code, 200)
        project = created.json()["project"]
        changed = self.client.patch(
            f"/api/project-library/{project['id']}/canvas",
            json={
                "mutations": [
                    {"op": "upsert_node", "node": {"id": "prompt", "type": "prompt", "text": "集成提示词"}},
                    {"op": "set_viewport", "viewport": {"x": 10, "y": 20, "scale": 1}},
                ],
                "base_revision": 0,
            },
        )
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(self.client.post(f"/api/project-library/{project['id']}/flush").status_code, 200)
        reopened = self.client.get(f"/api/project-library/{project['id']}")
        self.assertEqual(reopened.status_code, 200)
        body = reopened.json()
        self.assertEqual(body["project"]["id"], project["id"])
        self.assertEqual(body["canvas"]["id"], project["canvas_id"])
        self.assertEqual(body["canvas"]["kind"], "core")
        self.assertEqual(body["canvas"]["nodes"][0]["text"], "集成提示词")


if __name__ == "__main__":
    unittest.main()
