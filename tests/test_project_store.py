import json
import io
import hashlib
import os
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

import firefly_projects
from firefly_projects import InvalidMutation, ProjectNotFound, ProjectStore, ProjectStoreError, RevisionConflict


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

    def test_cleanup_preview_lists_unowned_transient_generation_without_mutating_project(self):
        project = self.store.create_project("待清理项目")
        candidate = self.store.import_media(
            project["id"],
            b"transient-generation-bytes",
            "failed-output.png",
            "image/png",
            source_info={"type": "online_generation", "lifecycle": "transient"},
        )
        project_dir = self.root / project["id"]

        def tree_snapshot():
            return {
                path.relative_to(project_dir).as_posix(): (
                    "directory" if path.is_dir() else "file",
                    path.stat().st_size,
                    path.stat().st_mtime_ns,
                    path.read_bytes() if path.is_file() else b"",
                )
                for path in sorted(project_dir.rglob("*"))
            }

        before = tree_snapshot()
        preview = self.store.preview_project_cleanup(project["id"])

        self.assertEqual(preview["project_id"], project["id"])
        self.assertEqual(preview["revision"], 0)
        self.assertRegex(preview["ownership_proof"], r"^[0-9a-f]{64}$")
        self.assertEqual(preview["candidate_count"], 1)
        self.assertEqual(preview["total_bytes"], len(b"transient-generation-bytes"))
        self.assertEqual(preview["candidates"], [{
            "asset_id": candidate["asset_id"],
            "name": "failed-output.png",
            "kind": "image",
            "mime_type": "image/png",
            "size": len(b"transient-generation-bytes"),
            "source_category": "transient_generation",
        }])
        self.assertEqual(tree_snapshot(), before)

    def test_cleanup_preview_lists_unowned_failed_generation(self):
        project = self.store.create_project("失败生成残留")
        candidate = self.store.import_media(
            project["id"],
            b"failed-generation",
            "failed.png",
            "image/png",
            source_info={"type": "online_generation", "lifecycle": "failed"},
        )

        preview = self.store.preview_project_cleanup(project["id"])

        self.assertEqual(preview["candidate_count"], 1)
        self.assertEqual(preview["candidates"][0]["asset_id"], candidate["asset_id"])
        self.assertEqual(preview["candidates"][0]["source_category"], "failed_generation")

    def test_cleanup_preview_preserves_conflicting_generation_lifecycles(self):
        project = self.store.create_project("生成生命周期冲突")
        succeeded = self.store.import_media(
            project["id"],
            b"shared-generation-lifecycle",
            "succeeded.png",
            "image/png",
            source_info={"type": "online_generation", "lifecycle": "succeeded"},
        )
        transient = self.store.import_media(
            project["id"],
            b"shared-generation-lifecycle",
            "transient.png",
            "image/png",
            source_info={"type": "online_generation", "lifecycle": "transient"},
        )
        self.assertEqual(transient["asset_id"], succeeded["asset_id"])

        preview = self.store.preview_project_cleanup(project["id"])

        self.assertEqual(preview["candidate_count"], 0)
        self.assertEqual(preview["candidates"], [])

    def test_cleanup_preview_fails_closed_on_unknown_candidate_schema(self):
        project = self.store.create_project("未知候选 schema")
        candidate = self.store.import_media(
            project["id"],
            b"unknown-candidate-schema",
            "candidate.png",
            "image/png",
            source_info={"type": "online_generation", "lifecycle": "transient"},
        )
        index_path = self.root / project["id"] / "media" / "index.json"
        index = json.loads(index_path.read_text(encoding="utf-8"))
        index["items"][candidate["asset_id"]]["future_owner"] = {"kind": "unknown"}
        index_path.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")

        with self.assertRaisesRegex(ProjectStoreError, "媒体 schema"):
            self.store.preview_project_cleanup(project["id"])

    def test_cleanup_preview_fails_closed_on_noncanonical_media_provenance(self):
        project = self.store.create_project("非规范来源信息")
        candidate = self.store.import_media(
            project["id"],
            b"noncanonical-provenance",
            "candidate.png",
            "image/png",
            source_info={"type": "online_generation", "lifecycle": "transient"},
        )
        index_path = self.root / project["id"] / "media" / "index.json"
        index = json.loads(index_path.read_text(encoding="utf-8"))
        index["items"][candidate["asset_id"]]["source"]["future_owner"] = "unknown"
        index_path.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")

        with self.assertRaisesRegex(ProjectStoreError, "媒体来源"):
            self.store.preview_project_cleanup(project["id"])

    def test_cleanup_preview_fails_closed_on_malformed_media_provenance(self):
        project = self.store.create_project("损坏来源信息")
        candidate = self.store.import_media(
            project["id"],
            b"malformed-provenance",
            "candidate.png",
            "image/png",
            source_info={"type": "online_generation", "lifecycle": "transient"},
        )
        index_path = self.root / project["id"] / "media" / "index.json"
        index = json.loads(index_path.read_text(encoding="utf-8"))
        index["items"][candidate["asset_id"]]["sources"].append("unknown-owner")
        index_path.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")

        with self.assertRaisesRegex(ProjectStoreError, "媒体来源"):
            self.store.preview_project_cleanup(project["id"])

    def test_cleanup_preview_preserves_media_with_user_owned_provenance(self):
        project = self.store.create_project("媒体所有权")
        transient = self.store.import_media(
            project["id"],
            b"shared-content",
            "failed-output.png",
            "image/png",
            source_info={"type": "online_generation", "lifecycle": "transient"},
        )
        imported = self.store.import_media(
            project["id"],
            b"shared-content",
            "user-import.png",
            "image/png",
            source_info={"type": "remote_url", "locator": "https://example.test/user.png"},
        )
        self.assertEqual(imported["asset_id"], transient["asset_id"])

        preview = self.store.preview_project_cleanup(project["id"])

        self.assertEqual(preview["candidate_count"], 0)
        self.assertEqual(preview["candidates"], [])
        self.assertTrue(Path(self.store.media_record(project["id"], transient["asset_id"])["path"]).is_file())

    def test_cleanup_preview_treats_the_persisted_canvas_as_a_recovery_owner(self):
        project = self.store.create_project("磁盘快照所有权")
        candidate = self.store.import_media(
            project["id"],
            b"persisted-canvas-owner",
            "persisted.png",
            "image/png",
            source_info={"type": "online_generation", "lifecycle": "transient"},
        )
        canvas_path = self.root / project["id"] / "canvas.json"
        persisted = json.loads(canvas_path.read_text(encoding="utf-8"))
        persisted["future_recovery_owner"] = candidate["asset_id"]
        canvas_path.write_text(json.dumps(persisted, ensure_ascii=False), encoding="utf-8")

        preview = self.store.preview_project_cleanup(project["id"])

        self.assertEqual(preview["candidate_count"], 0)

    def test_cleanup_preview_uses_the_current_in_memory_canvas_as_an_owner(self):
        project = self.store.create_project("尚未快照的内容")
        candidate = self.store.import_media(
            project["id"],
            b"current-memory-owner",
            "current.png",
            "image/png",
            source_info={"type": "online_generation", "lifecycle": "transient"},
        )
        self.store.apply_mutations(project["id"], [{
            "op": "upsert_node",
            "node": {
                "id": "current-result",
                "type": "generated_image",
                "images": [{"asset_id": candidate["asset_id"]}],
            },
        }], base_revision=0)

        preview = self.store.preview_project_cleanup(project["id"])

        self.assertEqual(preview["revision"], 1)
        self.assertEqual(preview["candidate_count"], 0)

    def test_cleanup_preview_fails_closed_on_conflicting_backup_ownership(self):
        project = self.store.create_project("冲突恢复所有权")
        self.store.import_media(
            project["id"],
            b"backup-conflict",
            "candidate.png",
            "image/png",
            source_info={"type": "online_generation", "lifecycle": "transient"},
        )
        project_dir = self.root / project["id"]
        backup = json.loads((project_dir / "canvas.json").read_text(encoding="utf-8"))
        backup["project_id"] = "another-project"
        (project_dir / "canvas.json.bak").write_text(
            json.dumps(backup, ensure_ascii=False),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ProjectStoreError, "恢复数据所有权"):
            self.store.preview_project_cleanup(project["id"])

    def test_cleanup_preview_treats_unknown_backup_reference_as_an_owner(self):
        project = self.store.create_project("恢复所有权")
        candidate = self.store.import_media(
            project["id"],
            b"recovery-owned",
            "recovery.png",
            "image/png",
            source_info={"type": "online_generation", "lifecycle": "transient"},
        )
        project_dir = self.root / project["id"]
        backup = json.loads((project_dir / "canvas.json").read_text(encoding="utf-8"))
        backup["future_recovery_owner"] = candidate["asset_id"]
        (project_dir / "canvas.json.bak").write_text(
            json.dumps(backup, ensure_ascii=False),
            encoding="utf-8",
        )

        preview = self.store.preview_project_cleanup(project["id"])

        self.assertEqual(preview["candidate_count"], 0)
        self.assertEqual(preview["candidates"], [])

    def test_cleanup_preview_treats_unknown_undo_snapshot_reference_as_an_owner(self):
        project = self.store.create_project("撤销所有权")
        candidate = self.store.import_media(
            project["id"],
            b"undo-owned",
            "undo.png",
            "image/png",
            source_info={"type": "online_generation", "lifecycle": "transient"},
        )
        undo_dir = self.root / project["id"] / "trash" / "undo"
        undo_dir.mkdir(parents=True, exist_ok=True)
        (undo_dir / "future-deletion.json").write_text(
            json.dumps({"future_undo_owner": candidate["asset_id"]}),
            encoding="utf-8",
        )
        trash_index_path = self.root / project["id"] / "trash" / "index.json"
        trash_index_path.write_text(json.dumps({"items": [{
            "asset_id": "deleted-owner",
            "undo": {"snapshot_filename": "future-deletion.json"},
        }]}), encoding="utf-8")

        preview = self.store.preview_project_cleanup(project["id"])

        self.assertEqual(preview["candidate_count"], 0)
        self.assertEqual(preview["candidates"], [])

    def test_cleanup_preview_preserves_generation_that_was_successfully_committed(self):
        project = self.store.create_project("成功生成所有权")
        media = self.store.import_media(
            project["id"],
            b"committed-generation",
            "committed.png",
            "image/png",
            source_info={"type": "online_generation", "lifecycle": "transient"},
        )
        output = {"asset_id": media["asset_id"], "kind": "image", "name": "committed.png"}
        self.store.complete_image_generation(
            project["id"],
            "result-node",
            [output],
            {
                "id": "generation-success",
                "type": "online-image",
                "status": "succeeded",
                "outputs": [output],
                "result_node_id": "result-node",
            },
        )
        self.store.flush(project["id"])
        # 删除最后一个节点属于有意清空，需显式 force_empty=True（空画布覆盖防护）。
        revision = self.store.open_project(project["id"])["canvas"]["revision"]
        self.store.apply_mutations(project["id"], [{"op": "remove_node", "node_id": "result-node"}], base_revision=revision, force_empty=True)
        revision = self.store.open_project(project["id"])["canvas"]["revision"]
        self.store.delete_history_record(
            project["id"],
            "generation-success",
            base_revision=revision,
        )
        self.store.flush(project["id"])
        self.store.flush(project["id"])

        preview = self.store.preview_project_cleanup(project["id"])

        self.assertEqual(preview["candidate_count"], 0)
        self.assertEqual(preview["candidates"], [])

    def test_cleanup_preview_fails_closed_on_unindexed_trash_media(self):
        project = self.store.create_project("未索引恢复媒体")
        self.store.import_media(
            project["id"],
            b"valid-candidate",
            "candidate.png",
            "image/png",
            source_info={"type": "online_generation", "lifecycle": "transient"},
        )
        trash_media = self.root / project["id"] / "trash" / "media"
        trash_media.mkdir()
        (trash_media / "unknown.png").write_bytes(b"unknown-recovery-owner")

        with self.assertRaisesRegex(ProjectStoreError, "恢复媒体"):
            self.store.preview_project_cleanup(project["id"])

    def test_cleanup_preview_fails_closed_while_original_media_transaction_exists(self):
        project = self.store.create_project("事务中项目")
        self.store.import_media(
            project["id"],
            b"transaction-owned",
            "transaction.png",
            "image/png",
            source_info={"type": "online_generation", "lifecycle": "transient"},
        )
        transaction_path = self.root / project["id"] / "trash" / firefly_projects.ORIGINAL_MEDIA_TRANSACTION_NAME
        transaction_path.write_text(
            json.dumps({
                "format": firefly_projects.ORIGINAL_MEDIA_TRANSACTION_FORMAT,
                "project_id": project["id"],
                "phase": "prepared",
            }),
            encoding="utf-8",
        )
        before = transaction_path.read_bytes()

        with self.assertRaisesRegex(ProjectStoreError, "事务尚未完成"):
            self.store.preview_project_cleanup(project["id"])

        self.assertEqual(transaction_path.read_bytes(), before)

    def test_cleanup_preview_fails_closed_on_unknown_media_directory_entry(self):
        project = self.store.create_project("媒体目录未知条目")
        self.store.import_media(
            project["id"],
            b"valid-candidate",
            "candidate.png",
            "image/png",
            source_info={"type": "online_generation", "lifecycle": "transient"},
        )
        media_root = self.root / project["id"] / "media"
        (media_root / "unexpected").mkdir()

        with self.assertRaisesRegex(ProjectStoreError, "媒体目录"):
            self.store.preview_project_cleanup(project["id"])

    def test_cleanup_preview_fails_closed_on_unindexed_media_file(self):
        project = self.store.create_project("未索引媒体")
        self.store.import_media(
            project["id"],
            b"valid-candidate",
            "candidate.png",
            "image/png",
            source_info={"type": "online_generation", "lifecycle": "transient"},
        )
        media_root = self.root / project["id"] / "media"
        (media_root / "unknown.bin").write_bytes(b"unknown-owner")

        with self.assertRaisesRegex(ProjectStoreError, "未索引媒体"):
            self.store.preview_project_cleanup(project["id"])

    def test_cleanup_preview_fails_closed_when_any_indexed_media_file_is_missing(self):
        project = self.store.create_project("媒体读取不完整")
        candidate = self.store.import_media(
            project["id"],
            b"candidate-present",
            "candidate.png",
            "image/png",
            source_info={"type": "online_generation", "lifecycle": "transient"},
        )
        owned = self.store.import_media(
            project["id"],
            b"owned-but-missing",
            "owned.png",
            "image/png",
            source_info={"type": "remote_url", "locator": "https://example.test/owned.png"},
        )
        Path(self.store.media_record(project["id"], owned["asset_id"])["path"]).unlink()

        with self.assertRaisesRegex(ProjectStoreError, "媒体读取不完整"):
            self.store.preview_project_cleanup(project["id"])

        self.assertTrue(Path(
            self.store.media_record(project["id"], candidate["asset_id"])["path"]
        ).is_file())

    def test_cleanup_preview_fails_closed_on_unknown_top_level_schema(self):
        project = self.store.create_project("未知顶层 schema")
        history_path = self.root / project["id"] / "history.json"
        history_path.write_text("[]", encoding="utf-8")

        with self.assertRaisesRegex(ProjectStoreError, "项目数据"):
            self.store.preview_project_cleanup(project["id"])

    def test_cleanup_preview_fails_closed_on_corrupt_history_without_mutating_project(self):
        project = self.store.create_project("损坏历史")
        self.store.import_media(
            project["id"],
            b"untrusted-candidate",
            "candidate.png",
            "image/png",
            source_info={"type": "online_generation", "lifecycle": "transient"},
        )
        project_dir = self.root / project["id"]
        (project_dir / "history.json").write_text("{not-json", encoding="utf-8")

        def tree_snapshot():
            return {
                path.relative_to(project_dir).as_posix(): (
                    path.stat().st_size,
                    path.stat().st_mtime_ns,
                    path.read_bytes() if path.is_file() else None,
                )
                for path in sorted(project_dir.rglob("*"))
            }

        before = tree_snapshot()
        with self.assertRaisesRegex(ProjectStoreError, "无法读取项目数据"):
            self.store.preview_project_cleanup(project["id"])
        self.assertEqual(tree_snapshot(), before)

    def test_cleanup_execute_permanently_deletes_exact_confirmed_candidates_without_recovery_writes(self):
        project = self.store.create_project("永久清理执行")
        transient = self.store.import_media(
            project["id"],
            b"transient-orphan",
            "orphan.png",
            "image/png",
            source_info={"type": "online_generation", "lifecycle": "transient"},
        )
        failed = self.store.import_media(
            project["id"],
            b"failed-orphan",
            "failed.png",
            "image/png",
            source_info={"type": "online_generation", "lifecycle": "failed"},
        )
        owned = self.store.import_media(
            project["id"],
            b"user-owned",
            "user.png",
            "image/png",
            source_info={"locator": "https://example.test/user.png", "type": "remote_url"},
        )
        preview = self.store.preview_project_cleanup(project["id"])
        self.assertEqual(
            [item["asset_id"] for item in preview["candidates"]],
            sorted([failed["asset_id"], transient["asset_id"]]),
        )
        project_dir = self.root / project["id"]

        def file_tree_snapshot():
            return {
                path.relative_to(project_dir).as_posix(): (
                    path.stat().st_size,
                    path.stat().st_mtime_ns,
                    path.read_bytes(),
                )
                for path in sorted(project_dir.rglob("*"))
                if path.is_file()
            }

        def directory_tree():
            return {
                path.relative_to(project_dir).as_posix()
                for path in sorted(project_dir.rglob("*"))
                if path.is_dir()
            }

        before_files = file_tree_snapshot()
        before_directories = directory_tree()

        result = self.store.execute_project_cleanup(
            project["id"],
            revision=preview["revision"],
            ownership_proof=preview["ownership_proof"],
            candidate_asset_ids=[item["asset_id"] for item in preview["candidates"]],
        )

        self.assertEqual(result["project_id"], project["id"])
        self.assertEqual(result["deleted_count"], 2)
        self.assertEqual(result["deleted_bytes"], len(b"transient-orphan") + len(b"failed-orphan"))
        self.assertTrue(result["complete"])
        self.assertEqual(result["failed"], [])
        self.assertEqual(
            sorted(item["asset_id"] for item in result["deleted"]),
            sorted([failed["asset_id"], transient["asset_id"]]),
        )
        after_files = file_tree_snapshot()
        changed = sorted(set(before_files) ^ set(after_files))
        self.assertEqual(
            changed,
            [
                f"media/{failed['asset_id']}.png",
                f"media/{transient['asset_id']}.png",
            ],
        )
        unchanged = set(before_files) & set(after_files)
        self.assertTrue(all(
            before_files[name] == after_files[name]
            for name in unchanged
            if name != "media/index.json"
        ))
        self.assertEqual(directory_tree(), before_directories)
        index = json.loads(
            (project_dir / "media" / "index.json").read_text(encoding="utf-8")
        )
        self.assertEqual(set(index["items"]), {owned["asset_id"]})
        self.assertFalse((project_dir / "media" / f"{failed['asset_id']}.png").exists())
        self.assertFalse((project_dir / "media" / f"{transient['asset_id']}.png").exists())
        self.assertTrue((project_dir / "media" / f"{owned['asset_id']}.png").is_file())
        self.assertFalse((project_dir / "trash" / "original-media-transaction.json").exists())
        with self.assertRaises(ProjectStoreError):
            self.store.media_record(project["id"], failed["asset_id"])
        # 再次提交同一确认：候选集合已经变化，必须要求重新预览。
        with self.assertRaisesRegex(firefly_projects.CleanupDrift, "重新预览"):
            self.store.execute_project_cleanup(
                project["id"],
                revision=preview["revision"],
                ownership_proof=preview["ownership_proof"],
                candidate_asset_ids=[item["asset_id"] for item in preview["candidates"]],
            )

    def test_cleanup_execute_rejects_revision_drift_without_deleting(self):
        project = self.store.create_project("版本漂移")
        candidate = self.store.import_media(
            project["id"],
            b"revision-drift",
            "drift.png",
            "image/png",
            source_info={"type": "online_generation", "lifecycle": "transient"},
        )
        preview = self.store.preview_project_cleanup(project["id"])
        self.apply_mutations(project["id"], [
            {"op": "upsert_node", "node": {"id": "drift", "type": "prompt", "x": 10, "y": 20}},
        ])

        with self.assertRaisesRegex(firefly_projects.CleanupDrift, "重新预览"):
            self.store.execute_project_cleanup(
                project["id"],
                revision=preview["revision"],
                ownership_proof=preview["ownership_proof"],
                candidate_asset_ids=[candidate["asset_id"]],
            )

        self.assertTrue((self.root / project["id"] / "media" / f"{candidate['asset_id']}.png").is_file())
        index = json.loads(
            (self.root / project["id"] / "media" / "index.json").read_text(encoding="utf-8")
        )
        self.assertIn(candidate["asset_id"], index["items"])

    def test_cleanup_execute_rejects_ownership_proof_drift_without_deleting(self):
        project = self.store.create_project("证明漂移")
        candidate = self.store.import_media(
            project["id"],
            b"proof-drift",
            "proof.png",
            "image/png",
            source_info={"type": "online_generation", "lifecycle": "transient"},
        )
        preview = self.store.preview_project_cleanup(project["id"])
        history_path = self.root / project["id"] / "history.json"
        payload = json.loads(history_path.read_text(encoding="utf-8"))
        payload["extra"] = "drift"
        history_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        with self.assertRaisesRegex(firefly_projects.CleanupDrift, "所有权证明"):
            self.store.execute_project_cleanup(
                project["id"],
                revision=preview["revision"],
                ownership_proof=preview["ownership_proof"],
                candidate_asset_ids=[candidate["asset_id"]],
            )

        self.assertTrue((self.root / project["id"] / "media" / f"{candidate['asset_id']}.png").is_file())
        index = json.loads(
            (self.root / project["id"] / "media" / "index.json").read_text(encoding="utf-8")
        )
        self.assertIn(candidate["asset_id"], index["items"])

    def test_cleanup_execute_rejects_candidate_set_drift_without_deleting(self):
        project = self.store.create_project("候选漂移")
        first = self.store.import_media(
            project["id"],
            b"first-candidate",
            "first.png",
            "image/png",
            source_info={"type": "online_generation", "lifecycle": "transient"},
        )
        preview = self.store.preview_project_cleanup(project["id"])
        second = self.store.import_media(
            project["id"],
            b"second-candidate",
            "second.png",
            "image/png",
            source_info={"type": "online_generation", "lifecycle": "failed"},
        )

        with self.assertRaisesRegex(firefly_projects.CleanupDrift, "重新预览"):
            self.store.execute_project_cleanup(
                project["id"],
                revision=preview["revision"],
                ownership_proof=preview["ownership_proof"],
                candidate_asset_ids=[first["asset_id"]],
            )

        for asset_id in (first["asset_id"], second["asset_id"]):
            self.assertTrue(
                (self.root / project["id"] / "media" / f"{asset_id}.png").is_file()
            )
        index = json.loads(
            (self.root / project["id"] / "media" / "index.json").read_text(encoding="utf-8")
        )
        self.assertEqual(set(index["items"]), {first["asset_id"], second["asset_id"]})

    def test_cleanup_execute_rejects_invalid_confirmation_shapes_without_deleting(self):
        project = self.store.create_project("无效确认")
        candidate = self.store.import_media(
            project["id"],
            b"invalid-confirmation",
            "candidate.png",
            "image/png",
            source_info={"type": "online_generation", "lifecycle": "transient"},
        )
        preview = self.store.preview_project_cleanup(project["id"])
        cases = [
            {"revision": None},
            {"revision": "0"},
            {"revision": -1},
            {"revision": True},
            {"ownership_proof": ""},
            {"ownership_proof": "not-a-proof"},
            {"ownership_proof": "A" * 64},
            {"candidate_asset_ids": None},
            {"candidate_asset_ids": "abc"},
            {"candidate_asset_ids": ["../outside.png"]},
            {"candidate_asset_ids": ["deadbeef"]},
            {"candidate_asset_ids": [candidate["asset_id"], candidate["asset_id"]]},
        ]
        for override in cases:
            confirmation = {
                "revision": preview["revision"],
                "ownership_proof": preview["ownership_proof"],
                "candidate_asset_ids": [candidate["asset_id"]],
            }
            confirmation.update(override)
            with self.subTest(override=override):
                with self.assertRaises(ProjectStoreError):
                    self.store.execute_project_cleanup(project["id"], **confirmation)
                self.assertTrue(
                    (self.root / project["id"] / "media" / f"{candidate['asset_id']}.png").is_file()
                )

    def test_cleanup_execute_reports_exact_partial_unlink_failure_without_claiming_deletion(self):
        project = self.store.create_project("部分删除失败")
        failing = self.store.import_media(
            project["id"],
            b"failing-unlink",
            "failing.png",
            "image/png",
            source_info={"type": "online_generation", "lifecycle": "transient"},
        )
        removed = self.store.import_media(
            project["id"],
            b"removed-unlink",
            "removed.png",
            "image/png",
            source_info={"type": "online_generation", "lifecycle": "failed"},
        )
        preview = self.store.preview_project_cleanup(project["id"])
        original_unlink = firefly_projects.os.unlink
        failing_name = f"{failing['asset_id']}.png"

        def selective_unlink(path, *args, **kwargs):
            if str(path).endswith(failing_name):
                raise OSError("injected unlink failure")
            return original_unlink(path, *args, **kwargs)

        with patch.object(firefly_projects.os, "unlink", side_effect=selective_unlink):
            result = self.store.execute_project_cleanup(
                project["id"],
                revision=preview["revision"],
                ownership_proof=preview["ownership_proof"],
                candidate_asset_ids=[failing["asset_id"], removed["asset_id"]],
            )

        self.assertFalse(result["complete"])
        self.assertEqual(result["deleted_count"], 1)
        self.assertEqual([item["asset_id"] for item in result["failed"]], [failing["asset_id"]])
        self.assertEqual([item["asset_id"] for item in result["deleted"]], [removed["asset_id"]])
        index = json.loads(
            (self.root / project["id"] / "media" / "index.json").read_text(encoding="utf-8")
        )
        self.assertEqual(set(index["items"]), {failing["asset_id"]})
        self.assertTrue((self.root / project["id"] / "media" / failing_name).is_file())
        self.assertFalse((self.root / project["id"] / "media" / f"{removed['asset_id']}.png").exists())

    def test_cleanup_execute_interrupted_deletion_fails_closed(self):
        project = self.store.create_project("中断删除")
        candidate = self.store.import_media(
            project["id"],
            b"interrupted-deletion",
            "interrupted.png",
            "image/png",
            source_info={"type": "online_generation", "lifecycle": "transient"},
        )
        preview = self.store.preview_project_cleanup(project["id"])
        # 模拟“文件已删除、索引尚未提交”的中断状态。
        (self.root / project["id"] / "media" / f"{candidate['asset_id']}.png").unlink()
        project_dir = self.root / project["id"]

        def tree_snapshot():
            return {
                path.relative_to(project_dir).as_posix(): (
                    path.stat().st_size,
                    path.stat().st_mtime_ns,
                    path.read_bytes() if path.is_file() else None,
                )
                for path in sorted(project_dir.rglob("*"))
            }

        before = tree_snapshot()
        with self.assertRaises(ProjectStoreError):
            self.store.execute_project_cleanup(
                project["id"],
                revision=preview["revision"],
                ownership_proof=preview["ownership_proof"],
                candidate_asset_ids=[candidate["asset_id"]],
            )
        self.assertEqual(tree_snapshot(), before)
        self.assertFalse((project_dir / "trash" / "original-media-transaction.json").exists())

    def test_cleanup_execute_preserves_owned_user_and_succeeded_generation_media(self):
        project = self.store.create_project("保留归属媒体")
        succeeded = self.store.import_media(
            project["id"],
            b"succeeded-generation",
            "succeeded.png",
            "image/png",
            source_info={"type": "online_generation", "lifecycle": "succeeded"},
        )
        user = self.store.import_media(
            project["id"],
            b"imported-by-user",
            "imported.png",
            "image/png",
            source_info={"locator": "https://example.test/imported.png", "type": "remote_url"},
        )
        preview = self.store.preview_project_cleanup(project["id"])
        self.assertEqual(preview["candidate_count"], 0)
        project_dir = self.root / project["id"]

        def file_tree_snapshot():
            return {
                path.relative_to(project_dir).as_posix(): (
                    path.stat().st_size,
                    path.stat().st_mtime_ns,
                    path.read_bytes(),
                )
                for path in sorted(project_dir.rglob("*"))
                if path.is_file()
            }

        before = file_tree_snapshot()
        result = self.store.execute_project_cleanup(
            project["id"],
            revision=preview["revision"],
            ownership_proof=preview["ownership_proof"],
            candidate_asset_ids=[],
        )

        self.assertTrue(result["complete"])
        self.assertEqual(result["deleted_count"], 0)
        self.assertEqual(result["deleted"], [])
        self.assertEqual(file_tree_snapshot(), before)
        for asset_id in (succeeded["asset_id"], user["asset_id"]):
            self.assertTrue((project_dir / "media" / f"{asset_id}.png").is_file())

    def test_create_project_removes_partial_directory_when_initialization_fails(self):
        original_write = firefly_projects._write_json_atomic
        write_count = 0

        def fail_during_initialization(path, payload, **kwargs):
            nonlocal write_count
            write_count += 1
            if write_count == 3:
                raise OSError("injected project initialization failure")
            return original_write(path, payload, **kwargs)

        with patch.object(firefly_projects, "_write_json_atomic", side_effect=fail_during_initialization):
            with self.assertRaisesRegex(OSError, "injected project initialization failure"):
                self.store.create_project("半成品")

        self.assertEqual(self.store.list_projects(), [])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_create_project_uuid_collision_does_not_delete_existing_project(self):
        existing = self.store.create_project("existing")
        metadata_path = self.root / existing["id"] / "project.json"
        original_metadata = metadata_path.read_bytes()
        collision = type("Collision", (), {"hex": existing["id"]})()

        with patch.object(firefly_projects.uuid, "uuid4", return_value=collision):
            with self.assertRaises(FileExistsError):
                self.store.create_project("collision")

        self.assertEqual(self.store.open_project(existing["id"])["project"]["name"], "existing")
        self.assertEqual(metadata_path.read_bytes(), original_metadata)
        self.assertEqual([item["id"] for item in self.store.list_projects()], [existing["id"]])

    def test_duplicate_project_preserves_durable_content_with_a_new_project_identity(self):
        source = self.store.create_project("原项目")
        referenced = self.store.import_media(
            source["id"],
            b"referenced-original",
            "referenced.png",
            "image/png",
            source_info={"locator": "https://example.test/reference.png?token=secret", "type": "remote_url"},
        )
        unreferenced = self.store.import_media(
            source["id"],
            b"unreferenced-original",
            "unreferenced.png",
            "image/png",
            source_info={"locator": "file:///D:/assets/unreferenced.png", "type": "external_path"},
        )
        self.apply_mutations(source["id"], [
            {"op": "upsert_node", "node": {"id": "prompt", "type": "prompt", "x": 120, "y": 80, "w": 280, "h": 180}},
            {"op": "upsert_node", "node": {"id": "image", "type": "image", "x": 520, "y": 140, "w": 320, "h": 240, "images": [{"asset_id": referenced["asset_id"]}]}},
            {"op": "upsert_edge", "edge": {"id": "edge-1", "from": "prompt", "to": "image"}},
            {"op": "set_canvas_meta", "history": [{"id": "log-1", "output_asset_id": referenced["asset_id"]}]},
        ])
        self.store.flush(source["id"])

        duplicate = self.store.duplicate_project(source["id"])

        self.assertNotEqual(duplicate["id"], source["id"])
        self.assertEqual(duplicate["id"], duplicate["canvas_id"])
        self.assertEqual(duplicate["name"], "原项目 副本")
        source_opened = self.store.open_project(source["id"])
        copied_opened = self.store.open_project(duplicate["id"])
        self.assertEqual(copied_opened["canvas"]["id"], duplicate["id"])
        self.assertEqual(copied_opened["canvas"]["project_id"], duplicate["id"])
        self.assertEqual(copied_opened["canvas"]["project"], duplicate["id"])
        self.assertEqual(copied_opened["canvas"]["title"], duplicate["name"])
        self.assertEqual(copied_opened["canvas"]["revision"], 0)
        self.assertEqual(copied_opened["canvas"]["nodes"], source_opened["canvas"]["nodes"])
        self.assertEqual(copied_opened["canvas"]["connections"], source_opened["canvas"]["connections"])
        self.assertEqual(copied_opened["canvas"]["history"], source_opened["canvas"]["history"])
        backup = json.loads((self.root / duplicate["id"] / "canvas.json.bak").read_text(encoding="utf-8"))
        self.assertEqual(backup["nodes"], source_opened["canvas"]["nodes"])
        self.assertEqual(backup["connections"], source_opened["canvas"]["connections"])
        self.assertEqual(
            {item["asset_id"] for item in self.store.list_media(duplicate["id"])},
            {referenced["asset_id"], unreferenced["asset_id"]},
        )
        for asset_id in (referenced["asset_id"], unreferenced["asset_id"]):
            original = self.store.media_record(source["id"], asset_id)
            copied = self.store.media_record(duplicate["id"], asset_id)
            self.assertEqual(copied["sha256"], original["sha256"])
            self.assertEqual(copied.get("source"), original.get("source"))
            self.assertNotEqual(copied["path"], original["path"])
            self.assertEqual(Path(copied["path"]).read_bytes(), Path(original["path"]).read_bytes())

        self.apply_mutations(duplicate["id"], [{"op": "remove_node", "node_id": "image"}])
        self.store.trash_project(duplicate["id"])
        self.assertEqual(len(self.store.open_project(source["id"])["canvas"]["nodes"]), 2)
        self.assertTrue(Path(self.store.media_record(source["id"], referenced["asset_id"])["path"]).is_file())
        self.store.restore_project(duplicate["id"])
        self.assertEqual(len(self.store.open_project(duplicate["id"])["canvas"]["nodes"]), 1)

    def test_duplicate_project_reserves_the_copy_suffix_at_the_name_limit(self):
        source = self.store.create_project("甲" * 60)

        duplicate = self.store.duplicate_project(source["id"])

        self.assertEqual(duplicate["name"], ("甲" * 57) + " 副本")
        self.assertEqual(len(duplicate["name"]), 60)

    def test_duplicate_project_does_not_copy_runtime_task_state(self):
        source = self.store.create_project("运行中项目")
        runtime_fields = {
            "pending": 2,
            "running": True,
            "queued": True,
            "pendingTasks": [{"taskId": "task-source", "kind": "image"}],
            "jimengPending": {"taskId": "jimeng-source"},
            "runStartedAt": 123,
            "runFinishedAt": 456,
            "runElapsedMs": 333,
            "runTimerHidden": True,
            "_runMetaTargetId": "output-source",
            "conversationSending": True,
            "agentPlanning": True,
            "agentExecuting": True,
        }
        creative_fields = {"id": "prompt", "type": "smart-prompt", "text": "保留创作内容", "x": 120, "y": 80}
        self.apply_mutations(source["id"], [
            {"op": "upsert_node", "node": {**creative_fields, **runtime_fields}},
        ])

        duplicate = self.store.duplicate_project(source["id"])

        source_node = self.store.open_project(source["id"])["canvas"]["nodes"][0]
        copied_node = self.store.open_project(duplicate["id"])["canvas"]["nodes"][0]
        for key, value in creative_fields.items():
            self.assertEqual(copied_node[key], value)
        for key in runtime_fields:
            self.assertIn(key, source_node)
            self.assertNotIn(key, copied_node)

    def test_duplicate_project_media_copy_does_not_block_other_projects(self):
        source = self.store.create_project("large source")
        other = self.store.create_project("other project")
        imported = self.store.import_media(source["id"], b"media", "source.png", "image/png")
        source_media_path = Path(self.store.media_record(source["id"], imported["asset_id"])["path"])
        copy_started = threading.Event()
        release_copy = threading.Event()
        duplicate_errors = []
        original_copyfile = firefly_projects.shutil.copyfile

        def slow_copy(source_path, target_path):
            if Path(source_path) == source_media_path:
                copy_started.set()
                if not release_copy.wait(2):
                    raise TimeoutError("test did not release duplicate media copy")
            return original_copyfile(source_path, target_path)

        def duplicate_source():
            try:
                self.store.duplicate_project(source["id"])
            except Exception as error:  # pragma: no cover - asserted below
                duplicate_errors.append(error)

        with patch.object(firefly_projects.shutil, "copyfile", side_effect=slow_copy):
            worker = threading.Thread(target=duplicate_source)
            worker.start()
            self.assertTrue(copy_started.wait(1))
            release_timer = threading.Timer(1, release_copy.set)
            release_timer.start()
            started = time.monotonic()
            try:
                self.apply_mutations(other["id"], [
                    {"op": "upsert_node", "node": {"id": "other-node", "type": "prompt"}},
                ])
                elapsed = time.monotonic() - started
            finally:
                release_timer.cancel()
                release_copy.set()
                worker.join(3)

        self.assertLess(elapsed, 0.5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(duplicate_errors, [])

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
        # 删除最后一个节点属于有意清空，需显式 force_empty=True（空画布覆盖防护）。
        self.store.apply_mutations(project["id"], [
            {"op": "remove_node", "node_id": "shared"},
        ], base_revision=1, force_empty=True)

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
            {"op": "set_canvas_meta", "history": [{"id": "log-1", "prompt": "萤火"}]},
        ])
        self.store.flush(project["id"])
        project_dir = self.root / project["id"]
        canvas_payload = json.loads((project_dir / "canvas.json").read_text(encoding="utf-8"))
        history_payload = json.loads((project_dir / "history.json").read_text(encoding="utf-8"))
        self.assertNotIn("history", canvas_payload)
        self.assertEqual(history_payload, {"records": [{"id": "log-1", "prompt": "萤火"}]})
        restarted = ProjectStore(self.root, snapshot_delay=0)
        try:
            self.assertEqual(restarted.open_project(project["id"])["canvas"]["history"], history_payload["records"])
        finally:
            restarted.close()

    def test_history_delete_uses_same_revision_cas_as_canvas_mutations(self):
        project = self.store.create_project("历史并发")
        self.apply_mutations(project["id"], [
            {"op": "set_canvas_meta", "history": [{"id": "log-1"}, {"id": "log-2"}]},
        ])
        deleted = self.store.delete_history_record(project["id"], "log-1", base_revision=1)
        self.assertEqual(deleted["revision"], 2)
        self.assertEqual(deleted["history"], [{"id": "log-2"}])
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
            {"op": "set_canvas_meta", "history": [{"id": "generation-b", "prompt": "乙（浏览器已同步）"}]},
        ])

        history = self.store.open_project(project["id"])["canvas"]["history"]
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
        self.assertEqual(failed["history"][-1]["id"], "failure-1")

    def test_generation_registration_uses_revision_cas_and_allows_same_node_overlap(self):
        project = self.store.create_project("生成并发")
        self.apply_mutations(project["id"], [
            {"op": "upsert_node", "node": {"id": "result", "type": "smart-image"}},
        ])
        started = self.store.begin_image_generation(
            project["id"], "result", {"taskId": "first", "count": 1}, base_revision=1,
        )
        with self.assertRaises(RevisionConflict):
            self.store.begin_image_generation(
                project["id"], "result", {"taskId": "stale", "count": 1}, base_revision=1,
            )
        overlapped = self.store.begin_image_generation(
            project["id"], "result", {"taskId": "overlap", "count": 1}, base_revision=int(started["revision"]),
        )
        result_node = next(item for item in overlapped["nodes"] if item["id"] == "result")
        self.assertEqual([task["taskId"] for task in result_node["pendingTasks"]], ["first", "overlap"])
        self.assertEqual(result_node["pending"], 2)

    def test_ordinary_node_upsert_cannot_drop_or_resurrect_server_owned_pending_tasks(self):
        project = self.store.create_project("并发任务身份由服务端持有")
        self.apply_mutations(project["id"], [
            {"op": "upsert_node", "node": {"id": "result", "type": "smart-image", "title": "initial"}},
            {"op": "upsert_node", "node": {"id": "other", "type": "smart-image", "title": "no task"}},
        ])
        first = self.store.begin_image_generation(
            project["id"], "result", {"taskId": "first", "count": 3, "requestPayload": {"prompt": "server first"}, "route": {"model": "server-model"}}, base_revision=1,
        )
        overlapped = self.store.begin_image_generation(
            project["id"], "result", {"taskId": "second", "count": 1, "requestPayload": {"prompt": "server second"}, "route": {"model": "server-model"}}, base_revision=int(first["revision"]),
        )

        # A stale browser only knows about second. Its ordinary content save
        # cannot erase first, which may already be executing at the provider.
        saved = self.store.apply_mutations(
            project["id"],
            [{"op": "upsert_node", "node": {"id": "result", "type": "smart-image", "title": "client edit", "pendingTasks": [{"taskId": "second", "count": 99, "requestPayload": {"prompt": "tampered"}, "route": {"model": "tampered"}}, {"taskId": "invented", "count": 50}]}}],
            base_revision=int(overlapped["revision"]),
        )
        node = next(item for item in self.store.open_project(project["id"])["canvas"]["nodes"] if item["id"] == "result")
        self.assertEqual([item["taskId"] for item in node["pendingTasks"]], ["first", "second"])
        self.assertEqual(node["pending"], 4)
        second_task = next(item for item in node["pendingTasks"] if item["taskId"] == "second")
        self.assertEqual(second_task["count"], 1)
        self.assertEqual(second_task["requestPayload"]["prompt"], "server second")
        self.assertEqual(second_task["route"]["model"], "server-model")

        # A node with no server task cannot invent a ghost through an ordinary
        # canvas save either.
        injected = self.store.apply_mutations(
            project["id"],
            [
                {"op": "upsert_node", "node": {"id": "other", "type": "smart-image", "title": "edited", "pendingTasks": [{"taskId": "ghost", "count": 7}]}},
                {"op": "upsert_node", "node": {"id": "result", "title": "type omitted", "pendingTasks": []}},
            ],
            base_revision=int(saved["revision"]),
        )
        opened_after_injection = self.store.open_project(project["id"])["canvas"]
        other = next(item for item in opened_after_injection["nodes"] if item["id"] == "other")
        protected = next(item for item in opened_after_injection["nodes"] if item["id"] == "result")
        self.assertNotIn("pendingTasks", other)
        self.assertEqual(other.get("pending"), 0)
        self.assertEqual(protected["type"], "smart-image")
        self.assertEqual([item["taskId"] for item in protected["pendingTasks"]], ["first", "second"])

        changed_type = self.store.apply_mutations(
            project["id"],
            [{"op": "upsert_node", "node": {"id": "result", "type": "smart-prompt", "title": "type tampered", "pendingTasks": [{"taskId": "invented"}]}}],
            base_revision=int(injected["revision"]),
        )
        protected = next(item for item in self.store.open_project(project["id"])["canvas"]["nodes"] if item["id"] == "result")
        self.assertEqual(protected["type"], "smart-image")
        self.assertEqual([item["taskId"] for item in protected["pendingTasks"]], ["first", "second"])

        forged_terminal = self.store.apply_mutations(
            project["id"],
            [
                {"op": "set_canvas_meta", "history": [{"id": "forged-success", "task_id": "second", "status": "succeeded"}]},
                {"op": "upsert_node", "node": {"id": "result", "type": "smart-image", "title": "forged terminal attempted"}},
            ],
            base_revision=int(changed_type["revision"]),
        )
        opened_after_forgery = self.store.open_project(project["id"])["canvas"]
        protected = next(item for item in opened_after_forgery["nodes"] if item["id"] == "result")
        self.assertEqual([item["taskId"] for item in protected["pendingTasks"]], ["first", "second"])
        self.assertFalse(any(item.get("id") == "forged-success" for item in opened_after_forgery["history"]))

        cancelled = self.store.cancel_image_generation(
            project["id"],
            "result",
            "first",
            {"id": "cancel-first", "task_id": "first", "status": "cancelled", "error": "cancelled"},
        )
        # A later stale upsert cannot resurrect the terminal first identity.
        self.store.apply_mutations(
            project["id"],
            [{"op": "upsert_node", "node": {"id": "result", "type": "smart-image", "title": "later edit", "pendingTasks": [{"taskId": "first", "count": 3}, {"taskId": "second", "count": 1}]}}],
            base_revision=int(cancelled["revision"]),
        )
        node = next(item for item in self.store.open_project(project["id"])["canvas"]["nodes"] if item["id"] == "result")
        self.assertEqual([item["taskId"] for item in node["pendingTasks"]], ["second"])
        self.assertEqual(node["pending"], 1)
        self.assertEqual(node["title"], "later edit")

    def test_overlapping_generation_completions_form_one_result_group(self):
        project = self.store.create_project("并发生图分组")
        old = self.store.import_media(project["id"], b"old", "old.png", "image/png")
        first = self.store.import_media(project["id"], b"first", "first.png", "image/png")
        second = self.store.import_media(project["id"], b"second", "second.png", "image/png")
        self.apply_mutations(project["id"], [{
            "op": "upsert_node",
            "node": {
                "id": "result",
                "type": "smart-image",
                "images": [{"asset_id": old["asset_id"], "kind": "image"}],
            },
        }])
        started = self.store.begin_image_generation(
            project["id"], "result", {"taskId": "first", "count": 1}, base_revision=1,
        )
        self.store.begin_image_generation(
            project["id"], "result", {"taskId": "second", "count": 1}, base_revision=int(started["revision"]),
        )

        after_second = self.store.complete_image_generation(
            project["id"],
            "result",
            [{"asset_id": second["asset_id"], "kind": "image"}],
            {"id": "generation-second", "task_id": "second", "status": "succeeded"},
            task_id="second",
        )
        result_node = next(item for item in after_second["nodes"] if item["id"] == "result")
        self.assertEqual([item["asset_id"] for item in result_node["images"]], [second["asset_id"]])
        self.assertEqual([task["taskId"] for task in result_node["pendingTasks"]], ["first"])
        self.assertEqual(result_node["pending"], 1)

        completed = self.store.complete_image_generation(
            project["id"],
            "result",
            [{"asset_id": first["asset_id"], "kind": "image"}],
            {"id": "generation-first", "task_id": "first", "status": "succeeded"},
            task_id="first",
        )
        result_node = next(item for item in completed["nodes"] if item["id"] == "result")
        self.assertEqual(
            [item["asset_id"] for item in result_node["images"]],
            [second["asset_id"], first["asset_id"]],
        )
        self.assertNotIn("pendingTasks", result_node)
        self.assertEqual(result_node["pending"], 0)
        self.assertEqual(result_node["title"], "Group")

    def test_overlapping_generation_completions_keep_distinct_occurrences_for_shared_asset(self):
        project = self.store.create_project("共享原件输出项")
        shared = self.store.import_media(
            project["id"],
            b"identical-generated-bytes",
            "shared.png",
            "image/png",
            {"type": "online_generation", "name": "shared.png"},
        )
        self.apply_mutations(project["id"], [
            {"op": "upsert_node", "node": {"id": "result", "type": "smart-image"}},
        ])
        started = self.store.begin_image_generation(
            project["id"], "result", {"taskId": "first", "count": 1}, base_revision=1,
        )
        self.store.begin_image_generation(
            project["id"], "result", {"taskId": "second", "count": 1},
            base_revision=int(started["revision"]),
        )

        for task_id in ("second", "first"):
            self.store.complete_image_generation(
                project["id"],
                "result",
                [{"asset_id": shared["asset_id"], "kind": "image"}],
                {
                    "id": f"generation-{task_id}",
                    "task_id": task_id,
                    "status": "succeeded",
                    "outputs": [{"asset_id": shared["asset_id"], "kind": "image"}],
                    "result_node_id": "result",
                },
                task_id=task_id,
            )

        completed = self.store.canvas(project["id"])
        result_node = next(node for node in completed["nodes"] if node["id"] == "result")
        node_output_ids = [image["output_id"] for image in result_node["images"]]
        history_output_ids = [
            record["outputs"][0]["output_id"]
            for record in completed["history"]
        ]
        self.assertEqual(
            [image["asset_id"] for image in result_node["images"]],
            [shared["asset_id"], shared["asset_id"]],
        )
        self.assertEqual(len(set(node_output_ids)), 2)
        self.assertEqual(node_output_ids, history_output_ids)

        selected_record = completed["history"][0]
        deleted = self.store.delete_generated_original(
            project["id"],
            shared["asset_id"],
            base_revision=completed["revision"],
            history_id=selected_record["id"],
            output_id=selected_record["outputs"][0]["output_id"],
            output_index=0,
        )["canvas"]
        remaining_node = next(node for node in deleted["nodes"] if node["id"] == "result")
        remaining_records = [record for record in deleted["history"] if record.get("outputs")]
        self.assertEqual(len(remaining_node["images"]), 1)
        self.assertEqual(len(remaining_records), 1)
        self.assertEqual(
            remaining_node["images"][0]["output_id"],
            remaining_records[0]["outputs"][0]["output_id"],
        )

    def test_invalid_batch_does_not_partially_change_history(self):
        project = self.store.create_project("事务边界")
        with self.assertRaises(InvalidMutation):
            self.apply_mutations(project["id"], [
                {"op": "upsert_node", "node": {"id": "must-not-stick"}},
                {"op": "set_canvas_meta", "history": [{"id": "must-not-stick"}]},
                {"op": "unsupported"},
            ])
        opened = self.store.open_project(project["id"])["canvas"]
        self.assertEqual(opened["nodes"], [])
        self.assertEqual(opened["history"], [])

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
        self.assertEqual(opened["history"], [{"id": "h1"}])
        self.assertEqual((self.root / imported["id"] / "media" / "asset-1.png").read_bytes(), b"synthetic-media")

    def test_utf8_roundtrip_preserves_chinese_project_names(self):
        """UTF-8 全链路回归（H35/H39 历史乱码排查，commit 93c6e7a 后续）。

        覆盖 create_project → set_canvas_meta 改名 → flush 持久化 → .fly 导出 → 导入。
        防止未来任何环节引入 GBK/latin-1 等编码回归。
        """
        # 1. 创建中文名项目
        original = "H39重力理疗垫·终极版（测试）"
        project = self.store.create_project(original)
        self.assertEqual(project["name"], original)
        # 2. 通过 set_canvas_meta mutation 改名
        revision = self.store.open_project(project["id"])["canvas"]["revision"]
        renamed = "改名后的测试·第二版"
        self.store.apply_mutations(
            project["id"],
            [{"op": "set_canvas_meta", "title": renamed}],
            base_revision=revision,
        )
        self.store.flush(project["id"])
        # 3. 磁盘持久化字节级验证
        metadata = json.loads((self.root / project["id"] / "project.json").read_bytes().decode("utf-8"))
        canvas = json.loads((self.root / project["id"] / "canvas.json").read_bytes().decode("utf-8"))
        self.assertEqual(metadata["name"], renamed)
        self.assertEqual(canvas["title"], renamed)
        # 4. .fly 导出 → 导入 roundtrip
        export_path = self.root.parent / f"{project['id']}.fly"
        self.store.export_fly_to(project["id"], export_path)
        raw = export_path.read_bytes()
        imported = self.store.import_fly(raw)
        imported_meta = json.loads((self.root / imported["id"] / "project.json").read_bytes().decode("utf-8"))
        imported_canvas = json.loads((self.root / imported["id"] / "canvas.json").read_bytes().decode("utf-8"))
        self.assertEqual(imported_meta["name"], renamed)
        self.assertEqual(imported_canvas["title"], renamed)
        export_path.unlink(missing_ok=True)

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
            {"op": "set_canvas_meta", "history": [{"id": "history-1", "outputs": [{"asset_id": media["asset_id"]}]}]},
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
        node_image = reopened["nodes"][0]["images"][0]
        history_output = reopened["history"][0]["outputs"][0]
        self.assertEqual(
            {key: value for key, value in node_image.items() if key not in {"output_id", "output_order"}},
            {"asset_id": media["asset_id"]},
        )
        self.assertEqual(
            {key: value for key, value in history_output.items() if key not in {"output_id", "output_order"}},
            {"asset_id": media["asset_id"]},
        )
        self.assertRegex(history_output["output_id"], r"^output-[a-f0-9]{32}$")
        self.assertEqual(history_output["output_order"], 0)
        self.assertNotIn("output_id", node_image)
        self.assertNotIn("output_order", node_image)
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

    def test_atomic_snapshot_rotates_backups_and_recovers_corrupt_primary(self):
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

        # ADR 0018：create(r0) + 两次 flush 后轮转出 .bak(r1) 与 .bak-2(r0)。
        self.assertTrue(primary.exists())
        self.assertTrue(backup.exists())
        self.assertEqual(sorted(p.name for p in project_dir.glob("canvas.json.bak*")), ["canvas.json.bak", "canvas.json.bak-2"])
        primary.write_text("{broken", encoding="utf-8")

        restarted = ProjectStore(self.root, snapshot_delay=0)
        try:
            recovered = restarted.open_project(project["id"])["canvas"]
        finally:
            restarted.close()
        self.assertEqual([node["id"] for node in recovered["nodes"]], ["kept"])
        json.loads(primary.read_text(encoding="utf-8"))

    def test_canvas_backup_rotation_keeps_three_newest_states(self):
        project = self.store.create_project("轮转测试")
        project_dir = self.root / project["id"]
        for index in range(1, 5):
            self.apply_mutations(project["id"], [
                {"op": "upsert_node", "node": {"id": f"n{index}", "x": index, "y": index}},
            ])
            self.store.flush(project["id"])
        files = {p.name: json.loads(p.read_text(encoding="utf-8")) for p in project_dir.glob("canvas.json.bak*")}
        self.assertEqual(set(files), {"canvas.json.bak", "canvas.json.bak-2", "canvas.json.bak-3"})
        self.assertEqual([node["id"] for node in files["canvas.json.bak"]["nodes"]], ["n1", "n2", "n3"])
        self.assertEqual([node["id"] for node in files["canvas.json.bak-2"]["nodes"]], ["n1", "n2"])
        self.assertEqual([node["id"] for node in files["canvas.json.bak-3"]["nodes"]], ["n1"])

    def test_recovery_falls_back_through_rotated_backups(self):
        project = self.store.create_project("回退链测试")
        project_dir = self.root / project["id"]
        for index in range(1, 5):
            self.apply_mutations(project["id"], [
                {"op": "upsert_node", "node": {"id": f"n{index}", "x": index, "y": index}},
            ])
            self.store.flush(project["id"])
        (project_dir / "canvas.json.bak").write_text("{broken", encoding="utf-8")
        (project_dir / "canvas.json").write_text("{broken", encoding="utf-8")
        restarted = ProjectStore(self.root, snapshot_delay=0)
        try:
            recovered = restarted.open_project(project["id"])["canvas"]
        finally:
            restarted.close()
        self.assertEqual([node["id"] for node in recovered["nodes"]], ["n1", "n2"])

    def test_restore_from_backup_quarantines_corrupt_primary(self):
        project = self.store.create_project("恢复入口测试")
        project_dir = self.root / project["id"]
        primary = project_dir / "canvas.json"
        self.apply_mutations(project["id"], [
            {"op": "upsert_node", "node": {"id": "before", "x": 1, "y": 2}},
        ])
        self.store.flush(project["id"])
        self.apply_mutations(project["id"], [
            {"op": "upsert_node", "node": {"id": "after", "x": 3, "y": 4}},
        ])
        self.store.flush(project["id"])
        primary.write_text("{broken", encoding="utf-8")

        entries = self.store.list_recovery_backups(project["id"])
        by_file = {item["file"]: item for item in entries}
        self.assertTrue(by_file["canvas.json"]["primary"])
        self.assertTrue(by_file["canvas.json"]["exists"])
        self.assertFalse(by_file["canvas.json"]["valid"])
        self.assertTrue(by_file["canvas.json.bak"]["valid"])
        self.assertTrue(by_file["project.json"]["primary"])
        self.assertTrue(by_file["project.json"]["valid"])

        result = self.store.restore_from_backup(project["id"], "canvas.json")
        self.assertEqual(result["restored_from"], "canvas.json.bak")
        self.assertTrue(result["quarantined"].startswith("canvas.json.corrupt-"))
        recovered = self.store.open_project(project["id"])["canvas"]
        self.assertEqual([node["id"] for node in recovered["nodes"]], ["before"])
        self.assertEqual(json.loads(primary.read_text(encoding="utf-8"))["nodes"][0]["id"], "before")
        self.assertEqual((project_dir / result["quarantined"]).read_text(encoding="utf-8"), "{broken")

    def test_restore_from_backup_selects_explicit_slot_and_rejects_invalid(self):
        project = self.store.create_project("显式槽位测试")
        project_dir = self.root / project["id"]
        for index in range(1, 5):
            self.apply_mutations(project["id"], [
                {"op": "upsert_node", "node": {"id": f"n{index}", "x": index, "y": index}},
            ])
            self.store.flush(project["id"])
        (project_dir / "canvas.json").write_text("{broken", encoding="utf-8")
        result = self.store.restore_from_backup(project["id"], "canvas.json", backup="canvas.json.bak-2")
        self.assertEqual(result["restored_from"], "canvas.json.bak-2")
        recovered = self.store.open_project(project["id"])["canvas"]
        self.assertEqual([node["id"] for node in recovered["nodes"]], ["n1", "n2"])

        with self.assertRaisesRegex(ProjectStoreError, "不支持恢复该文件"):
            self.store.restore_from_backup(project["id"], "history.json")

    def test_restore_from_backup_fails_without_any_valid_copy(self):
        project = self.store.create_project("无备份测试")
        project_dir = self.root / project["id"]
        (project_dir / "canvas.json").write_text("{broken", encoding="utf-8")
        for name in ("canvas.json.bak", "canvas.json.bak-2", "canvas.json.bak-3"):
            (project_dir / name).write_text("{broken", encoding="utf-8")
        with self.assertRaisesRegex(ProjectStoreError, "没有可读取的备份"):
            self.store.restore_from_backup(project["id"], "canvas.json")

    def test_metadata_corruption_recovers_from_single_backup(self):
        project = self.store.create_project("元数据恢复")
        project_dir = self.root / project["id"]
        self.apply_mutations(project["id"], [
            {"op": "upsert_node", "node": {"id": "n1", "x": 1, "y": 2}},
        ])
        self.store.flush(project["id"])
        metadata_path = project_dir / "project.json"
        backup = json.loads((project_dir / "project.json.bak").read_text(encoding="utf-8"))
        self.assertEqual(backup["id"], project["id"])
        metadata_path.write_text("{broken", encoding="utf-8")
        restarted = ProjectStore(self.root, snapshot_delay=0)
        try:
            opened = restarted.open_project(project["id"])
        finally:
            restarted.close()
        self.assertEqual(opened["project"]["id"], project["id"])
        json.loads(metadata_path.read_text(encoding="utf-8"))

    def test_open_project_reports_recovery_mark_until_next_flush(self):
        """issue #94/ADR 0018：自动回退后 open_project 携带 _recovery 标记（不持久化），flush 后清除。"""
        project = self.store.create_project("回退标记")
        self.apply_mutations(project["id"], [{"op": "upsert_node", "node": {"id": "n1", "x": 1, "y": 2}}])
        self.store.flush(project["id"])
        canvas_path = self.root / project["id"] / "canvas.json"
        canvas_path.write_text("{broken", encoding="utf-8")
        restarted = ProjectStore(self.root, snapshot_delay=0)
        try:
            mark = restarted.open_project(project["id"])["canvas"].pop("_recovery", None)
            self.assertIsNotNone(mark)
            self.assertEqual(mark["file"], "canvas.json")
            self.assertEqual(mark["restored_from"], "canvas.json.bak")
            self.assertNotIn("_recovery", json.loads(canvas_path.read_text(encoding="utf-8")))
            restarted.flush(project["id"])
            self.assertNotIn("_recovery", restarted.open_project(project["id"])["canvas"])
        finally:
            restarted.close()

    def test_restore_from_backup_clears_recovery_mark(self):
        """issue #94/ADR 0018：从备份恢复成功后清除 _recovery 标记。"""
        project = self.store.create_project("恢复清标记")
        self.apply_mutations(project["id"], [{"op": "upsert_node", "node": {"id": "n1", "x": 1, "y": 2}}])
        self.store.flush(project["id"])
        canvas_path = self.root / project["id"] / "canvas.json"
        canvas_path.write_text("{broken", encoding="utf-8")
        restarted = ProjectStore(self.root, snapshot_delay=0)
        try:
            self.assertIn("_recovery", restarted.open_project(project["id"])["canvas"])
            restarted.restore_from_backup(project["id"], "canvas.json", "canvas.json.bak")
            self.assertNotIn("_recovery", restarted.open_project(project["id"])["canvas"])
        finally:
            restarted.close()

    def test_photoshop_round_trip_table_save_load_roundtrip(self):
        """issue #95：往返请求表落盘/读取/空表删文件。"""
        project = self.store.create_project("PS 表")
        table = {"req-1": {"request_id": "req-1", "project_id": project["id"], "state": "pending"}}
        self.store.save_photoshop_round_trips(project["id"], table)
        path = self.root / project["id"] / "photoshop-round-trips.json"
        self.assertTrue(path.exists())
        self.assertEqual(self.store.load_photoshop_round_trips(project["id"]), table)
        self.store.save_photoshop_round_trips(project["id"], {})
        self.assertFalse(path.exists())
        self.assertEqual(self.store.load_photoshop_round_trips(project["id"]), {})

    def test_photoshop_round_trip_table_corrupt_is_quarantined(self):
        """issue #95：损坏的表文件被隔离为 .corrupt-<ts>，读取返回空表。"""
        project = self.store.create_project("PS 表损坏")
        path = self.root / project["id"] / "photoshop-round-trips.json"
        path.write_text("{broken", encoding="utf-8")
        self.assertEqual(self.store.load_photoshop_round_trips(project["id"]), {})
        self.assertFalse(path.exists())
        self.assertTrue(any(
            p.name.startswith("photoshop-round-trips.json.corrupt-")
            for p in (self.root / project["id"]).iterdir()
        ))

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


    def test_failed_media_generation_clears_pending_and_records_history(self):
        project = self.store.create_project("生成失败回滚")
        self.apply_mutations(project["id"], [
            {"op": "upsert_node", "node": {"id": "out", "type": "smart-image", "x": 1, "y": 2, "title": "Out", "images": []}},
        ])
        revision = self.store.open_project(project["id"])["canvas"]["revision"]
        task = {"taskId": "task-fail", "count": 1}
        self.store.begin_media_generation(project["id"], "out", task, base_revision=revision, media_kind="image")
        opened = self.store.open_project(project["id"])["canvas"]
        node = next(item for item in opened["nodes"] if item["id"] == "out")
        self.assertEqual(node.get("pending"), 1)
        self.assertTrue(node.get("pendingTasks"))
        result = self.store.fail_media_generation(
            project["id"], "out", "task-fail",
            {"id": "history-fail", "type": "agent-image", "status": "failed", "error": "boom"},
        )
        node = next(item for item in result["nodes"] if item["id"] == "out")
        self.assertFalse(node.get("pendingTasks"))
        self.assertEqual(node.get("pending"), 0)
        self.assertIs(node.get("running"), False)
        history = result.get("history") or []
        self.assertTrue(any(item.get("id") == "history-fail" and item.get("status") == "failed" for item in history))

    def test_delete_media_if_unreferenced_and_purge_deleted_media(self):
        project = self.store.create_project("回收站清理")
        media = self.store.import_media(project["id"], b"png-bytes", "x.png", "image/png")
        self.apply_mutations(project["id"], [
            {"op": "upsert_node", "node": {"id": "n1", "type": "image", "x": 1, "y": 2, "images": [{"asset_id": media["asset_id"], "name": "x.png"}]}},
        ])
        self.assertEqual(self.store.delete_media_if_unreferenced(project["id"], media["asset_id"]), False)
        revision = self.store.open_project(project["id"])["canvas"]["revision"]
        self.apply_mutations(project["id"], [
            {"op": "upsert_node", "node": {"id": "n1", "type": "image", "x": 1, "y": 2, "images": []}},
        ])
        self.assertEqual(self.store.delete_media_if_unreferenced(project["id"], media["asset_id"]), True)
        self.assertEqual(self.store.list_media(project["id"]), [])
        self.assertEqual(self.store.purge_deleted_media(project["id"]), 0)

        trash_media = self.store.import_media(
            project["id"],
            b"png-bytes-2",
            "trash.png",
            "image/png",
            {"type": "online_generation", "name": "trash.png"},
        )
        revision = self.store.open_project(project["id"])["canvas"]["revision"]
        self.apply_mutations(project["id"], [
            {"op": "upsert_node", "node": {"id": "n2", "type": "smart-image", "x": 3, "y": 4, "images": [{"asset_id": trash_media["asset_id"], "name": "trash.png"}]}},
        ])
        revision = self.store.open_project(project["id"])["canvas"]["revision"]
        self.store.delete_generated_original_project_wide(
            project["id"], trash_media["asset_id"], base_revision=revision,
        )
        deleted = self.store.list_deleted_media(project["id"])
        self.assertTrue(any(item["asset_id"] == trash_media["asset_id"] for item in deleted))
        self.assertEqual(self.store.purge_deleted_media(project["id"], trash_media["asset_id"]), 1)
        remaining = self.store.list_deleted_media(project["id"])
        self.assertFalse(any(item["asset_id"] == trash_media["asset_id"] for item in remaining))


    def test_safe_project_path_survives_concurrent_parent_creation_prefix_race(self):
        """#194: 父目录在两次 C 级查询之间被并发创建时，候选路径仍须通过包含检查。

        ``Path.resolve(strict=False)``（``ntpath.realpath``）对不存在的路径先做严格
        查询记录 ``initial_winerror``，最后用去前缀路径再查询一次，仅当两次 winerror
        一致才剥离 ``\\\\?\\`` 扩展前缀。质量门里 worker 线程并发创建
        ``.conversation-tasks`` 目录时，父目录缺席→存在使 winerror 由 3 变为 2
        （本机实测），前缀残留，``os.path.commonpath`` 把 ``\\\\?\\c:`` 当作与
        ``c:`` 不同的盘而抛出 ``ValueError: Paths don't have the same drive``。
        本测试用与实测一致的 winerror 序列（先 3 后 2）确定性地重建该竞态。
        """
        import ntpath
        if not hasattr(ntpath, "_getfinalpathname"):
            self.skipTest("ntpath._getfinalpathname 不可用")
        real_getfinalpathname = ntpath._getfinalpathname
        project = self.store.create_project("前缀竞态回归")
        project_dir = self.store._project_directory(project["id"])
        relative = ".conversation-tasks/" + "a" * 64 + ".json"
        target = os.path.normcase(str(project_dir / relative))
        state = {"parent_created": False}

        def racing_getfinalpathname(path):
            key = str(path)
            if key.startswith("\\\\?\\"):
                key = key[4:]
            if os.path.normcase(key) == target:
                winerror = 2 if state["parent_created"] else 3
                state["parent_created"] = True
                exc = OSError(winerror, "模拟并发父目录状态")
                exc.winerror = winerror
                raise exc
            return real_getfinalpathname(path)

        def strip_extended_prefix(path_text):
            normalized = os.path.normcase(str(path_text))
            return normalized[4:] if normalized.startswith("\\\\?\\") else normalized

        expected = strip_extended_prefix(project_dir / relative)
        with patch("ntpath._getfinalpathname", side_effect=racing_getfinalpathname):
            result = self.store._safe_project_path(project_dir, relative)
        self.assertEqual(strip_extended_prefix(result), expected)
        self.assertTrue(state["parent_created"], "竞态模拟未被触发")


