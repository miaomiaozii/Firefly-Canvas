"""Router: /api/shared-folders domain (auto-split from main.py)."""
from __future__ import annotations
import sys as _sys
import os as _os
_app_main = _sys.modules.get(_os.environ.get('FIREFLY_MAIN_MODULE', 'main'), None) or _sys.modules['main']
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
from app.core import *
_from_main_names = ['DATA_DIR', 'SHARED_FOLDERS_FILE', 'classify_asset_image_best_effort', 'find_asset_category_in_library', 'load_asset_library', 'make_asset_library_item', 'now_ms', 'output_file_from_url', 'save_asset_library', 'shared_folder_by_id', 'shared_folders_load', 'shared_folders_save']
for _nm in _from_main_names:
    globals()[_nm] = getattr(_app_main, _nm)
router = APIRouter()

class SharedFolderRegister(BaseModel):
    path: str = ''
    name: str = ''

class SharedFolderImport(BaseModel):
    library_id: str = ''
    category_id: str = ''
    folder_id: str = ''
    paths: List[str] = []

def shared_folders_load():
    try:
        with open(SHARED_FOLDERS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    folders = data.get('folders')
    if not isinstance(folders, list):
        folders = []
    return {'folders': [f for f in folders if isinstance(f, dict)]}

def shared_folders_save(data):
    os.makedirs(_app_main.DATA_DIR, exist_ok=True)
    with open(SHARED_FOLDERS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def shared_folder_by_id(folder_id):
    for entry in shared_folders_load().get('folders', []):
        if entry.get('id') == folder_id:
            return entry
    return None

def shared_folder_abs(entry):
    rel = (entry or {}).get('rel') or ''
    return os.path.normpath(os.path.join(BASE_DIR, rel))

def shared_resolve_register(path):
    """校验 path 必须位于项目目录内、是一个存在的子目录（非项目根）。返回 (abs, rel)。"""
    raw = (path or '').strip().strip('"').strip("'")
    if not raw:
        raise HTTPException(status_code=400, detail='请提供文件夹路径')
    candidate = raw if os.path.isabs(raw) else os.path.join(BASE_DIR, raw)
    abs_path = os.path.normpath(os.path.abspath(candidate))
    base = os.path.normpath(os.path.abspath(BASE_DIR))
    try:
        common = os.path.commonpath([abs_path, base])
    except ValueError:
        raise HTTPException(status_code=400, detail='只允许登记项目目录内的文件夹')
    if common != base:
        raise HTTPException(status_code=400, detail='只允许登记项目目录内的文件夹')
    if abs_path == base:
        raise HTTPException(status_code=400, detail='不能直接登记项目根目录，请选择子文件夹')
    if not os.path.isdir(abs_path):
        raise HTTPException(status_code=400, detail='文件夹不存在')
    rel = os.path.relpath(abs_path, base)
    return (abs_path, rel)

def shared_child_abs(folder_abs, rel):
    """把相对 folder_abs 的子路径解析为绝对路径，并防止越界访问。"""
    rel = (rel or '').replace('\\', '/').lstrip('/')
    abs_path = os.path.normpath(os.path.join(folder_abs, rel))
    base = os.path.normpath(os.path.abspath(folder_abs))
    try:
        common = os.path.commonpath([os.path.abspath(abs_path), base])
    except ValueError:
        raise HTTPException(status_code=400, detail='非法路径')
    if common != base:
        raise HTTPException(status_code=400, detail='非法路径')
    return abs_path

def scan_shared_tree(folder_id, folder_abs, rel_prefix='', display='', counter=None):
    """递归扫描共享文件夹，返回 {id,name,path,items,children}。"""
    if counter is None:
        counter = {'n': 0}
    node = {'id': f"{folder_id}:{rel_prefix or '__root__'}", 'name': display or os.path.basename(folder_abs) or folder_abs, 'path': rel_prefix, 'items': [], 'children': []}
    try:
        entries = sorted(os.scandir(folder_abs), key=lambda e: (not e.is_dir(), e.name.lower()))
    except OSError:
        return node
    for ent in entries:
        if counter['n'] >= SHARED_SCAN_MAX_ENTRIES:
            break
        if ent.name.startswith('.') or ent.name.startswith('._'):
            continue
        child_rel = f'{rel_prefix}/{ent.name}'.lstrip('/')
        if ent.is_dir():
            child = scan_shared_tree(folder_id, ent.path, child_rel, ent.name, counter)
            if child['items'] or child['children']:
                node['children'].append(child)
        elif ent.is_file():
            ext = os.path.splitext(ent.name)[1].lower()
            if ext not in SHARED_MEDIA_EXTS:
                continue
            counter['n'] += 1
            try:
                st = ent.stat()
                size = st.st_size
                mtime = int(st.st_mtime * 1000)
            except OSError:
                size = 0
                mtime = 0
            node['items'].append({'id': f'{folder_id}:{child_rel}', 'name': ent.name, 'url': f'/api/shared-folders/{folder_id}/file?path={urllib.parse.quote(child_rel)}', 'kind': asset_library_media_kind(ent.name), 'size': size, 'lastModified': mtime, 'relativePath': child_rel, 'folderId': folder_id})
    return node

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