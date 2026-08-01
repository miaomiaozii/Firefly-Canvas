"""Router: /api/local-assets domain (auto-split from main.py)."""
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
from app.core import (BaseModel, File, Form, HTTPException, List, LocalAssetCaptionRequest, LocalAssetCaptionSaveRequest, LocalAssetClassifyRequest, LocalAssetFolderRequest, LocalAssetRenameRequest, LocalAssetUrlImportItem, LocalAssetUrlImportRequest, Request, UploadFile, _local_upload_folder_node, _local_upload_kind_ext, _local_upload_rel_path, _local_upload_safe_file_stem, _local_upload_safe_folder_name, _os, _sniff_image_ext_bytes, _sys, base64, ensure_same_origin_request, httpx, json, os, re, sanitize_asset_name, urllib, uuid)
from app.core import _local_upload_folder_node, _local_upload_kind_ext, _local_upload_rel_path, _local_upload_safe_file_stem, _local_upload_safe_folder_name, _sniff_image_ext_bytes
_from_main_names = ['LOCAL_UPLOAD_DIR', '_local_upload_abs', '_local_upload_caption_path', '_local_upload_classification_path', '_local_upload_item', '_local_upload_safe_folder', '_local_upload_safe_path', '_local_upload_tree_and_items', '_write_local_upload_classification', 'caption_image_with_provider', 'classify_asset_image_best_effort', 'classify_image_with_provider', 'normalize_asset_classification']
import app.services as _svc
for _nm in _from_main_names:
    _v = getattr(_app_main, _nm, None)
    if _v is None:
        _v = getattr(_svc, _nm, None)
    globals()[_nm] = _v

router = APIRouter()

class LocalAssetCaptionRequest(BaseModel):
    names: List[str] = []
    provider: str = 'comfly'
    model: str = ''
    ms_model: str = ''
    prompt: str = '描述图片'

class LocalAssetCaptionSaveRequest(BaseModel):
    name: str = ''
    caption: str = ''

class LocalAssetClassifyRequest(BaseModel):
    names: List[str] = []
    provider: str = 'comfly'
    model: str = ''
    ms_model: str = ''
    prompt: str = ''

class LocalAssetUrlImportItem(BaseModel):
    url: str = ''
    name: str = ''
    data: str = ''
    content_type: str = ''

class LocalAssetUrlImportRequest(BaseModel):
    items: List[LocalAssetUrlImportItem] = []
    folder: str = ''
    classify: bool = False
    provider: str = 'comfly'
    model: str = ''
    ms_model: str = ''
    prompt: str = ''

class LocalAssetFolderRequest(BaseModel):
    parent: str = ''
    path: str = ''
    name: str = ''

class LocalAssetRenameRequest(BaseModel):
    path: str = ''
    name: str = ''

def _write_local_upload_classification(filename, classification):
    path = _local_upload_classification_path(filename)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(normalize_asset_classification(classification), f, ensure_ascii=False, indent=2)

def _local_upload_abs(rel):
    rel_path = _local_upload_rel_path(rel)
    path = os.path.abspath(os.path.join(_app_main.LOCAL_UPLOAD_DIR, rel_path))
    root = os.path.abspath(_app_main.LOCAL_UPLOAD_DIR)
    try:
        common = os.path.commonpath([root, path])
    except ValueError:
        raise HTTPException(status_code=400, detail='非法路径')
    if common != root:
        raise HTTPException(status_code=400, detail='非法路径')
    return (rel_path, path)

def _local_upload_safe_path(name):
    filename, path = _local_upload_abs(name)
    if not filename:
        raise HTTPException(status_code=400, detail='文件名不能为空')
    return (filename, path)

def _local_upload_safe_folder(path_value):
    rel, path = _local_upload_abs(path_value)
    return (rel, path)

def _local_upload_safe_folder_name(name):
    cleaned = sanitize_asset_name(os.path.basename(str(name or '').strip()), '')
    cleaned = re.sub('[\\\\/]+', '_', cleaned).strip(' ._')
    if not cleaned:
        raise HTTPException(status_code=400, detail='文件夹名称不能为空')
    return cleaned[:60]

