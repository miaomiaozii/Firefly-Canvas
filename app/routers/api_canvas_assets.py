"""Router: /api/canvas-assets domain (auto-split from main.py)."""
from __future__ import annotations
import sys as _sys
import os as _os
_app_main = _sys.modules.get(_os.environ.get('FIREFLY_MAIN_MODULE', '')) or _sys.modules.get('main') or _sys.modules['__main__']
import asyncio
import os
import json
import time
import re
import hashlib
from typing import Any, Optional
from fastapi.responses import StreamingResponse
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

def _media_path_is_trashed(candidate) -> bool:
    """#225：路径里任何一段是 trash（Windows / POSIX 分隔符都算）= 素材已移入回收区。"""
    parts = [part.lower() for part in re.split(r'[\\/]+', str(candidate or '')) if part]
    return 'trash' in parts

ZIP_STREAM_CHUNK_BYTES = 256 * 1024


class _ChunkZipSink:
    """#229：zipfile 的写字节出口，故意做成不可 seek。

    不可 seek → zipfile 改用数据描述符（flag bit 3）把 CRC 和尺寸写在条目尾部，
    我们就不必为了算头部字段先把整包攒在内存里：写进来的每一块当场倒出去。
    线路开销实测为 Σ(92 + 2×utf8名长 + 尺寸) + 22（ASCII 与中文文件名都验过）。
    """

    def __init__(self):
        self._chunks = []
        self._pos = 0

    def write(self, data):
        self._chunks.append(bytes(data))
        self._pos += len(data)
        return len(data)

    def flush(self):
        return None

    def tell(self):
        return self._pos

    def seekable(self):
        return False

    def close(self):
        return None

    def drain(self):
        if not self._chunks:
            return b''
        data = self._chunks[0] if len(self._chunks) == 1 else b''.join(self._chunks)
        self._chunks.clear()
        return data


def _zip_wire_bytes(entries):
    """全部尺寸已知时给出精确的响应字节数，否则 None。

    宁可不发 Content-Length，也不能发一个错的——前端拿它判断下载有没有被截断
    （那是 #223 的防线）。尺寸逼近 4GiB 会转 zip64、头部变长，同样返回 None。
    """
    total = 22
    for entry in entries:
        size = entry.get('size')
        if size is None or size >= 0xFFFFFFFF:
            return None
        total += 92 + 2 * len(str(entry['name']).encode('utf-8')) + size
        if total >= 0xFFFFFFFF:
            return None
    return total


def _iter_file_chunks(path):
    with open(path, 'rb') as handle:
        while True:
            chunk = handle.read(ZIP_STREAM_CHUNK_BYTES)
            if not chunk:
                return
            yield chunk


def _stream_zip_entries(entries):
    """读盘 → 写 zip → 立刻 yield，全程驻留内存只有一块。"""
    sink = _ChunkZipSink()
    with zipfile.ZipFile(sink, 'w', zipfile.ZIP_STORED) as archive:
        for entry in entries:
            info = zipfile.ZipInfo(entry['name'], date_time=entry['date_time'])
            info.compress_type = zipfile.ZIP_STORED
            info.external_attr = 0o600 << 16
            with archive.open(info, 'w') as dest:
                chunks = _iter_file_chunks(entry['path']) if entry['kind'] == 'file' else iter([entry['data']])
                for chunk in chunks:
                    dest.write(chunk)
                    pending = sink.drain()
                    if pending:
                        yield pending
            pending = sink.drain()
            if pending:
                yield pending
    pending = sink.drain()
    if pending:
        yield pending


def _entry_date_time(path):
    try:
        return time.localtime(os.path.getmtime(path))[:6]
    except OSError:
        return time.localtime()[:6]


