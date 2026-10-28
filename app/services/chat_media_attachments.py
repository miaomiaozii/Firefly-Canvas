"""聊天附件视/音频 → 模型可见输入（#268，从 #260 拆出）。

设计对照上游笔记 §聊天附件 contact sheet：
- 视频 → 带时间戳的 contact sheet（抽 N 帧拼成一张 JPEG，每帧角标秒数）。
  模型看到一张图即可指认「第 3.5 秒」，比逐帧灌 image_url 省 token 也更易对齐。
- 音频 → 波形卡（保守版）。本仓没有现成 ASR/外部转写通道，故不私自引重依赖；
  用 ffmpeg 解码出一段真实波形画成卡片，并显式返回 ``transcription: None``，
  由前端/维护者决定后续是否接转写选型。

约束（硬）：
- 独立模块，只读 stdlib + PIL + ffmpeg/ffprobe（均为既有依赖/工具），
  **不修改** 禁区 app/services/{media,generation,agent_host}.py 与 fal 相关代码。
- 所有函数在本地文件路径上工作（URL/UploadFile → 路径的物理解析由 router 负责）。
- 重活（ffmpeg/ffprobe/解码）都是阻塞 IO，caller 用 asyncio.to_thread 包装。
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import math
import os
import random
import shutil
import struct
import subprocess
import tempfile
from typing import Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

# ---- 限额（保持回传 payload 小，喂模型不爆 token）----
VIDEO_MAX_FRAMES_DEFAULT = 4      # 上游 bot_media 协议：默认 4 帧
VIDEO_MAX_FRAMES_CAP = 8
VIDEO_FRAME_MAX_SIZE = 640        # 单帧长边
SHEET_MAX_PX = 1280               # contact sheet 长边
SHEET_LABEL_PX = 28               # 时间戳标签条高
AUDIO_CARD_MAX_PX = 960           # 波形卡长边
AUDIO_WAVE_BUCKETS = 96
AUDIO_WAVE_SAMPLE_RATE = 8000
AUDIO_WAVE_MAX_SECONDS = 30       # 只解前 30s 做波形，避免大文件
PROBE_TIMEOUT = 30
FFMPEG_FRAME_TIMEOUT = 60


class MediaAttachmentError(RuntimeError):
    """媒体解析/转换失败。message 可直接展示给维护者/用户。"""


# --------------------------------------------------------------------------- #
# 探测（ffprobe）
# --------------------------------------------------------------------------- #
def _ffprobe_path() -> Optional[str]:
    return shutil.which('ffprobe')


def _ffmpeg_path() -> Optional[str]:
    return shutil.which('ffmpeg')


def probe_duration(path: str) -> Optional[float]:
    """ffprobe 取时长（秒）；无 ffprobe 或失败 → None。"""
    ffprobe = _ffprobe_path()
    if not ffprobe:
        return None
    try:
        proc = subprocess.run(
            [ffprobe, '-v', 'error', '-show_entries', 'format=duration',
             '-of', 'json', path],
            capture_output=True, text=True, timeout=PROBE_TIMEOUT,
        )
        data = json.loads(proc.stdout or '{}')
        value = data.get('format', {}).get('duration')
        if value is None:
            return None
        duration = float(value)
        return duration if duration > 0 else None
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# 字体
# --------------------------------------------------------------------------- #
_CJK_FONT_NAMES = (
    'C:\\Windows\\Fonts\\msyh.ttc', 'C:\\Windows\\Fonts\\msyh.ttf',
    'C:\\Windows\\Fonts\\simhei.ttf', 'C:\\Windows\\Fonts\\simkai.ttf',
    'C:\\Windows\\Fonts\\NotoSansCJKsc-Regular.otf',
    'NotoSansCJK-Regular.ttc', 'NotoSansSC-Regular.otf',
)
_ASCII_FONT_NAMES = ('DejaVuSans.ttf', 'arial.ttf', 'C:\\Windows\\Fonts\\arial.ttf')


def _has_cjk(text: str) -> bool:
    for ch in text or '':
        o = ord(ch)
        if (0x2E80 <= o <= 0x9FFF) or (0xF900 <= o <= 0xFAFF) \
                or (0x3000 <= o <= 0x303F) or (0xFF00 <= o <= 0xFFEF):
            return True
    return False


def _load_font(size: int, text: str = '') -> ImageFont.ImageFont:
    """按文本内容选字体：含 CJK 时优先 CJK 字体，避免豆腐块。"""
    names = list(_CJK_FONT_NAMES) + list(_ASCII_FONT_NAMES) if _has_cjk(text) \
        else list(_ASCII_FONT_NAMES) + list(_CJK_FONT_NAMES)
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    try:
        return ImageFont.load_default()
    except Exception:  # pragma: no cover - PIL 总提供 default
        return ImageFont.ImageFont()  # type: ignore[call-arg]


# --------------------------------------------------------------------------- #
# 视频 → contact sheet
# --------------------------------------------------------------------------- #
def _seek_frame(ffmpeg: str, path: str, at_s: float, out_path: str, max_size: int) -> bool:
    """在指定时间点抽一帧（精确 seek）。成功写 out_path 返回 True。"""
    cmd = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-y',
           '-ss', f'{at_s:.3f}', '-i', path, '-frames:v', '1',
           '-vf', f"scale='min({max_size},iw)':-2", out_path]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=FFMPEG_FRAME_TIMEOUT)
        return proc.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0
    except Exception:
        return False


def _sample_frames_fallback(ffmpeg: str, path: str, count: int, max_size: int,
                            out_dir: str) -> List[Tuple[float, str]]:
    """时长未知时：fps=1 顺序抽前 count 帧，时间戳取近似 0,1,2,... 秒。"""
    pattern = os.path.join(out_dir, 'f_%03d.jpg')
    cmd = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-y', '-i', path,
           '-vf', f"fps=1,scale='min({max_size},iw)':-2", '-frames:v', str(count), pattern]
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=FFMPEG_FRAME_TIMEOUT * 2)
    except Exception:
        return []
    frames: List[Tuple[float, str]] = []
    for i in range(count):
        p = os.path.join(out_dir, f'f_{i:03d}.jpg')
        if os.path.exists(p) and os.path.getsize(p) > 0:
            frames.append((float(i), p))
    return frames


def extract_frames(path: str, count: int, max_size: int) -> List[Tuple[float, str]]:
    """按时长均匀取 count 帧，返回 [(timestamp_s, frame_path), ...]。

    时长已知 → 精确 seek 到每个目标秒（失败向前回退重试，防流末尾）；
    时长未知 → fps=1 顺序抽帧（近似秒）。返回空列表表示抽不到帧（调用方决定降级）。
    帧文件保留在临时目录，由调用方用 cleanup_frame_dir() 清理。
    """
    ffmpeg = _ffmpeg_path()
    if not ffmpeg or not os.path.exists(path):
        raise MediaAttachmentError('缺少 ffmpeg 或视频文件不存在，无法抽帧')
    count = max(1, min(int(count), VIDEO_MAX_FRAMES_CAP))
    tmp = tempfile.mkdtemp(prefix='firefly_csheet_')
    duration = probe_duration(path)
    frames: List[Tuple[float, str]] = []
    if duration is not None and duration > 0:
        if count == 1:
            times = [duration / 2.0]
        else:
            step = duration / (count - 1)
            times = [min(duration - 0.05, duration * i / (count - 1)) for i in range(count)]
        for idx, at in enumerate(times):
            out = os.path.join(tmp, f'frame_{idx:03d}.jpg')
            used_at = max(0.0, at)
            got = False
            # seek 点可能落在最后一帧之后（流末尾），向前逐步回退重试
            for delta in (0.0, 0.05, 0.1, 0.2, 0.3, 0.5):
                candidate = max(0.0, at - delta)
                if _seek_frame(ffmpeg, path, candidate, out, max_size):
                    used_at = candidate
                    got = True
                    break
            if got:
                frames.append((used_at, out))
    if not frames:
        frames = _sample_frames_fallback(ffmpeg, path, count, max_size, tmp)
    return frames


def cleanup_frame_dir(frames: List[Tuple[float, str]]) -> None:
    """删除 extract_frames 产出的临时帧目录（帧共享同一目录，删一个即可）。"""
    if not frames:
        return
    try:
        shutil.rmtree(os.path.dirname(frames[0][1]), ignore_errors=True)
    except Exception:
        pass


def build_contact_sheet(frames: List[Tuple[float, str]], max_px: int = SHEET_MAX_PX) -> bytes:
    """把 [(t, frame_path)] 拼成一张带时间戳标签的 JPEG（返回 JPEG bytes）。"""
    if not frames:
        raise MediaAttachmentError('没有可用帧，无法生成 contact sheet')
    n = len(frames)
    cols = 1 if n == 1 else (2 if n <= 4 else 3)
    rows = math.ceil(n / cols)
    cell_w = max_px // cols
    cell_h = int(cell_w * 0.75)  # 4:3 单元格
    label = SHEET_LABEL_PX
    canvas_w = cols * cell_w
    canvas_h = rows * (cell_h + label)
    sheet = Image.new('RGB', (max(1, canvas_w), max(1, canvas_h)), (18, 20, 24))
    draw = ImageDraw.Draw(sheet)
    font = _load_font(max(12, int(label * 0.62)))
    for idx, (at, fp) in enumerate(frames):
        r, c = divmod(idx, cols)
        x0 = c * cell_w
        y0 = r * (cell_h + label)
        try:
            img = Image.open(fp).convert('RGB')
        except Exception:
            continue
        img.thumbnail((cell_w, cell_h), Image.LANCZOS)
        px = x0 + (cell_w - img.width) // 2
        py = y0 + (cell_h - img.height) // 2
        sheet.paste(img, (px, py))
        # 时间戳标签条
        draw.rectangle([x0, y0 + cell_h, x0 + cell_w, y0 + cell_h + label], fill=(0, 0, 0))
        label_text = f'{at:.2f}s'
        draw.text((x0 + 6, y0 + cell_h + (label - font.size) // 2), label_text,
                  fill=(255, 255, 255), font=font)
        draw.rectangle([x0, y0, x0 + cell_w - 1, y0 + cell_h + label - 1], outline=(70, 74, 82), width=1)
    buf = io.BytesIO()
    sheet.save(buf, 'JPEG', quality=88, optimize=True)
    return buf.getvalue()


def video_contact_sheet_from_path(path: str, frames: int = VIDEO_MAX_FRAMES_DEFAULT,
                                  max_size: int = VIDEO_FRAME_MAX_SIZE) -> Dict:
    """视频 → contact sheet。返回 {data_url, duration_s, frames_at_s, name}。"""
    if not os.path.exists(path):
        raise MediaAttachmentError(f'视频文件不存在: {path}')
    duration = probe_duration(path)
    frames = extract_frames(path, frames, max_size)
    try:
        if not frames:
            raise MediaAttachmentError('视频抽帧失败（ffmpeg 未能产出帧）')
        jpeg_bytes = build_contact_sheet(frames, SHEET_MAX_PX)
    finally:
        cleanup_frame_dir(frames)
    data_url = 'data:image/jpeg;base64,' + base64.b64encode(jpeg_bytes).decode('ascii')
    return {
        'data_url': data_url,
        'duration_s': round(duration, 2) if duration is not None else None,
        'frames_at_s': [round(t, 2) for t, _ in frames],
        'name': os.path.basename(path),
        'kind': 'video_contact_sheet',
    }


# --------------------------------------------------------------------------- #
# 音频 → 波形卡
# --------------------------------------------------------------------------- #
def _decode_audio_envelope(path: str, buckets: int) -> Optional[List[float]]:
    """用 ffmpeg 解码前 N 秒为 mono float32，归一化成 buckets 个 0..1 包络。"""
    ffmpeg = _ffmpeg_path()
    if not ffmpeg:
        return None
    try:
        proc = subprocess.run(
            [ffmpeg, '-hide_banner', '-loglevel', 'error', '-y', '-i', path,
             '-t', str(AUDIO_WAVE_MAX_SECONDS), '-f', 'f32le', '-ac', '1',
             '-ar', str(AUDIO_WAVE_SAMPLE_RATE), 'pipe:1'],
            capture_output=True, timeout=FFMPEG_FRAME_TIMEOUT,
        )
        raw = proc.stdout
        if not raw or len(raw) < 4:
            return None
        count = len(raw) // 4
        samples = list(struct.unpack(f'<{count}f', raw[: count * 4]))
        if not samples:
            return None
        per = max(1, len(samples) // buckets)
        envelope = []
        for b in range(buckets):
            chunk = samples[b * per: (b + 1) * per] or [0.0]
            envelope.append(min(1.0, max(abs(x) for x in chunk)))
        peak = max(envelope) or 1.0
        return [v / peak for v in envelope]
    except Exception:
        return None


def _synthetic_envelope(path: str, buckets: int) -> List[float]:
    """无 ffmpeg 时的确定性占位波形（由文件字节派生，仅示意）。"""
    seed = 0
    try:
        with open(path, 'rb') as f:
            head = f.read(4096)
        seed = int.from_bytes(hashlib.sha256(head).digest()[:8], 'big')
    except Exception:
        seed = 12345
    rng = random.Random(seed)
    return [max(0.08, abs(rng.gauss(0.0, 0.5))) for _ in range(buckets)]


def _pref_language() -> str:
    """读全局偏好 language（zh/en），任何异常都安全回退 zh。"""
    try:
        import sys as _sys
        main_mod = _sys.modules.get('main') or _sys.modules.get('__main__')
        path = getattr(main_mod, 'GLOBAL_SETTINGS_FILE', None)
        if path and os.path.exists(path):
            with open(path, 'r', encoding='utf-8-sig') as fh:
                raw = json.load(fh)
            lang = raw.get('language') if isinstance(raw, dict) else None
            if lang in ('zh', 'en'):
                return lang
    except Exception:
        pass
    return 'zh'


# 音频卡副标题（i18n 中英）
_AUDIO_CARD_SUB = {
    'zh': '音频附件 · {dur} · 波形预览（未转写）',
    'en': 'Audio attachment · {dur} · Waveform preview (not transcribed)',
}


def build_audio_card(path: str, name: str = '', max_px: int = AUDIO_CARD_MAX_PX,
                     lang: str = '') -> Dict:
    """音频 → 波形卡 JPEG。保守版：不转写（transcription=None）。"""
    if not os.path.exists(path):
        raise MediaAttachmentError(f'音频文件不存在: {path}')
    duration = probe_duration(path)
    decoded = _decode_audio_envelope(path, AUDIO_WAVE_BUCKETS)
    real_wave = decoded is not None
    envelope = decoded or _synthetic_envelope(path, AUDIO_WAVE_BUCKETS)
    name = name or os.path.basename(path)
    lang = lang if lang in ('zh', 'en') else _pref_language()
    W = max_px
    H = int(max_px * 0.5)
    pad = max(16, int(W * 0.03))
    header_h = int(H * 0.28)
    card = Image.new('RGB', (W, H), (16, 18, 22))
    draw = ImageDraw.Draw(card)
    title_font = _load_font(max(14, int(W * 0.032)), name)
    sub_font = _load_font(max(11, int(W * 0.022)), '音频附件 · 波形预览（未转写）时长未知')
    # 头部：文件名 + 时长
    draw.text((pad, pad), name[: max(8, W // 14)], fill=(235, 238, 245), font=title_font)
    dur_text = f'{duration:.1f}s' if duration is not None else ('时长未知' if lang == 'zh' else 'duration unknown')
    subtitle = _AUDIO_CARD_SUB[lang].format(dur=dur_text)
    draw.text((pad, pad + int(header_h * 0.55)), subtitle, fill=(150, 156, 168), font=sub_font)
    # 波形条
    wave_top = pad + header_h
    wave_h = H - wave_top - pad
    n = len(envelope)
    bar_w = max(1, (W - 2 * pad) // n)
    mid = wave_top + wave_h // 2
    for i, v in enumerate(envelope):
        h = max(2, int(wave_h * v * 0.92))
        x = pad + i * bar_w
        draw.rectangle([x, mid - h // 2, x + max(1, bar_w - 2), mid + h // 2],
                       fill=(96, 165, 250) if real_wave else (120, 126, 138))
    buf = io.BytesIO()
    card.save(buf, 'JPEG', quality=88, optimize=True)
    data_url = 'data:image/jpeg;base64,' + base64.b64encode(buf.getvalue()).decode('ascii')
    return {
        'data_url': data_url,
        'name': name,
        'duration_s': round(duration, 2) if duration is not None else None,
        'subtitle': subtitle,
        'transcription': None,
        'real_waveform': bool(real_wave),
        'kind': 'audio_card',
        'note': ('已生成波形卡；本仓暂无音频转写通道，transcription 待维护者选型。'
                 if duration is not None else
                 '已生成波形卡（占位波形，无 ffprobe）；本仓暂无音频转写通道，transcription 待维护者选型。'),
    }


def audio_card_from_path(path: str, name: str = '') -> Dict:
    """音频 → 波形卡（对外入口）。"""
    return build_audio_card(path, name)