def _local_upload_safe_file_stem(name):
    raw = os.path.splitext(os.path.basename(str(name or '').strip()))[0]
    cleaned = sanitize_asset_name(raw, '')
    cleaned = re.sub('[\\\\/]+', '_', cleaned).strip(' ._')
    if not cleaned:
        raise HTTPException(status_code=400, detail='文件名称不能为空')
    return cleaned[:120]

def _local_upload_folder_node(path='', name='全部上传'):
    rel = _local_upload_rel_path(path)
    return {'id': rel or '__root__', 'path': rel, 'name': name if not rel else os.path.basename(rel), 'items': [], 'children': []}

def _local_upload_tree_and_items():
    root_node = _local_upload_folder_node('', '全部上传')
    folder_map = {'': root_node}
    items = []
    for current, dirs, files in os.walk(_app_main.LOCAL_UPLOAD_DIR):
        dirs[:] = sorted([d for d in dirs if not d.startswith('.') and (not d.startswith('._'))], key=str.lower)
        rel_dir = os.path.relpath(current, _app_main.LOCAL_UPLOAD_DIR).replace('\\', '/')
        if rel_dir == '.':
            rel_dir = ''
        node = folder_map.get(rel_dir)
        if node is None:
            node = _local_upload_folder_node(rel_dir)
            folder_map[rel_dir] = node
        for dirname in dirs:
            child_rel = f'{rel_dir}/{dirname}'.lstrip('/')
            child = _local_upload_folder_node(child_rel)
            folder_map[child_rel] = child
            node['children'].append(child)
        for name in sorted(files, key=str.lower):
            if name.startswith('.') or name.startswith('._'):
                continue
            rel_file = f'{rel_dir}/{name}'.lstrip('/')
            kind, _ = _local_upload_kind_ext(name, '')
            if kind is None:
                continue
            item = _local_upload_item(rel_file)
            node['items'].append(item)
            items.append(item)

    def fill_counts(node):
        total = len(node.get('items') or [])
        for child in node.get('children') or []:
            total += fill_counts(child)
        node['count'] = total
        return total
    fill_counts(root_node)
    items.sort(key=lambda it: it.get('created_at') or 0, reverse=True)
    return (root_node, items)

@router.post('/api/local-assets/upload')
async def upload_local_assets(files: List[UploadFile]=File(...), folder: str=Form('')):
    uploaded = []
    folder_rel, folder_abs = _local_upload_safe_folder(folder)
    os.makedirs(folder_abs, exist_ok=True)
    for file in files:
        content = await file.read()
        if not content:
            continue
        kind, ext = _local_upload_kind_ext(file.filename, file.content_type)
        if kind is None:
            continue
        base = os.path.splitext(os.path.basename(file.filename or 'file'))[0]
        base = re.sub('[^0-9A-Za-z一-鿿._-]+', '_', base).strip('_') or 'file'
        base = base[:60]
        filename = f'up_{uuid.uuid4().hex[:12]}_{base}{ext}'
        rel_name = f'{folder_rel}/{filename}'.lstrip('/')
        path = os.path.join(folder_abs, filename)
        with open(path, 'wb') as f:
            f.write(content)
        if kind == 'image':
            classification = await classify_asset_image_best_effort(path)
            if classification:
                _write_local_upload_classification(rel_name, classification)
        uploaded.append(_local_upload_item(rel_name))
    return {'files': uploaded}

