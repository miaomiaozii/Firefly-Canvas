"""Router: /api/canvas-assets domain (auto-split from main.py)."""
from __future__ import annotations
import sys as _sys
import os as _os
_app_main = _sys.modules.get(_os.environ.get('FIREFLY_MAIN_MODULE', '')) or _sys.modules.get('main') or _sys.modules['__main__']
import asyncio
import os
import json
import re
import hashlib
from typing import Any, Optional
from fastapi import APIRouter, BackgroundTasks, File, Form, Header, HTTPException, Query, Request, Response, UploadFile, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from app.core import (Any, BaseModel, BytesIO, CanvasAssetCheckRequest, CanvasAssetDownloadRequest, Dict, HTTPException, List, Response, _os, _sys, asset_library_media_kind, asyncio, canvas_asset_downloadable_url, canvas_asset_kind, canvas_asset_name, canvas_asset_url_value, canvas_node_title, canvas_record, extract_canvas_assets, fetch_remote_media_bytes, filename_from_media_url, hashlib, iter_canvas_asset_values, json, os, re, requests, rewrite_runninghub_file_url, sanitize_asset_name, sanitize_export_filename, urllib, zipfile)
_from_main_names = ['CANVAS_DIR', 'canvas_assets_index', 'cleanup_expired_canvas_trash', 'local_media_file_by_basename', 'output_file_from_url']
import app.services as _svc
for _nm in _from_main_names:
    _v = getattr(_app_main, _nm, None)
    if _v is None:
        _v = getattr(_svc, _nm, None)
    globals()[_nm] = _v

router = APIRouter()


def canvas_asset_url_value(value):
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ('url', 'path', 'src', 'uri', 'output', 'output_url', 'outputUrl', 'video', 'video_url', 'videoUrl'):
            text = str(value.get(key) or '').strip()
            if text:
                return text
    return ''

def canvas_asset_downloadable_url(url):
    text = str(url or '').strip()
    return text if text.startswith(('/output/', '/assets/', 'http://', 'https://')) else ''

def canvas_asset_kind(value, url=''):
    explicit = ''
    if isinstance(value, dict):
        explicit = str(value.get('kind') or value.get('mediaKind') or value.get('type') or '').lower()
    if 'video' in explicit:
        return 'video'
    if 'audio' in explicit:
        return 'audio'
    if 'text' in explicit:
        return 'text'
    return asset_library_media_kind(url or canvas_asset_url_value(value))

def canvas_asset_name(value, url='', fallback='asset'):
    if isinstance(value, dict):
        for key in ('name', 'filename', 'file', 'title'):
            name = str(value.get(key) or '').strip()
            if name:
                return sanitize_asset_name(name, fallback)
    return sanitize_asset_name(filename_from_media_url(url, fallback), fallback)

