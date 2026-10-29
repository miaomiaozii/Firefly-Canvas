"""#249 UI 卫生：「可能不存在的文件」读取端点缺失时返 204 而非 404。

前端缩略图/预览（<img src> 与 fetch）会在资源被清理后继续请求这些端点。
404 只会在浏览器 console 刷屏、并触发 fetch 抛错，没有任何 UI 收益；
ComfyArt 的 api/userdata.py 对缺失资源返回 204 No Content，前端按「资源
不可用」静默处理。这里钉住两个读取端点的行为：

1. /api/media-preview 目标文件不存在 → 204（原来 404）。
2. /api/storage-files/{kind}/{rel} 目标文件不存在 → 204（原来 404）。
3. 文件存在时仍正常 200（防止「一刀切 204」的回归）。
4. 路径越界（../）仍是 400——204 改造不得放宽安全边界。
"""
import unittest

from fastapi.testclient import TestClient

import main


class MissingMedia204Tests(unittest.TestCase):
    """缺失媒体/存储文件必须返回 204，存在时 200，越界仍 400。"""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(
            main.app,
            base_url="http://127.0.0.1",
            headers={"origin": "http://127.0.0.1"},
        )

    @classmethod
    def tearDownClass(cls):
        cls.client.close()

    def test_media_preview_missing_file_returns_204(self):
        response = self.client.get(
            "/api/media-preview",
            params={"url": "/output/definitely-missing-249.png", "w": 64},
        )
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.content, b"")

    def test_storage_file_missing_returns_204(self):
        response = self.client.get("/api/storage-files/generated/definitely-missing-249.png")
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.content, b"")

    def test_storage_file_existing_still_returns_200(self):
        from PIL import Image

        import os

        root = main.OUTPUT_OUTPUT_DIR
        os.makedirs(root, exist_ok=True)
        filename = "issue249-exists-check.png"
        path = os.path.join(root, filename)
        try:
            Image.new("RGB", (4, 4), "white").save(path, format="PNG")
            response = self.client.get(f"/api/storage-files/generated/{filename}")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(len(response.content) > 0, True)

            # 同一文件经 media-preview 也必须 200（走 /output/ URL 映射）
            preview = self.client.get(
                "/api/media-preview",
                params={"url": f"/output/{filename}", "w": 64},
            )
            self.assertEqual(preview.status_code, 200)
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_storage_file_path_traversal_still_400(self):
        response = self.client.get("/api/storage-files/generated/..%2Fsecret.txt")
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
