"""Router: /api/image-jpeg domain (auto-split from main.py)."""
from __future__ import annotations
import sys as _sys
import os as _os
_app_main = _sys.modules.get(_os.environ.get('FIREFLY_MAIN_MODULE', '')) or _sys.modules.get('main') or _sys.modules['__main__']
import asyncio
import os
import json
import re
import time
import base64
import shutil
import uuid
import hashlib
import mimetypes
from datetime import datetime
from typing import Any, Optional
from fastapi import APIRouter, BackgroundTasks, File, Form, Header, HTTPException, Query, Request, Response, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field
from app.core import (FileResponse, HTTPException, Image, ImageOps, _os, _sys, asyncio, hashlib, os)
_from_main_names = ['MEDIA_PREVIEW_DIR', 'output_file_from_url']
import app.services as _svc
for _nm in _from_main_names:
    _v = getattr(_app_main, _nm, None)
    if _v is None:
        _v = getattr(_svc, _nm, None)
    globals()[_nm] = _v

router = APIRouter()

@router.get('/api/image-jpeg')
async def image_jpeg(url: str, w: int=0):
    """把任意图片转成 JPEG 返回（带缓存）。给不支持 WebP 等格式显示的客户端（PS UXP）用。
    w>0 时同时缩放到该宽度（缩略图）；w=0 输出原尺寸。"""
    path = _app_main.output_file_from_url(url)
    if not path or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail='媒体文件不存在')
    width = max(0, min(4096, int(w or 0)))
    stat = os.stat(path)
    key = hashlib.sha1(f'{os.path.realpath(path)}|{stat.st_mtime_ns}|{stat.st_size}|{width}|jpg'.encode('utf-8', 'ignore')).hexdigest()
    cache_path = os.path.join(_app_main.MEDIA_PREVIEW_DIR, f'{key}.jpg')
    if os.path.exists(cache_path):
        return FileResponse(cache_path, media_type='image/jpeg')

    def _build():
        os.makedirs(_app_main.MEDIA_PREVIEW_DIR, exist_ok=True)
        with Image.open(path) as src:
            img = ImageOps.exif_transpose(src)
            if width:
                img.thumbnail((width, width), Image.LANCZOS)
            if img.mode in ('RGBA', 'LA', 'P'):
                bg = Image.new('RGB', img.size, (255, 255, 255))
                rgba = img.convert('RGBA')
                bg.paste(rgba, mask=rgba.split()[-1])
                img = bg
            else:
                img = img.convert('RGB')
            img.save(cache_path, format='JPEG', quality=86)
        return cache_path
    try:
        out_path = await asyncio.to_thread(_build)
        return FileResponse(out_path, media_type='image/jpeg')
    except Exception as exc:
        raise HTTPException(status_code=415, detail=f'无法转换图片：{exc}') from exc