@router.post('/api/canvas-assets/download')
async def download_canvas_assets(payload: CanvasAssetDownloadRequest):
    """打包画布素材。#229：先把条目解析出来，再把 zip 流出去。

    旧实现把整包攒在 BytesIO 里一次性返回，峰值内存约等于整个包体积
    （48MB 的星标批量下载就是 48MB 常驻）。现在只有远程外链那一张会短暂驻留
    （仍受单文件上限保护），本地文件是一块一块流过管道的。
    """
    used_names = set()
    entries = []
    # #225：每一张跳过都记下「是哪一张 + 为什么」，通过 X-Download-Report 回传。
    skipped = []
    raw_items = payload.items or [{'url': url} for url in payload.urls]

    def _record_skip(raw, text: str, reason: str) -> None:
        label = str(raw.get('name') or '').strip() if isinstance(raw, dict) else ''
        if not label:
            label = filename_from_media_url(text, '') or os.path.basename(str(text).split('?')[0])
        skipped.append({'name': str(label or text or ''), 'reason': reason})

    def _add_entry(kind: str, archive_base: str, *, path: str = '', data: bytes = b'') -> None:
        stem, ext = os.path.splitext(archive_base)
        archive_name = archive_base
        suffix = 2
        while archive_name in used_names:
            archive_name = f'{stem}-{suffix}{ext}'
            suffix += 1
        used_names.add(archive_name)
        if kind == 'file':
            try:
                size = os.path.getsize(path)
            except OSError:
                # 尺寸拿不准（并发删除、权限）：条目照常打包，但不再承诺 Content-Length。
                size = None
            entries.append({'name': archive_name, 'kind': 'file', 'path': path, 'size': size,
                            'date_time': _entry_date_time(path)})
        else:
            entries.append({'name': archive_name, 'kind': 'bytes', 'data': data, 'size': len(data),
                            'date_time': time.localtime()[:6]})

    for raw in raw_items[:1000]:
        if isinstance(raw, dict):
            text = str(raw.get('url') or '').strip()
            requested_name = str(raw.get('name') or '').strip()
        else:
            text = str(raw or '').strip()
            requested_name = ''
        if not text:
            _record_skip(raw, text, 'empty_url')
            continue
        path = _app_main.output_file_from_url(text)
        saw_path = bool(path)
        if not (path and os.path.isfile(path)):
            # 项目素材 URL 指向元数据端点（返回 JSON 不是字节），走 ProjectStore 找原始文件。
            project_media_path = project_media_file_from_url(text)
            if project_media_path:
                path = project_media_path
                saw_path = True
        if path and _media_path_is_trashed(path):
            # 已移入回收区：既不是「文件缺失」也不是「路径解析不了」，必须单独说清楚。
            _record_skip(raw, text, 'trashed')
            continue
        if path and os.path.isfile(path):
            _add_entry('file',
                       sanitize_export_filename(requested_name or os.path.basename(path),
                                                os.path.basename(path) or f'image-{len(entries) + 1}.png'),
                       path=path)
            continue
        local_by_name = local_media_file_by_basename(filename_from_media_url(text, ''))
        if local_by_name and os.path.isfile(local_by_name):
            # 同名回退也可能命中回收区的同名文件：不补这一道，#225 的「已回收」就漏了。
            if _media_path_is_trashed(local_by_name):
                _record_skip(raw, text, 'trashed')
                continue
            _add_entry('file',
                       sanitize_export_filename(requested_name or os.path.basename(local_by_name),
                                                os.path.basename(local_by_name) or f'image-{len(entries) + 1}.png'),
                       path=local_by_name)
            continue
        try:
            remote = fetch_remote_media_bytes(text)
        except Exception:
            remote = None
        if not remote:
            # URL 能解析成本地路径但盘上没有 = 文件缺失；三级解析全落空 = 路径无法解析。
            _record_skip(raw, text, 'missing_file' if saw_path else 'unresolved_url')
            continue
        content, _content_type = remote
        _add_entry('bytes',
                   sanitize_export_filename(requested_name or filename_from_media_url(text, f'image-{len(entries) + 1}.bin'),
                                            f'image-{len(entries) + 1}.bin'),
                   data=content)

    if not entries:
        raise HTTPException(status_code=404, detail='没有可下载的本地图片')

    filename = re.sub('[\\\\/:*?"<>|]+', '_', payload.filename or 'canvas-output-images.zip')
    if not filename.lower().endswith('.zip'):
        filename += '.zip'
    encoded = urllib.parse.quote(filename)
    known_sizes = [entry['size'] for entry in entries if entry['size'] is not None]
    report = {
        'total': len(raw_items[:1000]),
        'packed': len(entries),
        'skipped': skipped,
        # #229：流式之后未必有 Content-Length（例如某张图尺寸读不出来），
        # 所以把素材字节合计也放进回执，前端仍然有一个可用的截断判据。
        'expected_bytes': sum(known_sizes),
        'size_known': len(known_sizes) == len(entries),
    }
    headers = {
        'Content-Disposition': f"attachment; filename*=UTF-8''{encoded}",
        'X-Download-Report': urllib.parse.quote(json.dumps(report, ensure_ascii=False)),
    }
    wire_bytes = _zip_wire_bytes(entries)
    if wire_bytes is not None:
        headers['Content-Length'] = str(wire_bytes)
    return StreamingResponse(_stream_zip_entries(entries), media_type='application/zip', headers=headers)
