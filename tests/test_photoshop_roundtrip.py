import asyncio
import base64
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Importing main constructs its default ProjectStore.  Keep direct test runs
# isolated even when the caller did not provide a project-library override.
_import_root = None
if not os.environ.get("FIREFLY_PROJECTS_ROOT"):
    _import_root = Path(tempfile.mkdtemp(prefix="firefly-issue16-import-"))
    os.environ["FIREFLY_PROJECTS_ROOT"] = str(_import_root)
    import atexit
    atexit.register(lambda: shutil.rmtree(_import_root, ignore_errors=True))

import main
import firefly_projects as project_module
from firefly_projects import ProjectStore


def png(color=(20, 30, 40, 255), size=(8, 6)):
    image = Image.new("RGBA", size, color)
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def jpeg(color=(20, 30, 40), size=(8, 6)):
    image = Image.new("RGB", size, color)
    out = io.BytesIO()
    image.save(out, format="JPEG")
    return out.getvalue()


class PhotoshopRoundTripApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = ProjectStore(Path(self.temp.name) / "projects", snapshot_delay=0)
        self.previous = main.FIREFLY_PROJECT_STORE
        main.FIREFLY_PROJECT_STORE = self.store
        main.PHOTOSHOP_ROUND_TRIP_REQUESTS.clear()
        self.client = TestClient(main.app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000))
        self.project = self.store.create_project("PS 往返")
        self.source_bytes = png()
        self.source = self.store.import_media(self.project["id"], self.source_bytes, "source.png", "image/png")
        self.store.apply_mutations(self.project["id"], [{
            "op": "upsert_node",
            "node": {
                "id": "source-node", "type": "smart-image", "x": 120, "y": 80,
                "images": [{"asset_id": self.source["asset_id"], "kind": "image"}],
            },
        }], base_revision=0)
        self.store.flush(self.project["id"])

    def tearDown(self):
        main.PHOTOSHOP_ROUND_TRIP_REQUESTS.clear()
        self.client.close()
        self.store.close()
        main.FIREFLY_PROJECT_STORE = self.previous
        self.temp.cleanup()

    def create_request(self, **overrides):
        payload = {
            "source_node_id": "source-node",
            "source_media_id": self.source["asset_id"],
            "ttl_seconds": 300,
        }
        payload.update(overrides)
        response = self.client.post(f"/api/project-library/{self.project['id']}/photoshop/round-trips", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_remote_peer_is_rejected_even_when_host_header_says_loopback(self):
        remote = TestClient(
            main.app,
            base_url="http://127.0.0.1",
            headers={"host": "127.0.0.1"},
            client=("192.0.2.1", 50001),
        )
        try:
            response = remote.post(
                f"/api/project-library/{self.project['id']}/photoshop/round-trips",
                json={"source_node_id": "source-node", "source_media_id": self.source["asset_id"]},
            )
        finally:
            remote.close()
        self.assertEqual(response.status_code, 403)

    def test_photoshop_websocket_loopback_guard_uses_transport_peer(self):
        with self.assertRaises(main.HTTPException) as raised:
            main._ensure_photoshop_loopback(SimpleNamespace(
                client=SimpleNamespace(host="192.0.2.10"),
                url=SimpleNamespace(hostname="127.0.0.1"),
            ))
        self.assertEqual(raised.exception.status_code, 403)

    def test_unknown_request_is_reported_before_display_name_validation(self):
        response = self.client.post(
            f"/api/project-library/{self.project['id']}/photoshop/round-trips/unknown/return",
            json={
                "project_id": self.project["id"], "source_node_id": "source-node", "request_id": "unknown",
                "name": "..\\outside.png", "sha256": "0" * 64, "bytes_base64": "AA==",
            },
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"]["code"], "REQUEST_NOT_FOUND")

    def test_create_and_source_are_project_bound(self):
        created = self.create_request()
        self.assertTrue(created["request_id"])
        self.assertEqual(created["project_id"], self.project["id"])
        self.assertEqual(created["source_node_id"], "source-node")
        self.assertLess(created["created_at"], created["expires_at"])
        source = self.client.get(
            f"/api/project-library/{self.project['id']}/photoshop/round-trips/{created['request_id']}/source"
        )
        self.assertEqual(source.status_code, 200, source.text)
        body = source.json()
        self.assertEqual(body["source_media_id"], self.source["asset_id"])
        self.assertEqual(base64.b64decode(body["bytes_base64"]), self.source_bytes)

    def test_canvas_created_binding_reaches_ps_loopback_socket(self):
        with self.client.websocket_connect("/ws/stats?client_id=ps_test") as socket:
            self.assertEqual(socket.receive_json()["type"], "stats")
            created = self.create_request()
            received = socket.receive_json()
            self.assertEqual(received["type"], "photoshop_round_trip_created")
            self.assertEqual(received["data"]["request_id"], created["request_id"])
            self.assertEqual(received["data"]["source_media_id"], self.source["asset_id"])

    def test_valid_return_creates_new_media_node_and_derived_edge_without_overwriting_source(self):
        created = self.create_request()
        returned = jpeg((210, 110, 70), (5, 5))
        response = self.client.post(
            f"/api/project-library/{self.project['id']}/photoshop/round-trips/{created['request_id']}/return",
            json={
                "project_id": self.project["id"], "source_node_id": "source-node",
                "request_id": created["request_id"], "name": "edited.png", "mime_type": "image/jpeg",
                "sha256": hashlib.sha256(returned).hexdigest(),
                "bytes_base64": base64.b64encode(returned).decode("ascii"),
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        result_node = body["result_node"]
        self.assertEqual(result_node["type"], "ps_edit_result")
        self.assertNotEqual(result_node["id"], "source-node")
        self.assertEqual(result_node["source_node_id"], "source-node")
        self.assertNotEqual(result_node["images"][0]["asset_id"], self.source["asset_id"])
        result_media = self.store.media_record(self.project["id"], result_node["images"][0]["asset_id"])
        self.assertEqual(result_media["mime_type"], "image/jpeg")
        self.assertTrue(result_media["filename"].endswith(".jpg"))
        canvas = self.store.open_project(self.project["id"])["canvas"]
        source_node = next(node for node in canvas["nodes"] if node["id"] == "source-node")
        self.assertEqual(source_node["images"][0]["asset_id"], self.source["asset_id"])
        edge = next(edge for edge in canvas["connections"] if edge["to"] == result_node["id"])
        self.assertEqual((edge["from"], edge["kind"]), ("source-node", "derived_from"))
        self.assertEqual(len(self.store.list_media(self.project["id"])), 2)
        status = self.client.get(
            f"/api/project-library/{self.project['id']}/photoshop/round-trips/{created['request_id']}"
        )
        self.assertEqual(status.json()["state"], "returned")

    def test_invalid_hash_path_mismatch_duplicate_and_expired_returns_do_not_write(self):
        bad_hash_request = self.create_request()
        before = self.store.open_project(self.project["id"])["canvas"]
        bad_hash = self.client.post(
            f"/api/project-library/{self.project['id']}/photoshop/round-trips/{bad_hash_request['request_id']}/return",
            json={
                "project_id": self.project["id"], "source_node_id": "source-node",
                "request_id": bad_hash_request["request_id"], "name": "ok.png", "sha256": "0" * 64,
                "bytes_base64": base64.b64encode(png()).decode("ascii"),
            },
        )
        self.assertEqual(bad_hash.status_code, 422)
        self.assertEqual(self.store.open_project(self.project["id"])["canvas"], before)
        self.assertEqual(len(self.store.list_media(self.project["id"])), 1)

        mismatch = self.create_request()
        wrong_source = self.client.post(
            f"/api/project-library/{self.project['id']}/photoshop/round-trips/{mismatch['request_id']}/return",
            json={
                "project_id": self.project["id"], "source_node_id": "other-node",
                "request_id": mismatch["request_id"], "name": "ok.png",
                "sha256": hashlib.sha256(png()).hexdigest(),
                "bytes_base64": base64.b64encode(png()).decode("ascii"),
            },
        )
        self.assertEqual(wrong_source.status_code, 409)
        self.assertEqual(self.store.open_project(self.project["id"])["canvas"], before)

        traversal = self.create_request()
        path = self.client.post(
            f"/api/project-library/{self.project['id']}/photoshop/round-trips/{traversal['request_id']}/return",
            json={
                "project_id": self.project["id"], "source_node_id": "source-node",
                "request_id": traversal["request_id"], "name": "..\\outside.png",
                "sha256": hashlib.sha256(png()).hexdigest(),
                "bytes_base64": base64.b64encode(png()).decode("ascii"),
            },
        )
        self.assertEqual(path.status_code, 400)
        self.assertEqual(len(self.store.list_media(self.project["id"])), 1)
        self.assertEqual(
            self.client.get(f"/api/project-library/{self.project['id']}/photoshop/round-trips/{traversal['request_id']}").json()["state"],
            "pending",
        )
        retry_data = png((55, 66, 77, 255))
        retry = self.client.post(
            f"/api/project-library/{self.project['id']}/photoshop/round-trips/{traversal['request_id']}/return",
            json={
                "project_id": self.project["id"], "source_node_id": "source-node",
                "request_id": traversal["request_id"], "name": "retry.png",
                "sha256": hashlib.sha256(retry_data).hexdigest(),
                "bytes_base64": base64.b64encode(retry_data).decode("ascii"),
            },
        )
        self.assertEqual(retry.status_code, 200, retry.text)

        snapshot_failure = self.create_request()
        failed_data = png((41, 42, 43, 255))
        with patch.object(self.store, "_schedule_snapshot", side_effect=RuntimeError("snapshot failure")):
            failed = self.client.post(
                f"/api/project-library/{self.project['id']}/photoshop/round-trips/{snapshot_failure['request_id']}/return",
                json={
                    "project_id": self.project["id"], "source_node_id": "source-node",
                    "request_id": snapshot_failure["request_id"], "name": "failed.png",
                    "sha256": hashlib.sha256(failed_data).hexdigest(),
                    "bytes_base64": base64.b64encode(failed_data).decode("ascii"),
                },
            )
        self.assertEqual(failed.status_code, 500)
        self.assertEqual(len(self.store.list_media(self.project["id"])), 2)
        failed_canvas = self.store.open_project(self.project["id"])["canvas"]
        self.assertFalse(any(node.get("request_id") == snapshot_failure["request_id"] for node in failed_canvas["nodes"]))
        self.assertEqual(
            self.client.get(
                f"/api/project-library/{self.project['id']}/photoshop/round-trips/{snapshot_failure['request_id']}"
            ).json()["state"],
            "pending",
        )

        disk_failure = self.create_request()
        disk_data = png((51, 52, 53, 255))
        original_write = project_module._write_json_atomic

        def fail_history(path, value, *, keep_backup, backup_count=1):
            if Path(path).name == "history.json":
                raise RuntimeError("history failure")
            return original_write(path, value, keep_backup=keep_backup)

        with patch.object(project_module, "_write_json_atomic", side_effect=fail_history):
            disk_failed = self.client.post(
                f"/api/project-library/{self.project['id']}/photoshop/round-trips/{disk_failure['request_id']}/return",
                json={
                    "project_id": self.project["id"], "source_node_id": "source-node",
                    "request_id": disk_failure["request_id"], "name": "disk-failed.png",
                    "sha256": hashlib.sha256(disk_data).hexdigest(),
                    "bytes_base64": base64.b64encode(disk_data).decode("ascii"),
                },
            )
        self.assertEqual(disk_failed.status_code, 500)
        disk_canvas = self.store.open_project(self.project["id"])["canvas"]
        self.assertFalse(any(node.get("request_id") == disk_failure["request_id"] for node in disk_canvas["nodes"]))
        persisted_canvas = json.loads((Path(self.store.root) / self.project["id"] / "canvas.json").read_text(encoding="utf-8"))
        self.assertFalse(any(node.get("request_id") == disk_failure["request_id"] for node in persisted_canvas["nodes"]))
        self.assertEqual(len(self.store.list_media(self.project["id"])), 2)

        expired = self.create_request(ttl_seconds=1)
        with patch.object(main, "now_ms", return_value=(expired["expires_at"] + 1) * 1000):
            expired_response = self.client.post(
                f"/api/project-library/{self.project['id']}/photoshop/round-trips/{expired['request_id']}/return",
                json={
                    "project_id": self.project["id"], "source_node_id": "source-node",
                    "request_id": expired["request_id"], "name": "expired.png",
                    "sha256": hashlib.sha256(png()).hexdigest(),
                    "bytes_base64": base64.b64encode(png()).decode("ascii"),
                },
            )
        self.assertEqual(expired_response.status_code, 410)
        self.assertEqual(len(self.store.list_media(self.project["id"])), 2)

        valid = self.create_request()
        data = png((1, 2, 3, 255))
        first = self.client.post(
            f"/api/project-library/{self.project['id']}/photoshop/round-trips/{valid['request_id']}/return",
            json={
                "project_id": self.project["id"], "source_node_id": "source-node",
                "request_id": valid["request_id"], "name": "one.png",
                "sha256": hashlib.sha256(data).hexdigest(), "bytes_base64": base64.b64encode(data).decode("ascii"),
            },
        )
        self.assertEqual(first.status_code, 200, first.text)
        second = self.client.post(
            f"/api/project-library/{self.project['id']}/photoshop/round-trips/{valid['request_id']}/return",
            json={
                "project_id": self.project["id"], "source_node_id": "source-node",
                "request_id": valid["request_id"], "name": "two.png",
                "sha256": hashlib.sha256(data).hexdigest(), "bytes_base64": base64.b64encode(data).decode("ascii"),
            },
        )
        self.assertEqual(second.status_code, 409)
        self.assertEqual(len(self.store.list_media(self.project["id"])), 3)

    def test_media_index_failure_rolls_back_new_original_file(self):
        created = self.create_request()
        data = png((8, 9, 10, 255))
        original_write = project_module._write_json_atomic

        def fail_media_index(path, value, *, keep_backup):
            if Path(path).name == "index.json" and Path(path).parent.name == "media":
                raise RuntimeError("media index failure")
            return original_write(path, value, keep_backup=keep_backup)

        with patch.object(project_module, "_write_json_atomic", side_effect=fail_media_index):
            response = self.client.post(
                f"/api/project-library/{self.project['id']}/photoshop/round-trips/{created['request_id']}/return",
                json={
                    "project_id": self.project["id"], "source_node_id": "source-node",
                    "request_id": created["request_id"], "name": "index-failed.png",
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "bytes_base64": base64.b64encode(data).decode("ascii"),
                },
            )
        self.assertEqual(response.status_code, 500)
        self.assertEqual(len(self.store.list_media(self.project["id"])), 1)
        media_files = list((Path(self.store.root) / self.project["id"] / "media").glob("*"))
        self.assertEqual(len([path for path in media_files if path.name != "index.json"]), 1)

    def test_concurrent_returns_consume_one_request_only_once(self):
        created = self.create_request()
        data = png((99, 88, 77, 255))
        payload = {
            "project_id": self.project["id"], "source_node_id": "source-node",
            "request_id": created["request_id"], "name": "concurrent.png",
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes_base64": base64.b64encode(data).decode("ascii"),
        }
        path = f"/api/project-library/{self.project['id']}/photoshop/round-trips/{created['request_id']}/return"
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: self.client.post(path, json=payload), range(2)))
        self.assertEqual(sorted(response.status_code for response in responses), [200, 409])
        self.assertEqual(len(self.store.list_media(self.project["id"])), 2)

    def test_cancelled_return_waits_for_commit_and_marks_request_returned(self):
        created = self.create_request()
        data = png((33, 44, 55, 255))
        payload = main.PhotoshopRoundTripReturnRequest(
            project_id=self.project["id"], source_node_id="source-node", request_id=created["request_id"],
            name="cancelled.png", mime_type="image/png", sha256=hashlib.sha256(data).hexdigest(),
            bytes_base64=base64.b64encode(data).decode("ascii"),
        )
        started = threading.Event()
        release = threading.Event()

        def slow_commit(*_args):
            started.set()
            release.wait(2)
            return {
                "media": {"asset_id": "cancelled-media"},
                "result_node": {"id": "cancelled-node"},
                "edge": {},
                "canvas": {"updated_at": 1},
            }

        async def no_broadcast(*_args, **_kwargs):
            return None

        request = SimpleNamespace(
            client=SimpleNamespace(host="127.0.0.1"),
            url=SimpleNamespace(hostname="127.0.0.1"),
        )

        async def cancel_request():
            with patch.object(main, "project_store_call", side_effect=slow_commit), \
                    patch.object(main.manager, "broadcast_canvas_updated", new=no_broadcast):
                task = asyncio.create_task(main.return_photoshop_round_trip(
                    self.project["id"], created["request_id"], payload, request,
                ))
                self.assertTrue(await asyncio.to_thread(started.wait, 2))
                task.cancel()
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task

        asyncio.run(cancel_request())
        self.assertEqual(main.PHOTOSHOP_ROUND_TRIP_REQUESTS[created["request_id"]]["state"], "returned")


if __name__ == "__main__":
    unittest.main()
