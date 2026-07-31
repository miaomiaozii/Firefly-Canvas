"""Router: /api/prompt-libraries domain (auto-split from main.py)."""
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
from app.core import *
_from_main_names = ['DATA_DIR', 'PROMPT_LIBRARY_PATH', 'default_prompt_libraries', 'load_prompt_libraries', 'normalize_prompt_libraries', 'normalize_prompt_library_item', 'now_ms', 'public_prompt_libraries', 'save_prompt_libraries']
for _nm in _from_main_names:
    globals()[_nm] = getattr(_app_main, _nm)
router = APIRouter()

class PromptLibraryRequest(BaseModel):
    name: str = '提示词库'

class PromptLibraryItemRequest(BaseModel):
    library_id: str = ''
    item_id: str = ''
    name: str = '提示词'
    category: str = 'custom'
    positive: str = ''
    negative: str = ''
    scene: str = ''

class PromptLibraryBatchDeleteRequest(BaseModel):
    ids: List[str] = []

class PromptLibraryCategoryRequest(BaseModel):
    name: str = '新分组'
    library_id: str = ''

def normalize_prompt_category_id(category='custom'):
    category_id = re.sub('[^A-Za-z0-9_-]+', '_', str(category or 'custom'))[:40] or 'custom'
    return 'custom' if category_id in {'mine', 'my', 'personal'} else category_id

def normalize_prompt_library_item(item):
    if not isinstance(item, dict):
        item = {}
    name = sanitize_asset_name(item.get('name') or '提示词', '提示词')
    positive = str(item.get('positive') or item.get('text') or '').strip()
    return {'id': re.sub('[^A-Za-z0-9_-]+', '_', str(item.get('id') or item.get('item_id') or f'tpl_{uuid.uuid4().hex[:12]}'))[:60], 'name': name, 'category': normalize_prompt_category_id(item.get('category') or 'custom'), 'scene': str(item.get('scene') or '').strip()[:500], 'positive': positive, 'negative': str(item.get('negative') or '').strip(), 'params': item.get('params') if isinstance(item.get('params'), dict) else {}, 'created_at': int(item.get('created_at') or _app_main.now_ms()), 'updated_at': int(item.get('updated_at') or item.get('created_at') or _app_main.now_ms())}

def seed_system_prompt_library():
    return {'id': 'system', 'name': '系统提示词库', 'type': 'prompt', 'items': builtin_prompt_templates(), 'categories': defaultPromptTemplateCategories()}

def default_prompt_libraries():
    return {'active_library_id': 'system', 'libraries': [seed_system_prompt_library()], 'updated_at': _app_main.now_ms()}

def defaultPromptTemplateCategories():
    return [{'id': 'view', 'name': '视角'}, {'id': 'storyboard', 'name': '分镜'}, {'id': 'character', 'name': '角色'}, {'id': 'product', 'name': '产品'}, {'id': 'lighting', 'name': '光影'}, {'id': 'custom', 'name': '我的'}]

def normalize_prompt_template_categories(*category_lists, include_defaults=True):
    normalized = []
    seen = set()

    def add_category(category):
        if not isinstance(category, dict):
            return
        cat_id = normalize_prompt_category_id(category.get('id') or category.get('name') or 'custom')
        if cat_id in seen:
            return
        seen.add(cat_id)
        name = sanitize_asset_name(category.get('name') or cat_id, cat_id)
        normalized.append({'id': cat_id, 'name': name})
    for categories in category_lists:
        if isinstance(categories, list):
            for category in categories:
                add_category(category)
    if include_defaults and (not normalized):
        for category in defaultPromptTemplateCategories():
            add_category(category)
    return normalized

def normalize_prompt_libraries(data):
    if not isinstance(data, dict):
        data = default_prompt_libraries()
    raw_libraries = data.get('libraries') if isinstance(data.get('libraries'), list) else []
    raw_libraries = [lib for lib in raw_libraries if isinstance(lib, dict)]
    if not any((lib.get('id') == 'system' for lib in raw_libraries)):
        raw_libraries = [seed_system_prompt_library()] + raw_libraries
    libraries = []
    seen_lib_ids = set()
    for raw in raw_libraries:
        is_system = raw.get('id') == 'system'
        if is_system:
            lib_id = 'system'
        else:
            lib_id = re.sub('[^A-Za-z0-9_-]+', '_', str(raw.get('id') or f'lib_{uuid.uuid4().hex[:12]}'))[:60] or f'lib_{uuid.uuid4().hex[:12]}'
        if lib_id in seen_lib_ids:
            continue
        seen_lib_ids.add(lib_id)
        items = []
        seen_items = set()
        for raw_item in raw.get('items') if isinstance(raw.get('items'), list) else []:
            if not isinstance(raw_item, dict):
                continue
            item = normalize_prompt_library_item(raw_item)
            item_id = item.get('id') or f'tpl_{uuid.uuid4().hex[:12]}'
            if item_id in seen_items:
                continue
            seen_items.add(item_id)
            items.append(item)
        default_name = '系统提示词库' if is_system else '提示词库'
        raw_categories = raw.get('categories') if isinstance(raw.get('categories'), list) else []
        if not is_system:
            builtin_ids = {'view', 'storyboard', 'character', 'product', 'lighting', 'custom'}
            raw_categories = [c for c in raw_categories if isinstance(c, dict) and normalize_prompt_category_id(c.get('id') or c.get('name') or '') not in builtin_ids]
        libraries.append({'id': lib_id, 'name': sanitize_asset_name(raw.get('name') or default_name, default_name), 'type': 'prompt', 'readonly': False, 'system': is_system, 'categories': normalize_prompt_template_categories(raw_categories, include_defaults=is_system), 'items': items})
    active = str(data.get('active_library_id') or 'system')
    if not any((lib['id'] == active for lib in libraries)):
        active = 'system' if any((lib['id'] == 'system' for lib in libraries)) else libraries[0]['id'] if libraries else 'system'
    return {'active_library_id': active, 'libraries': libraries, 'updated_at': int(data.get('updated_at') or _app_main.now_ms())}

