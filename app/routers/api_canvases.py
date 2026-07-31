"""Router: /api/canvases domain (auto-split from main.py)."""
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
_from_main_names = ['ASSETS_DIR', 'ASSET_LIBRARY_PATH', 'CANVAS_DIR', 'CONVERSATION_DIR', 'HISTORY_FILE', 'MEDIA_PREVIEW_DIR', 'OUTPUT_DIR', 'OUTPUT_OUTPUT_DIR', 'canvas_path', 'cleanup_expired_canvas_trash', 'delete_media_preview_cache', 'expand_canvas_generated_media_paths', 'generated_media_path_from_url', 'iter_canvas_records', 'json_references_media_path', 'list_canvases', 'list_deleted_canvases', 'load_canvas', 'load_canvas_any', 'local_media_path_from_url', 'manager', 'now_ms', 'persisted_json_references_media_path', 'project_store_call', 'prune_generation_history_for_media', 'reset_canvas_result_nodes_for_media', 'save_canvas', 'smart_owned_result_items', 'storage_file_path']
for _nm in _from_main_names:
    globals()[_nm] = getattr(_app_main, _nm)
router = APIRouter()

class DeleteCanvasLogRequest(BaseModel):
    log_id: str
    delete_unreferenced_media: bool = False
    reset_referencing_nodes: bool = False
    base_updated_at: int = 0

class CanvasCreateRequest(BaseModel):
    title: str = '未命名画布'
    icon: str = '🧩'
    kind: str = 'classic'
    project: Optional[str] = None
    board_x: Optional[float] = None
    board_y: Optional[float] = None

class CanvasMetaUpdate(BaseModel):
    title: Optional[str] = None
    icon: Optional[str] = None
    owner: Optional[str] = None
    color: Optional[str] = None
    pinned: Optional[bool] = None
    project: Optional[str] = None
    board_x: Optional[float] = None
    board_y: Optional[float] = None

class CanvasSaveRequest(BaseModel):
    title: str = '未命名画布'
    icon: str = '🧩'
    nodes: List[Dict[str, Any]] = []
    connections: List[Dict[str, Any]] = []
    viewport: Dict[str, Any] = {}
    logs: List[Dict[str, Any]] = []
    settings: Dict[str, Any] = {}
    client_id: str = ''
    base_updated_at: int = 0

def canvas_path(canvas_id):
    cleaned = re.sub('[^a-zA-Z0-9_-]', '', canvas_id or '')
    if not cleaned:
        raise HTTPException(status_code=400, detail='无效的画布 ID')
    return os.path.join(_app_main.CANVAS_DIR, f'{cleaned}.json')

def save_canvas(canvas):
    if canvas.get('format') == 'firefly-canvas-v1' or canvas.get('kind') == 'core':
        raise HTTPException(status_code=410, detail='核心画布必须通过带版本号的增量项目接口保存')
    with CANVAS_LOCK:
        canvas['updated_at'] = max(_app_main.now_ms(), int(canvas.get('updated_at') or 0) + 1)
        with open(canvas_path(canvas['id']), 'w', encoding='utf-8') as f:
            json.dump(canvas, f, ensure_ascii=False, indent=2)

def load_canvas(canvas_id):
    try:
        return _app_main.project_store_call('canvas', canvas_id)
    except ProjectNotFound:
        pass
    path = canvas_path(canvas_id)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail='画布不存在')
    with open(path, 'r', encoding='utf-8') as f:
        canvas = json.load(f)
    if canvas.get('deleted_at'):
        raise HTTPException(status_code=404, detail='画布已在回收站')
    return canvas

def load_canvas_any(canvas_id):
    path = canvas_path(canvas_id)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail='画布不存在')
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)

