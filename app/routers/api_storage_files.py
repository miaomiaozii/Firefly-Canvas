"""Router: /api/storage-files domain (auto-split from main.py)."""
from __future__ import annotations
import sys as _sys
import os as _os
_app_main = _sys.modules.get(_os.environ.get('FIREFLY_MAIN_MODULE', '')) or _sys.modules.get('main') or _sys.modules['__main__']
import os
from typing import Any, Optional
from fastapi import APIRouter, BackgroundTasks, File, Form, Header, HTTPException, Query, Request, Response, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from app.core import (Any, Dict, FileResponse, HTTPException, Image, STORAGE_IMAGE_EXTS, _os, _sys, content_type_for_path, os, storage_file_item, urllib)
_from_main_names = ['storage_file_path', 'storage_kind_dir']
import app.services as _svc
for _nm in _from_main_names:
    _v = getattr(_app_main, _nm, None)
    if _v is None:
        _v = getattr(_svc, _nm, None)
    globals()[_nm] = _v

router = APIRouter()

def storage_file_item(kind, root, path):
    rel = os.path.relpath(path, root).replace('\\', '/')
    try:
        stat = os.stat(path)
    except OSError:
        return None
    item = {'id': f'{kind}:{rel}', 'kind': kind, 'rel': rel, 'name': os.path.basename(path), 'folder': os.path.dirname(rel).replace('\\', '/'), 'url': f"/api/storage-files/{kind}/{urllib.parse.quote(rel, safe='/')}", 'size': stat.st_size, 'created_at': stat.st_mtime}
    try:
        with Image.open(path) as img:
            item['width'], item['height'] = img.size
    except Exception:
        pass
    return item

@router.get('/api/storage-files')
async def list_storage_files(kind: str='generated', offset: int=0, limit: int=80):
    root = storage_kind_dir(kind)
    os.makedirs(root, exist_ok=True)
    offset = max(0, int(offset or 0))
    limit = max(20, min(200, int(limit or 80)))
    items = []
    for current, dirs, files in os.walk(root):
        dirs[:] = sorted([d for d in dirs if not d.startswith('.') and (not d.startswith('._'))], key=str.lower)
        for name in sorted(files, key=str.lower):
            if name.startswith('.') or name.startswith('._'):
                continue
            if os.path.splitext(name)[1].lower() not in STORAGE_IMAGE_EXTS:
                continue
            item = storage_file_item(kind, root, os.path.join(current, name))
            if item:
                items.append(item)
    items.sort(key=lambda item: item.get('created_at') or 0, reverse=True)
    total = len(items)
    page_items = items[offset:offset + limit]
    return {'kind': kind, 'root': root, 'items': page_items, 'total': total, 'offset': offset, 'limit': limit, 'has_more': offset + len(page_items) < total}

@router.get('/api/storage-files/{kind}/{rel_path:path}')
async def get_storage_file(kind: str, rel_path: str):
    path = storage_file_path(kind, rel_path)
    if not path or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail='文件不存在')
    return FileResponse(path, media_type=content_type_for_path(path))

@router.post('/api/storage-files/delete')
async def delete_storage_files(payload: Dict[str, Any]):
    kind = str((payload or {}).get('kind') or '').strip()
    rels = [str(item or '').strip() for item in (payload or {}).get('items') or [] if str(item or '').strip()]
    if not rels:
        raise HTTPException(status_code=400, detail='请选择要删除的文件')
    removed = 0
    for rel in rels:
        path = storage_file_path(kind, rel)
        if not path or not os.path.isfile(path):
            continue
        try:
            os.remove(path)
            removed += 1
        except OSError:
            pass
    return {'removed': removed}