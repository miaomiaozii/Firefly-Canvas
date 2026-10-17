"""#187 regression (endpoint): the outpaint image-task path must not bypass provider capability checks.

Outpaint submissions (after the #187 client fix) have a distinctive shape:

    size=''  width=None  height=None  aspect_ratio=<outpaintSize ratio>  resolution=<user preset>

These tests pin the server contract of POST /api/project-library/{id}/image-generation-tasks
against fixture providers with explicit capability lists:

- an outpaint ratio no model supports must STOP at registration with HTTP 400 and name
  ``aspect_ratio`` in ``incompatible_parameters`` — never fall through to a default 1K
  generation (ADR 0006: docs/adr/0006-model-routing-preserves-user-parameters.md);
- an outpaint resolution no model supports must likewise stop and name ``resolution``;
- a supported outpaint combination (21:9 + 4k) must route and forward the user's
  resolution to the provider call unchanged (``image_parameters['resolution'] == '4k'``),
  with no fabricated size/width/height.
"""

import base64
import io
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main
from firefly_projects import ProjectStore


def tiny_png_base64(color=(38, 190, 133)):
    buffer = io.BytesIO()
    Image.new("RGB", (3, 2), color).save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


class OutpaintEndpointRoutingTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store_root = Path(self.temp_dir.name) / "projects"
        self.store = ProjectStore(self.store_root, snapshot_delay=0)
        self.previous_store = main.FIREFLY_PROJECT_STORE
        main.FIREFLY_PROJECT_STORE = self.store
        self.client = TestClient(main.app, base_url="http://127.0.0.1", headers={"origin": "http://127.0.0.1"})
        self.providers = [
            {
                "id": "outpaint-fixture",
                "name": "扩图夹具服务商",
                "enabled": True,
                "protocol": "openai",
                "image_models": ["square-1k", "ultrawide-4k"],
                "image_model_capabilities": {
                    "square-1k": {"sizes": ["1024x1024"], "aspect_ratios": ["1:1"], "resolutions": ["1k"], "max_reference_images": 4},
                    "ultrawide-4k": {"sizes": ["3840x1648"], "aspect_ratios": ["21:9"], "resolutions": ["4k"], "max_reference_images": 4},
                },
            }
        ]

    def tearDown(self):
        self.client.close()
        self.store.close()
        main.FIREFLY_PROJECT_STORE = self.previous_store
        main.PROJECT_IMAGE_GENERATION_ORPHANS.clear()
        self.temp_dir.cleanup()

    def make_project_with_result_node(self, name):
        project = self.store.create_project(name)
        self.store.apply_mutations(
            project["id"],
            [{"op": "upsert_node", "node": {"id": "result-node", "type": "smart-image", "x": 420, "y": 80, "images": [], "pending": 1}}],
            base_revision=0,
        )
        return project

    def outpaint_payload(self, project, *, aspect_ratio, resolution, base_revision=1):
        # The exact wire shape runApiGeneration sends after outpaint confirmation:
        # no size/width/height, ratio from outpaintSize, resolution from the panel preset.
        return {
            "prompt": "扩图：向四周补全场景",
            "routing_mode": "auto",
            "size": "",
            "width": None,
            "height": None,
            "aspect_ratio": aspect_ratio,
            "resolution": resolution,
            "quality": "auto",
            "n": 1,
            "reference_asset_ids": [],
            "media_type": "image",
            "result_node_id": "result-node",
            "angle_preset": "none",
            "base_revision": base_revision,
        }

    def assert_rejected(self, project, *, aspect_ratio, resolution, parameter):
        task_ids_before = set(main.CANVAS_TASKS.keys())
        with patch.object(main, "load_api_providers", return_value=self.providers):
            response = self.client.post(
                f"/api/project-library/{project['id']}/image-generation-tasks",
                json=self.outpaint_payload(project, aspect_ratio=aspect_ratio, resolution=resolution),
            )
        self.assertEqual(response.status_code, 400, response.text)
        detail = response.json().get("detail") or {}
        self.assertEqual(detail.get("code"), "no_compatible_image_model")
        self.assertIn(parameter, detail.get("incompatible_parameters", []))
        # Stopped before task creation: no silent fallback generation was registered.
        self.assertEqual(set(main.CANVAS_TASKS.keys()), task_ids_before)

    def test_outpaint_unsupported_ratio_stops_and_reports_aspect_ratio(self):
        project = self.make_project_with_result_node("扩图比例拒绝")
        # 5:3 is a plausible outpaint "free" mode ratio that no fixture model supports.
        self.assert_rejected(project, aspect_ratio="5:3", resolution="4k", parameter="aspect_ratio")

    def test_outpaint_unsupported_resolution_stops_and_reports_resolution(self):
        project = self.make_project_with_result_node("扩图分辨率拒绝")
        # 21:9 is supported by ultrawide-4k, but 8k is not in any fixture model.
        self.assert_rejected(project, aspect_ratio="21:9", resolution="8k", parameter="resolution")

    def test_outpaint_supported_combo_routes_and_forwards_user_resolution(self):
        project = self.make_project_with_result_node("扩图支持组合")
        fake_generate = AsyncMock(return_value=(
            {"type": "b64", "value": tiny_png_base64(), "mime_type": "image/png"},
            {"data": [], "id": "upstream-outpaint"},
        ))
        with patch.object(main, "load_api_providers", return_value=self.providers), patch.object(main, "generate_ai_image", fake_generate):
            response = self.client.post(
                f"/api/project-library/{project['id']}/image-generation-tasks",
                json=self.outpaint_payload(project, aspect_ratio="21:9", resolution="4k"),
            )
            self.assertEqual(response.status_code, 202, response.text)
            submitted = response.json()
            self.assertEqual(submitted["route"]["model"], "ultrawide-4k")
            deadline = time.monotonic() + 3
            task = {}
            while time.monotonic() < deadline:
                task = self.client.get(f"/api/canvas-image-tasks/{submitted['task_id']}").json()
                if task["status"] in {"succeeded", "failed", "cancelled"}:
                    break
                time.sleep(0.02)
        self.assertEqual(task["status"], "succeeded", task)
        call = fake_generate.await_args
        # The provider call carries the user's resolution and the outpaint ratio,
        # with no fabricated size/width/height (size stays empty end to end).
        self.assertEqual(call.args[1], "")
        self.assertEqual(call.kwargs["image_parameters"], {
            "width": None,
            "height": None,
            "aspect_ratio": "21:9",
            "resolution": "4k",
            "media_type": "image",
        })


if __name__ == "__main__":
    unittest.main()
