"""#256 OCR→可编辑 prompt 节点：服务端 OCR 端点单测。

接缝：`app/routers/api_ocr.py::ocr_image` 是普通 async 函数，直接 asyncio.run
调用（不起 TestClient、不 import main），与 tests/test_canvas_asset_zip_report.py
同款接缝风格。项目媒体解析走 `_app_main.project_store_call('media_record', ...)`
（api_project_library.py:2996 同款解析链），用整体替换 `mod._app_main` 的 stub
精确控制「这个 asset 解析到哪张图」。

验收（GitHub #256）：中英文截图提取文字正确率可用——本文件用真实
rapidocr-onnxruntime 引擎跑真实 PNG（离线，本地轻量后处理模型，
非 ADR 0004 禁的生成推理运行时）。
"""
from __future__ import annotations

import asyncio
import base64
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi import HTTPException

from app.routers import api_ocr as mod
from firefly_projects import ProjectNotFound


OCR_MARKER = 'OCR Test 256'


def _png_bytes() -> bytes:
    """带文字的真实 PNG：rapidocr 必须能认出 OCR_MARKER。"""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new('RGB', (320, 80), 'white')
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 34)
    except OSError:
        font = ImageFont.load_default()
    draw.text((10, 20), OCR_MARKER, fill='black', font=font)
    buf = io.BytesIO()
    img.save(buf, 'PNG')
    return buf.getvalue()


def _plain_png_bytes() -> bytes:
    """纯色无文字 PNG：OCR 应返回空文本而不是报错。"""
    from PIL import Image

    img = Image.new('RGB', (100, 50), (20, 60, 120))
    buf = io.BytesIO()
    img.save(buf, 'PNG')
    return buf.getvalue()


def _data_url(data: bytes) -> str:
    return 'data:image/png;base64,' + base64.b64encode(data).decode('ascii')


class _MainStub:
    """api_ocr.py 在 import 时固定 `_app_main`（FIREFLY_MAIN_MODULE 模式），
    所以必须整体换掉 `mod._app_main`，而不是 patch 模块同名全局。"""

    def __init__(self, records: dict, media_root: str):
        self._records = records
        self._media_root = media_root

    def project_store_call(self, op, project_id, asset_id):
        assert op == 'media_record'
        record = self._records.get((project_id, asset_id))
        if record is None:
            raise ProjectNotFound(f'项目 {project_id} 的媒体 {asset_id} 不存在')
        return dict(record, path=Path(self._media_root) / record['file'])


def _run(payload, *, records=None, media_root=None, admissible=None):
    """调用被测端点；records 控制 asset→文件解析。"""
    stub = _MainStub(records or {}, media_root or tempfile.gettempdir())
    with mock.patch.object(mod, '_app_main', stub), \
            mock.patch.object(mod, 'assert_project_owner_admissible', admissible or (lambda project_id: None)):
        return asyncio.run(mod.ocr_image(payload))


def _write_media(tmp: Path, name: str, data: bytes) -> str:
    path = tmp / name
    path.write_bytes(data)
    return name


class OcrApiTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.text_png = _write_media(self.tmp, 'text.png', _png_bytes())

    def tearDown(self):
        self._tmp.cleanup()

    def test_data_url_returns_ocr_text(self):
        payload = mod.OcrRequest(image_url=_data_url(_png_bytes()))
        result = _run(payload)
        self.assertIn(OCR_MARKER, result['text'])
        self.assertTrue(result['items'])
        self.assertEqual(result['items'][0]['text'], OCR_MARKER)

    def test_asset_id_loads_project_media(self):
        payload = mod.OcrRequest(project_id='p1', asset_id='a1')
        result = _run(
            payload,
            records={('p1', 'a1'): {'file': self.text_png, 'kind': 'image', 'mime_type': 'image/png'}},
            media_root=str(self.tmp),
        )
        self.assertIn(OCR_MARKER, result['text'])

    def test_internal_media_url_is_resolved_in_process(self):
        # 前端 img.url 就是 /api/project-library/{pid}/media/{aid}（projectMediaUrl），
        # 服务端必须就地解析成文件字节，而不是回头发 HTTP。
        payload = mod.OcrRequest(image_url='/api/project-library/p1/media/a1')
        result = _run(
            payload,
            records={('p1', 'a1'): {'file': self.text_png, 'kind': 'image', 'mime_type': 'image/png'}},
            media_root=str(self.tmp),
        )
        self.assertIn(OCR_MARKER, result['text'])

    def test_missing_source_rejected(self):
        payload = mod.OcrRequest()
        with self.assertRaises(HTTPException) as ctx:
            _run(payload)
        self.assertEqual(ctx.exception.status_code, 422)

    def test_oversized_image_rejected(self):
        big = _write_media(self.tmp, 'big.png', b'\x89PNG' + b'\x00' * (26 * 1024 * 1024))
        payload = mod.OcrRequest(project_id='p1', asset_id='big')
        with self.assertRaises(HTTPException) as ctx:
            _run(
                payload,
                records={('p1', 'big'): {'file': big, 'kind': 'image', 'mime_type': 'image/png'}},
                media_root=str(self.tmp),
            )
        self.assertEqual(ctx.exception.status_code, 413)

    def test_non_image_bytes_rejected(self):
        payload = mod.OcrRequest(image_url='data:text/plain;base64,' + base64.b64encode(b'not-an-image').decode('ascii'))
        with self.assertRaises(HTTPException) as ctx:
            _run(payload)
        self.assertEqual(ctx.exception.status_code, 415)

    def test_image_without_text_returns_empty(self):
        payload = mod.OcrRequest(image_url=_data_url(_plain_png_bytes()))
        result = _run(payload)
        self.assertEqual(result['text'], '')
        self.assertEqual(result['items'], [])

    def test_project_not_found_maps_to_404(self):
        payload = mod.OcrRequest(project_id='p1', asset_id='missing')
        with self.assertRaises(HTTPException) as ctx:
            _run(payload, records={})
        self.assertEqual(ctx.exception.status_code, 404)

    def test_non_http_scheme_rejected(self):
        payload = mod.OcrRequest(image_url='file:///D:/Firefly-Canvas/NOTICE.md')
        with self.assertRaises(HTTPException) as ctx:
            _run(payload)
        self.assertEqual(ctx.exception.status_code, 400)


if __name__ == '__main__':
    unittest.main()
