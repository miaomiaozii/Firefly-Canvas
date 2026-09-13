"""Router: /api/ai domain (auto-split from main.py)."""
from __future__ import annotations
import sys as _sys
import os as _os
_app_main = _sys.modules.get(_os.environ.get('FIREFLY_MAIN_MODULE', '')) or _sys.modules.get('main') or _sys.modules['__main__']
import os
import re
import base64
import shutil
import uuid
import mimetypes
from fastapi import APIRouter, BackgroundTasks, File, Form, Header, HTTPException, Query, Request, Response, UploadFile, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from app.core import (Base64UploadRequest, BaseModel, Field, File, HTTPException, Image, LOCAL_IMAGE_IMPORT_EXTS, LOCAL_IMAGE_IMPORT_MAX_BYTES, List, LocalImageImportRequest, Request, UploadFile, _local_upload_kind_ext, _os, _sys, base64, ensure_same_origin_request, mimetypes, normalize_local_image_path, os, re, shutil, urllib, uuid)
from app.core import _local_upload_kind_ext
_from_main_names = ['import_local_image_file', 'output_path_for', 'output_url_for']
import app.services as _svc
for _nm in _from_main_names:
    _v = getattr(_app_main, _nm, None)
    if _v is None:
        _v = getattr(_svc, _nm, None)
    globals()[_nm] = _v

router = APIRouter()


def normalize_local_image_path(value):
    text = str(value or '').strip().strip('"').strip("'")
    if not text:
        raise HTTPException(status_code=400, detail='本地图片路径为空')
    if text.lower().startswith('file:'):
        parsed = urllib.parse.urlparse(text)
        if parsed.scheme.lower() != 'file':
            raise HTTPException(status_code=400, detail='只支持本地图片路径')
        if parsed.netloc and re.match('^[a-zA-Z]:$', parsed.netloc) and (os.name == 'nt'):
            path = f"{parsed.netloc}{urllib.request.url2pathname(parsed.path or '')}"
        elif parsed.netloc and parsed.netloc.lower() not in ('localhost',):
            raise HTTPException(status_code=400, detail='只支持本机图片路径')
        else:
            path = urllib.request.url2pathname(parsed.path or '')
    else:
        path = text
    path = path.strip().strip('"').strip("'")
    if re.match('^/[a-zA-Z]:[\\\\/]', path):
        path = path[1:]
    if re.match('^[a-zA-Z]:[\\\\/]', path):
        return os.path.abspath(path)
    if path.startswith('/') and os.name != 'nt':
        return os.path.abspath(path)
    raise HTTPException(status_code=400, detail='只支持本机绝对图片路径')

def import_local_image_file(path):
    ext = os.path.splitext(path)[1].lower()
    if ext not in LOCAL_IMAGE_IMPORT_EXTS:
        raise HTTPException(status_code=400, detail='仅支持 PNG、JPG、JPEG、WEBP、GIF 图片')
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail='本地图片不存在或无法读取')
    try:
        size = os.path.getsize(path)
    except OSError:
        raise HTTPException(status_code=404, detail='本地图片不存在或无法读取')
    if size <= 0:
        raise HTTPException(status_code=400, detail='本地图片为空')
    if size > LOCAL_IMAGE_IMPORT_MAX_BYTES:
        raise HTTPException(status_code=413, detail='本地图片过大，请使用 50MB 以内的图片')
    try:
        with Image.open(path) as img:
            img.verify()
    except Exception:
        raise HTTPException(status_code=400, detail='文件不是可识别的图片')
    filename = f'ai_ref_{uuid.uuid4().hex[:12]}{ext}'
    dest = output_path_for(filename, 'input')
    try:
        shutil.copyfile(path, dest)
    except OSError:
        raise HTTPException(status_code=500, detail='导入本地图片失败')
    return {'url': output_url_for(filename, 'input'), 'name': os.path.basename(path) or filename, 'kind': 'image'}

