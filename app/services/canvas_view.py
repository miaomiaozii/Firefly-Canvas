"""#266 canvas 视图面：纯 PIL 拼图逻辑（Request-free，无网络可测）。

ComfyArt §59.4 语义（docs/reference-comfyart-canvas.md §59.4）：
- canvas.view = 整页/选区 contact sheet（网格 + node_id 标签），
  max_px = min(1568, args.max_px || 1024)，PNG 不超过 1 MiB（#352）
- canvas.view_media = 单节点媒体查看：图像下缩放 / 视频抽帧拼图
  （抽帧走 app/services/generation.py 的 generate_video_preview_image 链路）

慢操作独立 90s 超时契约（ComfyArt SLOW_S=90s 模式）由 api_mcp 的
SLOW_TOOL_TIMEOUTS 施加（区别于 generate.wait 的 600s 契约）。
"""
from __future__ import annotations

import io
import math
import shutil
import subprocess
from typing import Callable, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageOps

# #352：模型预览最多 1568px；默认 1024 和项目媒体原件必须保持不变。
DEFAULT_VIEW_MAX_PX = 1024
MAX_VIEW_MAX_PX = 1568
MAX_VIEW_PNG_BYTES = 1024 * 1024

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


def encode_view_png(
    img: Image.Image, max_px: int = DEFAULT_VIEW_MAX_PX,
) -> Tuple[bytes, Tuple[int, int]]:
    """只编码预览副本；返回的尺寸必须属于最终满足长边与字节预算的 PNG（#352）。"""
    px = clamp_view_max_px(max_px)
    preview = fit_tile(img, px, px)
    # 原件的 ICC/EXIF 可比像素更大；预览不携带元数据，避免缩到一像素仍然超预算。
    preview.info.clear()
    longest = max(preview.size)

    def encode_at_edge(edge: int) -> Tuple[bytes, Tuple[int, int]]:
        size = tuple(max(1, round(side * edge / longest)) for side in preview.size)
        candidate = preview if size == preview.size else preview.resize(size, Image.LANCZOS)
        output = io.BytesIO()
        # 视图已是 RGB；不要经通用编辑编码器增加无用的 alpha 通道或执行高成本优化。
        candidate.save(output, format="PNG", compress_level=6)
        return output.getvalue(), candidate.size

    encoded = encode_at_edge(longest)
    if len(encoded[0]) <= MAX_VIEW_PNG_BYTES:
        return encoded

    # 只在超预算时缩小；始终从同一副本重采样，避免多轮缩放损伤文字与细节。
    best = encode_at_edge(1)
    if len(best[0]) > MAX_VIEW_PNG_BYTES:
        raise ValueError("PNG preview budget cannot fit one pixel")
    low, high = 2, longest - 1
    # 保留已经验证合规的最大候选；二分区间严格收缩，避免高熵图片造成无终止重试。
    while low <= high:
        edge = (low + high) // 2
        encoded = encode_at_edge(edge)
        if len(encoded[0]) <= MAX_VIEW_PNG_BYTES:
            best = encoded
            low = edge + 1
        else:
            high = edge - 1
    return best


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
