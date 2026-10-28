"""Router: /api/media-preview domain (auto-split from main.py)."""
from __future__ import annotations
import sys as _sys
import os as _os
_app_main = _sys.modules.get(_os.environ.get('FIREFLY_MAIN_MODULE', '')) or _sys.modules.get('main') or _sys.modules['__main__']
import asyncio
import os
import hashlib
from fastapi import APIRouter, BackgroundTasks, File, Form, Header, HTTPException, Query, Request, Response, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from app.core import (FileResponse, HTTPException, Image, ImageOps, _os, _sys, asyncio, hashlib, image_has_alpha, is_video_preview_file, os)
_from_main_names = ['MEDIA_PREVIEW_DIR', 'generate_video_preview_image', 'media_preview_cache_paths', 'output_file_from_url']
import app.services as _svc
for _nm in _from_main_names:
    _v = getattr(_app_main, _nm, None)
    if _v is None:
        _v = getattr(_svc, _nm, None)
    globals()[_nm] = _v

router = APIRouter()

def image_has_alpha(img: Image.Image) -> bool:
    if img.mode in ('RGBA', 'LA'):
        return True
    if img.mode == 'P':
        return 'transparency' in img.info
    return False

def media_preview_cache_paths(path: str, width: int):
    stat = os.stat(path)
    key = hashlib.sha1(f'{os.path.realpath(path)}|{stat.st_mtime_ns}|{stat.st_size}|{width}'.encode('utf-8', 'ignore')).hexdigest()
    return (os.path.join(_app_main.MEDIA_PREVIEW_DIR, f'{key}.webp'), os.path.join(_app_main.MEDIA_PREVIEW_DIR, f'{key}.png'))

def is_video_preview_file(path: str) -> bool:
    return os.path.splitext(str(path or '').split('?', 1)[0])[1].lower() in {'.mp4', '.webm', '.mov', '.m4v', '.avi', '.mkv'}

@router.get('/api/media-preview')
async def media_preview(url: str, w: int=512):
    path = _app_main.output_file_from_url(url)
    if not path or not os.path.isfile(path):
        # #249：媒体已被清理/删除时返回 204 No Content（ComfyArt userdata 缺资源约定），
        # 前端 <img> 的 onerror 兜底链不变，console 不再被 404 刷屏。
        return Response(status_code=204)
    width = max(64, min(2048, int(w or 512)))
    webp_path, png_path = media_preview_cache_paths(path, width)
    if os.path.exists(webp_path):
        return FileResponse(webp_path, media_type='image/webp')
    if os.path.exists(png_path):
        return FileResponse(png_path, media_type='image/png')

    def _build_preview():
        os.makedirs(_app_main.MEDIA_PREVIEW_DIR, exist_ok=True)
        if is_video_preview_file(path):
            img = _app_main.generate_video_preview_image(path, width)
        else:
            with Image.open(path) as source:
                img = ImageOps.exif_transpose(source)
                img.thumbnail((width, width), Image.LANCZOS)
                img = img.convert('RGBA' if image_has_alpha(img) else 'RGB')
        try:
            img.save(webp_path, format='WEBP', quality=80, method=1)
            return (webp_path, 'image/webp')
        except Exception:
            img.save(png_path, format='PNG')
            return (png_path, 'image/png')
    try:
        out_path, media_type = await asyncio.to_thread(_build_preview)
        return FileResponse(out_path, media_type=media_type)
    except Exception as exc:
        raise HTTPException(status_code=415, detail=f'无法生成预览图：{exc}') from exc