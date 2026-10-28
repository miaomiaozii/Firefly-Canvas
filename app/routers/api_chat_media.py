"""Router: 聊天附件视/音频 → 模型可见输入（#268）。

- POST /api/chat-media/video-contact-sheet  视频 → 带时间戳 contact sheet
- POST /api/chat-media/audio-card           音频 → 波形卡（保守版，不转写）

两种入参：multipart `file`（本地附件）或 `url` 表单字段（站内/公网媒体引用）。
重活走 asyncio.to_thread，不阻塞事件循环。只读调用禁区函数，不修改禁区代码。
"""
from __future__ import annotations

import os
import sys as _sys
import os as _os
import asyncio
import logging
import tempfile
import urllib.parse

import httpx
from fastapi import APIRouter, File, Form, HTTPException, UploadFile

logger = logging.getLogger(__name__)
_app_main = (_sys.modules.get(_os.environ.get('FIREFLY_MAIN_MODULE', ''))
             or _sys.modules.get('main') or _sys.modules.get('__main__'))
import app.services as _svc
from app.services import chat_media_attachments as cma

router = APIRouter()

_KIND_SUFFIX = {'video': '.mp4', 'audio': '.mp3'}


def _output_file_from_url(url: str) -> str:
    fn = getattr(_app_main, 'output_file_from_url', None)
    if callable(fn):
        try:
            return str(fn(url) or '')
        except Exception:
            return ''
    return ''


def _suffix_for(filename: str, kind: str) -> str:
    ext = os.path.splitext(filename or '')[1]
    return ext if ext else _KIND_SUFFIX.get(kind, '.bin')


async def _resolve_media_path(file: UploadFile, url: str, kind: str) -> tuple[str, bool]:
    """把入参解析成本地路径，返回 (path, is_temp)。is_temp 的由调用方清理。"""
    if file is not None and file.filename:
        data = await file.read()
        if not data:
            raise HTTPException(status_code=400, detail='上传文件为空')
        suffix = _suffix_for(file.filename, kind)
        fd, path = tempfile.mkstemp(prefix=f'firefly_chat_{kind}_', suffix=suffix)
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
        return path, True
    url = (url or '').strip()
    if not url:
        raise HTTPException(status_code=400, detail='缺少媒体文件（file）或 url')
    if url.startswith(('http://', 'https://')):
        suffix = os.path.splitext(urllib.parse.urlparse(url).path)[1] or _KIND_SUFFIX.get(kind, '.bin')
        fd, path = tempfile.mkstemp(prefix=f'firefly_chat_{kind}_', suffix=suffix)
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(connect=20.0, read=120.0, write=30.0, pool=10.0)) as client:
                resp = await client.get(url)
                resp.raise_for_status()
            with os.fdopen(fd, 'wb') as f:
                f.write(resp.content)
            return path, True
        except Exception as exc:
            try:
                os.close(fd)
            except Exception:
                pass
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
            raise HTTPException(status_code=400, detail=f'下载媒体失败: {exc}') from exc
    # 站内/本地引用
    local = _output_file_from_url(url)
    if local and os.path.exists(local):
        return local, False
    if os.path.exists(url):
        return url, False
    raise HTTPException(status_code=404, detail=f'找不到媒体文件: {url}')


def _cleanup(path: str, is_temp: bool) -> None:
    if is_temp and os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass


async def _run(fn, *args, **kwargs):
    try:
        return await asyncio.to_thread(fn, *args, **kwargs)
    except cma.MediaAttachmentError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning(f'[chat-media] conversion failed: {exc}')
        raise HTTPException(status_code=500, detail=f'媒体转换失败: {exc}') from exc


@router.post('/api/chat-media/video-contact-sheet')
async def video_contact_sheet(
    file: UploadFile = File(None),
    url: str = Form(''),
    name: str = Form(''),
    frames: int = Form(cma.VIDEO_MAX_FRAMES_DEFAULT),
):
    path, is_temp = await _resolve_media_path(file, url, 'video')
    try:
        frames = max(1, min(int(frames or cma.VIDEO_MAX_FRAMES_DEFAULT), cma.VIDEO_MAX_FRAMES_CAP))
        result = await _run(cma.video_contact_sheet_from_path, path, frames, cma.VIDEO_FRAME_MAX_SIZE)
    finally:
        _cleanup(path, is_temp)
    return result


@router.post('/api/chat-media/audio-card')
async def audio_card(
    file: UploadFile = File(None),
    url: str = Form(''),
    name: str = Form(''),
):
    path, is_temp = await _resolve_media_path(file, url, 'audio')
    try:
        result = await _run(cma.audio_card_from_path, path, name)
    finally:
        _cleanup(path, is_temp)
    return result