def load_prompt_libraries():
    if not os.path.exists(PROMPT_LIBRARY_PATH):
        data = default_prompt_libraries()
        return save_prompt_libraries(data)
    try:
        with open(PROMPT_LIBRARY_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        data = default_prompt_libraries()
    if not isinstance(data, dict):
        data = default_prompt_libraries()
    normalized = normalize_prompt_libraries(data)
    if normalized.get('active_library_id') != data.get('active_library_id') or normalized.get('libraries') != data.get('libraries'):
        return save_prompt_libraries(normalized)
    return normalized

def save_prompt_libraries(data):
    data = normalize_prompt_libraries(data)
    data['updated_at'] = _app_main.now_ms()
    os.makedirs(_app_main.DATA_DIR, exist_ok=True)
    with open(PROMPT_LIBRARY_PATH, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return data

def public_prompt_libraries(data=None):
    data = normalize_prompt_libraries(data or load_prompt_libraries())
    return {'active_library_id': data.get('active_library_id') or (data.get('libraries') or [{}])[0].get('id') or 'system', 'libraries': data.get('libraries') or [], 'updated_at': data.get('updated_at') or _app_main.now_ms()}

def find_prompt_library(data, library_id=''):
    if not isinstance(data, dict):
        return None
    libraries = data.get('libraries') if isinstance(data.get('libraries'), list) else []
    library_id = str(library_id or data.get('active_library_id') or '').strip()
    return next((item for item in libraries if item.get('id') == library_id), None) or (libraries[0] if libraries else None)

@router.get('/api/prompt-libraries')
async def get_prompt_libraries():
    return {'library': public_prompt_libraries()}

@router.post('/api/prompt-libraries')
async def create_prompt_library(payload: PromptLibraryRequest):
    data = load_prompt_libraries()
    library = {'id': f'lib_{uuid.uuid4().hex[:12]}', 'name': sanitize_asset_name(payload.name, '提示词库'), 'type': 'prompt', 'categories': [], 'items': []}
    data.setdefault('libraries', []).append(library)
    data['active_library_id'] = library['id']
    data = save_prompt_libraries(data)
    new_lib = next((lib for lib in data.get('libraries', []) if lib.get('id') == library['id']), library)
    return {'library': public_prompt_libraries(data), 'prompt_library': new_lib}

@router.patch('/api/prompt-libraries/{library_id}')
async def rename_prompt_library(library_id: str, payload: PromptLibraryRequest):
    data = load_prompt_libraries()
    library = find_prompt_library(data, library_id)
    if not library or library.get('id') != library_id:
        raise HTTPException(status_code=404, detail='提示词库不存在')
    library['name'] = sanitize_asset_name(payload.name, library.get('name') or '提示词库')
    data = save_prompt_libraries(data)
    return {'library': public_prompt_libraries(data), 'prompt_library': library}

@router.delete('/api/prompt-libraries/{library_id}')
async def delete_prompt_library(library_id: str):
    if library_id == 'system':
        raise HTTPException(status_code=400, detail='系统提示词库不能删除，可以删除其中的提示词')
    data = load_prompt_libraries()
    libraries = data.get('libraries', []) or []
    kept = [lib for lib in libraries if lib.get('id') != library_id]
    if len(kept) == len(libraries):
        raise HTTPException(status_code=404, detail='提示词库不存在')
    data['libraries'] = kept
    if data.get('active_library_id') == library_id:
        data['active_library_id'] = 'system'
    data = save_prompt_libraries(data)
    return {'library': public_prompt_libraries(data)}

@router.post('/api/prompt-libraries/items')
async def add_prompt_library_item(payload: PromptLibraryItemRequest):
    data = load_prompt_libraries()
    library = find_prompt_library(data, payload.library_id)
    if not library:
        raise HTTPException(status_code=404, detail='提示词库不存在')
    if not str(payload.positive or '').strip():
        raise HTTPException(status_code=400, detail='提示词内容不能为空')
    item = normalize_prompt_library_item({'id': f'tpl_{uuid.uuid4().hex[:12]}', 'name': payload.name, 'category': payload.category, 'positive': payload.positive, 'negative': payload.negative, 'scene': payload.scene, 'created_at': _app_main.now_ms(), 'updated_at': _app_main.now_ms()})
    library.setdefault('items', []).insert(0, item)
    data['active_library_id'] = library.get('id') or data.get('active_library_id')
    data = save_prompt_libraries(data)
    return {'library': public_prompt_libraries(data), 'item': item}

@router.patch('/api/prompt-libraries/items/{item_id}')
async def update_prompt_library_item(item_id: str, payload: PromptLibraryItemRequest):
    data = load_prompt_libraries()
    for library in data.get('libraries', []) or []:
        if payload.library_id and library.get('id') != payload.library_id:
            continue
        for index, item in enumerate(library.get('items', []) or []):
            if item.get('id') == item_id:
                next_item = normalize_prompt_library_item({**item, 'name': payload.name or item.get('name'), 'category': payload.category or item.get('category'), 'positive': payload.positive or item.get('positive'), 'negative': payload.negative, 'scene': payload.scene, 'updated_at': _app_main.now_ms()})
                library['items'][index] = next_item
                data = save_prompt_libraries(data)
                return {'library': public_prompt_libraries(data), 'item': next_item}
    raise HTTPException(status_code=404, detail='提示词不存在')

@router.delete('/api/prompt-libraries/items/{item_id}')
async def delete_prompt_library_item(item_id: str):
    data = load_prompt_libraries()
    removed = None
    for library in data.get('libraries', []) or []:
        keep = []
        for item in library.get('items', []) or []:
            if item.get('id') == item_id:
                removed = item
            else:
                keep.append(item)
        library['items'] = keep
    if not removed:
        raise HTTPException(status_code=404, detail='提示词不存在')
    data = save_prompt_libraries(data)
    return {'library': public_prompt_libraries(data), 'removed': 1}

@router.post('/api/prompt-libraries/items/delete')
async def batch_delete_prompt_library_items(payload: PromptLibraryBatchDeleteRequest):
    ids = {str(item) for item in payload.ids or [] if str(item)}
    if not ids:
        raise HTTPException(status_code=400, detail='没有选择提示词')
    data = load_prompt_libraries()
    removed = 0
    for library in data.get('libraries', []) or []:
        keep = []
        for item in library.get('items', []) or []:
            if item.get('id') in ids:
                removed += 1
            else:
                keep.append(item)
        library['items'] = keep
    data = save_prompt_libraries(data)
    return {'library': public_prompt_libraries(data), 'removed': removed}

@router.post('/api/prompt-libraries/categories')
async def add_prompt_library_category(payload: PromptLibraryCategoryRequest):
    data = load_prompt_libraries()
    library = find_prompt_library(data, payload.library_id) or find_prompt_library(data, 'system')
    if not library:
        raise HTTPException(status_code=404, detail='提示词库不存在')
    name = sanitize_asset_name(payload.name, '新分组')
    existing = {str(c.get('id')) for c in library.get('categories') or [] if isinstance(c, dict)} | PROMPT_BUILTIN_CATEGORY_IDS
    cat_id = f'pcat_{uuid.uuid4().hex[:10]}'
    while cat_id in existing:
        cat_id = f'pcat_{uuid.uuid4().hex[:10]}'
    category = {'id': cat_id, 'name': name}
    library.setdefault('categories', []).append(category)
    data = save_prompt_libraries(data)
    return {'library': public_prompt_libraries(data), 'category': category}

@router.patch('/api/prompt-libraries/categories/{category_id}')
async def rename_prompt_library_category(category_id: str, payload: PromptLibraryCategoryRequest):
    name = sanitize_asset_name(payload.name, '')
    if not name:
        raise HTTPException(status_code=400, detail='分组名称不能为空')
    data = load_prompt_libraries()
    updated = False
    for library in data.get('libraries', []) or []:
        for cat in library.get('categories') or []:
            if isinstance(cat, dict) and cat.get('id') == category_id:
                cat['name'] = name
                updated = True
    if not updated:
        raise HTTPException(status_code=404, detail='分组不存在')
    data = save_prompt_libraries(data)
    return {'library': public_prompt_libraries(data)}

@router.delete('/api/prompt-libraries/categories/{category_id}')
async def delete_prompt_library_category(category_id: str):
    data = load_prompt_libraries()
    found = False
    for library in data.get('libraries', []) or []:
        cats = library.get('categories') or []
        kept = [c for c in cats if not (isinstance(c, dict) and c.get('id') == category_id)]
        if len(kept) != len(cats):
            found = True
            library['categories'] = kept
            fallback = next((str(c.get('id')) for c in kept if isinstance(c, dict) and c.get('id')), '')
            for item in library.get('items', []) or []:
                if isinstance(item, dict) and item.get('category') == category_id:
                    item['category'] = fallback
    if not found:
        raise HTTPException(status_code=404, detail='分组不存在')
    data = save_prompt_libraries(data)
    return {'library': public_prompt_libraries(data)}