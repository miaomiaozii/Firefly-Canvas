"""Router: /api/download-output domain (auto-split from main.py)."""
from __future__ import annotations
import sys as _sys
import os as _os
_app_main = _sys.modules.get(_os.environ.get('FIREFLY_MAIN_MODULE', '')) or _sys.modules.get('main') or _sys.modules['__main__']
import os
from fastapi import APIRouter, BackgroundTasks, File, Form, Header, HTTPException, Query, Request, Response, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from app.core import (FileResponse, HTTPException, Request, StreamingResponse, _os, _sys, content_type_for_path, filename_from_media_url, os, requests, rewrite_runninghub_file_url, sanitize_export_filename, urllib)
_from_main_names = ['local_media_file_by_basename', 'output_file_from_url']
import app.services as _svc
for _nm in _from_main_names:
    _v = getattr(_app_main, _nm, None)
    if _v is None:
        _v = getattr(_svc, _nm, None)
    globals()[_nm] = _v

router = APIRouter()

@router.get('/api/download-output')
def download_output(request: Request, url: str, name: str='', inline: bool=False):
    url = rewrite_runninghub_file_url(url)
    path = _app_main.output_file_from_url(url)
    if not path:
        path = local_media_file_by_basename(filename_from_media_url(url, ''))
    if path:
        filename = sanitize_export_filename(os.path.basename(name) if name else os.path.basename(path), os.path.basename(path))
        return FileResponse(path, media_type=content_type_for_path(path), filename=None if inline else filename)
    parsed = urllib.parse.urlparse(str(url or '').strip())
    if parsed.scheme not in ('http', 'https') or not parsed.netloc:
        raise HTTPException(status_code=400, detail='无效的下载地址')
    try:
        upstream_headers = {'User-Agent': 'Firefly-Canvas/1.0'}
        range_header = request.headers.get('range')
        if range_header:
            upstream_headers['Range'] = range_header
        upstream = requests.get(url, stream=True, timeout=(10, 60), headers=upstream_headers)
        upstream.raise_for_status()
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f'远程文件下载失败：{exc}')
    content_type = upstream.headers.get('content-type') or 'application/octet-stream'
    fallback = filename_from_media_url(url, 'download.bin')
    filename = sanitize_export_filename(os.path.basename(name) if name else fallback, fallback)
    disposition = 'inline' if inline else 'attachment'
    headers = {'Content-Disposition': f"{disposition}; filename*=UTF-8''{urllib.parse.quote(filename)}"}
    for key in ('content-range', 'accept-ranges'):
        value = upstream.headers.get(key)
        if value:
            headers['-'.join((part.capitalize() for part in key.split('-')))] = value

    def stream_remote():
        try:
            for chunk in upstream.iter_content(chunk_size=256 * 1024):
                if chunk:
                    yield chunk
        finally:
            upstream.close()
    return StreamingResponse(stream_remote(), media_type=content_type, headers=headers, status_code=upstream.status_code)