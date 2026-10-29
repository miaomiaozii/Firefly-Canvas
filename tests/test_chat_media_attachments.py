"""#268 聊天附件视/音频 → 模型可见输入：service + router 单测。

TDD：先固定行为，再实现。unittest 风格（pytest 与质量门 unittest TestLoader 均可收集）。
- 视频用例依赖本机 ffmpeg（缺失则 skip）。
- 音频 WAV 用标准库 wave 生成，零第三方依赖。
- router 用例用 fastapi.testclient 直接挂 router，不拉起完整 main（output_file_from_url 已做 getattr 兜底）。
"""
from __future__ import annotations

import base64
import io
import math
import os
import shutil
import struct
import subprocess
import tempfile
import unittest
import wave

from PIL import Image

from app.services import chat_media_attachments as cma
from app.services.chat_media_attachments import (
    MediaAttachmentError,
    build_audio_card,
    build_contact_sheet,
    extract_frames,
    probe_duration,
    video_contact_sheet_from_path,
    audio_card_from_path,
)

FFMPEG = shutil.which('ffmpeg')


def _make_wav(path: str, seconds: float = 1.0, freq: float = 440.0, rate: int = 8000) -> None:
    frames = b''.join(
        struct.pack('<h', int(12000 * math.sin(2 * math.pi * freq * i / rate)))
        for i in range(int(rate * seconds))
    )
    with wave.open(path, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(frames)


def _make_video(path: str, seconds: int = 2) -> None:
    subprocess.run(
        ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y',
         '-f', 'lavfi', '-i', f'testsrc=duration={seconds}:size=320x240:rate=10',
         '-pix_fmt', 'yuv420p', path],
        capture_output=True, timeout=120, check=True,
    )


class ContactSheetUnitTests(unittest.TestCase):
    def _tmp(self, name, content):
        fd, p = tempfile.mkstemp(suffix=name)
        with os.fdopen(fd, 'wb') as f:
            f.write(content)
        self.addCleanup(os.remove, p)
        return p

    def test_build_contact_sheet_layout_and_labels(self):
        # 4 张不同色块小图 → 2x2 sheet，能解码、尺寸正确、非空
        paths = []
        for i, color in enumerate([(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)]):
            img = Image.new('RGB', (160, 120), color)
            img.save(self._tmp(f'f{i}.jpg', Image.open(io.BytesIO()) and None) if False else None) if False else None
            buf = io.BytesIO(); img.save(buf, 'JPEG'); p = self._tmp(f'f{i}.jpg', buf.getvalue()); paths.append((float(i), p))
        data = build_contact_sheet(paths)
        sheet = Image.open(io.BytesIO(data))
        self.assertEqual(sheet.format, 'JPEG')
        # 2 cols x 2 rows, cell_w=SHEET_MAX_PX//2, cell_h=cell_w*0.75, +label 每行
        cell_w = cma.SHEET_MAX_PX // 2
        cell_h = int(cell_w * 0.75)
        self.assertEqual(sheet.width, 2 * cell_w)
        self.assertEqual(sheet.height, 2 * (cell_h + cma.SHEET_LABEL_PX))
        self.assertGreater(len(data), 2000)  # 非空白图

    def test_build_contact_sheet_single_frame(self):
        img = Image.new('RGB', (100, 80), (10, 200, 10))
        buf = io.BytesIO(); img.save(buf, 'JPEG'); p = self._tmp('one.jpg', buf.getvalue())
        data = build_contact_sheet([(0.0, p)])
        sheet = Image.open(io.BytesIO(data))
        self.assertEqual(sheet.width, cma.SHEET_MAX_PX)
        self.assertGreater(len(data), 1000)

    def test_build_contact_sheet_empty_raises(self):
        with self.assertRaises(MediaAttachmentError):
            build_contact_sheet([])

    def test_video_contact_sheet_missing_file(self):
        with self.assertRaises(MediaAttachmentError):
            video_contact_sheet_from_path('/nonexistent/definitely_missing.mp4')

    def test_audio_card_missing_file(self):
        with self.assertRaises(MediaAttachmentError):
            audio_card_from_path('/nonexistent/definitely_missing.wav')

    def test_cjk_font_selection(self):
        self.assertTrue(cma._has_cjk('音频附件'))
        self.assertFalse(cma._has_cjk('hello.wav'))
        f_cjk = cma._load_font(14, '音频附件')
        f_ascii = cma._load_font(14, 'hello')
        p_cjk = getattr(f_cjk, 'path', '') or ''
        p_ascii = getattr(f_ascii, 'path', '') or ''
        self.assertTrue(p_cjk)  # 应加载到具体 truetype 字体而非 default
        if p_ascii:
            self.assertNotEqual(p_cjk, p_ascii)  # 含 CJK 文本优先 CJK 字体


