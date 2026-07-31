import json
import io
import hashlib
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from firefly_projects import InvalidMutation, ProjectStore, ProjectStoreError, RevisionConflict


def make_fly_package(
    project,
    canvas,
    extra_files=None,
    version=1,
    media_compression=zipfile.ZIP_STORED,
    metadata_compression=zipfile.ZIP_DEFLATED,
):
    files = {
        "project.json": json.dumps(project, ensure_ascii=False).encode("utf-8"),
        "canvas.json": json.dumps(canvas, ensure_ascii=False).encode("utf-8"),
        **(extra_files or {}),
    }
    manifest = {
        "format": "firefly-package-v1",
        "version": version,
        "files": [
            {"path": path, "size": len(content), "sha256": hashlib.sha256(content).hexdigest()}
            for path, content in sorted(files.items())
        ],
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
        for path, content in files.items():
            archive.writestr(
                path,
                content,
                compress_type=media_compression if path.startswith("media/") and path != "media/index.json" else metadata_compression,
            )
    return buffer.getvalue()


class ProjectStoreContractTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name) / "projects"
        self.store = ProjectStore(self.root, snapshot_delay=0)

    def tearDown(self):
        self.store.close()
        self.temp_dir.cleanup()

    def apply_mutations(self, project_id, mutations):
        revision = self.store.open_project(project_id)["canvas"]["revision"]
        return self.store.apply_mutations(project_id, mutations, base_revision=revision)

    def test_empty_library_has_no_default_project_and_create_owns_one_canvas(self):
        self.assertEqual(self.store.list_projects(), [])

        project = self.store.create_project("第一束光")

        self.assertEqual([item["id"] for item in self.store.list_projects()], [project["id"]])
        opened = self.store.open_project(project["id"])
        self.assertEqual(opened["project"]["id"], project["id"])
        self.assertEqual(opened["canvas"]["id"], project["canvas_id"])
        self.assertEqual(opened["canvas"]["project_id"], project["id"])
        self.assertEqual(opened["canvas"]["kind"], "core")
        self.assertEqual(list((self.root / project["id"]).glob("canvas*.json")), [self.root / project["id"] / "canvas.json"])

    def test_open_migrates_legacy_viewport_and_settings_out_of_project_files(self):
        project = self.store.create_project("旧格式迁移")
        project_dir = self.root / project["id"]
        legacy = self.store.open_project(project["id"])["canvas"]
        legacy.update({"viewport": {"x": 99, "y": -20, "scale": 0.4}, "settings": {"provider_id": "secret"}})
        encoded = json.dumps(legacy, ensure_ascii=False)
        (project_dir / "canvas.json").write_text(encoded, encoding="utf-8")
        (project_dir / "canvas.json.bak").write_text(encoded, encoding="utf-8")
        self.store.close()
        restarted = ProjectStore(self.root, snapshot_delay=0)
        try:
            opened = restarted.open_project(project["id"])["canvas"]
            self.assertNotIn("viewport", opened)
            self.assertNotIn("settings", opened)
            persisted = json.loads((project_dir / "canvas.json").read_text(encoding="utf-8"))
            backup = json.loads((project_dir / "canvas.json.bak").read_text(encoding="utf-8"))
            self.assertNotIn("viewport", persisted)
            self.assertNotIn("settings", persisted)
            self.assertNotIn("viewport", backup)
            self.assertNotIn("settings", backup)
        finally:
            restarted.close()

    def test_project_library_rejects_relative_and_repository_paths(self):
        with self.assertRaisesRegex(ProjectStoreError, "绝对路径"):
            ProjectStore("project-data")
        with self.assertRaisesRegex(ProjectStoreError, "代码仓库"):
            ProjectStore(ROOT / "project-data")

    def test_incremental_mutations_are_isolated_by_project(self):
        first = self.store.create_project("甲")
        second = self.store.create_project("乙")

        result = self.apply_mutations(first["id"], [
            {"op": "upsert_node", "node": {"id": "node-1", "x": 120, "y": 80, "type": "prompt"}},
            {"op": "upsert_node", "node": {"id": "node-2", "x": 420, "y": 80, "type": "image"}},
            {"op": "upsert_edge", "edge": {"id": "edge-1", "from": "node-1", "to": "node-2"}},
            {"op": "set_viewport", "viewport": {"x": 11, "y": 22, "scale": 0.8}},
        ])

        self.assertEqual(result["applied"], 4)
        first_canvas = self.store.open_project(first["id"])["canvas"]
        second_canvas = self.store.open_project(second["id"])["canvas"]
        self.assertEqual([node["id"] for node in first_canvas["nodes"]], ["node-1", "node-2"])
        self.assertEqual(first_canvas["connections"], [{"id": "edge-1", "from": "node-1", "to": "node-2"}])
        self.assertNotIn("viewport", first_canvas)
        self.assertNotIn("settings", first_canvas)
        self.assertEqual(second_canvas["nodes"], [])
        self.assertEqual(second_canvas["connections"], [])

        self.apply_mutations(first["id"], [
            {"op": "upsert_node", "node": {"id": "node-1", "x": 150, "y": 95, "type": "prompt"}},
            {"op": "remove_edge", "edge_id": "edge-1"},
            {"op": "remove_node", "node_id": "node-2"},
        ])
        changed = self.store.open_project(first["id"])["canvas"]
        self.assertEqual(changed["nodes"], [{"id": "node-1", "x": 150, "y": 95, "type": "prompt"}])
        self.assertEqual(changed["connections"], [])

    def test_stale_mutation_is_rejected_without_resurrecting_deleted_node(self):
        project = self.store.create_project("并发")
        self.store.apply_mutations(project["id"], [
            {"op": "upsert_node", "node": {"id": "shared", "x": 1, "y": 1}},
        ], base_revision=0)
        self.store.apply_mutations(project["id"], [
            {"op": "remove_node", "node_id": "shared"},
        ], base_revision=1)

        with self.assertRaises(RevisionConflict):
            self.store.apply_mutations(project["id"], [
                {"op": "upsert_node", "node": {"id": "shared", "x": 99, "y": 99}},
            ], base_revision=1)

        self.assertEqual(self.store.open_project(project["id"])["canvas"]["nodes"], [])

    def test_project_canvas_rejects_global_or_remote_media_references(self):
        project = self.store.create_project("媒体边界")
        for url in (
            "/output/generated.png", "/assets/input.png", "https://example.test/image.png",
            "data:image/png;base64,AA==", r"C:\outside\a.png", "file:///C:/outside/a.png",
            "blob:http://localhost/id", "/api/media-preview?id=1", "relative/image.png",
        ):
            with self.subTest(url=url), self.assertRaises(InvalidMutation):
                self.apply_mutations(project["id"], [
                    {"op": "upsert_node", "node": {"id": "image", "images": [{"url": url}]}},
                ])
        media = self.store.import_media(project["id"], b"project-owned-image", "image.png", "image/png")
        duplicate = self.store.import_media(project["id"], b"project-owned-image", "renamed.jpg", "image/jpeg")
        self.assertEqual(media["asset_id"], duplicate["asset_id"])
        self.assertEqual(media["filename"], duplicate["filename"])
        accepted = self.apply_mutations(project["id"], [
            {"op": "upsert_node", "node": {"id": "image", "images": [{"asset_id": media["asset_id"]}]}},
        ])
        self.assertEqual(accepted["applied"], 1)
        self.assertTrue(Path(self.store.media_record(project["id"], media["asset_id"])["path"]).is_file())
        other = self.store.create_project("另一个项目")
        with self.assertRaises(InvalidMutation):
            self.apply_mutations(other["id"], [
                {"op": "upsert_node", "node": {"id": "foreign", "images": [{"asset_id": media["asset_id"]}]}},
            ])
        with self.assertRaises(InvalidMutation):
            self.apply_mutations(project["id"], [
                {"op": "upsert_node", "node": {"id": "hidden", "downloadUrl": "file:///outside.png"}},
            ])

    def test_project_media_catalog_classifies_image_video_and_audio_originals(self):
        project = self.store.create_project("多媒体原件")
        expected = {
            "image": self.store.import_media(project["id"], b"image-bytes", "image.png", "image/png"),
            "video": self.store.import_media(project["id"], b"video-bytes", "video.mp4", "video/mp4"),
            "audio": self.store.import_media(project["id"], b"audio-bytes", "audio.mp3", "audio/mpeg"),
        }

        catalog = self.store.list_media(project["id"])

        self.assertEqual({item["kind"] for item in catalog}, {"image", "video", "audio"})
        self.assertEqual({item["asset_id"] for item in catalog}, {item["asset_id"] for item in expected.values()})
        self.assertTrue(all("path" not in item for item in catalog))

    def test_project_media_dedupe_keeps_sanitized_source_history(self):
        project = self.store.create_project("来源历史")
        first = self.store.import_media(
            project["id"], b"same-content", "first.png", "image/png",
            source_info={"locator": "https://user:password@example.test/original.png?token=SECRET#part", "type": "remote_url"},
        )
        second = self.store.import_media(
            project["id"], b"same-content", "second.png", "image/png",
            source_info={"locator": "file:///D:/references/second.png?credential=SECRET", "type": "external_path"},
        )

        self.assertEqual(first["asset_id"], second["asset_id"])
        record = self.store.media_record(project["id"], first["asset_id"])
        self.assertEqual(record["source"]["locator"], "https://example.test/original.png")
        self.assertEqual(
            [item["locator"] for item in record["sources"]],
            ["https://example.test/original.png", "file:///D:/references/second.png"],
        )
        self.assertNotIn("SECRET", json.dumps(record, ensure_ascii=False))
        self.assertNotIn("password", json.dumps(record, ensure_ascii=False))

        index_path = self.root / project["id"] / "media" / "index.json"
        legacy_index = json.loads(index_path.read_text(encoding="utf-8"))
        legacy_record = legacy_index["items"][first["asset_id"]]
        legacy_record["source"] = {"locator": "https://legacy:password@example.test/old.png?token=SECRET"}
        legacy_record["sources"] = [legacy_record["source"]]
        index_path.write_text(json.dumps(legacy_index, ensure_ascii=False), encoding="utf-8")

        migrated = self.store.media_record(project["id"], first["asset_id"])
        persisted = index_path.read_text(encoding="utf-8")

        self.assertEqual(migrated["source"]["locator"], "https://example.test/old.png")
        self.assertNotIn("SECRET", persisted)
        self.assertNotIn("password", persisted)

    def test_generation_history_is_saved_outside_canvas_snapshot(self):
        project = self.store.create_project("历史分离")
        self.apply_mutations(project["id"], [
            {"op": "set_canvas_meta", "logs": [{"id": "log-1", "prompt": "萤火"}]},
        ])
        self.store.flush(project["id"])
        project_dir = self.root / project["id"]
        canvas_payload = json.loads((project_dir / "canvas.json").read_text(encoding="utf-8"))
        history_payload = json.loads((project_dir / "history.json").read_text(encoding="utf-8"))
        self.assertNotIn("logs", canvas_payload)
        self.assertEqual(history_payload, {"records": [{"id": "log-1", "prompt": "萤火"}]})
        restarted = ProjectStore(self.root, snapshot_delay=0)
        try:
            self.assertEqual(restarted.open_project(project["id"])["canvas"]["logs"], history_payload["records"])
        finally:
            restarted.close()

    def test_history_delete_uses_same_revision_cas_as_canvas_mutations(self):
        project = self.store.create_project("历史并发")
        self.apply_mutations(project["id"], [
            {"op": "set_canvas_meta", "logs": [{"id": "log-1"}, {"id": "log-2"}]},
        ])
        deleted = self.store.delete_history_record(project["id"], "log-1", base_revision=1)
        self.assertEqual(deleted["revision"], 2)
        self.assertEqual(deleted["logs"], [{"id": "log-2"}])
        with self.assertRaises(RevisionConflict):
            self.store.delete_history_record(project["id"], "log-2", base_revision=1)

    def test_browser_history_save_merges_with_completed_server_generations(self):
        project = self.store.create_project("并发生图历史")
        first = self.store.import_media(project["id"], b"first", "first.png", "image/png")
        second = self.store.import_media(project["id"], b"second", "second.png", "image/png")
        self.store.complete_image_generation(
            project["id"], "result-a", [{"asset_id": first["asset_id"], "kind": "image"}],
            {"id": "generation-a", "prompt": "甲"},
        )
        self.store.complete_image_generation(
            project["id"], "result-b", [{"asset_id": second["asset_id"], "kind": "image"}],
            {"id": "generation-b", "prompt": "乙"},
        )

        self.apply_mutations(project["id"], [
            {"op": "set_canvas_meta", "logs": [{"id": "generation-b", "prompt": "乙（浏览器已同步）"}]},
        ])

        history = self.store.open_project(project["id"])["canvas"]["logs"]
        self.assertEqual([item["id"] for item in history], ["generation-a", "generation-b"])
        self.assertEqual(history[-1]["prompt"], "乙（浏览器已同步）")

    def test_generation_result_cannot_overwrite_a_non_image_node(self):
        project = self.store.create_project("节点类型边界")
        media = self.store.import_media(project["id"], b"image", "image.png", "image/png")
        self.apply_mutations(project["id"], [
            {"op": "upsert_node", "node": {"id": "prompt-node", "type": "prompt", "text": "保留我"}},
        ])

        with self.assertRaisesRegex(InvalidMutation, "图片节点"):
            self.store.complete_image_generation(
                project["id"], "prompt-node", [{"asset_id": media["asset_id"], "kind": "image"}],
                {"id": "generation-unsafe"},
            )

        node = self.store.open_project(project["id"])["canvas"]["nodes"][0]
        self.assertEqual(node, {"id": "prompt-node", "type": "prompt", "text": "保留我"})

    def test_generation_lifecycle_is_registered_and_failure_is_persisted(self):
        project = self.store.create_project("任务生命周期")
        self.apply_mutations(project["id"], [
            {"op": "upsert_node", "node": {"id": "result", "type": "smart-image", "x": 321, "y": 123}},
        ])
        begun = self.store.begin_image_generation(
            project["id"], "result",
            {"taskId": "task-1", "kind": "image", "count": 2, "providerId": "provider", "model": "model"},
            base_revision=1,
        )
        pending = next(item for item in begun["nodes"] if item["id"] == "result")
        self.assertEqual((pending["x"], pending["y"]), (321, 123))
        self.assertEqual(pending["pendingTasks"][0]["taskId"], "task-1")
        self.assertEqual(pending["pending"], 2)

        failed = self.store.fail_image_generation(
            project["id"], "result", "task-1",
            {"id": "failure-1", "status": "failed", "error": "上游失败"},
        )
        node = next(item for item in failed["nodes"] if item["id"] == "result")
        self.assertNotIn("pendingTasks", node)
        self.assertEqual(node["pending"], 0)
        self.assertEqual(failed["logs"][-1]["id"], "failure-1")

    def test_generation_registration_uses_revision_cas_and_rejects_same_node_overlap(self):
        project = self.store.create_project("生成并发")
        self.apply_mutations(project["id"], [
            {"op": "upsert_node", "node": {"id": "result", "type": "smart-image"}},
        ])
        self.store.begin_image_generation(
            project["id"], "result", {"taskId": "first", "count": 1}, base_revision=1,
        )
        with self.assertRaises(RevisionConflict):
            self.store.begin_image_generation(
                project["id"], "result", {"taskId": "stale", "count": 1}, base_revision=1,
            )
        with self.assertRaisesRegex(InvalidMutation, "正在运行"):
            self.store.begin_image_generation(
                project["id"], "result", {"taskId": "overlap", "count": 1}, base_revision=2,
            )

    def test_invalid_batch_does_not_partially_change_history(self):
        project = self.store.create_project("事务边界")
        with self.assertRaises(InvalidMutation):
            self.apply_mutations(project["id"], [
                {"op": "upsert_node", "node": {"id": "must-not-stick"}},
                {"op": "set_canvas_meta", "logs": [{"id": "must-not-stick"}]},
                {"op": "unsupported"},
            ])
        opened = self.store.open_project(project["id"])["canvas"]
        self.assertEqual(opened["nodes"], [])
        self.assertEqual(opened["logs"], [])

    def test_fly_import_creates_new_identity_and_one_project_owned_canvas(self):
        package = make_fly_package(
            {"format": "firefly-project-v1", "id": "foreign", "name": "导入的光"},
            {
                "format": "firefly-canvas-v1", "id": "foreign-canvas", "title": "导入画布",
                "nodes": [{"id": "n1", "images": [{"asset_id": "asset-1"}]}], "connections": [],
            },
            {
                "history.json": json.dumps({"records": [{"id": "h1"}]}).encode("utf-8"),
                "media/asset-1.png": b"synthetic-media",
            },
        )
        imported = self.store.import_fly(package)
        self.assertNotEqual(imported["id"], "foreign")
        self.assertEqual(imported["id"], imported["canvas_id"])
        opened = self.store.open_project(imported["id"])["canvas"]
        self.assertEqual(opened["id"], imported["id"])
        self.assertEqual(opened["nodes"][0]["images"], [{"asset_id": "asset-1"}])
        self.assertEqual(opened["logs"], [{"id": "h1"}])
        self.assertEqual((self.root / imported["id"] / "media" / "asset-1.png").read_bytes(), b"synthetic-media")

    def test_parallel_fly_imports_serialize_space_check_and_extraction(self):
        first_entered = threading.Event()
        release_first = threading.Event()
        second_finished = threading.Event()
        active = 0
        maximum_active = 0
        counter_lock = threading.Lock()

        def observed_import(package):
            nonlocal active, maximum_active
            with counter_lock:
                active += 1
                maximum_active = max(maximum_active, active)
            try:
                if package == b"first":
                    first_entered.set()
                    self.assertTrue(release_first.wait(2))
                return {"id": package.decode()}
            finally:
                with counter_lock:
                    active -= 1

        def run_second():
            self.store.import_fly(b"second")
            second_finished.set()

        with patch.object(self.store, "_import_fly", side_effect=observed_import):
            first = threading.Thread(target=self.store.import_fly, args=(b"first",))
            second = threading.Thread(target=run_second)
            first.start()
            self.assertTrue(first_entered.wait(1))
            second.start()
            self.assertFalse(second_finished.wait(0.1))
            release_first.set()
            first.join(2)
            second.join(2)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(maximum_active, 1)
        self.assertTrue(second_finished.is_set())

    def test_fly_export_contains_complete_project_without_cache_or_trash(self):
        project = self.store.create_project("完整导出")
        media = self.store.import_media(project["id"], b"project-original", "original.png", "image/png")
        self.apply_mutations(project["id"], [
            {"op": "upsert_node", "node": {"id": "result", "images": [{"asset_id": media["asset_id"]}]}},
            {"op": "set_canvas_meta", "logs": [{"id": "history-1", "outputs": [{"asset_id": media["asset_id"]}]}]},
        ])
        project_dir = self.root / project["id"]
        (project_dir / "previews" / "rebuild.webp").write_bytes(b"preview")
        (project_dir / "trash" / "deleted.png").write_bytes(b"trash")
        (project_dir / "media" / ".incoming.test.tmp").write_bytes(b"temporary")
        package_path = Path(self.temp_dir.name) / "complete.fly"

        result = self.store.export_fly_to(project["id"], package_path)

        self.assertEqual(result["path"], str(package_path.resolve()))
        with zipfile.ZipFile(package_path) as archive:
            names = set(archive.namelist())
            self.assertEqual(names, {
                "manifest.json", "project.json", "canvas.json", "history.json",
                "media/index.json", f"media/{media['filename']}",
            })
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(manifest["format"], "firefly-package-v1")
            self.assertEqual(manifest["version"], 1)
            declared = {item["path"]: item for item in manifest["files"]}
            self.assertEqual(set(declared), names - {"manifest.json"})
            for path, item in declared.items():
                content = archive.read(path)
                self.assertEqual(item["size"], len(content))
                self.assertEqual(item["sha256"], hashlib.sha256(content).hexdigest())
            self.assertEqual(archive.getinfo(f"media/{media['filename']}").compress_type, zipfile.ZIP_STORED)
            exported_index = json.loads(archive.read("media/index.json"))
            self.assertNotIn("source", exported_index["items"][media["asset_id"]])
            self.assertNotIn("sources", exported_index["items"][media["asset_id"]])

        imported = self.store.import_fly(package_path.read_bytes())
        self.assertNotEqual(imported["id"], project["id"])
        reopened = self.store.open_project(imported["id"])["canvas"]
        self.assertEqual(reopened["nodes"][0]["images"], [{"asset_id": media["asset_id"]}])
        self.assertEqual(reopened["logs"][0]["outputs"], [{"asset_id": media["asset_id"]}])
        imported_media = self.store.media_record(imported["id"], media["asset_id"])
        self.assertEqual(imported_media["name"], "original.png")
        self.assertEqual(imported_media["mime_type"], "image/png")
        self.assertEqual(imported_media["kind"], "image")
        self.assertEqual(imported_media["created_at"], media["created_at"])
        self.assertNotIn("source", imported_media)
        self.assertNotIn("sources", imported_media)

    def test_large_export_does_not_hold_the_store_lock_for_other_projects(self):
        exporting = self.store.create_project("慢导出")
        editable = self.store.create_project("继续编辑")
        self.store.import_media(exporting["id"], b"media-for-export", "large.png", "image/png")
        hash_started = threading.Event()
        release_hash = threading.Event()
        original_hash = __import__("firefly_projects")._file_sha256

        def slow_hash(path):
            hash_started.set()
            self.assertTrue(release_hash.wait(2))
            return original_hash(path)

        package_path = Path(self.temp_dir.name) / "slow.fly"
        with patch("firefly_projects._file_sha256", side_effect=slow_hash):
            worker = threading.Thread(target=self.store.export_fly_to, args=(exporting["id"], package_path))
            worker.start()
            self.assertTrue(hash_started.wait(1))
            started = time.perf_counter()
            changed = self.store.apply_mutations(
                editable["id"],
                [{"op": "upsert_node", "node": {"id": "still-responsive", "x": 1, "y": 2}}],
                base_revision=0,
            )
            elapsed = time.perf_counter() - started
            release_hash.set()
            worker.join(2)

        self.assertFalse(worker.is_alive())
        self.assertEqual(changed["revision"], 1)
        self.assertLess(elapsed, 0.5)
        self.assertTrue(package_path.is_file())

    def test_project_trash_restores_or_permanently_deletes_whole_project(self):
        project = self.store.create_project("整项目回收")
        media = self.store.import_media(project["id"], b"keep-together", "owned.mp3", "audio/mpeg")

        trashed = self.store.trash_project(project["id"])

        self.assertEqual(self.store.list_projects(), [])
        self.assertEqual(self.store.list_trashed_projects()[0]["id"], project["id"])
        self.assertTrue(Path(trashed["path"], "media", media["filename"]).is_file())

        restored = self.store.restore_project(project["id"])
        self.assertEqual(restored["id"], project["id"])
        self.assertEqual(self.store.list_trashed_projects(), [])
        self.assertEqual(Path(self.store.media_record(project["id"], media["asset_id"])["path"]).read_bytes(), b"keep-together")

        self.store.trash_project(project["id"])
        deleted = self.store.purge_trashed_project(project["id"])
        self.assertEqual(deleted["id"], project["id"])
        self.assertEqual(self.store.list_trashed_projects(), [])
        with self.assertRaisesRegex(ProjectStoreError, "回收站中不存在"):
            self.store.restore_project(project["id"])

    def test_failed_project_trash_move_leaves_the_live_project_recoverable(self):
        project = self.store.create_project("移动失败")
        project_dir = self.root / project["id"]
        trash_target = self.store.project_trash_root / project["id"]
        original_replace = __import__("firefly_projects").os.replace

        def fail_directory_move(source, target):
            if Path(source).is_dir() and Path(source).name == project["id"] and Path(target).name == project["id"]:
                raise OSError("simulated move failure")
            return original_replace(source, target)

        with patch("firefly_projects.os.replace", side_effect=fail_directory_move):
            with self.assertRaisesRegex(OSError, "simulated move failure"):
                self.store.trash_project(project["id"])

        self.assertEqual(self.store.open_project(project["id"])["project"]["name"], "移动失败")
        self.assertFalse((project_dir / "trash.json").exists())
        self.assertFalse(trash_target.exists())

    def test_fly_import_rejects_drive_paths_and_checksum_mismatch(self):
        missing_version = make_fly_package(
            {"format": "firefly-project-v1", "name": "缺版本"},
            {"format": "firefly-canvas-v1", "nodes": [], "connections": []},
        )
        package_buffer = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(missing_version)) as source, zipfile.ZipFile(package_buffer, "w") as target:
            for member in source.infolist():
                content = source.read(member)
                if member.filename == "manifest.json":
                    manifest = json.loads(content)
                    manifest.pop("version")
                    content = json.dumps(manifest).encode()
                target.writestr(member, content)
        with self.assertRaisesRegex(ProjectStoreError, "格式版本不受支持"):
            self.store.import_fly(package_buffer.getvalue())

        unsupported = make_fly_package(
            {"format": "firefly-project-v1", "name": "未来版本"},
            {"format": "firefly-canvas-v1", "nodes": [], "connections": []},
            version=2,
        )
        with self.assertRaisesRegex(ProjectStoreError, "格式版本不受支持"):
            self.store.import_fly(unsupported)

        compressed_media = make_fly_package(
            {"format": "firefly-project-v1", "name": "压缩媒体"},
            {
                "format": "firefly-canvas-v1", "connections": [],
                "nodes": [{"id": "n1", "images": [{"asset_id": "compressed"}]}],
            },
            {"media/compressed.png": bytes(range(256)) * 4},
            media_compression=zipfile.ZIP_DEFLATED,
        )
        imported_compressed = self.store.import_fly(compressed_media)
        self.assertEqual(
            Path(self.store.media_record(imported_compressed["id"], "compressed")["path"]).read_bytes(),
            bytes(range(256)) * 4,
        )

        compressed_bomb = make_fly_package(
            {"format": "firefly-project-v1", "name": "高压缩比媒体"},
            {
                "format": "firefly-canvas-v1", "connections": [],
                "nodes": [{"id": "n1", "images": [{"asset_id": "bomb"}]}],
            },
            {"media/bomb.png": b"A" * (2 * 1024 * 1024)},
            media_compression=zipfile.ZIP_DEFLATED,
        )
        opened_media = []
        original_zip_open = zipfile.ZipFile.open

        def observe_zip_open(archive, member, *args, **kwargs):
            name = member.filename if isinstance(member, zipfile.ZipInfo) else str(member)
            if name.startswith("media/") and name != "media/index.json":
                opened_media.append(name)
            return original_zip_open(archive, member, *args, **kwargs)

        with patch.object(zipfile.ZipFile, "open", new=observe_zip_open):
            with self.assertRaisesRegex(ProjectStoreError, "压缩比超过安全限制"):
                self.store.import_fly(compressed_bomb)
        self.assertEqual(opened_media, [])

        unsafe = make_fly_package(
            {"format": "firefly-project-v1", "name": "不安全"},
            {"format": "firefly-canvas-v1", "nodes": [], "connections": []},
            {"media/C:/escape.png": b"escape"},
        )
        with self.assertRaisesRegex(Exception, "不安全路径"):
            self.store.import_fly(unsafe)

        absolute = make_fly_package(
            {"format": "firefly-project-v1", "name": "绝对路径"},
            {"format": "firefly-canvas-v1", "nodes": [], "connections": []},
            {"/media/escape.png": b"escape"},
        )
        with self.assertRaisesRegex(ProjectStoreError, "不安全路径"):
            self.store.import_fly(absolute)

        dotted = make_fly_package(
            {"format": "firefly-project-v1", "name": "点路径"},
            {"format": "firefly-canvas-v1", "nodes": [], "connections": []},
            {"media/./escape.png": b"escape"},
        )
        with self.assertRaisesRegex(ProjectStoreError, "不安全路径"):
            self.store.import_fly(dotted)

        package = make_fly_package(
            {"format": "firefly-project-v1", "name": "校验"},
            {"format": "firefly-canvas-v1", "nodes": [], "connections": []},
            metadata_compression=zipfile.ZIP_STORED,
        )
        corrupted = bytearray(package)
        with zipfile.ZipFile(io.BytesIO(package)) as archive:
            member = archive.getinfo("project.json")
            payload_offset = member.header_offset + 30 + len(member.filename.encode()) + len(member.extra)
        corrupted[payload_offset] ^= 1
        with self.assertRaisesRegex(ProjectStoreError, "损坏或无法读取"):
            self.store.import_fly(bytes(corrupted))

        missing_media = make_fly_package(
            {"format": "firefly-project-v1", "name": "缺媒体"},
            {
                "format": "firefly-canvas-v1", "connections": [],
                "nodes": [{"id": "n1", "images": [{"asset_id": "not-packaged"}]}],
            },
        )
        with self.assertRaisesRegex(Exception, "缺少媒体原件"):
            self.store.import_fly(missing_media)

        unsafe_history = make_fly_package(
            {"format": "firefly-project-v1", "name": "历史外链"},
            {"format": "firefly-canvas-v1", "nodes": [], "connections": []},
            {"history.json": json.dumps({"records": [{"outputUrl": "file:///outside.png"}]}).encode()},
        )
        with self.assertRaisesRegex(Exception, "生成历史包含外部媒体"):
            self.store.import_fly(unsafe_history)

        case_collision = make_fly_package(
            {"format": "firefly-project-v1", "name": "大小写冲突"},
            {
                "format": "firefly-canvas-v1", "connections": [],
                "nodes": [{"id": "n1", "images": [{"asset_id": "A"}, {"asset_id": "a"}]}],
            },
            {"media/A.png": b"FIRST", "media/a.png": b"SECOND"},
        )
        with self.assertRaisesRegex(ProjectStoreError, "Windows 路径冲突"):
            self.store.import_fly(case_collision)

        asset_id_collision = make_fly_package(
            {"format": "firefly-project-v1", "name": "媒体标识冲突"},
            {
                "format": "firefly-canvas-v1", "connections": [],
                "nodes": [{"id": "n1", "images": [{"asset_id": "same"}]}],
            },
            {"media/same.png": b"PNG", "media/same.jpg": b"JPG"},
        )
        with self.assertRaisesRegex(ProjectStoreError, "媒体 ID 冲突"):
            self.store.import_fly(asset_id_collision)

        parent_file_collision = make_fly_package(
            {"format": "firefly-project-v1", "name": "父路径冲突"},
            {"format": "firefly-canvas-v1", "nodes": [], "connections": []},
            {"media/foo": b"FILE", "media/foo/bar.png": b"CHILD"},
        )
        with self.assertRaisesRegex(ProjectStoreError, "文件与目录路径冲突"):
            self.store.import_fly(parent_file_collision)

    def test_media_file_import_streams_to_project_owned_storage(self):
        project = self.store.create_project("分块媒体")
        source = Path(self.temp_dir.name) / "large-source.bin"
        content = b"streamed-media" * 100000
        source.write_bytes(content)
        record = self.store.import_media_file(project["id"], source, "source.bin", "application/octet-stream")
        self.assertEqual(record["size"], len(content))
        self.assertEqual(record["sha256"], hashlib.sha256(content).hexdigest())
        self.assertEqual(Path(self.store.media_record(project["id"], record["asset_id"])["path"]).read_bytes(), content)
        orphan = self.root / project["id"] / "media" / ".incoming.crashed.tmp"
        orphan.write_bytes(b"partial")
        restarted = ProjectStore(self.root, snapshot_delay=0)
        try:
            self.assertFalse(orphan.exists())
        finally:
            restarted.close()

    def test_500_nodes_and_1000_edges_reopen_under_capacity_contract(self):
        project = self.store.create_project("容量")
        mutations = [
            {"op": "upsert_node", "node": {"id": f"n{index}", "x": index, "y": index, "type": "prompt"}}
            for index in range(500)
        ]
        mutations.extend(
            {"op": "upsert_edge", "edge": {"id": f"e{index}", "from": f"n{index % 500}", "to": f"n{(index + 1) % 500}"}}
            for index in range(1000)
        )
        apply_started = time.monotonic()
        self.apply_mutations(project["id"], mutations)
        apply_seconds = time.monotonic() - apply_started
        self.store.flush(project["id"])

        restarted = ProjectStore(self.root, snapshot_delay=0)
        try:
            open_started = time.monotonic()
            canvas = restarted.open_project(project["id"])["canvas"]
            open_seconds = time.monotonic() - open_started
        finally:
            restarted.close()
        self.assertEqual((len(canvas["nodes"]), len(canvas["connections"])), (500, 1000))
        self.assertLess(apply_seconds, 2.0)
        self.assertLess(open_seconds, 3.0)

    def test_atomic_snapshot_keeps_one_backup_and_recovers_corrupt_primary(self):
        project = self.store.create_project("恢复测试")
        project_dir = self.root / project["id"]
        primary = project_dir / "canvas.json"
        backup = project_dir / "canvas.json.bak"

        self.apply_mutations(project["id"], [
            {"op": "upsert_node", "node": {"id": "kept", "x": 1, "y": 2}},
        ])
        self.store.flush(project["id"])
        self.apply_mutations(project["id"], [
            {"op": "upsert_node", "node": {"id": "latest", "x": 3, "y": 4}},
        ])
        self.store.flush(project["id"])

        self.assertTrue(primary.exists())
        self.assertTrue(backup.exists())
        self.assertEqual(len(list(project_dir.glob("canvas.json.bak*"))), 1)
        primary.write_text("{broken", encoding="utf-8")

        restarted = ProjectStore(self.root, snapshot_delay=0)
        try:
            recovered = restarted.open_project(project["id"])["canvas"]
        finally:
            restarted.close()
        self.assertEqual([node["id"] for node in recovered["nodes"]], ["kept"])
        json.loads(primary.read_text(encoding="utf-8"))

    def test_background_snapshot_is_throttled_and_finishes_within_contract(self):
        delayed = ProjectStore(self.root, snapshot_delay=0.15)
        try:
            project = delayed.create_project("后台保存")
            primary = self.root / project["id"] / "canvas.json"
            delayed.apply_mutations(project["id"], [
                {"op": "upsert_node", "node": {"id": "released", "x": 9, "y": 10}},
            ], base_revision=0)
            self.assertFalse(delayed.save_status(project["id"])["saved"])
            immediate = json.loads(primary.read_text(encoding="utf-8"))
            self.assertEqual(immediate["nodes"], [])

            deadline = time.monotonic() + 2.0
            persisted = immediate
            while time.monotonic() < deadline:
                time.sleep(0.03)
                persisted = json.loads(primary.read_text(encoding="utf-8"))
                if persisted["nodes"]:
                    break
            self.assertEqual(persisted["nodes"], [{"id": "released", "x": 9, "y": 10}])
            self.assertTrue(delayed.save_status(project["id"])["saved"])
            self.assertTrue(primary.with_name("canvas.json.bak").exists())
        finally:
            delayed.close()


if __name__ == "__main__":
    unittest.main()