@router.post('/api/local-assets/import-urls')
async def import_local_assets_from_urls(payload: LocalAssetUrlImportRequest):
    uploaded = []
    results = []
    folder_rel, folder_abs = _local_upload_safe_folder(payload.folder)
    os.makedirs(folder_abs, exist_ok=True)
    timeout = httpx.Timeout(connect=20.0, read=120.0, write=30.0, pool=20.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers={'User-Agent': 'Firefly-Canvas-Asset-Importer/1.0'}) as client:
        for entry in (payload.items or [])[:200]:
            src_url = str(entry.url or '').strip()
            inline_data = str(entry.data or '').strip()
            result = {'url': src_url, 'ok': False, 'file': '', 'error': ''}
            if not inline_data and (not src_url.startswith(('http://', 'https://'))):
                result['error'] = '仅支持 http(s) 素材地址'
                results.append(result)
                continue
            try:
                if inline_data:
                    content_type = str(entry.content_type or '').split(';', 1)[0].strip().lower()
                    b64 = inline_data
                    if inline_data.startswith('data:'):
                        header, _, b64 = inline_data.partition(',')
                        if not content_type:
                            content_type = header[5:].split(';', 1)[0].strip().lower()
                    try:
                        content = base64.b64decode(b64, validate=False)
                    except Exception:
                        raise HTTPException(status_code=400, detail='素材数据无法解码')
                    name_path = urllib.parse.urlparse(src_url).path
                else:
                    response = await client.get(src_url)
                    response.raise_for_status()
                    content_type = response.headers.get('Content-Type', '').split(';', 1)[0].strip().lower()
                    content = response.content
                    name_path = urllib.parse.urlparse(src_url).path
                kind, ext = _local_upload_kind_ext(name_path, content_type)
                if kind == 'image':
                    real = _sniff_image_ext_bytes(content[:16])
                    if real and (not (real == '.jpg' and ext == '.jpeg')):
                        ext = real
                if kind not in ('image', 'video'):
                    raise HTTPException(status_code=400, detail=f'不是图片或视频资源：{content_type or src_url}')
                if not content:
                    raise HTTPException(status_code=400, detail='素材内容为空')
                if entry.name:
                    base = os.path.splitext(entry.name)[0]
                else:
                    base = os.path.splitext(os.path.basename(urllib.parse.unquote(name_path)))[0]
                base = base or ('web-video' if kind == 'video' else 'web-image')
                base = re.sub('[^0-9A-Za-z一-鿿._-]+', '_', base).strip('_') or ('web-video' if kind == 'video' else 'web-image')
                base = base[:60]
                if ext and base.lower().endswith(ext.lower()):
                    base = base[:-len(ext)].rstrip('.') or ('web-video' if kind == 'video' else 'web-image')
                filename = f'up_{uuid.uuid4().hex[:12]}_{base}{ext}'
                rel_name = f'{folder_rel}/{filename}'.lstrip('/')
                path = os.path.join(folder_abs, filename)
                with open(path, 'wb') as f:
                    f.write(content)
                if payload.classify and kind == 'image':
                    classification = await classify_asset_image_best_effort(path, payload.provider, payload.model, payload.ms_model, payload.prompt)
                    if classification:
                        _write_local_upload_classification(rel_name, classification)
                item = _local_upload_item(rel_name)
                uploaded.append(item)
                result.update({'ok': True, 'file': rel_name, 'item': item})
            except HTTPException as exc:
                result['error'] = str(exc.detail or '导入失败')
            except Exception as exc:
                result['error'] = str(exc) or '导入失败'
            results.append(result)
    return {'ok': True, 'count': len(uploaded), 'files': uploaded, 'items': results}

@router.get('/api/local-assets')
async def list_local_assets():
    tree, items = _local_upload_tree_and_items()
    return {'items': items, 'tree': tree}

@router.post('/api/local-assets/folders')
async def create_local_asset_folder(payload: LocalAssetFolderRequest, request: Request):
    ensure_same_origin_request(request)
    parent_rel, parent_abs = _local_upload_safe_folder(payload.parent)
    if not os.path.isdir(parent_abs):
        raise HTTPException(status_code=404, detail='父文件夹不存在')
    name = _local_upload_safe_folder_name(payload.name)
    rel = f'{parent_rel}/{name}'.lstrip('/')
    _, abs_path = _local_upload_safe_folder(rel)
    if os.path.exists(abs_path):
        raise HTTPException(status_code=400, detail='同名文件夹已存在')
    os.makedirs(abs_path, exist_ok=False)
    tree, items = _local_upload_tree_and_items()
    return {'ok': True, 'folder': {'path': rel, 'name': name}, 'tree': tree, 'items': items}