@router.post('/api/ai/upload')
async def upload_ai_reference(files: List[UploadFile]=File(...)):
    uploaded = []
    image_exts = {'.png', '.jpg', '.jpeg', '.webp', '.gif'}
    video_exts = {'.mp4', '.webm', '.mov', '.m4v', '.flv'}
    audio_exts = {'.mp3', '.wav', '.m4a', '.aac', '.ogg', '.flac'}
    doc_exts = {'.pdf', '.txt', '.md', '.markdown', '.doc', '.docx', '.xls', '.xlsx', '.csv', '.json', '.zip', '.yaml', '.yml', '.log'}
    max_upload_bytes = 50 * 1024 * 1024
    for file in files:
        content = await file.read()
        if not content:
            continue
        if len(content) > max_upload_bytes:
            raise HTTPException(status_code=413, detail=f"{file.filename or '文件'} 超过 50MB，无法上传")
        ext = os.path.splitext(file.filename or '')[1].lower()
        content_type = (file.content_type or '').lower()
        kind = 'image'
        if ext in video_exts or content_type.startswith('video/'):
            kind = 'video'
            if ext not in video_exts:
                ext = '.webm' if 'webm' in content_type else '.mov' if 'quicktime' in content_type else '.mp4'
        elif ext in audio_exts or content_type.startswith('audio/'):
            kind = 'audio'
            if ext not in audio_exts:
                ext = '.wav' if 'wav' in content_type else '.ogg' if 'ogg' in content_type else '.m4a' if 'mp4' in content_type else '.mp3'
        elif ext in image_exts or content_type.startswith('image/'):
            kind = 'image'
            if ext not in image_exts:
                ext = '.jpg' if 'jpeg' in content_type else '.webp' if 'webp' in content_type else '.gif' if 'gif' in content_type else '.png'
        elif ext in doc_exts or content_type.startswith(('text/', 'application/')):
            kind = 'file'
            if not ext:
                ext = mimetypes.guess_extension(content_type) or '.bin'
        else:
            kind = 'file'
            if not ext:
                ext = '.bin'
        filename = f'ai_ref_{uuid.uuid4().hex[:12]}{ext}'
        path = output_path_for(filename, 'input')
        with open(path, 'wb') as f:
            f.write(content)
        uploaded.append({'url': output_url_for(filename, 'input'), 'name': file.filename or filename, 'kind': kind, 'mime': content_type})
    return {'files': uploaded}


@router.post('/api/ai/upload-base64')
async def upload_ai_base64(payload: Base64UploadRequest):
    """以 base64 JSON 方式上传字节到 assets/input，返回 /assets 地址。
    给不便用 multipart/FormData 的客户端（如 PS UXP 面板）用——UXP 的 fetch+FormData 经常发不出有效 multipart。"""
    raw = (payload.data or '').strip()
    ct = (payload.content_type or '').split(';', 1)[0].strip().lower()
    if raw.startswith('data:'):
        header, _, raw = raw.partition(',')
        if not ct:
            ct = header[5:].split(';', 1)[0].strip().lower()
    try:
        content = base64.b64decode(raw, validate=False)
    except Exception:
        raise HTTPException(status_code=400, detail='数据无法解码')
    if not content:
        raise HTTPException(status_code=400, detail='内容为空')
    if len(content) > 50 * 1024 * 1024:
        raise HTTPException(status_code=413, detail='超过 50MB')
    kind, ext = _local_upload_kind_ext(payload.name or '', ct or 'image/png')
    if kind is None:
        kind, ext = ('image', '.png')
    filename = f'ai_ref_{uuid.uuid4().hex[:12]}{ext}'
    path = output_path_for(filename, 'input')
    with open(path, 'wb') as f:
        f.write(content)
    return {'files': [{'url': output_url_for(filename, 'input'), 'name': payload.name or filename, 'kind': kind}]}

@router.post('/api/ai/import-local-image')
async def import_local_ai_reference(payload: LocalImageImportRequest, request: Request):
    ensure_same_origin_request(request)
    requested = [payload.path] if payload.path else []
    requested.extend(payload.paths or [])
    requested = [p for p in requested if str(p or '').strip()][:20]
    if not requested:
        raise HTTPException(status_code=400, detail='没有可导入的本地图片')
    return {'files': [import_local_image_file(normalize_local_image_path(path)) for path in requested]}