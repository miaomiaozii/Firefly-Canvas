import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main
from fastapi.testclient import TestClient


class LegacyHistoryDeleteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.output_dir = self.root / "output"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.history_file = self.root / "history.json"
        self.history_file.write_text("[]", encoding="utf-8")
        self.assets_dir = self.root / "assets"
        self.assets_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir = self.root / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.canvases_dir = self.data_dir / "canvases"
        self.canvases_dir.mkdir(parents=True, exist_ok=True)
        self.conversations_dir = self.data_dir / "conversations"
        self.conversations_dir.mkdir(parents=True, exist_ok=True)
        self.previews_dir = self.data_dir / "media_previews"
        self.previews_dir.mkdir(parents=True, exist_ok=True)
        self.global_config = self.root / "global_config.json"
        self.global_config.write_text("{}", encoding="utf-8")
        self.asset_library = self.data_dir / "asset_library.json"

        self.patches = [
            patch.object(main, "OUTPUT_DIR", str(self.output_dir)),
            patch.object(main, "HISTORY_FILE", str(self.history_file)),
            patch.object(main, "ASSETS_DIR", str(self.assets_dir)),
            patch.object(main, "DATA_DIR", str(self.data_dir)),
            patch.object(main, "CANVAS_DIR", str(self.canvases_dir)),
            patch.object(main, "CONVERSATION_DIR", str(self.conversations_dir)),
            patch.object(main, "MEDIA_PREVIEW_DIR", str(self.previews_dir)),
            patch.object(main, "GLOBAL_CONFIG_FILE", str(self.global_config)),
            patch.object(main, "ASSET_LIBRARY_PATH", str(self.asset_library)),
        ]
        for item in self.patches:
            item.start()

        self.client = TestClient(
            main.app,
            base_url="http://127.0.0.1",
            headers={"origin": "http://127.0.0.1"},
        )

    def tearDown(self):
        self.client.close()
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def test_delete_history_record_preserves_image_files(self):
        """Deleting a legacy history record must not delete the image files."""
        # Create an image file on disk
        image_path = self.output_dir / "test_image.png"
        image_path.write_bytes(b"fake image data")

        # Create a history record referencing the image
        history_record = {
            "id": "record-1",
            "timestamp": 1700000000.0,
            "type": "zimage",
            "prompt": "test prompt",
            "images": [f"/api/files/output/test_image.png"],
        }
        self.history_file.write_text(
            json.dumps([history_record], ensure_ascii=False, indent=4),
            encoding="utf-8",
        )

        # Call the legacy delete endpoint via HTTP
        response = self.client.post(
            "/api/history/delete",
            json={"timestamp": 1700000000.0},
        )
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertTrue(result["success"])

        # Assert the record was removed from history
        with open(self.history_file, "r", encoding="utf-8") as f:
            history = json.load(f)
        self.assertEqual(len(history), 0)

        # Assert the image file still exists on disk
        self.assertTrue(image_path.exists())
        self.assertEqual(image_path.read_bytes(), b"fake image data")

    def test_delete_history_record_removes_only_target_record(self):
        """Deleting one record should not affect other records."""
        # Create two image files
        image1_path = self.output_dir / "image1.png"
        image2_path = self.output_dir / "image2.png"
        image1_path.write_bytes(b"image 1 data")
        image2_path.write_bytes(b"image 2 data")

        # Create two history records
        record1 = {
            "id": "record-1",
            "timestamp": 1700000000.0,
            "type": "zimage",
            "prompt": "prompt 1",
            "images": ["/api/files/output/image1.png"],
        }
        record2 = {
            "id": "record-2",
            "timestamp": 1700000100.0,
            "type": "zimage",
            "prompt": "prompt 2",
            "images": ["/api/files/output/image2.png"],
        }
        self.history_file.write_text(
            json.dumps([record1, record2], ensure_ascii=False, indent=4),
            encoding="utf-8",
        )

        # Delete only the first record via HTTP
        response = self.client.post(
            "/api/history/delete",
            json={"timestamp": 1700000000.0},
        )
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertTrue(result["success"])

        # Assert the first record was removed, second remains
        with open(self.history_file, "r", encoding="utf-8") as f:
            history = json.load(f)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["id"], "record-2")

        # Assert both image files still exist
        self.assertTrue(image1_path.exists())
        self.assertTrue(image2_path.exists())


if __name__ == "__main__":
    unittest.main()
