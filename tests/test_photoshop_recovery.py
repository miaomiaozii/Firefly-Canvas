"""issue #95：Photoshop round-trip 请求表持久化 + 启动对账。

覆盖：
- create / return 状态变化落盘（内存与磁盘一致）
- 重启（清空内存）后：未过期 pending 可继续 return；
  超龄 pending → expired；processing 崩溃对账（已提交→returned / 未提交→pending）；
  超保留期的终态被丢弃。
"""
import base64
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_import_root = None
if not os.environ.get("FIREFLY_PROJECTS_ROOT"):
    _import_root = Path(tempfile.mkdtemp(prefix="firefly-ps95-"))
    os.environ["FIREFLY_PROJECTS_ROOT"] = str(_import_root)
    import atexit
    atexit.register(lambda: shutil.rmtree(_import_root, ignore_errors=True))

from fastapi.testclient import TestClient
from PIL import Image

import main
from firefly_projects import ProjectStore


def png(color=(20, 30, 40, 255), size=(8, 6)):
    image = Image.new("RGBA", size, color)
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


class PhotoshopRoundTripRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = ProjectStore(Path(self.temp.name) / "projects", snapshot_delay=0)
        self.previous_store = main.FIREFLY_PROJECT_STORE
        self.previous_requests = dict(main.PHOTOSHOP_ROUND_TRIP_REQUESTS)
        main.FIREFLY_PROJECT_STORE = self.store
        main.PHOTOSHOP_ROUND_TRIP_REQUESTS.clear()
        self.client = TestClient(main.app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000))
        self.project = self.store.create_project("PS 对账")
        self.media = self.store.import_media(self.project["id"], png(), "source.png", "image/png")
        self.store.apply_mutations(self.project["id"], [{
            "op": "upsert_node",
            "node": {
                "id": "source-node", "type": "smart-image", "x": 0, "y": 0,
                "images": [{"asset_id": self.media["asset_id"], "kind": "image"}],
            },
        }], base_revision=0)
        self.store.flush(self.project["id"])

    def tearDown(self):
        main.PHOTOSHOP_ROUND_TRIP_REQUESTS.clear()
        main.PHOTOSHOP_ROUND_TRIP_REQUESTS.update(self.previous_requests)
        main.FIREFLY_PROJECT_STORE = self.previous_store
        self.client.close()
        self.store.close()
        self.temp.cleanup()

    @property
    def table_path(self):
        return self.store.root / self.project["id"] / "photoshop-round-trips.json"

    def read_table(self):
        if not self.table_path.exists():
            return {}
        return json.loads(self.table_path.read_text(encoding="utf-8"))["requests"]

    def create_request(self):
        res = self.client.post(
            f"/api/project-library/{self.project['id']}/photoshop/round-trips",
            json={"source_node_id": "source-node", "source_media_id": self.media["asset_id"], "ttl_seconds": 3600},
        )
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    def post_return(self, request_id, content):
        return self.client.post(
            f"/api/project-library/{self.project['id']}/photoshop/round-trips/{request_id}/return",
            json={
                "project_id": self.project["id"],
                "request_id": request_id,
                "source_node_id": "source-node",
                "name": "edited.png",
                "mime_type": "image/png",
                "sha256": hashlib.sha256(content).hexdigest(),
                "bytes_base64": base64.b64encode(content).decode("ascii"),
            },
        )

    def test_create_persists_table_to_disk(self):
        record = self.create_request()
        table = self.read_table()
        self.assertEqual(table[record["request_id"]]["state"], "pending")
        self.assertEqual(table[record["request_id"]]["project_id"], self.project["id"])

    def test_return_persists_returned_state(self):
        record = self.create_request()
        res = self.post_return(record["request_id"], png(color=(255, 0, 0, 255)))
        self.assertEqual(res.status_code, 200, res.text)
        table = self.read_table()
        self.assertEqual(table[record["request_id"]]["state"], "returned")
        self.assertEqual(table[record["request_id"]]["result_node_id"], res.json()["result_node"]["id"])

    def test_restart_keeps_pending_returnable(self):
        record = self.create_request()
        # 模拟服务重启：内存清空，走启动对账。
        main.PHOTOSHOP_ROUND_TRIP_REQUESTS.clear()
        main._photoshop_recover_round_trips()
        restored = main.PHOTOSHOP_ROUND_TRIP_REQUESTS.get(record["request_id"])
        self.assertIsNotNone(restored)
        self.assertEqual(restored["state"], "pending")
        res = self.post_return(record["request_id"], png(color=(0, 255, 0, 255)))
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(self.read_table()[record["request_id"]]["state"], "returned")

    def test_restart_expires_stale_pending(self):
        now = main.now_ms()
        self.store.save_photoshop_round_trips(self.project["id"], {
            "stale-req": {
                "request_id": "stale-req", "project_id": self.project["id"],
                "source_node_id": "source-node", "source_media_id": self.media["asset_id"],
                "created_at": (now - 7200_000) // 1000,
                "expires_at": (now - 3600_000) // 1000,
                "expires_at_ms": now - 3600_000,
                "state": "pending",
            },
        })
        main._photoshop_recover_round_trips()
        restored = main.PHOTOSHOP_ROUND_TRIP_REQUESTS.get("stale-req")
        self.assertEqual(restored["state"], "expired")
        self.assertEqual(self.read_table()["stale-req"]["state"], "expired")
        res = self.client.get(f"/api/project-library/{self.project['id']}/photoshop/round-trips/stale-req")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["state"], "expired")
        res = self.post_return("stale-req", png())
        self.assertEqual(res.status_code, 410, res.text)

    def test_restart_processing_with_committed_node_becomes_returned(self):
        request_id = "crashed-committed"
        result_media = self.store.import_media(self.project["id"], png(color=(1, 2, 3, 255)), "crash-result.png", "image/png")
        revision = self.store.open_project(self.project["id"])["canvas"]["revision"]
        self.store.apply_mutations(self.project["id"], [{
            "op": "upsert_node",
            "node": {
                "id": "ps-edit-crash", "type": "ps_edit_result", "x": 400, "y": 0, "w": 100, "h": 100,
                "source_node_id": "source-node", "source_media_id": self.media["asset_id"],
                "request_id": request_id,
                "images": [{"asset_id": result_media["asset_id"], "kind": "image", "name": "crash-result.png"}],
            },
        }], base_revision=revision)
        self.store.flush(self.project["id"])
        now = main.now_ms()
        self.store.save_photoshop_round_trips(self.project["id"], {
            request_id: {
                "request_id": request_id, "project_id": self.project["id"],
                "source_node_id": "source-node", "source_media_id": self.media["asset_id"],
                "created_at": now // 1000, "expires_at": (now + 3600_000) // 1000,
                "expires_at_ms": now + 3600_000, "state": "processing",
            },
        })
        main._photoshop_recover_round_trips()
        restored = main.PHOTOSHOP_ROUND_TRIP_REQUESTS.get(request_id)
        self.assertEqual(restored["state"], "returned")
        self.assertEqual(restored["result_node_id"], "ps-edit-crash")
        self.assertEqual(restored["result_media_id"], result_media["asset_id"])
        # 重试 return 得到明确拒绝而不是重复提交。
        res = self.post_return(request_id, png())
        self.assertEqual(res.status_code, 409, res.text)
        self.assertIn("DUPLICATE_RETURN", res.text)

    def test_restart_processing_without_commit_drops_back_to_pending(self):
        request_id = "crashed-uncommitted"
        now = main.now_ms()
        self.store.save_photoshop_round_trips(self.project["id"], {
            request_id: {
                "request_id": request_id, "project_id": self.project["id"],
                "source_node_id": "source-node", "source_media_id": self.media["asset_id"],
                "created_at": now // 1000, "expires_at": (now + 3600_000) // 1000,
                "expires_at_ms": now + 3600_000, "state": "processing",
            },
        })
        main._photoshop_recover_round_trips()
        restored = main.PHOTOSHOP_ROUND_TRIP_REQUESTS.get(request_id)
        self.assertEqual(restored["state"], "pending")
        # 回退后 PS 端可以重试并完成。
        res = self.post_return(request_id, png(color=(9, 9, 9, 255)))
        self.assertEqual(res.status_code, 200, res.text)

    def test_restart_drops_terminal_records_beyond_retention(self):
        now = main.now_ms()
        self.store.save_photoshop_round_trips(self.project["id"], {
            "old-returned": {
                "request_id": "old-returned", "project_id": self.project["id"],
                "source_node_id": "source-node", "source_media_id": self.media["asset_id"],
                "created_at": (now - 3 * 86400_000) // 1000,
                "expires_at": (now - 3 * 86400_000 + 3600_000) // 1000,
                "expires_at_ms": now - 3 * 86400_000 + 3600_000,
                "state": "returned", "returned_at": (now - 2 * 86400_000) // 1000,
                "result_node_id": "ps-edit-old", "result_media_id": "m-old",
            },
        })
        main._photoshop_recover_round_trips()
        self.assertNotIn("old-returned", main.PHOTOSHOP_ROUND_TRIP_REQUESTS)
        self.assertNotIn("old-returned", self.read_table())

    def test_corrupt_table_is_isolated_and_recovered_empty(self):
        self.table_path.write_text("{broken", encoding="utf-8")
        main._photoshop_recover_round_trips()
        self.assertFalse(self.table_path.exists())
        self.assertTrue(any(
            p.name.startswith("photoshop-round-trips.json.corrupt-")
            for p in (self.store.root / self.project["id"]).iterdir()
        ))
        self.assertEqual(main.PHOTOSHOP_ROUND_TRIP_REQUESTS, {})


if __name__ == "__main__":
    unittest.main()
