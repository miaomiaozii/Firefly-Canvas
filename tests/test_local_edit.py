import asyncio
import base64
import io
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main
from firefly_projects import ProjectStore
from local_edit import composite_preserve, composite_rect, encode_png, rasterize_preserve_mask


def png(image):
    return encode_png(image)


class LocalEditPixelTests(unittest.TestCase):
    def test_rect_rgba_contract_changes_only_half_open_rectangle(self):
        original = Image.new("RGBA", (16, 12), (10, 20, 30, 40))
        model = Image.new("RGBA", (3, 2), (200, 100, 50, 7))
        output = composite_rect(original, model, (4, 3, 11, 8), 3)
        self.assertEqual(output.size, original.size)
        for y in range(12):
            for x in range(16):
                if 4 <= x < 11 and 3 <= y < 8:
                    self.assertNotEqual(output.getpixel((x, y)), original.getpixel((x, y)))
                else:
                    self.assertEqual(output.getpixel((x, y)), original.getpixel((x, y)))

    def test_free_shapes_rasterize_to_255_restore_and_keep_alpha(self):
        original = Image.new("RGBA", (20, 16), (1, 2, 3, 64))
        model = Image.new("RGBA", (20, 16), (90, 80, 70, 0))
        mask = rasterize_preserve_mask({"type": "curve", "points": [[3, 3], [10, 8], [16, 4]], "width": 3}, original.size)
        self.assertEqual(mask.mode, "L")
        output = composite_preserve(original, model, mask, 0)
        self.assertEqual(output.getpixel((3, 3)), original.getpixel((3, 3)))
        self.assertEqual(output.getpixel((0, 0)), model.getpixel((0, 0)))
        self.assertEqual(output.getpixel((3, 3))[3], 64)

    def test_hand_drawn_lasso_and_curve_masks_all_restore_original_pixels(self):
        original = Image.new("RGBA", (24, 18), (11, 22, 33, 44))
        model = Image.new("RGBA", original.size, (201, 101, 51, 7))
        selections = [
            {"type": "freehand", "points": [[2, 2], [8, 8], [14, 3]], "width": 4},
            {"type": "polygon", "points": [[4, 4], [18, 4], [18, 14], [4, 14]]},
            {"type": "curve", "points": [[3, 14], [10, 5], [20, 12]], "width": 3},
        ]
        for selection in selections:
            mask = rasterize_preserve_mask(selection, original.size)
            self.assertGreater(max(mask.getdata()), 0)
            output = composite_preserve(original, model, mask)
            preserved = next((point for point in selection["points"] if 0 <= point[0] < 24 and 0 <= point[1] < 18), None)
            self.assertEqual(output.getpixel(tuple(preserved)), original.getpixel(tuple(preserved)))


class LocalEditApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = ProjectStore(Path(self.temp.name) / "projects", snapshot_delay=0)
        self.previous = main.FIREFLY_PROJECT_STORE
        main.FIREFLY_PROJECT_STORE = self.store
        self.client = TestClient(main.app, base_url="http://127.0.0.1", headers={"origin": "http://127.0.0.1"})
        self.project = self.store.create_project("局部编辑")
        self.source = self.store.import_media(self.project["id"], png(Image.new("RGBA", (12, 8), (10, 20, 30, 40))), "source.png", "image/png")
        self.reference = self.store.import_media(self.project["id"], png(Image.new("RGBA", (4, 4), (1, 2, 3, 4))), "reference.png", "image/png")
        self.store.apply_mutations(self.project["id"], [
            {"op": "upsert_node", "node": {"id": "source-node", "type": "smart-image", "images": [{"asset_id": self.source["asset_id"]}]}},
            {"op": "upsert_node", "node": {"id": "reference-node", "type": "smart-image", "images": [{"asset_id": self.reference["asset_id"]}]}},
            {"op": "upsert_node", "node": {
                "id": "local-1", "type": "local-edit", "x": 417, "y": 263, "w": 492, "h": 381,
                "title": "我的局部编辑", "localEditInputRoles": {
                    "originalNode": "source-node", "referenceNodes": ["reference-node"], "promptNode": "prompt-node"
                }, "localEditActiveRole": "references", "selectionType": "curve", "aspect_ratio": "4:3",
                "images": []
            }},
            {"op": "upsert_edge", "edge": {"from": "source-node", "to": "local-1", "kind": "input", "role": "original"}},
            {"op": "upsert_edge", "edge": {"from": "reference-node", "to": "local-1", "kind": "input", "role": "references"}},
        ], base_revision=0)
        self.store.flush(self.project["id"])

    def tearDown(self):
        self.client.close()
        self.store.close()
        main.FIREFLY_PROJECT_STORE = self.previous
        self.temp.cleanup()

    def test_modify_adapter_gets_crop_and_persisted_node_preserves_source(self):
        source_before = Path(self.store.media_record(self.project["id"], self.source["asset_id"])["path"]).read_bytes()
        seen = {}
        def fake(request):
            seen.update(request)
            return png(Image.new("RGBA", (7, 5), (200, 100, 50, 7)))
        with patch.object(main, "local_edit_adapter", fake):
            response = self.client.post(f"/api/project-library/{self.project['id']}/local-edit", json={
                "original_asset_id": self.source["asset_id"], "reference_asset_ids": [self.reference["asset_id"]],
                "prompt": "replace", "mode": "modify", "selection": [2, 1, 9, 6],
                "feather_radius": 0, "size": "2048x2048", "resolution": "2k",
                "result_node_id": "local-1", "base_revision": 1,
            })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(Image.open(io.BytesIO(seen["image"])).size, (7, 5))
        self.assertNotIn("mask", seen)
        self.assertEqual(seen["size"], "2048x2048")
        self.assertEqual(seen["resolution"], "2k")
        canvas = self.store.open_project(self.project["id"])["canvas"]
        node = next(item for item in canvas["nodes"] if item["id"] == "local-1")
        # The API updates output fields but must preserve the user's canvas
        # placement and local-edit UI state.
        self.assertEqual({key: node.get(key) for key in ("x", "y", "w", "h", "title", "localEditInputRoles", "localEditActiveRole", "selectionType", "aspect_ratio")}, {
            "x": 417, "y": 263, "w": 492, "h": 381, "title": "我的局部编辑",
            "localEditInputRoles": {"originalNode": "source-node", "referenceNodes": ["reference-node"], "promptNode": "prompt-node"},
            "localEditActiveRole": "references", "selectionType": "curve", "aspect_ratio": "4:3",
        })
        self.assertEqual(node["inputs"]["original"]["asset_id"], self.source["asset_id"])
        self.assertEqual(node["reference_asset_ids"], [self.reference["asset_id"]])
        self.assertEqual(
            {(edge["from"], edge["to"], edge.get("role")) for edge in canvas["connections"] if edge.get("to") == "local-1"},
            {("source-node", "local-1", "original"), ("reference-node", "local-1", "references")},
        )
        self.assertEqual(len(self.store.list_media(self.project["id"])), 6)
        self.assertEqual(Path(self.store.media_record(self.project["id"], self.source["asset_id"])["path"]).read_bytes(), source_before)
        self.assertTrue(any(item.get("type") == "local-edit" and item.get("output_asset_id") for item in self.store.open_project(self.project["id"])["canvas"]["history"]))

    def test_history_keeps_existing_logs_and_records_selection_contract(self):
        old_log = {"id": "old-log", "type": "prior-operation", "status": "succeeded"}
        revision = self.store.open_project(self.project["id"])["canvas"]["revision"]
        self.store.apply_mutations(
            self.project["id"], [{"op": "set_canvas_meta", "history": [old_log]}], base_revision=revision
        )
        revision = self.store.open_project(self.project["id"])["canvas"]["revision"]
        with patch.object(main, "local_edit_adapter", lambda request: png(Image.new("RGBA", (7, 5), (200, 100, 50, 7)))):
            response = self.client.post(f"/api/project-library/{self.project['id']}/local-edit", json={
                "original_asset_id": self.source["asset_id"], "reference_asset_ids": [],
                "prompt": "explicit prompt", "mode": "modify", "selection": [1, 1, 9, 7],
                "aspect_ratio": "4:3", "feather_radius": 2, "result_node_id": "local-1", "base_revision": revision,
            })
        self.assertEqual(response.status_code, 200, response.text)
        logs = self.store.open_project(self.project["id"])["canvas"]["history"]
        self.assertIn(old_log, logs)
        local_log = next(item for item in logs if item.get("type") == "local-edit")
        self.assertEqual(local_log["selection"], [1, 1, 9, 7])
        self.assertEqual(local_log["aspect_ratio"], "4:3")

    def test_multiple_prompt_edges_are_composed_in_order(self):
        revision = self.store.open_project(self.project["id"])["canvas"]["revision"]
        self.store.apply_mutations(self.project["id"], [
            {"op": "upsert_node", "node": {"id": "prompt-a", "type": "smart-prompt", "text": "first instruction"}},
            {"op": "upsert_node", "node": {"id": "prompt-b", "type": "smart-prompt", "text": "second instruction"}},
            {"op": "upsert_edge", "edge": {"from": "prompt-a", "to": "local-1", "kind": "input", "role": "prompt"}},
            {"op": "upsert_edge", "edge": {"from": "prompt-b", "to": "local-1", "kind": "input", "role": "prompt"}},
        ], base_revision=revision)
        revision = self.store.open_project(self.project["id"])["canvas"]["revision"]
        seen = {}
        def fake(request):
            seen.update(request)
            return png(Image.new("RGBA", (7, 5), (200, 100, 50, 7)))
        with patch.object(main, "local_edit_adapter", fake):
            response = self.client.post(f"/api/project-library/{self.project['id']}/local-edit", json={
                "original_asset_id": self.source["asset_id"], "reference_asset_ids": [],
                "prompt": "first instruction\n\nsecond instruction", "mode": "modify",
                "selection": [1, 1, 9, 7], "result_node_id": "local-1", "base_revision": revision,
            })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(seen["prompt"], "first instruction\n\nsecond instruction")
        node = next(item for item in self.store.open_project(self.project["id"])["canvas"]["nodes"] if item["id"] == "local-1")
        self.assertEqual(node["inputs"]["prompt"], "first instruction\n\nsecond instruction")

    def test_modify_selection_is_persisted_as_canonical_half_open_rect(self):
        revision = self.store.open_project(self.project["id"])["canvas"]["revision"]
        with patch.object(main, "local_edit_adapter", lambda request: png(Image.new("RGBA", (7, 5), (200, 100, 50, 7)))):
            response = self.client.post(f"/api/project-library/{self.project['id']}/local-edit", json={
                "original_asset_id": self.source["asset_id"], "reference_asset_ids": [],
                "prompt": "canonical", "mode": "modify",
                "selection": {"rect": [1.2, 1.4, 9.6, 7.1]}, "result_node_id": "local-1", "base_revision": revision,
            })
        self.assertEqual(response.status_code, 200, response.text)
        node = next(item for item in self.store.open_project(self.project["id"])["canvas"]["nodes"] if item["id"] == "local-1")
        self.assertEqual(node["selection"], [1, 1, 10, 7])
        local_log = next(item for item in self.store.open_project(self.project["id"])["canvas"]["history"] if item.get("type") == "local-edit")
        self.assertEqual(local_log["selection"], [1, 1, 10, 7])

    def test_original_role_must_contain_exactly_one_image_resource(self):
        second = self.store.import_media(self.project["id"], png(Image.new("RGBA", (3, 3), (8, 9, 10, 11))), "second.png", "image/png")
        revision = self.store.open_project(self.project["id"])["canvas"]["revision"]
        self.store.apply_mutations(self.project["id"], [{"op": "upsert_node", "node": {
            "id": "source-node", "type": "smart-image", "images": [
                {"asset_id": self.source["asset_id"]}, {"asset_id": second["asset_id"]}
            ]
        }}], base_revision=revision)
        revision = self.store.open_project(self.project["id"])["canvas"]["revision"]
        response = self.client.post(f"/api/project-library/{self.project['id']}/local-edit", json={
            "original_asset_id": self.source["asset_id"], "reference_asset_ids": [], "prompt": "explicit prompt",
            "mode": "modify", "selection": [2, 1, 9, 6], "result_node_id": "local-1", "base_revision": revision,
        })
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("恰好一个图片资源", response.text)

    def test_retain_adapter_gets_full_source_and_mask_is_never_uploaded(self):
        seen = {}
        def fake(request):
            seen.update(request)
            return png(Image.new("RGBA", (12, 8), (200, 100, 50, 7)))
        selection = {"type": "polygon", "points": [[2, 2], [9, 2], [9, 6], [2, 6]]}
        with patch.object(main, "local_edit_adapter", fake):
            response = self.client.post(f"/api/project-library/{self.project['id']}/local-edit", json={
                "original_asset_id": self.source["asset_id"], "reference_asset_ids": [], "prompt": "retain",
                "mode": "retain", "selection": selection, "feather_radius": 0,
                "result_node_id": "local-1", "base_revision": 1,
            })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(Image.open(io.BytesIO(seen["image"])).size, (12, 8))
        self.assertNotIn("mask", seen)
        node = next(item for item in self.store.open_project(self.project["id"])["canvas"]["nodes"] if item["id"] == "local-1")
        self.assertTrue(node["mask_asset_id"])
        self.assertEqual(node["original_asset_id"], self.source["asset_id"])
        # With no prompt edge, the explicit prompt field is accepted and persisted.
        self.assertEqual(node["inputs"]["prompt"], "retain")

    def test_online_adapter_decodes_existing_bare_base64_image_protocol(self):
        expected = png(Image.new("RGBA", (5, 3), (90, 80, 70, 255)))
        seen = {}

        async def fake_agent(request):
            seen.update(request)
            return {"images": [{
                "type": "b64",
                "value": base64.b64encode(expected).decode("ascii"),
                "mime_type": "image/png",
            }]}

        with patch.object(main, "route_image_generation", return_value=SimpleNamespace(provider_id="custom-api", model="image-model")), \
             patch.object(main, "load_api_providers", return_value=[]), \
             patch.object(main, "project_agent_default_image_adapter", fake_agent):
            actual = asyncio.run(main.project_local_edit_online_adapter({
                "project_id": "project-1", "input_asset_id": "asset-1",
                "reference_asset_ids": ["asset-1"], "prompt": "online",
                "size": "2048x2048", "resolution": "2k", "aspect_ratio": "4:3",
                "image": png(Image.new("RGBA", (2, 2), (1, 2, 3, 255))),
            }))

        self.assertEqual(actual, expected)
        self.assertEqual(seen["params"]["size"], "2048x2048")
        self.assertEqual(seen["params"]["resolution"], "2k")
        self.assertEqual(seen["params"]["provider_id"], "custom-api")


if __name__ == "__main__":
    unittest.main()