@router.patch('/api/local-assets/folders')
async def rename_local_asset_folder(payload: LocalAssetFolderRequest, request: Request):
    ensure_same_origin_request(request)
    rel, abs_path = _local_upload_safe_folder(payload.path)
    if not rel:
        raise HTTPException(status_code=400, detail='根目录不能重命名')
    if not os.path.isdir(abs_path):
        raise HTTPException(status_code=404, detail='文件夹不存在')
    name = _local_upload_safe_folder_name(payload.name)
    parent = os.path.dirname(rel).replace('\\', '/')
    new_rel = f'{parent}/{name}'.lstrip('/')
    _, new_abs = _local_upload_safe_folder(new_rel)
    if os.path.exists(new_abs):
        raise HTTPException(status_code=400, detail='同名文件夹已存在')
    os.rename(abs_path, new_abs)
    tree, items = _local_upload_tree_and_items()
    return {'ok': True, 'folder': {'path': new_rel, 'name': name}, 'tree': tree, 'items': items}

@router.patch('/api/local-assets/items')
async def rename_local_asset_item(payload: LocalAssetRenameRequest, request: Request):
    ensure_same_origin_request(request)
    rel, abs_path = _local_upload_safe_path(payload.path)
    if not os.path.isfile(abs_path):
        raise HTTPException(status_code=404, detail='本地素材不存在')
    kind, ext = _local_upload_kind_ext(rel, '')
    if kind is None:
        raise HTTPException(status_code=400, detail='不支持的素材类型')
    new_stem = _local_upload_safe_file_stem(payload.name)
    old_ext = os.path.splitext(rel)[1] or ext
    parent = os.path.dirname(rel).replace('\\', '/')
    new_rel = f'{parent}/{new_stem}{old_ext}'.lstrip('/')
    if new_rel == rel:
        tree, items = _local_upload_tree_and_items()
        return {'ok': True, 'item': _local_upload_item(rel), 'tree': tree, 'items': items}
    _, new_abs = _local_upload_abs(new_rel)
    if os.path.exists(new_abs):
        raise HTTPException(status_code=400, detail='同名素材已存在')
    os.rename(abs_path, new_abs)
    old_caption = _local_upload_caption_path(rel)
    new_caption = _local_upload_caption_path(new_rel)
    if os.path.isfile(old_caption) and (not os.path.exists(new_caption)):
        os.rename(old_caption, new_caption)
    old_classification = _local_upload_classification_path(rel)
    new_classification = _local_upload_classification_path(new_rel)
    if os.path.isfile(old_classification) and (not os.path.exists(new_classification)):
        os.rename(old_classification, new_classification)
    tree, items = _local_upload_tree_and_items()
    return {'ok': True, 'item': _local_upload_item(new_rel), 'old_path': rel, 'tree': tree, 'items': items}

@router.post('/api/local-assets/delete')
async def delete_local_assets(payload: dict, request: Request):
    ensure_same_origin_request(request)
    names = payload.get('names') if isinstance(payload, dict) else None
    if not isinstance(names, list):
        names = []
    deleted = []
    for name in names:
        try:
            rel, path = _local_upload_safe_path(name)
        except HTTPException:
            continue
        if os.path.isfile(path):
            try:
                os.remove(path)
                txt_path = _local_upload_caption_path(rel)
                if os.path.isfile(txt_path):
                    os.remove(txt_path)
                cls_path = _local_upload_classification_path(rel)
                if os.path.isfile(cls_path):
                    os.remove(cls_path)
                deleted.append(rel)
            except OSError:
                pass
    return {'deleted': deleted}

@router.post('/api/local-assets/move')
async def move_local_assets(payload: dict, request: Request):
    """把选中的本地素材移动到目标文件夹（folder 为空表示根目录）；连同 .txt / .classification.json 兄弟文件一起搬。"""
    ensure_same_origin_request(request)
    names = payload.get('names') if isinstance(payload, dict) else None
    if not isinstance(names, list) or not names:
        raise HTTPException(status_code=400, detail='没有选择素材')
    folder_value = str(payload.get('folder') or '').strip() if isinstance(payload, dict) else ''
    target_rel, target_abs = _local_upload_safe_folder(folder_value)
    if target_rel and (not os.path.isdir(target_abs)):
        raise HTTPException(status_code=404, detail='目标文件夹不存在')
    moved = 0
    for name in names:
        try:
            rel, abs_path = _local_upload_safe_path(name)
        except HTTPException:
            continue
        if not os.path.isfile(abs_path):
            continue
        base = os.path.basename(rel)
        new_rel = f'{target_rel}/{base}'.lstrip('/') if target_rel else base
        if new_rel == rel:
            continue
        _, new_abs = _local_upload_abs(new_rel)
        if os.path.exists(new_abs):
            stem, ext = os.path.splitext(base)
            base = f'{stem}_{uuid.uuid4().hex[:6]}{ext}'
            new_rel = f'{target_rel}/{base}'.lstrip('/') if target_rel else base
            _, new_abs = _local_upload_abs(new_rel)
        try:
            os.makedirs(os.path.dirname(new_abs), exist_ok=True)
            os.rename(abs_path, new_abs)
            for src_sib, dst_sib in ((_local_upload_caption_path(rel), _local_upload_caption_path(new_rel)), (_local_upload_classification_path(rel), _local_upload_classification_path(new_rel))):
                if os.path.isfile(src_sib) and (not os.path.exists(dst_sib)):
                    os.rename(src_sib, dst_sib)
            moved += 1
        except OSError:
            continue
    tree, items = _local_upload_tree_and_items()
    return {'ok': True, 'moved': moved, 'items': items, 'tree': tree}

