"""Tests for agent media tools API endpoints (issue #122)."""
import sys
import tempfile
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main
from firefly_projects import ProjectStore


class AgentMediaToolsTests(unittest.TestCase):
    """Test the media tools API endpoints used by the Pi agent."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="firefly-media-tools-tests-")
        self.store = ProjectStore(Path(self.temp_dir.name) / "projects", snapshot_delay=0)
        self.previous_store = main.FIREFLY_PROJECT_STORE
        main.FIREFLY_PROJECT_STORE = self.store
        self.client = TestClient(main.app, base_url="http://127.0.0.1", headers={"origin": "http://127.0.0.1"})

        # Create a test project
        self.project = self.store.create_project("Media Tools Test")
        self.project_id = self.project["id"]

        # Create a test image file
        self.test_image = Path(self.temp_dir.name) / "test-image.png"
        # Create a minimal valid PNG (1x1 red pixel)
        import struct
        import zlib

        def png_chunk(chunk_type, data):
            chunk = chunk_type + data
            crc = zlib.crc32(chunk) & 0xFFFFFFFF
            return struct.pack(">I", len(data)) + chunk + struct.pack(">I", crc)

        signature = b"\x89PNG\r\n\x1a\n"
        ihdr_data = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)  # 1x1, 8-bit, RGB
        ihdr = png_chunk(b"IHDR", ihdr_data)
        raw_row = b"\x00" + b"\xff\x00\x00"  # filter byte + red pixel
        idat_data = zlib.compress(raw_row)
        idat = png_chunk(b"IDAT", idat_data)
        iend = png_chunk(b"IEND", b"")

        with open(self.test_image, "wb") as f:
            f.write(signature + ihdr + idat + iend)

    def tearDown(self):
        self.client.close()
        self.store.close()
        main.FIREFLY_PROJECT_STORE = self.previous_store
        self.temp_dir.cleanup()

    def test_list_media_empty(self):
        """media.list returns empty list for new project."""
        response = self.client.get(
            f"/api/projects/{self.project_id}/media/list"
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("items", data)
        self.assertEqual(len(data["items"]), 0)

    def test_upload_media(self):
        """media.upload imports a file into the project."""
        response = self.client.post(
            f"/api/projects/{self.project_id}/media/upload",
            json={"file_path": str(self.test_image)},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("success", data)
        self.assertTrue(data["success"])
        self.assertIn("asset_id", data)
        self.assertIn("media", data)
        self.assertIn("asset_id", data["media"])

    def test_upload_media_file_not_found(self):
        """media.upload returns 404 for missing file."""
        response = self.client.post(
            f"/api/projects/{self.project_id}/media/upload",
            json={"file_path": "/nonexistent/file.png"},
        )
        self.assertEqual(response.status_code, 404)
        self.assertIn("File not found", response.json()["detail"])

    def test_upload_media_missing_file_path(self):
        """media.upload returns 400 when file_path is missing."""
        response = self.client.post(
            f"/api/projects/{self.project_id}/media/upload",
            json={},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("file_path", response.json()["detail"].lower())

    def test_list_media_after_upload(self):
        """media.list shows uploaded media."""
        # Upload a file
        self.client.post(
            f"/api/projects/{self.project_id}/media/upload",
            json={"file_path": str(self.test_image)},
        )

        # List media
        response = self.client.get(
            f"/api/projects/{self.project_id}/media/list"
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data["items"]), 1)
        self.assertEqual(data["items"][0]["name"], "test-image.png")

    def test_project_not_found(self):
        """Media tools return 404 for missing project."""
        response = self.client.get(
            "/api/projects/nonexistent/media/list"
        )
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