class GenerationHistoryMergeAndDeletionOwnershipTests(unittest.TestCase):
    """#83 历史累加合并 / #79 孤儿回收区 / #63 统一归属判定。"""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name) / "projects"
        self.store = ProjectStore(self.root, snapshot_delay=0)

    def tearDown(self):
        self.temp_dir.cleanup()

    def apply_mutations(self, project_id, mutations):
        revision = self.store.open_project(project_id)["canvas"]["revision"]
        return self.store.apply_mutations(project_id, mutations, base_revision=revision)

    def _start_batch(self, project_id, node_id, task_id, count):
        self.apply_mutations(project_id, [
            {"op": "upsert_node", "node": {"id": node_id, "type": "smart-image"}},
        ])
        return self.store.begin_image_generation(
            project_id, node_id, {"taskId": task_id, "count": count}, base_revision=1,
        )

    def _partial(self, project_id, node_id, task_id, history_id, media, index):
        return self.store.complete_image_generation(
            project_id, node_id,
            [{"asset_id": media["asset_id"], "kind": "image"}],
            {
                "id": history_id,
                "task_id": task_id,
                "status": "running",
                "outputs": [{"asset_id": media["asset_id"], "kind": "image"}],
                "result_node_id": node_id,
            },
            task_id=task_id,
            require_pending=True,
            partial=True,
        )

    def test_partial_history_records_accumulate_outputs_instead_of_replacing(self):
        """#83: 4 张生 3 张卡 1 张——历史记录必须保留全部 3 张完成图（此前永远只剩最后 1 张）。"""
        project = self.store.create_project("partial 历史累加")
        a = self.store.import_media(project["id"], b"img-a", "a.png", "image/png")
        b = self.store.import_media(project["id"], b"img-b", "b.png", "image/png")
        c = self.store.import_media(project["id"], b"img-c", "c.png", "image/png")
        self._start_batch(project["id"], "result", "task-1", 4)
        history_id = "generation-run-1"
        for media in (a, b, c):
            self._partial(project["id"], "result", "task-1", history_id, media, None)

        opened = self.store.open_project(project["id"])["canvas"]
        record = next(item for item in opened["history"] if item["id"] == history_id)
        self.assertEqual(
            [output["asset_id"] for output in record["outputs"]],
            [a["asset_id"], b["asset_id"], c["asset_id"]],
        )
        self.assertEqual(record["status"], "running")
        node = next(item for item in opened["nodes"] if item["id"] == "result")
        self.assertEqual(
            [img["asset_id"] for img in node["images"]],
            [a["asset_id"], b["asset_id"], c["asset_id"]],
        )
        # 剩余占位保留（任务未结束，pendingTasks 仍在）
        self.assertTrue(any(t.get("taskId") == "task-1" for t in node["pendingTasks"]))

    def test_final_completion_merges_with_partials_without_duplicates(self):
        """#83: final 全量写回与 partial 累加合并后按 asset_id 去重，不产生重复图。"""
        project = self.store.create_project("final 合并去重")
        a = self.store.import_media(project["id"], b"img-a", "a.png", "image/png")
        b = self.store.import_media(project["id"], b"img-b", "b.png", "image/png")
        self._start_batch(project["id"], "result", "task-1", 2)
        history_id = "generation-run-1"
        self._partial(project["id"], "result", "task-1", history_id, a, None)
        final = self.store.complete_image_generation(
            project["id"], "result",
            [
                {"asset_id": a["asset_id"], "kind": "image"},
                {"asset_id": b["asset_id"], "kind": "image"},
            ],
            {
                "id": history_id,
                "task_id": "task-1",
                "status": "succeeded",
                "outputs": [
                    {"asset_id": a["asset_id"], "kind": "image"},
                    {"asset_id": b["asset_id"], "kind": "image"},
                ],
                "result_node_id": "result",
            },
            task_id="task-1",
            require_pending=True,
        )
        record = next(item for item in final["history"] if item["id"] == history_id)
        self.assertEqual(
            [output["asset_id"] for output in record["outputs"]],
            [a["asset_id"], b["asset_id"]],
        )
        self.assertEqual(record["status"], "succeeded")
        node = next(item for item in final["nodes"] if item["id"] == "result")
        self.assertEqual([img["asset_id"] for img in node["images"]], [a["asset_id"], b["asset_id"]])
        self.assertNotIn("pendingTasks", node)
        # T4 回填：调用方 record 的 outputs 必须拿到 store 分配的 output_id（按 asset_id 匹配）
        self.assertTrue(record["outputs"][0].get("output_id"))

    def test_cancel_terminal_merges_into_running_record_keeps_outputs(self):
        """#83/#79: 取消记录的 outputs=[] 不得清掉 partial 已归档的图；历史只剩一条终态记录。"""
        project = self.store.create_project("取消保留已归档图")
        a = self.store.import_media(project["id"], b"img-a", "a.png", "image/png")
        b = self.store.import_media(project["id"], b"img-b", "b.png", "image/png")
        self._start_batch(project["id"], "result", "task-1", 4)
        history_id = "generation-run-1"
        self._partial(project["id"], "result", "task-1", history_id, a, None)
        self._partial(project["id"], "result", "task-1", history_id, b, None)

        cancelled = self.store.cancel_image_generation(
            project["id"], "result", "task-1",
            {"id": "generation-cancel-1", "task_id": "task-1", "outputs": [], "error": "任务已取消"},
        )
        task_records = [item for item in cancelled["history"] if str(item.get("task_id") or "") == "task-1"]
        self.assertEqual(len(task_records), 1)
        record = task_records[0]
        self.assertEqual(record["status"], "cancelled")
        self.assertEqual(
            [output["asset_id"] for output in record["outputs"]],
            [a["asset_id"], b["asset_id"]],
        )
        self.assertNotIn(history_id, [item["id"] for item in cancelled["history"]])
        node = next(item for item in cancelled["nodes"] if item["id"] == "result")
        self.assertNotIn("pendingTasks", node)
        # 已归档图仍被历史记录引用 → 孤儿清理不会动它们
        self.assertEqual(self.store.delete_media_if_unreferenced(project["id"], a["asset_id"]), False)

    def test_failure_terminal_merges_salvage_outputs(self):
        """#83/#79: 失败记录带取回兑底 outputs，与 partial 累加合并；节点 pending 清零。"""
        project = self.store.create_project("失败取回合并")
        a = self.store.import_media(project["id"], b"img-a", "a.png", "image/png")
        b = self.store.import_media(project["id"], b"img-b", "b.png", "image/png")
        self._start_batch(project["id"], "result", "task-1", 4)
        history_id = "generation-run-1"
        self._partial(project["id"], "result", "task-1", history_id, a, None)

        failed = self.store.fail_image_generation(
            project["id"], "result", "task-1",
            {
                "id": "generation-fail-1",
                "task_id": "task-1",
                "status": "timeout",
                "outputs": [
                    {"asset_id": a["asset_id"], "kind": "image"},
                    {"asset_id": b["asset_id"], "kind": "image"},
                ],
                "error": "生成任务执行超时",
            },
            require_pending=True,
        )
        task_records = [item for item in failed["history"] if str(item.get("task_id") or "") == "task-1"]
        self.assertEqual(len(task_records), 1)
        record = task_records[0]
        self.assertEqual(record["status"], "timeout")
        self.assertEqual(
            [output["asset_id"] for output in record["outputs"]],
            [a["asset_id"], b["asset_id"]],
        )
        self.assertTrue(record["outputs"][0].get("output_id"))

    def test_dragged_in_node_with_run_stamp_is_not_output_owner(self):
        """#63: 拖入图节点带 runStartedAt 戳（无 output_id、非 result_node）不得误判为生成节点。"""
        project = self.store.create_project("拖入图节点归属")
        original = self.store.import_media(
            project["id"], b"gen-bytes", "gen.png", "image/png",
            {"type": "online_generation", "name": "gen.png"},
        )
        self.apply_mutations(project["id"], [
            {"op": "upsert_node", "node": {
                "id": "dragged", "type": "smart-image",
                "runStartedAt": 1700000000000, "runElapsedMs": 42,
                "images": [{"asset_id": original["asset_id"], "kind": "image"}],
            }},
            {"op": "set_canvas_meta", "history": [{
                "id": "log-gen", "status": "succeeded", "result_node_id": "some-other-node",
                "outputs": [{"asset_id": original["asset_id"], "kind": "image"}],
            }]},
        ])
        revision = self.store.open_project(project["id"])["canvas"]["revision"]
        result = self.store.delete_history_record_with_media(project["id"], "log-gen", base_revision=revision)
        node = next(item for item in result["canvas"]["nodes"] if item["id"] == "dragged")
        tombstones = node.get("deletedInputTombstones") or []
        self.assertTrue(any(str(t.get("deleted_asset_id") or "") == original["asset_id"] for t in tombstones))
        self.assertNotIn("deletedOutputTombstones", node)
        self.assertNotIn("resultDeleted", node)
        self.assertNotIn("needsRegeneration", node)

    def test_generated_output_node_with_output_id_is_output_owner(self):
        """#63: 真正生成节点（images 带 output_id）仍判为输出所有者（不回归）。"""
        project = self.store.create_project("生成节点归属")
        original = self.store.import_media(
            project["id"], b"gen-bytes-2", "gen2.png", "image/png",
            {"type": "online_generation", "name": "gen2.png"},
        )
        self.apply_mutations(project["id"], [
            {"op": "upsert_node", "node": {
                "id": "result", "type": "smart-image",
                "runStartedAt": 1700000000000,
                "images": [{"asset_id": original["asset_id"], "kind": "image", "output_id": "out-1"}],
            }},
            {"op": "set_canvas_meta", "history": [{
                "id": "log-gen", "status": "succeeded", "result_node_id": "result",
                "outputs": [{"asset_id": original["asset_id"], "kind": "image", "output_id": "out-1"}],
            }]},
        ])
        revision = self.store.open_project(project["id"])["canvas"]["revision"]
        result = self.store.delete_history_record_with_media(project["id"], "log-gen", base_revision=revision)
        node = next(item for item in result["canvas"]["nodes"] if item["id"] == "result")
        tombstones = node.get("deletedOutputTombstones") or []
        self.assertTrue(any(str(t.get("deleted_asset_id") or "") == original["asset_id"] for t in tombstones))

    def test_orphan_generation_media_goes_to_trash_and_can_restore(self):
        """#79: 无引用的生成孤儿移入回收区（7 天可恢复），不再静默硬删。"""
        project = self.store.create_project("孤儿回收")
        orphan = self.store.import_media(project["id"], b"orphan-bytes", "orphan.png", "image/png")
        referenced = self.store.import_media(project["id"], b"ref-bytes", "ref.png", "image/png")
        self.apply_mutations(project["id"], [
            {"op": "upsert_node", "node": {"id": "n1", "type": "image", "images": [{"asset_id": referenced["asset_id"]}]}},
        ])
        # 被引用的不回收
        self.assertFalse(self.store.trash_unreferenced_generation_media(project["id"], referenced["asset_id"]))
        # 孤儿进回收区
        self.assertTrue(self.store.trash_unreferenced_generation_media(project["id"], orphan["asset_id"]))
        self.assertFalse(any(item["asset_id"] == orphan["asset_id"] for item in self.store.list_media(project["id"])))
        items = self.store.list_deleted_media(project["id"])
        entry = next(item for item in items if item["asset_id"] == orphan["asset_id"])
        self.assertEqual(entry["reason"], "generation-orphan")
        self.assertGreater(entry["expires_at"], entry["deleted_at"])
        self.assertFalse((self.root / project["id"] / "media" / orphan["filename"]).is_file())
        self.assertTrue((self.root / project["id"] / "trash" / "media" / orphan["filename"]).is_file())

        # 通用恢复：文件移回 + 资源库重新注册
        revision = self.store.open_project(project["id"])["canvas"]["revision"]
        result = self.store.restore_trash_media(project["id"], orphan["asset_id"], base_revision=revision)
        self.assertTrue((self.root / project["id"] / "media" / orphan["filename"]).is_file())
        self.assertTrue(any(item["asset_id"] == orphan["asset_id"] for item in self.store.list_media(project["id"])))
        self.assertFalse(any(item["asset_id"] == orphan["asset_id"] for item in self.store.list_deleted_media(project["id"])))
        self.assertEqual(result["canvas"]["revision"], revision + 1)
        # 恢复后再删除（幂等保护：重复恢复报错）
        with self.assertRaises(ProjectStoreError):
            self.store.restore_trash_media(project["id"], orphan["asset_id"], base_revision=revision + 1)


    def test_archive_unreferenced_media_node_delete_cascade(self):
        """#74/D1: 级联归档——节点/单图删除后无引用的媒体移入回收区（7 天可恢复）。"""
        project = self.store.create_project("级联归档")
        asset = self.store.import_media(project["id"], b"cascade-bytes", "cascade.png", "image/png")
        result = self.store.archive_unreferenced_media(project["id"], asset["asset_id"], reason="node-delete")
        self.assertEqual(result["archived"], asset["asset_id"])
        self.assertFalse(result["file_missing"])
        self.assertFalse(any(item["asset_id"] == asset["asset_id"] for item in self.store.list_media(project["id"])))
        entry = next(item for item in self.store.list_deleted_media(project["id"]) if item["asset_id"] == asset["asset_id"])
        self.assertEqual(entry["reason"], "node-delete")
        self.assertGreater(entry["expires_at"], entry["deleted_at"])
        self.assertFalse((self.root / project["id"] / "media" / asset["filename"]).is_file())
        self.assertTrue((self.root / project["id"] / "trash" / "media" / asset["filename"]).is_file())
        # 回收区条目可恢复（通用恢复端点同一路径）
        revision = self.store.open_project(project["id"])["canvas"]["revision"]
        restored = self.store.restore_trash_media(project["id"], asset["asset_id"], base_revision=revision)
        self.assertEqual(restored["restored"], asset["asset_id"])
        self.assertTrue(any(item["asset_id"] == asset["asset_id"] for item in self.store.list_media(project["id"])))

    def test_archive_unreferenced_media_referenced_rejected(self):
        """#74/D1: 仍被画布节点或历史记录引用 → 拒收（前端 409 后保留在库）。"""
        project = self.store.create_project("归档引用检查")
        node_ref = self.store.import_media(project["id"], b"nref-bytes", "nref.png", "image/png")
        hist_ref = self.store.import_media(project["id"], b"href-bytes", "href.png", "image/png")
        self.apply_mutations(project["id"], [
            {"op": "upsert_node", "node": {"id": "n1", "type": "image", "images": [{"asset_id": node_ref["asset_id"]}]}},
            {"op": "set_canvas_meta", "history": [{
                "id": "log-1", "status": "succeeded",
                "outputs": [{"asset_id": hist_ref["asset_id"], "kind": "image"}],
            }]},
        ])
        with self.assertRaises(ProjectStoreError):
            self.store.archive_unreferenced_media(project["id"], node_ref["asset_id"], reason="node-delete")
        with self.assertRaises(ProjectStoreError):
            self.store.archive_unreferenced_media(project["id"], hist_ref["asset_id"], reason="node-delete")
        ids = [item["asset_id"] for item in self.store.list_media(project["id"])]
        self.assertIn(node_ref["asset_id"], ids)
        self.assertIn(hist_ref["asset_id"], ids)

    def test_archive_unreferenced_media_boundaries(self):
        """#74/D1: 非法标识/媒体不存在报错；文件缺失时仅清索引（归档成功）。"""
        project = self.store.create_project("归档边界")
        with self.assertRaises(ProjectStoreError):
            self.store.archive_unreferenced_media(project["id"], "bad id!!", reason="node-delete")
        with self.assertRaises(ProjectNotFound):
            self.store.archive_unreferenced_media(project["id"], "no-such-asset", reason="node-delete")
        asset = self.store.import_media(project["id"], b"ghost-bytes", "ghost.png", "image/png")
        (self.root / project["id"] / "media" / asset["filename"]).unlink()
        result = self.store.archive_unreferenced_media(project["id"], asset["asset_id"], reason="node-delete")
        self.assertTrue(result["file_missing"])
        self.assertFalse(any(item["asset_id"] == asset["asset_id"] for item in self.store.list_media(project["id"])))


if __name__ == "__main__":
    unittest.main()
