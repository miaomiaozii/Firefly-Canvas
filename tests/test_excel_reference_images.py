# -*- coding: utf-8 -*-
"""#110：带参考图的 Excel 任务展开后应生成参考图节点。

验证：Excel 解析阶段提取的参考图（单元格图 / 锚点图）在展开建节点时
正确填充到 generation_references，布局器据此生成参考图节点并连入生图节点。
"""

import base64
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

import main
import app.routers.api_excel_expand as api_excel_expand
import app.routers.api_excel_import as api_excel_import
from firefly_projects import ProjectStore
from tests.test_excel_importer import make_xlsx_bytes


class TestExcelReferenceImages(unittest.TestCase):
    """带参考图的 Excel 导入展开测试。"""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="firefly-excel-ref-")
        self.store = ProjectStore(Path(self.temp_dir.name) / "projects", snapshot_delay=0)
        self.previous_store = main.FIREFLY_PROJECT_STORE
        main.FIREFLY_PROJECT_STORE = self.store
        self.previous_app_main = api_excel_expand._app_main
        api_excel_expand._app_main = main
        self.client = TestClient(
            main.app, base_url="http://127.0.0.1", headers={"origin": "http://127.0.0.1"}
        )

    def tearDown(self):
        self.client.close()
        main.FIREFLY_PROJECT_STORE = self.previous_store
        api_excel_expand._app_main = self.previous_app_main
        self.store.close()
        self.temp_dir.cleanup()

    def test_import_plan_populates_generation_references(self):
        """import-plan：带锚点图的 Excel 应填充 generation_references。"""
        # 构造带参考图的 Excel：表头行 + 空行 + 1 个带参考图的任务行
        # make_xlsx_bytes(with_image=True) 将图片锚定在 row=2 (0-based) = row 3 (1-based), col=1 (0-based)
        # 所以任务需要在 row 3 (1-based)，且图片示例列在 col=1 (0-based)
        sheets = {
            "图片方案": [
                ["图序", "图片示例", "任务名称", "内容/要素"],
                [None, None, None, None],  # 空行（占位，让任务行到 row 3）
                ["1", "参考图", "主图 1", "整体产品图，白底"],
            ]
        }
        xlsx = make_xlsx_bytes(sheets=sheets, with_image=True, explicit_empty_rows=True)

        import io
        from fastapi import UploadFile

        file = UploadFile(
            file=io.BytesIO(xlsx),
            filename="test_ref.xlsx",
        )

        # 直接调用 import_excel_plan（避免 multipart 上传的复杂性）
        from app.routers.api_excel_import import import_excel_plan
        import asyncio

        plan = asyncio.run(import_excel_plan(file))
        tasks = plan.get("tasks", [])
        self.assertEqual(len(tasks), 1, f"期望 1 个任务，实际 {len(tasks)}")

        task = tasks[0]
        gen_refs = task.get("generation_references", [])
        op_refs = task.get("operation_references", [])
        self.assertGreater(
            len(gen_refs), 0,
            f"generation_references 应为空（bug）：{gen_refs}"
        )
        self.assertEqual(len(op_refs), 0, f"operation_references 应为空：{op_refs}")

    def test_expand_plan_creates_reference_image_nodes(self):
        """expand-plan：带参考图的任务应生成参考图节点并连入生图节点。"""
        project = self.store.create_project("Reference image test")
        # 创建媒体记录（模拟 import-plan 阶段已上传参考图）
        media = self.store.import_media(
            project_id=project["id"],
            content=b"fake image content",
            filename="test_ref.png",
            mime_type="image/png",
            source_info={"kind": "excel_reference"},
        )

        # 构造带参考图的 Plan IR（模拟 import-plan 的输出）
        plan = {
            "source_file": "test_ref.xlsx",
            "sheet_count": 1,
            "scanned_rows": 1,
            "tasks": [
                {
                    "task_id": "excel_1",
                    "name": "主图 1",
                    "section": "主图方案",
                    "display_no": "主图 1",
                    "base_prompt": "整体产品图，白底",
                    "requirements": "",
                    "target_aspect_ratio": "1:1",
                    "section_index": 1,
                    "source_row": "",
                    "generation_references": [
                        {
                            "asset_id": media["asset_id"],
                            "source": "Excel参考图",
                            "url": None,
                        }
                    ],
                }
            ],
        }

        response = self.client.post(
            "/api/excel/expand-plan",
            json={"project_id": project["id"], "plan": plan},
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["success"])
        # 3 节点：提示词 + 生图 + 参考图
        self.assertEqual(body["nodes_created"], 3, f"期望 3 节点，实际 {body['nodes_created']}")
        # 2 边：提示词→生图 + 参考图→生图
        self.assertEqual(body["edges_created"], 2, f"期望 2 边，实际 {body['edges_created']}")

        # 验证画布上确实有参考图节点
        canvas = self.store.canvas(project["id"])
        node_ids = {n["id"] for n in canvas["nodes"]}
        self.assertIn("excel_1-prompt", node_ids)
        self.assertIn("excel_1-image", node_ids)
        self.assertIn("excel_1-ref-0", node_ids, "缺少参考图节点 excel_1-ref-0")

        # 验证边连通性
        edge_pairs = {(e["from"], e["to"]) for e in canvas.get("connections", [])}
        self.assertIn(("excel_1-prompt", "excel_1-image"), edge_pairs)
        self.assertIn(("excel_1-ref-0", "excel_1-image"), edge_pairs)

    def test_expand_plan_without_reference_images(self):
        """expand-plan：无参考图的任务不应生成参考图节点。"""
        project = self.store.create_project("No reference image test")

        plan = {
            "source_file": "test_no_ref.xlsx",
            "sheet_count": 1,
            "scanned_rows": 1,
            "tasks": [
                {
                    "task_id": "excel_1",
                    "name": "主图 1",
                    "section": "主图方案",
                    "display_no": "主图 1",
                    "base_prompt": "整体产品图，白底",
                    "requirements": "",
                    "target_aspect_ratio": "1:1",
                    "section_index": 1,
                    "source_row": "",
                    # 无 generation_references
                }
            ],
        }

        response = self.client.post(
            "/api/excel/expand-plan",
            json={"project_id": project["id"], "plan": plan},
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["success"])
        # 2 节点：提示词 + 生图（无参考图）
        self.assertEqual(body["nodes_created"], 2, f"期望 2 节点，实际 {body['nodes_created']}")
        # 1 边：提示词→生图
        self.assertEqual(body["edges_created"], 1, f"期望 1 边，实际 {body['edges_created']}")

        canvas = self.store.canvas(project["id"])
        node_ids = {n["id"] for n in canvas["nodes"]}
        self.assertIn("excel_1-prompt", node_ids)
        self.assertIn("excel_1-image", node_ids)
        self.assertNotIn("excel_1-ref-0", node_ids)


    def test_expand_plan_uploads_base64_reference_images(self):
        """expand-plan：带 base64 data 的参考图应上传为项目资产并替换 asset_id。"""
        project = self.store.create_project("Base64 reference upload test")

        # 构造带 base64 data 的参考图（模拟 import-plan 的输出）
        image_bytes = b"fake image bytes for testing"
        image_b64 = base64.b64encode(image_bytes).decode('ascii')

        plan = {
            "source_file": "test_b64_ref.xlsx",
            "sheet_count": 1,
            "scanned_rows": 1,
            "tasks": [
                {
                    "task_id": "excel_1",
                    "name": "主图 1",
                    "section": "主图方案",
                    "display_no": "主图 1",
                    "base_prompt": "整体产品图，白底",
                    "requirements": "",
                    "target_aspect_ratio": "1:1",
                    "section_index": 1,
                    "source_row": "",
                    "generation_references": [
                        {
                            "asset_id": "maps/fake_ref.png",  # 假的路径
                            "source": "Excel参考图",
                            "url": None,
                            "data": image_b64,  # base64 编码的字节
                        }
                    ],
                }
            ],
        }

        response = self.client.post(
            "/api/excel/expand-plan",
            json={"project_id": project["id"], "plan": plan},
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["nodes_created"], 3, f"期望 3 节点，实际 {body['nodes_created']}")

        # 验证画布上确实有参考图节点
        canvas = self.store.canvas(project["id"])
        node_ids = {n["id"] for n in canvas["nodes"]}
        self.assertIn("excel_1-ref-0", node_ids, "缺少参考图节点 excel_1-ref-0")

        # 验证参考图节点的 asset_id 不是假的路径，而是真实的 SHA256 digest
        ref_node = next(n for n in canvas["nodes"] if n["id"] == "excel_1-ref-0")
        ref_images = ref_node.get("images", [])
        self.assertGreater(len(ref_images), 0, "参考图节点没有 images 数据")
        asset_id = ref_images[0].get("asset_id", "")
        self.assertNotEqual(asset_id, "maps/fake_ref.png", "asset_id 仍为假路径，上传替换失败")
        self.assertTrue(len(asset_id) == 64, f"asset_id 应为 64 位 SHA256，实际长度 {len(asset_id)}")

        # 验证媒体文件确实被上传到项目
        media_list = self.store.list_media(project["id"])
        self.assertGreater(len(media_list), 0, "项目中没有媒体文件，上传失败")
        # 验证上传的媒体记录存在
        uploaded = self.store.media_record(project["id"], asset_id)
        self.assertIsNotNone(uploaded, "无法通过 asset_id 获取上传的媒体记录")
        self.assertEqual(uploaded["name"], "fake_ref.png", "上传的媒体文件名不匹配")


if __name__ == "__main__":
    unittest.main()
