"""#266 canvas 视图面：纯 PIL 拼图逻辑（Request-free，无网络可测）。

ComfyArt §59.4 语义（docs/reference-comfyart-canvas.md §59.4）：
- canvas.view = 整页/选区 contact sheet（网格 + node_id 标签），
  max_px = min(2048, args.max_px || 1024)
- canvas.view_media = 单节点媒体查看：图像下缩放 / 视频抽帧拼图
  （抽帧走 app/services/generation.py 的 generate_video_preview_image 链路）

慢操作独立 90s 超时契约（ComfyArt SLOW_S=90s 模式）由 api_mcp 的
SLOW_TOOL_TIMEOUTS 施加（区别于 generate.wait 的 600s 契约）。
"""
from __future__ import annotations

import math
import shutil
import subprocess
from typing import Callable, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageOps

#: ComfyArt view_canvas 契约：maxPx = min(2048, args.max_px || 1024)
DEFAULT_VIEW_MAX_PX = 1024
MAX_VIEW_MAX_PX = 2048

#: 慢操作独立超时（秒）——canvas.view / canvas.view_media（ComfyArt SLOW_S=90s）
VIEW_SLOW_TIMEOUT_S = 90.0

#: 视频抽帧上限（contact sheet 单元格数）
MAX_VIDEO_FRAMES = 16


def clamp_view_max_px(value) -> int:
    """Clamp args.max_px to 64..MAX_VIEW_MAX_PX; invalid values fall back to the default."""
    try:
        px = int(value)
    except (TypeError, ValueError):
        return DEFAULT_VIEW_MAX_PX
    return max(64, min(MAX_VIEW_MAX_PX, px))


def fit_tile(img: Image.Image, cell_w: int, cell_h: int) -> Image.Image:
    """Downscale one tile into (cell_w x cell_h), EXIF-corrected RGB."""
    img = ImageOps.exif_transpose(img)
    img = img.convert("RGB")
    img.thumbnail((max(1, cell_w), max(1, cell_h)), Image.LANCZOS)
    return img


def compose_contact_sheet(tiles: List[Tuple[str, Image.Image]], max_px: int = DEFAULT_VIEW_MAX_PX) -> Image.Image:
    """Grid-compose (label, image) tiles with a per-cell label; output longest side <= max_px."""
    if not tiles:
        raise ValueError("no viewable media to compose")
    n = len(tiles)
    cols = max(1, min(4, math.ceil(math.sqrt(n))))  # <=4 列避免过宽
    rows = math.ceil(n / cols)
    max_px = max(64, int(max_px))
    label_h = 28
    pad = 8
    cell_w = max(64, max_px // cols)
    cell_h = max(64, (max_px - label_h * rows) // rows)
    width = pad * (cols + 1) + cell_w * cols
    height = pad * (rows + 1) + (cell_h + label_h) * rows
    sheet = Image.new("RGB", (width, height), (24, 26, 32))
    draw = ImageDraw.Draw(sheet)
    for i, (label, img) in enumerate(tiles):
        r, c = divmod(i, cols)
        x = pad + c * (cell_w + pad)
        y = pad + r * (cell_h + label_h + pad)
        tile = fit_tile(img, cell_w, cell_h)
        sheet.paste(tile, (x + (cell_w - tile.width) // 2, y + (cell_h - tile.height) // 2))
        draw.text((x + 2, y + cell_h + 4), str(label)[:32], fill=(230, 232, 240))
    if max(sheet.width, sheet.height) > max_px:  # 安全网：契约上限
        sheet.thumbnail((max_px, max_px), Image.LANCZOS)
    return sheet


def video_duration_seconds(path: str) -> Optional[float]:
    """Best-effort ffprobe duration; None when ffprobe is absent or the probe fails."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        proc = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=15,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return max(0.0, float(proc.stdout.strip()))
    except Exception:
        pass
    return None


def video_frame_times(duration: Optional[float], frames: int) -> List[float]:
    """Uniform mid-point sampling; unknown duration falls back to 0.5s + 1.0s/帧."""
    frames = max(1, min(MAX_VIDEO_FRAMES, int(frames or 1)))
    if duration and duration > 0:
        return [max(0.0, min(duration - 0.05, duration * (i + 0.5) / frames)) for i in range(frames)]
    return [0.5 + i * 1.0 for i in range(frames)]


def video_contact_sheet(
    path: str,
    frame_fn: Callable[[str, int, float], Image.Image],
    duration: Optional[float],
    frames: int,
    cell_w: int,
    max_px: int = DEFAULT_VIEW_MAX_PX,
) -> Optional[Image.Image]:
    """Extract frames via frame_fn (generate_video_preview_image 链路) and compose a contact sheet.

    Returns None when every frame extraction fails (caller degrades to an
    error message with the ffmpeg next-step hint).
    """
    tiles: List[Tuple[str, Image.Image]] = []
    for t in video_frame_times(duration, frames):
        try:
            tiles.append((f"t={t:.1f}s", frame_fn(path, cell_w, t)))
        except Exception:
            continue
    if not tiles:
        return None
    return compose_contact_sheet(tiles, max_px)
