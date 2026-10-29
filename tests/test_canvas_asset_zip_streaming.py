"""#229 打包必须在内存里拼整包 → 改成流式，同时保留完整性信号。

GitHub #229 原文的硬要求：改动必须同时保留完整性信号，不能退回「只看 zip 有没有生成」。
所以这里同时钉两件事：
  1) 端点不再把整包攒进内存（响应是流式，且单个 chunk 有上界）；
  2) 精确的 Content-Length（= 实际流出的字节数）+ 回执里的 expected_bytes 仍在，
     前端才能继续判断「下载是否被截断」（那是 #223 的防线）。

接缝同 tests/test_canvas_asset_zip_report.py：`_app_main` / `local_media_file_by_basename` /
`fetch_remote_media_bytes` 是 router 模块 globals，用 monkeypatch 控制三级解析链。
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import tempfile
import tracemalloc
import unittest
import urllib.parse
import zipfile
from unittest import mock

from app.routers import api_canvas_assets as mod

REPORT_HEADER = 'X-Download-Report'
# 与实现里的分块上限对齐：任何一块都不该接近「一张图」的大小。
MAX_CHUNK_BYTES = 512 * 1024


class _MainStub:
    def __init__(self, resolve):
        self._resolve = resolve

    def output_file_from_url(self, url):
        return self._resolve(url)

    def project_store_call(self, *args, **kwargs):
        raise RuntimeError('unit test has no ProjectStore')


def _download(payload_kwargs, *, resolve=None, by_basename=None, remote=None):
    payload = mod.CanvasAssetDownloadRequest(**payload_kwargs)
    stub = _MainStub(resolve or (lambda url: None))
    with mock.patch.object(mod, '_app_main', stub), \
            mock.patch.object(mod, 'local_media_file_by_basename', by_basename or (lambda name: None)), \
            mock.patch.object(mod, 'fetch_remote_media_bytes', remote or (lambda url: (_ for _ in ()).throw(ConnectionError('offline')))):
        return asyncio.run(mod.download_canvas_assets(payload))


def _collect(response) -> bytes:
    """把响应取成字节：流式响应必须走 body_iterator，攒在内存里的旧路径走 response.body。"""
    iterator = getattr(response, 'body_iterator', None)
    if iterator is None:
        return response.body or b''

    async def _drain():
        return b''.join([chunk async for chunk in iterator])

    return asyncio.run(_drain())


def _report(response) -> dict:
    raw = response.headers.get(REPORT_HEADER)
    assert raw is not None, f'响应缺少 {REPORT_HEADER} 头'
    return json.loads(urllib.parse.unquote(raw))


class CanvasAssetZipStreamingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def _file(self, name: str, payload: bytes) -> str:
        path = os.path.join(self.dir, name)
        with open(path, 'wb') as fh:
            fh.write(payload)
        return path

    # --- 1) 不再整包驻留内存 ---------------------------------------------
    def test_response_is_streamed_not_buffered(self):
        big = self._file('big.png', bytes(6 * 1024 * 1024))
        response = _download({'urls': ['/output/big.png'], 'filename': 'x.zip'},
                             resolve=lambda url: big)
        self.assertIsNone(getattr(response, 'body', None),
                          '响应里已经攒好整包字节 = 旧实现，峰值内存≈包体积（#229）')
        self.assertIsNotNone(getattr(response, 'body_iterator', None),
                             '必须是流式响应，边读盘边发')

    def test_each_chunk_is_bounded_even_for_a_big_file(self):
        """单个 chunk 有上界 = 读盘也是一块一块来的，不会把一张图整个吞进内存。"""
        big = self._file('big.png', os.urandom(6 * 1024 * 1024))
        response = _download({'urls': ['/output/big.png'], 'filename': 'x.zip'},
                             resolve=lambda url: big)

        async def _walk():
            sizes = [len(chunk) async for chunk in response.body_iterator]
            return sizes

        sizes = asyncio.run(_walk())
        self.assertGreater(len(sizes), 1, '一张 6MB 的图不该只出一块')
        self.assertTrue(all(size <= MAX_CHUNK_BYTES for size in sizes),
                        f'出现超大 chunk（最大 {max(sizes)}），说明按文件粒度缓冲')

    # --- 2) 完整性信号必须保留 -------------------------------------------
    def test_content_length_equals_the_bytes_actually_streamed(self):
        paths = [self._file(f'img{i}.png', os.urandom(300 * 1024)) for i in range(4)]
        response = _download({'urls': [f'/output/img{i}.png' for i in range(4)], 'filename': 'x.zip'},
                             resolve=lambda url: paths[int(url.split('img')[1].split('.')[0])])
        declared = response.headers.get('Content-Length')
        self.assertIsNotNone(declared, '全部尺寸已知时必须给 Content-Length（前端靠它判断截断）')
        body = _collect(response)
        self.assertEqual(int(declared), len(body),
                         'Content-Length 与实际流出字节不符 = 截断检测会误报或漏报')

    def test_report_carries_expected_bytes_and_packed_count(self):
        paths = [self._file(f'img{i}.png', os.urandom(120 * 1024 + i)) for i in range(3)]
        response = _download({'urls': [f'/output/img{i}.png' for i in range(3)], 'filename': 'x.zip'},
                             resolve=lambda url: paths[int(url.split('img')[1].split('.')[0])])
        report = _report(response)
        self.assertEqual(report['packed'], 3)
        self.assertEqual(report['expected_bytes'], sum(os.path.getsize(p) for p in paths),
                         '回执里要带素材字节合计：没有 Content-Length 时前端用它兜底')
        self.assertTrue(report['size_known'])

    def test_unknown_entry_size_omits_content_length_instead_of_lying(self):
        """尺寸拿不准就不能编一个 Content-Length —— 那是把截断检测变成噪音。"""
        a = self._file('a.png', os.urandom(4096))
        b = self._file('b.png', os.urandom(4096))
        real_getsize = os.path.getsize

        def flaky_getsize(path, *args, **kwargs):
            if str(path).endswith('b.png'):
                raise OSError('size unavailable')
            return real_getsize(path, *args, **kwargs)

        with mock.patch.object(mod.os.path, 'getsize', flaky_getsize):
            response = _download({'urls': ['/output/a.png', '/output/b.png'], 'filename': 'x.zip'},
                                 resolve=lambda url: a if url.endswith('a.png') else b)
        self.assertIsNone(response.headers.get('Content-Length'),
                          '尺寸未知却发 Content-Length = 前端会把正常下载判成截断')
        report = _report(response)
        self.assertFalse(report['size_known'])
        self.assertEqual(report['packed'], 2)
        with zipfile.ZipFile(io.BytesIO(_collect(response))) as zf:
            self.assertEqual(sorted(zf.namelist()), ['a.png', 'b.png'])

    # --- 3) 流式不能改坏数据 ---------------------------------------------
    def test_streamed_zip_content_matches_source_bytes(self):
        payloads = {f'img{i}.png': os.urandom(250 * 1024 + i * 7) for i in range(3)}
        paths = {name: self._file(name, data) for name, data in payloads.items()}
        response = _download({'urls': [f'/output/{name}' for name in payloads], 'filename': 'x.zip'},
                             resolve=lambda url: paths[url.removeprefix('/output/')])
        with zipfile.ZipFile(io.BytesIO(_collect(response))) as zf:
            self.assertEqual(zf.testzip(), None, 'zip 内部 CRC 校验失败 = 流式写坏了字节')
            for name, data in payloads.items():
                self.assertEqual(zf.read(name), data, f'{name} 内容不符')

    def test_peak_memory_stays_far_below_the_archive_size(self):
        """#229 的直接验收：峰值驻留内存要跟包体积脱钩。

        旧实现是 BytesIO 攻好整包再返回，峰值≈包体积（48MB 的星标批量下载就是 48MB 常驻）。
        这里只累加长度、不保留字节，所以测到的是端点自己的驻留。
        """
        per_file = 8 * 1024 * 1024
        paths = [self._file(f'big{i}.png', os.urandom(per_file)) for i in range(4)]
        payload_total = per_file * len(paths)

        async def _drain_without_keeping():
            seen = 0
            peak_seen = 0
            async for chunk in response.body_iterator:
                seen += len(chunk)
                peak_seen = max(peak_seen, len(chunk))
            return seen, peak_seen

        response = _download({'urls': [f'/output/big{i}.png' for i in range(4)], 'filename': 'x.zip'},
                             resolve=lambda url: paths[int(url.split('big')[1].split('.')[0])])
        tracemalloc.start()
        try:
            total, biggest_chunk = asyncio.run(_drain_without_keeping())
            _current, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()

        self.assertEqual(total, int(response.headers['Content-Length']))
        self.assertLessEqual(biggest_chunk, MAX_CHUNK_BYTES)
        self.assertLess(peak, payload_total // 4,
                        f'驻留峰值 {peak / 1024 / 1024:.1f}MB 对 {payload_total / 1024 / 1024:.0f}MB 的包——还是整包量级（#229）')

    def test_remote_bytes_are_still_packed_and_counted(self):
        """远程条目（画布上还没落盘的远程媒体）不能被流式改造丢掉。"""
        payload = os.urandom(700 * 1024)
        response = _download({'urls': ['/remote/one.png'], 'filename': 'x.zip'},
                             remote=lambda url: (payload, 'image/png'))
        report = _report(response)
        self.assertEqual(report['packed'], 1)
        self.assertEqual(report['expected_bytes'], len(payload))
        with zipfile.ZipFile(io.BytesIO(_collect(response))) as zf:
            self.assertEqual(zf.read('one.png'), payload)

    def test_skipped_entries_still_report_with_streaming(self):
        """#225 的回执不能被流式改造吃掉：打包 1/2，跳过的要有原因。"""
        good = self._file('good.png', os.urandom(2048))
        response = _download({'urls': ['/output/good.png', '/output/gone.png'], 'filename': 'x.zip'},
                             resolve=lambda url: good if url.endswith('good.png') else os.path.join(self.dir, 'deleted.png'))
        report = _report(response)
        self.assertEqual(report['packed'], 1)
        self.assertEqual(report['skipped'][0]['reason'], 'missing_file')


if __name__ == '__main__':
    unittest.main()
