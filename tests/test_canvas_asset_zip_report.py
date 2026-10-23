"""#225 打包部分失败必须如实回传，不能静默跳过。

接缝：`app/routers/api_canvas_assets.py::download_canvas_assets` 是普通 async 函数，
直接 asyncio.run 调用（不起 TestClient）。解析链的三个可注入名
（`output_file_from_url` / `local_media_file_by_basename` / `fetch_remote_media_bytes`）
都在 router 模块 globals 里（api_canvas_assets.py:16-21 从 main/services 注入），
所以用 monkeypatch 精确控制「这一张能不能解析」。

验收（GitHub #225 原文）：
  如实反馈「已打包 3/5，2 张不可用」+ 跳过原因（已移入回收区 / 文件缺失 / 路径无法解析）；
  只有**全部**失败才走笼统失败。
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import tempfile
import unittest
import zipfile
from unittest import mock

from fastapi import HTTPException

from app.routers import api_canvas_assets as mod


REPORT_HEADER = 'X-Download-Report'


class _MainStub:
    """api_canvas_assets.py:4 在 import 时就固定了 `_app_main`，函数体里走的是
    `_app_main.output_file_from_url(...)`（api_canvas_assets.py:212），所以必须整体换掉
    `_app_main`，而不是 patch 模块同名全局。"""

    def __init__(self, resolve):
        self._resolve = resolve

    def output_file_from_url(self, url):
        return self._resolve(url)

    def project_store_call(self, *args, **kwargs):
        raise RuntimeError('unit test has no ProjectStore')


def _download(payload_kwargs, *, resolve=None, by_basename=None, remote=None):
    """调用被测端点；resolve/by_basename/remote 控制三级解析链。"""
    payload = mod.CanvasAssetDownloadRequest(**payload_kwargs)
    stub = _MainStub(resolve or (lambda url: None))
    with mock.patch.object(mod, '_app_main', stub), \
            mock.patch.object(mod, 'local_media_file_by_basename', by_basename or (lambda name: None)), \
            mock.patch.object(mod, 'fetch_remote_media_bytes', remote or (lambda url: (_ for _ in ()).throw(ConnectionError('offline')))):
        return asyncio.run(mod.download_canvas_assets(payload))


def _report(response) -> dict:
    raw = response.headers.get(REPORT_HEADER)
    assert raw is not None, f'响应缺少 {REPORT_HEADER} 头：部分失败无法回传给调用方'
    import urllib.parse
    return json.loads(urllib.parse.unquote(raw))


def _zip_names(response) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(response.body)) as zf:
        return zf.namelist()


class CanvasAssetZipReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def _file(self, name: str) -> str:
        path = os.path.join(self.dir, name)
        with open(path, 'wb') as fh:
            fh.write(b'PNG' + name.encode())
        return path

    # --- 主验收：部分失败必须回传计数与原因 -------------------------------
    def test_partial_failure_reports_packed_and_skipped(self):
        ok = [self._file(f'ok{i}.png') for i in range(3)]
        urls = [f'/output/ok{i}.png' for i in range(3)] + ['/output/gone1.png', '/output/gone2.png']

        def resolve(url):
            return urls.index(url) < 3 and ok[urls.index(url)] or None

        response = _download({'urls': urls, 'filename': 'x.zip'}, resolve=resolve)
        report = _report(response)

        self.assertEqual(report['total'], 5)
        self.assertEqual(report['packed'], 3)
        self.assertEqual(len(report['skipped']), 2)
        self.assertEqual(len(_zip_names(response)), 3)

    def test_skipped_reason_distinguishes_missing_from_unreachable(self):
        good = self._file('good.png')
        urls = ['/output/good.png', '/output/missing.png', '/remote/only.png']

        def resolve(url):
            if url == '/output/good.png':
                return good
            if url == '/output/missing.png':
                # URL 能解析成一个具体路径，但盘上没有这个文件。
                return os.path.join(self.dir, 'deleted.png')
            return None

        def remote(url):
            # /remote/only.png 完全无法解析（本地没有、远程也拿不到）
            return None

        response = _download({'urls': urls, 'filename': 'x.zip'}, resolve=resolve, remote=remote)
        reasons = {entry['reason'] for entry in _report(response)['skipped']}
        self.assertIn('missing_file', reasons, '文件缺失要有明确原因')
        self.assertIn('unresolved_url', reasons, '路径无法解析要有明确原因')

    def test_trashed_asset_reports_trashed_reason(self):
        """已移入回收区的素材不能再被当成可用文件打包。"""
        trash_dir = os.path.join(self.dir, 'trash')
        os.makedirs(trash_dir, exist_ok=True)
        trashed = self._file('star.png')
        moved = os.path.join(trash_dir, 'star.png')
        os.replace(trashed, moved)
        good = self._file('good.png')

        def resolve(url):
            return moved if url == moved else good

        response = _download({'urls': [good, moved], 'filename': 'x.zip'}, resolve=resolve)
        report = _report(response)
        self.assertEqual(report['packed'], 1)
        self.assertEqual(report['skipped'][0]['reason'], 'trashed')

    def test_trashed_asset_found_by_basename_fallback_reports_trashed(self):
        """三级解析走到「按文件名回退」时命中回收区的同名文件，也必须报 trashed。

        前一道 trashed 检查只看了 URL 直接解析出的路径；local_media_file_by_basename
        这条支路不查的话，回收区的图会被静默打包，#225 的「已回收」原因就漏了。
        """
        trash_dir = os.path.join(self.dir, 'trash')
        os.makedirs(trash_dir, exist_ok=True)
        trashed = os.path.join(trash_dir, 'star.png')
        with open(trashed, 'wb') as fh:
            fh.write(b'PNG trashed')
        good = self._file('good.png')

        response = _download(
            {'urls': ['/output/good.png', '/media/star.png'], 'filename': 'x.zip'},
            resolve=lambda url: good if url == '/output/good.png' else None,
            by_basename=lambda name: trashed if name == 'star.png' else None,
        )
        report = _report(response)
        self.assertEqual(report['packed'], 1)
        self.assertEqual(report['skipped'][0]['reason'], 'trashed',
                         '按文件名回退命中回收区文件时不能当成可用素材打包')
        self.assertEqual(_zip_names(response), ['good.png'])

    def test_partial_success_skipped_entry_carries_a_human_name(self):
        """跳过原因必须落到具体某一张：带请求名或 URL 末段，用户才知道是哪张不可用。"""
        good = self._file('good.png')
        response = _download({
            'items': [
                {'url': '/output/good.png', 'name': '可用图.png'},
                {'url': '/output/gone.png', 'name': '海报主视觉.png'},
            ],
            'filename': 'x.zip',
        }, resolve=lambda url: good if url.endswith('good.png') else None)
        skipped = _report(response)['skipped']
        self.assertEqual(skipped[0]['name'], '海报主视觉.png')

    def test_total_failure_still_404(self):
        with self.assertRaises(HTTPException) as caught:
            _download({'urls': ['/output/none.png'], 'filename': 'x.zip'})
        self.assertEqual(caught.exception.status_code, 404)

    def test_full_success_reports_zero_skipped(self):
        good = self._file('only.png')
        response = _download(
            {'urls': ['/output/only.png'], 'filename': 'x.zip'},
            resolve=lambda url: good,
        )
        report = _report(response)
        self.assertEqual(report['total'], 1)
        self.assertEqual(report['packed'], 1)
        self.assertEqual(report['skipped'], [])


if __name__ == '__main__':
    unittest.main()
