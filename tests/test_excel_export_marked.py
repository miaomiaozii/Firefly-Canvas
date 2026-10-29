# -*- coding: utf-8 -*-
"""#206 单元测试：「导出打标」按图片粒度——一个节点 4 张图、只标 1 张 → 导出只含那 1 张。

覆盖两条导出路径（/api/excel/export-zip）：
- 浏览器 zip 路径（export_dir 留空，返回 zip 流）
- 服务器目录路径（export_dir 非空，#58 写目录）
并回归验证「导出选中节点」（node_ids 粒度、不传 asset_ids）仍导出该节点全部输出媒体，
两条语义互不影响（#206 的定调：不复用按节点的导出接口，节点粒度行为保持原样）。
"""

import io
import json
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


class MarkedExportTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="firefly-marked-export-")
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
        """建项目 + 1 个结果节点，节点里 4 张图（4 个不同媒体资产）。

        星标是前端行为：服务端只按请求里的 asset_ids 筛选，不读 marked 字段。
        """
        project = self.store.create_project("Marked export")
        assets = []
        for index in range(1, 5):
            asset = self.store.import_media(
                project["id"],
                f"fake-marked-image-bytes-{index}".encode("ascii"),
                f"gen_{index}.png",
                "image/png",
            )
            assets.append(asset["asset_id"])
        self.store.apply_mutations(
            project["id"],
            [
                {
                    "op": "upsert_node",
                    "node": {
                        "id": "gen-4",
                        "type": "smart-image",
                        "images": [
                            {"asset_id": aid, "kind": "image", "name": f"gen_{i}.png"}
                            for i, aid in enumerate(assets, start=1)
                        ],
                    },
                }
            ],
            base_revision=0,
        )
        return project, assets

    def zip_media_entries(self, content):
        """zip 里的媒体条目：(文件名, 字节, manifest 里的 asset_id)。"""
        zf = zipfile.ZipFile(io.BytesIO(content))
        names = zf.namelist()
        manifest = {
            entry["file"]: entry for entry in json.loads(zf.read("manifest.json"))
        }
        return [
            (name, zf.read(name), manifest[name]["asset_id"])
            for name in names
            if name not in ("manifest.json", "nodes.json")
        ]

    # ── #206：zip 路径（浏览器下载）────────────────────────────────────────

    def test_marked_export_zip_only_marked_image(self):
        project, assets = self.seed()
        res = self.client.post(
            "/api/excel/export-zip",
            json={"project_id": project["id"], "asset_ids": [assets[1]]},
        )
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.headers["content-type"], "application/zip")

        entries = self.zip_media_entries(res.content)
        # 4 张图只标 1 张 → zip 里只有那 1 张（不再把同节点另外 3 张顺带打包）
        self.assertEqual(len(entries), 1, [e[0] for e in entries])
        name, content, asset_id = entries[0]
        self.assertEqual(asset_id, assets[1])
        self.assertEqual(content, b"fake-marked-image-bytes-2")

    def test_marked_export_zip_two_of_four(self):
        project, assets = self.seed()
        res = self.client.post(
            "/api/excel/export-zip",
            json={"project_id": project["id"], "asset_ids": [assets[0], assets[3]]},
        )
        self.assertEqual(res.status_code, 200, res.text)
        entries = self.zip_media_entries(res.content)
        self.assertEqual(len(entries), 2, [e[0] for e in entries])
        self.assertEqual({e[2] for e in entries}, {assets[0], assets[3]})
        self.assertEqual(entries[0][1], b"fake-marked-image-bytes-1")
        self.assertEqual(entries[1][1], b"fake-marked-image-bytes-4")

    def test_marked_export_zip_unknown_asset_404(self):
        project, _assets = self.seed()
        res = self.client.post(
            "/api/excel/export-zip",
            json={"project_id": project["id"], "asset_ids": ["does-not-exist"]},
        )
        self.assertEqual(res.status_code, 404, res.text)

    def test_marked_export_zip_empty_asset_ids_404(self):
        project, _assets = self.seed()
        res = self.client.post(
            "/api/excel/export-zip",
            json={"project_id": project["id"], "asset_ids": []},
        )
        self.assertEqual(res.status_code, 404, res.text)

    # ── #206：服务器目录路径（#58）─────────────────────────────────────────

    def test_marked_export_dir_only_marked_image(self):
        project, assets = self.seed()
        res = self.client.post(
            "/api/excel/export-zip",
            json={
                "project_id": project["id"],
                "asset_ids": [assets[1]],
                "export_dir": "exported-marked",
            },
        )
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertEqual(body["mode"], "dir")
        # API 响应：只写 1 个文件
        self.assertEqual(body["count"], 1)
        self.assertEqual(len(body["files"]), 1)
        self.assertEqual(len(body["manifest"]), 1)
        self.assertEqual(body["manifest"][0]["asset_id"], assets[1])

        # 磁盘落盘：目录里也只有那 1 张（不是节点里全部 4 张）
        out_dir = Path(self.temp_dir.name) / "projects" / "exported-marked"
        written = sorted(p.name for p in out_dir.iterdir() if p.is_file())
        self.assertEqual(written, [Path(body["files"][0]).name])
        self.assertEqual(out_dir.joinpath(written[0]).read_bytes(), b"fake-marked-image-bytes-2")

    def test_marked_export_dir_two_of_four(self):
        project, assets = self.seed()
        res = self.client.post(
            "/api/excel/export-zip",
            json={
                "project_id": project["id"],
                "asset_ids": [assets[0], assets[2]],
                "export_dir": "exported-marked",
            },
        )
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertEqual(body["count"], 2)
        self.assertEqual({m["asset_id"] for m in body["manifest"]}, {assets[0], assets[2]})
        out_dir = Path(self.temp_dir.name) / "projects" / "exported-marked"
        written = sorted(p.name for p in out_dir.iterdir() if p.is_file())
        self.assertEqual(len(written), 2)

    # ── 回归：「导出选中节点」节点粒度语义不变 ─────────────────────────────

    def test_node_granularity_export_still_all_outputs(self):
        project, assets = self.seed()
        res = self.client.post(
            "/api/excel/export-zip",
            json={"project_id": project["id"], "node_ids": ["gen-4"]},
        )
        self.assertEqual(res.status_code, 200, res.text)
        entries = self.zip_media_entries(res.content)
        # 不传 asset_ids → 按节点导出全部输出媒体（4 张），行为保持原样
        self.assertEqual(len(entries), 4, [e[0] for e in entries])
        self.assertEqual({e[2] for e in entries}, set(assets))

    def test_node_granularity_export_dir_still_all_outputs(self):
        project, assets = self.seed()
        res = self.client.post(
            "/api/excel/export-zip",
            json={
                "project_id": project["id"],
                "node_ids": ["gen-4"],
                "export_dir": "exported-node",
            },
        )
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertEqual(body["count"], 4)
        out_dir = Path(self.temp_dir.name) / "projects" / "exported-node"
        written = [p for p in out_dir.iterdir() if p.is_file()]
        self.assertEqual(len(written), 4)

    def test_node_granularity_unknown_node_404(self):
        project, _assets = self.seed()
        res = self.client.post(
            "/api/excel/export-zip",
            json={"project_id": project["id"], "node_ids": ["ghost-node"]},
        )
        self.assertEqual(res.status_code, 404, res.text)


if __name__ == "__main__":
    unittest.main()
