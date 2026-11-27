"""Router: /api/shared-folders domain (auto-split from main.py)."""
from __future__ import annotations
import sys as _sys
import os as _os
_app_main = _sys.modules.get(_os.environ.get('FIREFLY_MAIN_MODULE', '')) or _sys.modules.get('main') or _sys.modules['__main__']
import os
import json
import uuid
import firefly_json_io
from fastapi import APIRouter, BackgroundTasks, File, Form, Header, HTTPException, Query, Request, Response, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field
from app.core import (BASE_DIR, BaseModel, FileResponse, HTTPException, List, SHARED_FOLDERS_LOCK, SHARED_MEDIA_EXTS, SHARED_SCAN_MAX_ENTRIES, SharedFolderImport, SharedFolderRegister, _os, _sys, asset_library_media_kind, content_type_for_path, json, os, sanitize_asset_name, scan_shared_tree, shared_child_abs, shared_folder_abs, shared_resolve_register, urllib, uuid)
_from_main_names = ['DATA_DIR', 'SHARED_FOLDERS_FILE', 'classify_asset_image_best_effort', 'find_asset_category_in_library', 'load_asset_library', 'make_asset_library_item', 'now_ms', 'output_file_from_url', 'save_asset_library', 'shared_folders_load', 'shared_folders_save']
import app.services as _svc
for _nm in _from_main_names:
    _v = getattr(_app_main, _nm, None)
    if _v is None:
        _v = getattr(_svc, _nm, None)
    globals()[_nm] = _v

router = APIRouter()


def shared_folders_load():
    data = firefly_json_io.read_global_json(SHARED_FOLDERS_FILE, dict, default={})
    folders = data.get('folders')
    if not isinstance(folders, list):
        folders = []
    return {'folders': [f for f in folders if isinstance(f, dict)]}

def shared_folders_save(data):
    firefly_json_io.read_global_json(SHARED_FOLDERS_FILE, dict)
    firefly_json_io.write_json_atomic(SHARED_FOLDERS_FILE, data)

def shared_folder_by_id(folder_id):
    for entry in shared_folders_load().get('folders', []):
        if entry.get('id') == folder_id:
            return entry
    return None

@router.get('/api/shared-folders')
async def list_shared_folders():
    data = shared_folders_load()
    folders = []
    for entry in data.get('folders', []):
        abs_path = shared_folder_abs(entry)
        folders.append({'id': entry.get('id'), 'name': entry.get('name') or os.path.basename(abs_path) or abs_path, 'rel': entry.get('rel') or '', 'path': abs_path, 'exists': os.path.isdir(abs_path), 'created_at': entry.get('created_at')})
    return {'folders': folders}

@router.post('/api/shared-folders')
async def register_shared_folder(payload: SharedFolderRegister):
    abs_path, rel = shared_resolve_register(payload.path)
    name = sanitize_asset_name(payload.name or os.path.basename(abs_path), '共享文件夹')
    with SHARED_FOLDERS_LOCK:
        data = shared_folders_load()
        for entry in data.get('folders', []):
            if os.path.normpath(shared_folder_abs(entry)) == os.path.normpath(abs_path):
                entry['name'] = name
                shared_folders_save(data)
                return {'folder': {**entry, 'path': abs_path, 'exists': True}}
        entry = {'id': f'shared_{uuid.uuid4().hex[:12]}', 'name': name, 'rel': rel, 'created_at': _app_main.now_ms()}
        data.setdefault('folders', []).append(entry)
        shared_folders_save(data)
    return {'folder': {**entry, 'path': abs_path, 'exists': True}}

@router.delete('/api/shared-folders/{folder_id}')
async def unregister_shared_folder(folder_id: str):
    with SHARED_FOLDERS_LOCK:
        data = shared_folders_load()
        before = len(data.get('folders', []))
        data['folders'] = [f for f in data.get('folders', []) if f.get('id') != folder_id]
        if len(data['folders']) == before:
            raise HTTPException(status_code=404, detail='共享文件夹不存在')
        shared_folders_save(data)
    return {'ok': True}

@router.get('/api/shared-folders/{folder_id}/tree')
async def get_shared_folder_tree(folder_id: str):
    entry = shared_folder_by_id(folder_id)
    if not entry:
        raise HTTPException(status_code=404, detail='共享文件夹不存在')
    abs_path = shared_folder_abs(entry)
    if not os.path.isdir(abs_path):
        raise HTTPException(status_code=404, detail='文件夹已不存在')
    tree = scan_shared_tree(folder_id, abs_path, '', entry.get('name') or os.path.basename(abs_path))
    return {'folder': {'id': folder_id, 'name': entry.get('name'), 'path': abs_path}, 'tree': tree}

@router.get('/api/shared-folders/{folder_id}/file')
async def get_shared_folder_file(folder_id: str, path: str=''):
    entry = shared_folder_by_id(folder_id)
    if not entry:
        raise HTTPException(status_code=404, detail='共享文件夹不存在')
    folder_abs = shared_folder_abs(entry)
    abs_path = shared_child_abs(folder_abs, path)
    if not os.path.isfile(abs_path):
        raise HTTPException(status_code=404, detail='文件不存在')
    ext = os.path.splitext(abs_path)[1].lower()
    if ext not in SHARED_MEDIA_EXTS:
        raise HTTPException(status_code=400, detail='不支持的文件类型')
    return FileResponse(abs_path, media_type=content_type_for_path(abs_path))

@router.post('/api/shared-folders/import')
async def import_shared_folder_files(payload: SharedFolderImport):
    entry = shared_folder_by_id(payload.folder_id)
    if not entry:
        raise HTTPException(status_code=404, detail='共享文件夹不存在')
    folder_abs = shared_folder_abs(entry)
    lib = load_asset_library()
    cat = find_asset_category_in_library(lib, payload.category_id, payload.library_id)
    if not cat:
        raise HTTPException(status_code=404, detail='分类不存在')
    if cat.get('type') != 'image':
        raise HTTPException(status_code=400, detail='该分类暂不支持添加媒体')
    added = []
    for rel in (payload.paths or [])[:200]:
        abs_path = shared_child_abs(folder_abs, rel)
        if not os.path.isfile(abs_path):
            continue
        ext = os.path.splitext(abs_path)[1].lower()
        if ext not in SHARED_MEDIA_EXTS:
            continue
        _, item = make_asset_library_item(abs_path, os.path.basename(abs_path), subdir=cat.get('dir') or '')
        if item.get('kind') == 'image':
            classification = await classify_asset_image_best_effort(_app_main.output_file_from_url(item.get('url') or '') or abs_path)
            if classification:
                item['classification'] = classification
        cat.setdefault('items', []).append(item)
        added.append(item)
    save_asset_library(lib)
    return {'library': lib, 'items': added}