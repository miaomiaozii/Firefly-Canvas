"""Router: /api/download-output domain (auto-split from main.py)."""
from __future__ import annotations
import sys as _sys
import os as _os
_app_main = _sys.modules.get(_os.environ.get('FIREFLY_MAIN_MODULE', '')) or _sys.modules.get('main') or _sys.modules['__main__']
import os
from fastapi import APIRouter, BackgroundTasks, File, Form, Header, HTTPException, Query, Request, Response, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from firefly_projects import ProjectNotFound
from app.core import (FileResponse, HTTPException, Request, StreamingResponse, _os, _sys, content_type_for_path, filename_from_media_url, os, requests, rewrite_runninghub_file_url, sanitize_export_filename, urllib)
from app.project_activity import (
    ProjectCleanupActive,
    assert_project_owner_admissible,
    project_lifecycle_lock,
)
from app.project_downloads import ProjectMediaFileResponse
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
    parsed = urllib.parse.urlparse(str(url or '').strip())
    segments = parsed.path.split('/')
    project_media_path = (
        not parsed.scheme
        and not parsed.netloc
        and len(segments) in (6, 7)
        and segments[1:3] == ['api', 'project-library']
        and segments[4] == 'media'
        and (len(segments) == 6 or segments[6] == 'preview')
    )
    if project_media_path:
        project_id = urllib.parse.unquote(segments[3])
        asset_id = urllib.parse.unquote(segments[5])
        if project_id and asset_id:
            try:
                assert_project_owner_admissible(project_id)
                with project_lifecycle_lock(project_id):
                    record = _app_main.project_store_call('media_record', project_id, asset_id)
                    media_path = str(record.get('path') or '')
                    if media_path:
                        fallback = os.path.basename(media_path)
                        filename = sanitize_export_filename(os.path.basename(name) if name else record.get('name') or fallback, fallback)
                        return ProjectMediaFileResponse(
                            media_path,
                            project_id=project_id,
                            media_type=record.get('mime_type') or content_type_for_path(media_path),
                            filename=None if inline else filename,
                        )
            except ProjectNotFound as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except ProjectCleanupActive as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
    # #207/#208：回收区预览 URL（/api/project-library/{pid}/trash/media/{aid}/preview）——
    # 「生成详情」面板（回收区收口）里的「下载原图」走这里：默认返回生成原件
    #（view=original，原件缺失回退预览图），文件名取回收区记录（URL 尾段是 preview，不能当下载名）。
    trash_media_path = (
        not parsed.scheme
        and not parsed.netloc
        and len(segments) in (7, 8)
        and segments[1:3] == ['api', 'project-library']
        and segments[4] == 'trash'
        and segments[5] == 'media'
        and (len(segments) == 7 or segments[7] == 'preview')
    )
    if trash_media_path:
        project_id = urllib.parse.unquote(segments[3])
        asset_id = urllib.parse.unquote(segments[6])
        if project_id and asset_id:
            trash_view = urllib.parse.parse_qs(parsed.query).get('view', ['original'])[0]
            if trash_view != 'preview':
                trash_view = 'original'
            try:
                assert_project_owner_admissible(project_id)
                with project_lifecycle_lock(project_id):
                    record = _app_main.project_store_call('trash_media_preview_path', project_id, asset_id, view=trash_view)
                    media_path = str(record.get('path') or '')
                    if media_path:
                        fallback = os.path.basename(media_path)
                        preferred = record.get('filename') or (os.path.basename(name) if name else '')
                        filename = sanitize_export_filename(preferred, fallback)
                        return ProjectMediaFileResponse(
                            media_path,
                            project_id=project_id,
                            media_type=record.get('media_type') or content_type_for_path(media_path),
                            filename=None if inline else filename,
                        )
            except ProjectNotFound as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except ProjectCleanupActive as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
    path = _app_main.output_file_from_url(url)
    if not path:
        path = local_media_file_by_basename(filename_from_media_url(url, ''))
    if path:
        filename = sanitize_export_filename(os.path.basename(name) if name else os.path.basename(path), os.path.basename(path))
        return FileResponse(path, media_type=content_type_for_path(path), filename=None if inline else filename)
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
