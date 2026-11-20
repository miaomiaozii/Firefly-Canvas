"""#461：附件的 metadata.json 也必须原子写。

#403 引入的 save_attachment 用 `write_text(json.dumps(...))` 写 metadata.json——
截断式写法在打开那一刻就清空旧内容，写到一半断电，这份附件的 sidecar 就归零。
sweep（test_local_asset_sidecar_atomic_write.py::TruncatingJsonWritesSweepTests）
在 main 上抓到了它。这里走真入口钉行为：写成功完整可读；写中断不留半份、目录清干净。
"""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import firefly_json_io
from app.services.agent_attachments import save_attachment


class _FakeAttachmentRequest:
    """只实现 save_attachment 用到的两个面：headers 与 stream()。"""

    def __init__(self, chunks, headers=None):
        self._chunks = chunks
        self.headers = headers or {}

    async def stream(self):
        for chunk in self._chunks:
            yield chunk


class AgentAttachmentMetadataWriteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        firefly_json_io.clear_data_diagnostics()

    def tearDown(self):
        firefly_json_io.clear_data_diagnostics()
        self.temp.cleanup()

    def save(self, chunks):
        request = _FakeAttachmentRequest(chunks, headers={"x-attachment-name": "photo.png",
                                                          "content-type": "image/png"})
        return asyncio.run(save_attachment(request, self.root))

    def test_successful_write_is_complete_and_leaves_no_staging(self):
        self.save([b"abc", b"def"])
        folders = [p for p in self.root.iterdir() if p.is_dir()]
        self.assertEqual(len(folders), 1)
        metadata = folders[0] / "metadata.json"
        self.assertTrue(metadata.is_file(), "metadata.json 必须落在附件目录里")
        record = json.loads(metadata.read_text(encoding="utf-8"))
        self.assertEqual(record["name"], "photo.png")
        self.assertEqual(record["size"], 6)
        self.assertTrue(metadata.read_text(encoding="utf-8").endswith("\n"),
                        "公共原子实现写出的文件末尾带一个换行")
        leftovers = [p.name for p in folders[0].iterdir()
                     if p.name not in ("metadata.json", record["file"])]
        self.assertEqual(leftovers, [], f"staging 临时文件必须清干净，实际残留：{leftovers}")

    def test_interrupted_write_leaves_no_half_metadata_and_cleans_folder(self):
        def half_dump(value, handle, **_kwargs):
            handle.write('{"id": "半份')
            handle.flush()
            raise OSError("写入中途被中断")

        with patch.object(firefly_json_io.json, "dump", half_dump):
            with self.assertRaises(OSError):
                self.save([b"abc"])
        self.assertEqual(list(self.root.iterdir()), [],
                         "写中断后附件目录必须整体清掉（内容文件、半份 metadata、staging 都不留）："
                         f"{[p.name for p in self.root.iterdir()]}")


if __name__ == "__main__":
    unittest.main()