def iter_canvas_asset_values(value, path=''):
    if isinstance(value, dict):
        url = canvas_asset_downloadable_url(canvas_asset_url_value(value))
        if url:
            yield (path, value, url)
        for key, child in value.items():
            if key in {'run', 'runs', 'settings', 'params', 'metadata', 'meta', 'prompt', 'text', 'caption', 'history'}:
                continue
            yield from iter_canvas_asset_values(child, f'{path}.{key}' if path else str(key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from iter_canvas_asset_values(child, f'{path}[{index}]')
    elif isinstance(value, str):
        url = canvas_asset_downloadable_url(value)
        if url:
            yield (path, value, url)

def canvas_node_title(node):
    if not isinstance(node, dict):
        return ''
    return str(node.get('title') or node.get('name') or node.get('label') or node.get('type') or '节点')[:120]

def extract_canvas_assets(canvas):
    record = canvas_record(canvas)
    canvas_id = str(record.get('id') or '')
    items = []
    seen = set()
    nodes = canvas.get('nodes') if isinstance(canvas.get('nodes'), list) else []
    for node_index, node in enumerate(nodes):
        if not isinstance(node, dict):
            continue
        node_id = str(node.get('id') or f'node_{node_index}')
        node_title = canvas_node_title(node)
        for field_path, raw, url in iter_canvas_asset_values(node):
            dedupe_key = url
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            kind = canvas_asset_kind(raw, url)
            if kind not in {'image', 'video', 'audio', 'text'}:
                continue
            fallback = f"{record.get('title') or 'canvas'}-{len(items) + 1}"
            item = {'id': hashlib.sha1(f'{canvas_id}:{url}'.encode('utf-8')).hexdigest()[:24], 'url': url, 'name': canvas_asset_name(raw, url, fallback), 'kind': kind, 'canvas_id': canvas_id, 'canvas_title': record.get('title') or '未命名画布', 'canvas_kind': record.get('kind') or 'classic', 'canvas_icon': record.get('icon') or 'layers', 'canvas_owner': record.get('owner') or '', 'canvas_color': record.get('color') or '', 'canvas_created_at': record.get('created_at') or 0, 'canvas_updated_at': record.get('updated_at') or 0, 'node_id': node_id, 'node_title': node_title, 'node_type': str(node.get('type') or ''), 'source_path': field_path, 'created_at': node.get('created_at') or record.get('updated_at') or record.get('created_at') or 0}
            if isinstance(raw, dict):
                for key in ('natural_w', 'natural_h', 'width', 'height', 'size', 'duration', 'runMs'):
                    if raw.get(key) is not None:
                        item[key] = raw.get(key)
            items.append(item)
    return items

def canvas_assets_index():
    canvases = []
    items = []
    canvas_counts = {'all': 0, 'smart': 0, 'classic': 0}
    item_counts = {'all': 0, 'smart': 0, 'classic': 0}
    cleanup_expired_canvas_trash()
    for filename in os.listdir(_app_main.CANVAS_DIR):
        if not filename.endswith('.json'):
            continue
        try:
            with open(os.path.join(_app_main.CANVAS_DIR, filename), 'r', encoding='utf-8') as f:
                canvas = json.load(f)
        except Exception:
            continue
        if canvas.get('deleted_at'):
            continue
        record = canvas_record(canvas)
        canvas_items = extract_canvas_assets(canvas)
        record['asset_count'] = len(canvas_items)
        canvases.append(record)
        items.extend(canvas_items)
        kind = record.get('kind') or 'classic'
        canvas_counts['all'] += 1
        canvas_counts[kind] = canvas_counts.get(kind, 0) + 1
        item_counts['all'] += len(canvas_items)
        item_counts[kind] = item_counts.get(kind, 0) + len(canvas_items)
    canvases.sort(key=lambda item: (0 if item.get('pinned') else 1, -int(item.get('updated_at') or item.get('created_at') or 0)))
    items.sort(key=lambda item: int(item.get('canvas_updated_at') or item.get('created_at') or 0), reverse=True)
    categories = [{'id': 'all', 'name': '全部画布', 'count': item_counts.get('all', 0), 'canvas_count': canvas_counts.get('all', 0)}, {'id': 'smart', 'name': '核心画布', 'count': item_counts.get('smart', 0), 'canvas_count': canvas_counts.get('smart', 0)}, {'id': 'classic', 'name': '经典画布', 'count': item_counts.get('classic', 0), 'canvas_count': canvas_counts.get('classic', 0)}]
    return {'categories': categories, 'canvases': canvases, 'items': items}

def fetch_remote_media_bytes(url: str, timeout: float=30.0, max_bytes: int=200 * 1024 * 1024):
    text = rewrite_runninghub_file_url(str(url or '').strip())
    parsed = urllib.parse.urlparse(text)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc:
        return None
    with requests.get(text, stream=True, timeout=timeout, headers={'User-Agent': 'Firefly-Canvas/1.0'}) as response:
        response.raise_for_status()
        content_type = response.headers.get('content-type') or 'application/octet-stream'
        chunks = []
        total = 0
        for chunk in response.iter_content(chunk_size=1024 * 256):
            if not chunk:
                continue
            total += len(chunk)
            if total > max_bytes:
                raise HTTPException(status_code=413, detail='文件太大，无法下载')
            chunks.append(chunk)
        return (b''.join(chunks), content_type)

@router.get('/api/canvas-assets')
async def list_canvas_assets():
    return await asyncio.to_thread(canvas_assets_index)

@router.post('/api/canvas-assets/check')
async def check_canvas_assets(payload: CanvasAssetCheckRequest):
    result = {}
    for url in payload.urls[:3000]:
        text = str(url or '').strip()
        if not text:
            continue
        if text.startswith('/output/') or text.startswith('/assets/'):
            result[text] = bool(_app_main.output_file_from_url(text))
        else:
            result[text] = True
    return {'exists': result}

def project_media_file_from_url(text):
    """Resolve /api/project-library/{pid}/media/{asset_id}( /preview) URLs to the
    original on-disk file via ProjectStore.media_record. These URLs are metadata
    endpoints (they return JSON, not bytes), so the generic local/remote resolvers
    cannot serve them — without this, zip downloads of project media 404."""
    match = re.match(r'^/api/project-library/([^/]+)/media/([^/]+?)(?:/preview)?/?$', str(text or '').strip())
    if not match:
        return None
    project_id = urllib.parse.unquote(match.group(1))
    asset_id = urllib.parse.unquote(match.group(2))
    try:
        record = _app_main.project_store_call('media_record', project_id, asset_id)
    except Exception:
        return None
    if not isinstance(record, dict):
        return None
    path = str(record.get('path') or '').strip()
    return path if path and os.path.isfile(path) else None

@router.post('/api/canvas-assets/download')
async def download_canvas_assets(payload: CanvasAssetDownloadRequest):
    buffer = BytesIO()
    used_names = set()
    count = 0
    raw_items = payload.items or [{'url': url} for url in payload.urls]
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        for raw in raw_items[:1000]:
            if isinstance(raw, dict):
                text = str(raw.get('url') or '').strip()
                requested_name = str(raw.get('name') or '').strip()
            else:
                text = str(raw or '').strip()
                requested_name = ''
            if not text:
                continue
            path = _app_main.output_file_from_url(text)
            if not (path and os.path.isfile(path)):
                # Project media URLs (canvas thumbnails / generation history / detail
                # panel star-mark downloads) point at the metadata endpoint, not the
                # bytes — resolve the original file through ProjectStore instead.
                project_media_path = project_media_file_from_url(text)
                if project_media_path:
                    path = project_media_path
            content = None
            content_type = ''
            if path and os.path.isfile(path):
                base = sanitize_export_filename(requested_name or os.path.basename(path), os.path.basename(path) or f'image-{count + 1}.png')
            else:
                local_by_name = local_media_file_by_basename(filename_from_media_url(text, ''))
                if local_by_name and os.path.isfile(local_by_name):
                    path = local_by_name
                    base = sanitize_export_filename(requested_name or os.path.basename(path), os.path.basename(path) or f'image-{count + 1}.png')
                else:
                    try:
                        remote = fetch_remote_media_bytes(text)
                    except Exception:
                        remote = None
                    if not remote:
                        continue
                    content, content_type = remote
                    base = sanitize_export_filename(requested_name or filename_from_media_url(text, f'image-{count + 1}.bin'), f'image-{count + 1}.bin')
            name, ext = os.path.splitext(base)
            archive_name = base
            suffix = 2
            while archive_name in used_names:
                archive_name = f'{name}-{suffix}{ext}'
                suffix += 1
            used_names.add(archive_name)
            if path and os.path.isfile(path):
                zf.write(path, archive_name)
            else:
                zf.writestr(archive_name, content)
            count += 1
    if count <= 0:
        raise HTTPException(status_code=404, detail='没有可下载的本地图片')
    buffer.seek(0)
    filename = re.sub('[\\\\/:*?"<>|]+', '_', payload.filename or 'canvas-output-images.zip')
    if not filename.lower().endswith('.zip'):
        filename += '.zip'
    encoded = urllib.parse.quote(filename)
    headers = {'Content-Disposition': f"attachment; filename*=UTF-8''{encoded}"}
    return Response(buffer.getvalue(), media_type='application/zip', headers=headers)