def iter_canvas_records(include_deleted=False):
    cleanup_expired_canvas_trash()
    records = []
    for filename in os.listdir(_app_main.CANVAS_DIR):
        if not filename.endswith('.json'):
            continue
        try:
            with open(os.path.join(_app_main.CANVAS_DIR, filename), 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            continue
        is_deleted = bool(data.get('deleted_at'))
        if include_deleted != is_deleted:
            continue
        records.append(canvas_record(data))
    return records

def list_canvases():
    records = iter_canvas_records(include_deleted=False)
    return sorted(records, key=lambda item: (0 if item.get('pinned') else 1, -int(item.get('updated_at') or item.get('created_at') or 0)))

def list_deleted_canvases():
    records = iter_canvas_records(include_deleted=True)
    return sorted(records, key=lambda item: item['deleted_at'], reverse=True)

def collect_local_media_urls(value: Any) -> List[str]:
    """Collect local /assets and /output URLs from nested canvas log payloads."""
    urls = []
    if isinstance(value, str):
        text = value.strip()
        if text.startswith(('/assets/', '/output/', '/api/storage-files/')):
            urls.append(text)
    elif isinstance(value, dict):
        for item in value.values():
            urls.extend(collect_local_media_urls(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            urls.extend(collect_local_media_urls(item))
    return urls

def local_media_path_from_url(url: str) -> Optional[str]:
    """Resolve a local media URL using the same one-to-one mapping as app mounts."""
    if not url:
        return None
    clean = urllib.parse.unquote(str(url).split('?', 1)[0]).replace('\\', '/')
    if clean.startswith('/api/storage-files/'):
        rest = clean[len('/api/storage-files/'):].lstrip('/')
        kind, _, rel = rest.partition('/')
        return storage_file_path(kind, rel) if kind and rel else None
    if clean.startswith('/assets/'):
        root = _app_main.ASSETS_DIR
        rel = clean[len('/assets/'):]
    elif clean.startswith('/output/'):
        root = _app_main.OUTPUT_DIR
        rel = clean[len('/output/'):]
    else:
        return None
    rel = rel.lstrip('/')
    if not rel:
        return None
    root = os.path.abspath(root)
    path = os.path.abspath(os.path.join(root, rel))
    try:
        return path if os.path.commonpath([root, path]) == root and os.path.exists(path) else None
    except ValueError:
        return None

def generated_media_path_from_url(url: str) -> Optional[str]:
    """Resolve a URL only when it points inside a generated-output directory."""
    try:
        path = local_media_path_from_url(url)
    except (HTTPException, OSError, ValueError):
        return None
    if not path or not os.path.isfile(path):
        return None
    path = os.path.realpath(path)
    for root in (_app_main.OUTPUT_OUTPUT_DIR, _app_main.OUTPUT_DIR):
        root = os.path.realpath(root)
        try:
            if os.path.commonpath([root, path]) == root:
                return path
        except ValueError:
            continue
    return None

def json_references_media_path(value: Any, target_path: str) -> bool:
    """Return True when a JSON-compatible value references the local media file."""
    target = os.path.normcase(os.path.realpath(target_path))
    if isinstance(value, str):
        try:
            resolved = local_media_path_from_url(value.strip())
        except (HTTPException, OSError, ValueError):
            return False
        return bool(resolved and os.path.normcase(os.path.realpath(resolved)) == target)
    if isinstance(value, dict):
        return any((json_references_media_path(item, target) for item in value.values()))
    if isinstance(value, (list, tuple)):
        return any((json_references_media_path(item, target) for item in value))
    return False

def persisted_json_references_media_path(target_path: str) -> bool:
    """Scan persisted user documents before deleting generated media.

    Canvas logs are only one possible owner. Nodes, other canvases, ordinary
    generation history, conversations, and asset metadata all count as live
    references and keep the file on disk.
    """
    candidates = [_app_main.ASSET_LIBRARY_PATH]
    for root in (_app_main.CANVAS_DIR, _app_main.CONVERSATION_DIR):
        if os.path.isdir(root):
            for current, _, files in os.walk(root):
                candidates.extend((os.path.join(current, name) for name in files if name.lower().endswith('.json')))
    seen = set()
    for path in candidates:
        path = os.path.abspath(path)
        if path in seen or not os.path.isfile(path):
            continue
        seen.add(path)
        try:
            with open(path, 'r', encoding='utf-8-sig') as handle:
                value = json.load(handle)
        except (OSError, UnicodeError, json.JSONDecodeError):
            return True
        if json_references_media_path(value, target_path):
            return True
    return False

def prune_generation_history_for_media(paths: List[str]) -> int:
    """Remove history cards that would otherwise point at deleted media."""
    if not paths or not os.path.isfile(_app_main.HISTORY_FILE):
        return 0
    try:
        with HISTORY_LOCK:
            with open(_app_main.HISTORY_FILE, 'r', encoding='utf-8-sig') as handle:
                history = json.load(handle)
            if not isinstance(history, list):
                return 0
            kept = [record for record in history if not any((json_references_media_path(record, path) for path in paths))]
            removed = len(history) - len(kept)
            if removed:
                with open(_app_main.HISTORY_FILE, 'w', encoding='utf-8') as handle:
                    json.dump(kept, handle, ensure_ascii=False, indent=4)
            return removed
    except (OSError, UnicodeError, json.JSONDecodeError):
        return 0

def smart_owned_result_items(images: List[Any], paths: List[str]) -> List[Any]:
    """Return generated results, including legacy results without the marker."""
    return [item for item in images if isinstance(item, dict) and item.get('loopInputPreview') is not True and (item.get('generatedResult') is True or any((json_references_media_path(item, path) for path in paths)))]

def expand_canvas_generated_media_paths(canvas: Dict[str, Any], paths: List[str]) -> List[str]:
    """Include every generated result owned by a result node touched by the log."""
    expanded = list(paths)
    for node in list(canvas.get('nodes') or []):
        node_type = str(node.get('type') or '').strip().lower()
        images = list(node.get('images') or [])
        if node_type == 'output':
            owned_items = images
        elif node_type == 'smart-image':
            owned_items = smart_owned_result_items(images, paths)
        else:
            owned_items = []
        if not any((json_references_media_path(item, path) for item in owned_items for path in paths)):
            continue
        for item in owned_items:
            for url in collect_local_media_urls(item):
                candidate = generated_media_path_from_url(url)
                if candidate and candidate not in expanded:
                    expanded.append(candidate)
    return expanded

def reset_canvas_result_nodes_for_media(canvas: Dict[str, Any], paths: List[str]) -> List[str]:
    """Clear generated media while preserving prompts, references, settings and links."""
    reset_ids = []
    updated_nodes = []
    for node in list(canvas.get('nodes') or []):
        node = dict(node)
        node_type = str(node.get('type') or '').strip().lower()
        changed = False
        if isinstance(node.get('generatedOutputs'), list):
            outputs = list(node.get('generatedOutputs') or [])
            kept_outputs = [item for item in outputs if not any((json_references_media_path(item, path) for path in paths))]
            if len(kept_outputs) != len(outputs):
                node['generatedOutputs'] = kept_outputs
                changed = True
        if node_type in {'smart-image', 'output'} and isinstance(node.get('images'), list):
            images = list(node.get('images') or [])
            if node_type == 'smart-image':
                owned_items = smart_owned_result_items(images, paths)
            else:
                owned_items = images
            owns_target = any((json_references_media_path(item, path) for item in owned_items for path in paths))
            kept_images = [item for item in images if not (owns_target and item in owned_items and any((json_references_media_path(item, path) for path in paths)))]
            if len(kept_images) != len(images):
                node['images'] = kept_images
                changed = True
                if node_type == 'output':
                    node['_pending'] = []
                    node['imageComparisons'] = {}
                if node_type == 'smart-image':
                    node['pending'] = 0
                    node['running'] = False
                    node['queued'] = False
                    for key in ('jimengPending', 'pendingTasks', 'runStartedAt', 'runFinishedAt', 'runElapsedMs', 'runTimerHidden', 'outputKind', 'w', 'h'):
                        node.pop(key, None)
        elif node_type == 'image' and any((json_references_media_path(node.get('url'), path) for path in paths)):
            node['url'] = ''
            node['mediaKind'] = 'image'
            node['name'] = '空白图片'
            changed = True
        if changed and node.get('id'):
            reset_ids.append(str(node['id']))
        updated_nodes.append(node)
    if reset_ids:
        canvas['nodes'] = updated_nodes
    return reset_ids

def delete_media_preview_cache(path: str) -> int:
    """Delete derived previews for a source file before the source disappears."""
    try:
        stat = os.stat(path)
    except OSError:
        return 0
    source = os.path.realpath(path)
    removed = 0
    for width in range(0, 4097):
        keys = [hashlib.sha1(f'{source}|{stat.st_mtime_ns}|{stat.st_size}|{width}|jpg'.encode('utf-8', 'ignore')).hexdigest() + '.jpg']
        if 64 <= width <= 2048:
            preview_key = hashlib.sha1(f'{source}|{stat.st_mtime_ns}|{stat.st_size}|{width}'.encode('utf-8', 'ignore')).hexdigest()
            keys.extend((preview_key + '.webp', preview_key + '.png'))
        for name in keys:
            cache_path = os.path.join(_app_main.MEDIA_PREVIEW_DIR, name)
            try:
                if os.path.isfile(cache_path):
                    os.remove(cache_path)
                    removed += 1
            except OSError:
                pass
    return removed

@router.get('/api/canvases')
async def canvases():
    return {'canvases': list_canvases()}

@router.get('/api/canvases/trash')
async def trashed_canvases():
    return {'canvases': list_deleted_canvases(), 'retention_days': 30}

@router.post('/api/canvases')
async def create_canvas(payload: CanvasCreateRequest):
    raise HTTPException(status_code=410, detail='旧画布创建入口已停用；一个项目只拥有一张核心画布')

@router.get('/api/canvases/{canvas_id}/meta')
async def get_canvas_meta(canvas_id: str):
    canvas = load_canvas(canvas_id)
    return {'id': canvas.get('id'), 'updated_at': canvas.get('updated_at', 0), 'title': canvas.get('title', '未命名画布'), 'icon': canvas.get('icon', 'layers'), 'kind': normalize_canvas_kind(canvas.get('kind'))}

@router.post('/api/canvases/{canvas_id}/meta')
async def update_canvas_meta(canvas_id: str, payload: CanvasMetaUpdate):
    """更新画布的轻量元数据（标题/图标/负责人/颜色/置顶）。
    刻意不走 save_canvas（它会刷新 updated_at），以免打标签/置顶把画布顶到列表最前。"""

    def mutate_meta():
        with CANVAS_LOCK:
            canvas = load_canvas(canvas_id)
            if canvas.get('format') == 'firefly-canvas-v1':
                raise HTTPException(status_code=410, detail='核心画布元数据必须通过增量项目接口保存')
            if payload.title is not None:
                canvas['title'] = (payload.title or canvas.get('title') or '未命名画布')[:80]
            if payload.icon is not None:
                canvas['icon'] = (payload.icon or 'layers')[:32]
            if payload.owner is not None:
                canvas['owner'] = str(payload.owner).strip()[:40]
            if payload.color is not None:
                canvas['color'] = normalize_canvas_color(payload.color)
            if payload.pinned is not None:
                canvas['pinned'] = bool(payload.pinned)
            if payload.project is not None:
                canvas['project'] = str(payload.project).strip() or DEFAULT_PROJECT_ID
            if payload.board_x is not None:
                canvas['board_x'] = float(payload.board_x)
            if payload.board_y is not None:
                canvas['board_y'] = float(payload.board_y)
            with open(canvas_path(canvas['id']), 'w', encoding='utf-8') as f:
                json.dump(canvas, f, ensure_ascii=False, indent=2)
            return canvas
    canvas = await asyncio.to_thread(mutate_meta)
    return {'canvas': canvas_record(canvas)}

@router.get('/api/canvases/{canvas_id}')
async def get_canvas(canvas_id: str):
    return {'canvas': load_canvas(canvas_id)}

@router.post('/api/canvases/{canvas_id}/touch')
async def touch_canvas(canvas_id: str):

    def touch():
        with CANVAS_LOCK:
            canvas = load_canvas(canvas_id)
            if canvas.get('format') == 'firefly-canvas-v1':
                raise HTTPException(status_code=410, detail='核心画布没有整包触碰接口')
            save_canvas(canvas)
            return canvas
    canvas = await asyncio.to_thread(touch)
    return {'canvas': canvas_record(canvas), 'updated_at': canvas.get('updated_at', 0)}

@router.put('/api/canvases/{canvas_id}')
async def update_canvas(canvas_id: str, payload: CanvasSaveRequest):

    def mutate_canvas():
        with CANVAS_LOCK:
            canvas = load_canvas(canvas_id)
            if canvas.get('format') == 'firefly-canvas-v1':
                raise HTTPException(status_code=410, detail='核心画布必须通过增量项目接口保存')
            current_updated_at = int(canvas.get('updated_at') or 0)
            if payload.base_updated_at and current_updated_at and (int(payload.base_updated_at) < current_updated_at):
                raise HTTPException(status_code=409, detail={'message': '画布已被其他页面更新，已拒绝旧版本覆盖。', 'canvas': canvas, 'updated_at': current_updated_at})
            canvas['title'] = (payload.title or canvas.get('title') or '未命名画布')[:80]
            canvas['icon'] = (payload.icon or canvas.get('icon') or 'layers')[:32]
            canvas['kind'] = normalize_canvas_kind(canvas.get('kind'))
            canvas['nodes'] = payload.nodes
            canvas['connections'] = payload.connections
            if canvas['kind'] == 'smart':
                canvas['viewport'] = payload.viewport
            else:
                canvas['viewport'] = canvas.get('viewport') or {'x': 0, 'y': 0, 'scale': 1}
            canvas['logs'] = payload.logs[-500:]
            canvas['settings'] = payload.settings or {}
            save_canvas(canvas)
            return canvas
    canvas = await asyncio.to_thread(mutate_canvas)
    await manager.broadcast_canvas_updated(canvas_id, int(canvas.get('updated_at') or _app_main.now_ms()), payload.client_id)
    return {'canvas': canvas}

@router.post('/api/canvases/{canvas_id}/logs/delete')
async def delete_canvas_log(canvas_id: str, payload: DeleteCanvasLogRequest):
    log_id = str(payload.log_id or '').strip()
    if not log_id:
        raise HTTPException(status_code=400, detail='缺少日志 ID')

    def remove_log_record():
        with CANVAS_LOCK:
            canvas = load_canvas(canvas_id)
            if canvas.get('format') == 'firefly-canvas-v1':
                raise HTTPException(status_code=410, detail='核心画布历史必须通过项目历史接口修改')
            current_updated_at = int(canvas.get('updated_at') or 0)
            if payload.base_updated_at and current_updated_at and (int(payload.base_updated_at) < current_updated_at):
                raise HTTPException(status_code=409, detail={'message': '画布已被其他页面更新，请刷新后重试。', 'canvas': canvas, 'updated_at': current_updated_at})
            logs = list(canvas.get('logs') or [])
            target = next((item for item in logs if str(item.get('id') or '') == log_id), None)
            if not target:
                raise HTTPException(status_code=404, detail='生成日志不存在')
            candidate_paths = []
            if payload.delete_unreferenced_media:
                for url in collect_local_media_urls(target.get('outputs') or []):
                    path = generated_media_path_from_url(url)
                    if path and path not in candidate_paths:
                        candidate_paths.append(path)
            reset_node_ids = []
            if payload.reset_referencing_nodes and candidate_paths:
                candidate_paths = expand_canvas_generated_media_paths(canvas, candidate_paths)
                reset_node_ids = reset_canvas_result_nodes_for_media(canvas, candidate_paths)
            canvas['logs'] = [item for item in logs if str(item.get('id') or '') != log_id]
            save_canvas(canvas)
            return (canvas, candidate_paths, reset_node_ids)
    canvas, candidate_paths, reset_node_ids = await asyncio.to_thread(remove_log_record)

    def cleanup_unreferenced_media():
        removed_files = []
        skipped_referenced = []
        removed_previews = 0
        deletable_paths = []
        for path in candidate_paths:
            if persisted_json_references_media_path(path):
                skipped_referenced.append(os.path.basename(path))
                continue
            deletable_paths.append(path)
        prune_generation_history_for_media(deletable_paths)
        for path in deletable_paths:
            try:
                removed_previews += delete_media_preview_cache(path)
                os.remove(path)
                removed_files.append(os.path.basename(path))
            except OSError:
                skipped_referenced.append(os.path.basename(path))
        return (removed_files, skipped_referenced, removed_previews)
    removed_files = []
    skipped_referenced = []
    removed_previews = 0
    if payload.delete_unreferenced_media:

        def locked_cleanup():
            with CANVAS_LOCK:
                return cleanup_unreferenced_media()
        removed_files, skipped_referenced, removed_previews = await asyncio.to_thread(locked_cleanup)
    await manager.broadcast_canvas_updated(canvas_id, int(canvas.get('updated_at') or _app_main.now_ms()))
    return {'ok': True, 'canvas': canvas, 'removed_files': removed_files, 'removed_previews': removed_previews, 'reset_node_ids': reset_node_ids, 'skipped_referenced': skipped_referenced}

@router.delete('/api/canvases/{canvas_id}')
async def delete_canvas(canvas_id: str):

    def soft_delete():
        with CANVAS_LOCK:
            canvas = load_canvas_any(canvas_id)
            if not canvas.get('deleted_at'):
                canvas['deleted_at'] = _app_main.now_ms()
                save_canvas(canvas)
    await asyncio.to_thread(soft_delete)
    return {'ok': True}

@router.post('/api/canvases/{canvas_id}/restore')
async def restore_canvas(canvas_id: str):

    def restore():
        with CANVAS_LOCK:
            canvas = load_canvas_any(canvas_id)
            if canvas.get('deleted_at'):
                canvas.pop('deleted_at', None)
                save_canvas(canvas)
            return canvas
    canvas = await asyncio.to_thread(restore)
    return {'canvas': canvas}

@router.delete('/api/canvases/{canvas_id}/purge')
async def purge_canvas(canvas_id: str):

    def purge():
        with CANVAS_LOCK:
            path = canvas_path(canvas_id)
            if os.path.exists(path):
                os.remove(path)
    await asyncio.to_thread(purge)
    return {'ok': True}