@router.post('/api/local-assets/caption')
async def caption_local_assets(payload: LocalAssetCaptionRequest):
    prompt = (payload.prompt or '描述图片').strip() or '描述图片'
    items = []
    ok_count = 0
    for name in (payload.names or [])[:100]:
        item = {'name': name, 'ok': False, 'caption': '', 'caption_file': '', 'error': ''}
        try:
            filename, path = _local_upload_safe_path(name)
            if not os.path.isfile(path):
                raise HTTPException(status_code=404, detail='文件不存在')
            kind, _ = _local_upload_kind_ext(filename, '')
            if kind != 'image':
                raise HTTPException(status_code=400, detail='仅支持图片素材反推提示词')
            caption, resolved_model = await caption_image_with_provider(path, prompt, payload.provider, payload.model, payload.ms_model)
            txt_path = _local_upload_caption_path(filename)
            with open(txt_path, 'w', encoding='utf-8', newline='') as f:
                f.write(caption)
            item.update({'ok': True, 'name': filename, 'caption': caption, 'caption_file': os.path.basename(txt_path), 'model': resolved_model})
            ok_count += 1
        except HTTPException as exc:
            item['error'] = str(exc.detail or '反推失败')
        except Exception as exc:
            item['error'] = str(exc) or '反推失败'
        items.append(item)
    return {'ok': True, 'count': ok_count, 'items': items}

@router.post('/api/local-assets/classify')
async def classify_local_assets(payload: LocalAssetClassifyRequest):
    items = []
    ok_count = 0
    for name in (payload.names or [])[:80]:
        item = {'name': name, 'ok': False, 'classification': None, 'classification_file': '', 'error': ''}
        try:
            filename, path = _local_upload_safe_path(name)
            if not os.path.isfile(path):
                raise HTTPException(status_code=404, detail='文件不存在')
            kind, _ = _local_upload_kind_ext(filename, '')
            if kind != 'image':
                raise HTTPException(status_code=400, detail='仅支持图片素材智能分类')
            classification = await classify_image_with_provider(path, payload.provider, payload.model, payload.ms_model, payload.prompt)
            _write_local_upload_classification(filename, classification)
            item.update({'ok': True, 'name': filename, 'classification': classification, 'classification_file': os.path.basename(_local_upload_classification_path(filename)), 'model': classification.get('model') or ''})
            ok_count += 1
        except HTTPException as exc:
            item['error'] = str(exc.detail or '智能分类失败')
        except Exception as exc:
            item['error'] = str(exc) or '智能分类失败'
        items.append(item)
    return {'ok': True, 'count': ok_count, 'items': items}

@router.patch('/api/local-assets/caption')
async def save_local_asset_caption(payload: LocalAssetCaptionSaveRequest):
    filename, path = _local_upload_safe_path(payload.name)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail='文件不存在')
    kind, _ = _local_upload_kind_ext(filename, '')
    if kind != 'image':
        raise HTTPException(status_code=400, detail='仅支持图片素材保存提示词')
    caption = str(payload.caption or '')[:100000]
    txt_path = _local_upload_caption_path(filename)
    with open(txt_path, 'w', encoding='utf-8', newline='') as f:
        f.write(caption)
    return {'ok': True, 'caption': caption, 'caption_file': os.path.basename(txt_path)}