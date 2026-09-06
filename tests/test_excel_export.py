# -*- coding: utf-8 -*-
"""#53/#58 导出 API 单测：zip 内容与命名 + 指定目录白名单/穿越防护。"""

import io
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

import main
from firefly_projects import ProjectStore


class ExcelExportTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="firefly-excel-export-")
        self.store = ProjectStore(Path(self.temp_dir.name) / "projects", snapshot_delay=0)
        self.previous_store = main.FIREFLY_PROJECT_STORE
        main.FIREFLY_PROJECT_STORE = self.store
        self.client = TestClient(
            main.app, base_url="http://127.0.0.1", headers={"origin": "http://127.0.0.1"}
        )

    def tearDown(self):
        self.client.close()
        main.FIREFLY_PROJECT_STORE = self.previous_store
        self.store.close()
        self.temp_dir.cleanup()

    def seed(self):
        """建项目 + 1 图片资产 + 1 视频资产 + 2 个带 excel 元数据的结果节点。"""
        project = self.store.create_project("Excel export")
        img = self.store.import_media(
            project["id"], b"\x89PNG-fake-image-bytes", "result_a.png", "image/png"
        )
        vid = self.store.import_media(
            project["id"], b"fake-video-bytes", "result_b.mp4", "video/mp4"
        )
        self.store.apply_mutations(
            project["id"],
            [
                {
                    "op": "upsert_node",
                    "node": {
                        "id": "excel_1-result",
                        "type": "smart-image",
                        "excel_task_id": "excel_1",
                        "excel_section": "主图方案",
                        "excel_task_data": {
                            "task_id": "excel_1",
                            "section": "主图方案",
                            "display_no": "主图 1",
                            "section_index": 1,
                        },
                        "outputKind": "image",
                        "images": [
                            {
                                "asset_id": img["asset_id"],
                                "kind": "image",
                                "name": "result_a.png",
                            }
                        ],
                    },
                },
                {
                    "op": "upsert_node",
                    "node": {
                        "id": "excel_2-result",
                        "type": "smart-image",
                        "excel_task_id": "excel_2",
                        "excel_section": "高级A+方案",
                        "excel_task_data": {
                            "task_id": "excel_2",
                            "section": "高级A+方案",
                            "display_no": "A+ 模块 1",
                            "section_index": 1,
                        },
                        "outputKind": "video",
                        "images": [
                            {
                                "asset_id": vid["asset_id"],
                                "kind": "video",
                                "name": "result_b.mp4",
                            }
                        ],
                    },
                },
            ],
            base_revision=0,
        )
        return project, img, vid

    def post(self, project_id, node_ids, **overrides):
        payload = {"project_id": project_id, "node_ids": node_ids}
        payload.update(overrides)
        return self.client.post("/api/excel/export-zip", json=payload)

    # ── #53：zip 内容与命名 ────────────────────────────────────────────────

    def test_export_zip_contents_and_naming(self):
        project, img, vid = self.seed()
        res = self.post(project["id"], ["excel_1-result", "excel_2-result"])
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.headers["content-type"], "application/zip")
        self.assertIn("attachment", res.headers.get("content-disposition", ""))

        zf = zipfile.ZipFile(io.BytesIO(res.content))
        names = set(zf.namelist())
        # 媒体文件按任务 ID 命名（非法字符清洗：display_no 空格 → 下划线）
        self.assertIn("excel_1_主图方案_主图_1.png", names)
        self.assertIn("excel_2_高级A+方案_A+_模块_1.mp4", names)
        # 元数据
        self.assertIn("manifest.json", names)
        self.assertIn("nodes.json", nodes := None or "nodes.json")

        manifest = {e["file"]: e for e in __import__("json").loads(zf.read("manifest.json"))}
        self.assertEqual(manifest["excel_1_主图方案_主图_1.png"]["asset_id"], img["asset_id"])
        self.assertEqual(manifest["excel_1_主图方案_主图_1.png"]["kind"], "image")
        self.assertEqual(manifest["excel_2_高级A+方案_A+_模块_1.mp4"]["kind"], "video")

        # 媒体内容一致
        self.assertEqual(
            zf.read("excel_1_主图方案_主图_1.png"), b"\x89PNG-fake-image-bytes"
        )
        self.assertEqual(
            zf.read("excel_2_高级A+方案_A+_模块_1.mp4"), b"fake-video-bytes"
        )

    def test_export_zip_filename_custom(self):
        project, img, vid = self.seed()
        res = self.post(project["id"], ["excel_1-result"], filename="我的 导出/../x")
        self.assertEqual(res.status_code, 200, res.text)
        disp = res.headers.get("content-disposition", "")
        # 非法字符清洗，无路径穿越
        self.assertNotIn("..", disp)
        self.assertNotIn("/", disp.split("filename=")[1])

    def test_duplicate_names_get_suffix(self):
        project, img, vid = self.seed()
        # 两个节点都指向 excel_1/主图方案/主图 1 且同为图片扩展 → 第二个加 _2
        self.store.apply_mutations(
            project["id"],
            [
                {
                    "op": "upsert_node",
                    "node": {
                        "id": "dup-result",
                        "type": "smart-image",
                        "excel_task_id": "excel_1",
                        "excel_section": "主图方案",
                        "excel_task_data": {
                            "task_id": "excel_1",
                            "section": "主图方案",
                            "display_no": "主图 1",
                        },
                        "images": [
                            {"asset_id": img["asset_id"], "kind": "image", "name": "result_a.png"}
                        ],
                    },
                }
            ],
            base_revision=self.store.canvas(project["id"])["revision"],
        )
        res = self.post(project["id"], ["excel_1-result", "dup-result"])
        self.assertEqual(res.status_code, 200, res.text)
        names = set(zipfile.ZipFile(io.BytesIO(res.content)).namelist())
        self.assertIn("excel_1_主图方案_主图_1.png", names)
        self.assertIn("excel_1_主图方案_主图_1_2.png", names)

    # ── #58：指定目录导出 + 白名单/穿越防护 ────────────────────────────────

    def test_export_dir_writes_files(self):
        project, img, vid = self.seed()
        res = self.post(
            project["id"], ["excel_1-result", "excel_2-result"], export_dir="exports"
        )
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertEqual(body["success"], True)
        self.assertEqual(body["mode"], "dir")
        self.assertEqual(body["count"], 2)
        self.assertEqual(len(body["files"]), 2)

        out_dir = Path(body["export_dir"])
        self.assertTrue(out_dir.is_dir())
        root = Path(self.store.root).resolve()
        for f in body["files"]:
            p = Path(f)
            self.assertTrue(p.is_file())
            self.assertEqual(
                os.path.commonpath([str(root), str(p.parent.resolve())]), str(root)
            )
        self.assertEqual(
            (out_dir / "excel_1_主图方案_主图_1.png").read_bytes(),
            b"\x89PNG-fake-image-bytes",
        )

    def test_export_dir_traversal_rejected(self):
        project, img, vid = self.seed()
        res = self.post(project["id"], ["excel_1-result"], export_dir="../../escape_dir")
        self.assertEqual(res.status_code, 400, res.text)
        outside = str(Path(self.temp_dir.name).resolve().parent / "outside_root")
        res2 = self.post(project["id"], ["excel_1-result"], export_dir=outside)
        self.assertEqual(res2.status_code, 400, res2.text)

    def test_export_dir_absolute_inside_root_ok(self):
        project, img, vid = self.seed()
        target = Path(self.store.root) / "exports_abs"
        res = self.post(project["id"], ["excel_1-result"], export_dir=str(target))
        self.assertEqual(res.status_code, 200, res.text)
        self.assertTrue(target.is_dir())

    def test_no_media_404(self):
        project, img, vid = self.seed()
        self.store.apply_mutations(
            project["id"],
            [{"op": "upsert_node", "node": {"id": "empty-1", "type": "smart-prompt", "text": "x"}}],
            base_revision=self.store.canvas(project["id"])["revision"],
        )
        res = self.post(project["id"], ["empty-1"])
        self.assertEqual(res.status_code, 404, res.text)

    def test_node_not_found_404(self):
        project, img, vid = self.seed()
        res = self.post(project["id"], ["does-not-exist"])
        self.assertEqual(res.status_code, 404, res.text)


if __name__ == "__main__":
    unittest.main()