@unittest.skipUnless(FFMPEG, 'ffmpeg not available on this machine')
class VideoContactSheetFfmpegTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='firefly_test_csheet_')
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        self.video = os.path.join(self.tmpdir, 'clip.mp4')
        _make_video(self.video, seconds=2)

    def test_probe_duration(self):
        d = probe_duration(self.video)
        self.assertIsNotNone(d)
        self.assertAlmostEqual(d, 2.0, delta=0.5)

    def test_extract_frames_evenly_spaced(self):
        from app.services.chat_media_attachments import cleanup_frame_dir
        frames = extract_frames(self.video, count=4, max_size=320)
        self.addCleanup(cleanup_frame_dir, frames)
        self.assertEqual(len(frames), 4)
        times = [t for t, _ in frames]
        self.assertGreaterEqual(times[0], 0.0)
        self.assertLessEqual(times[-1], 2.0 + 0.2)
        # 单调不减
        self.assertEqual(times, sorted(times))
        for _, p in frames:
            self.assertTrue(os.path.exists(p))
            self.assertGreater(os.path.getsize(p), 0)

    def test_video_contact_sheet_end_to_end(self):
        result = video_contact_sheet_from_path(self.video, frames=4)
        self.assertTrue(result['data_url'].startswith('data:image/jpeg;base64,'))
        self.assertEqual(result['kind'], 'video_contact_sheet')
        self.assertEqual(len(result['frames_at_s']), 4)
        self.assertIsNotNone(result['duration_s'])
        self.assertAlmostEqual(result['duration_s'], 2.0, delta=0.5)
        self.assertEqual(result['name'], 'clip.mp4')
        # 解码确认是合法 JPEG，且含真实帧内容（testsrc 色彩方差大，防空白画布回归）
        raw = base64.b64decode(result['data_url'].split(',', 1)[1])
        Image.open(io.BytesIO(raw)).verify()
        gray = Image.open(io.BytesIO(raw)).convert('L').resize((64, 64)).getdata()
        import statistics
        self.assertGreater(statistics.pstdev(gray), 15)

    def test_video_contact_sheet_frame_clamped(self):
        # 请求超过上限应被钳到 cap
        result = video_contact_sheet_from_path(self.video, frames=99)
        self.assertLessEqual(len(result['frames_at_s']), cma.VIDEO_MAX_FRAMES_CAP)
        # 请求 0/负应被钳到至少 1
        result2 = video_contact_sheet_from_path(self.video, frames=0)
        self.assertGreaterEqual(len(result2['frames_at_s']), 1)


class AudioCardTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='firefly_test_acard_')
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        self.wav = os.path.join(self.tmpdir, 'tone.wav')
        _make_wav(self.wav, seconds=1.0, freq=440.0)

    def test_audio_card_end_to_end(self):
        result = audio_card_from_path(self.wav, name='tone.wav')
        self.assertTrue(result['data_url'].startswith('data:image/jpeg;base64,'))
        self.assertEqual(result['kind'], 'audio_card')
        self.assertEqual(result['name'], 'tone.wav')
        self.assertIsNone(result['transcription'])
        self.assertIsNotNone(result['duration_s'])
        self.assertAlmostEqual(result['duration_s'], 1.0, delta=0.4)
        img = Image.open(io.BytesIO(base64.b64decode(result['data_url'].split(',', 1)[1])))
        self.assertEqual(img.format, 'JPEG')
        self.assertIn('transcription', result)

    def test_audio_card_default_name(self):
        result = audio_card_from_path(self.wav)
        self.assertEqual(result['name'], 'tone.wav')

    def test_audio_card_subtitle_i18n(self):
        zh = build_audio_card(self.wav, name='tone.wav', lang='zh')
        en = build_audio_card(self.wav, name='tone.wav', lang='en')
        self.assertIn('音频附件', zh['subtitle'])
        self.assertIn('波形预览（未转写）', zh['subtitle'])
        self.assertIn('Audio attachment', en['subtitle'])
        self.assertIn('not transcribed', en['subtitle'])
        self.assertNotIn('音频附件', en['subtitle'])
        # 非法 lang 回退（全局偏好缺失时默认 zh）
        bad = build_audio_card(self.wav, name='tone.wav', lang='fr')
        self.assertIn('音频附件', bad['subtitle'])


class ChatMediaRouterTests(unittest.TestCase):
    def _client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.routers import api_chat_media
        app = FastAPI()
        app.include_router(api_chat_media.router)
        return TestClient(app)

    def _wav_bytes(self):
        fd, p = tempfile.mkstemp(suffix='.wav')
        os.close(fd)
        _make_wav(p, seconds=0.5, freq=330.0)
        with open(p, 'rb') as f:
            data = f.read()
        os.remove(p)
        return data

    def test_audio_card_upload(self):
        client = self._client()
        resp = client.post('/api/chat-media/audio-card',
                           files={'file': ('tone.wav', self._wav_bytes(), 'audio/wav')},
                           data={'name': 'tone.wav'})
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertTrue(body['data_url'].startswith('data:image/jpeg;base64,'))
        self.assertIsNone(body['transcription'])

    def test_video_contact_sheet_upload(self):
        if not FFMPEG:
            self.skipTest('ffmpeg not available')
        fd, p = tempfile.mkstemp(suffix='.mp4')
        os.close(fd)
        _make_video(p, seconds=1)
        with open(p, 'rb') as f:
            data = f.read()
        os.remove(p)
        client = self._client()
        resp = client.post('/api/chat-media/video-contact-sheet',
                           files={'file': ('clip.mp4', data, 'video/mp4')},
                           data={'frames': '4'})
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body['kind'], 'video_contact_sheet')
        self.assertTrue(body['data_url'].startswith('data:image/jpeg;base64,'))

    def test_missing_media_returns_400(self):
        client = self._client()
        resp = client.post('/api/chat-media/audio-card', data={})
        self.assertEqual(resp.status_code, 400)

    def test_unknown_local_url_returns_404(self):
        client = self._client()
        resp = client.post('/api/chat-media/audio-card', data={'url': '/no/such/file.wav'})
        self.assertEqual(resp.status_code, 404)


if __name__ == '__main__':
    unittest.main()
