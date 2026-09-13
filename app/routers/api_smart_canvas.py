"""Router: /api/smart-canvas domain (auto-split from main.py)."""
from __future__ import annotations
import sys as _sys
import os as _os
import logging

logger = logging.getLogger(__name__)
_app_main = _sys.modules.get(_os.environ.get('FIREFLY_MAIN_MODULE', '')) or _sys.modules.get('main') or _sys.modules['__main__']
import os
import time
import shutil
from fastapi import APIRouter, BackgroundTasks, File, Form, Header, HTTPException, Query, Request, Response, UploadFile, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from app.core import (BASE_DIR, BaseModel, HTTPException, List, SmartCanvasGroupExportItem, SmartCanvasGroupExportRequest, _os, _sys, builtin_prompt_templates, os, prompt_template_markdown_path, sanitize_export_filename, shutil, time)
_from_main_names = ['OUTPUT_DIR', 'output_file_from_url', 'smart_group_export_folder']
import app.services as _svc
for _nm in _from_main_names:
    _v = getattr(_app_main, _nm, None)
    if _v is None:
        _v = getattr(_svc, _nm, None)
    globals()[_nm] = _v

router = APIRouter()

class SmartCanvasGroupExportItem(BaseModel):
    kind: str = ''
    url: str = ''
    text: str = ''
    name: str = ''

class SmartCanvasGroupExportRequest(BaseModel):
    folder: str = ''
    group_name: str = 'group'
    items: List[SmartCanvasGroupExportItem] = []

@router.get('/api/smart-canvas/prompt-templates')
async def smart_canvas_prompt_templates():
    try:
        template_path = prompt_template_markdown_path()
        source = os.path.relpath(template_path, BASE_DIR).replace('\\', '/') if template_path else ''
        return {'templates': builtin_prompt_templates(), 'source': source}
    except Exception as e:
        logger.warning(f'读取提示词模板失败: {e}')
        return {'templates': []}

def smart_group_export_folder(folder: str, group_name: str) -> str:
    text = str(folder or '').strip()
    if text:
        path = os.path.abspath(os.path.expanduser(text))
    else:
        stamp = time.strftime('%Y%m%d-%H%M%S')
        safe_group = sanitize_export_filename(group_name or 'group', 'group')
        path = os.path.abspath(os.path.join(_app_main.OUTPUT_DIR, 'smart-groups', f'{safe_group}-{stamp}'))
    os.makedirs(path, exist_ok=True)
    return path

@router.post('/api/smart-canvas/group-export')
async def export_smart_canvas_group(payload: SmartCanvasGroupExportRequest):
    target_dir = smart_group_export_folder(payload.folder, payload.group_name)
    used_names = set()
    count = 0
    text_index = 1
    for item in payload.items[:2000]:
        kind = str(item.kind or '').lower()
        if kind == 'text':
            text = str(item.text or '')
            if not text.strip():
                continue
            base = sanitize_export_filename(item.name or f'{text_index}.txt', f'{text_index}.txt')
            if not base.lower().endswith('.txt'):
                base += '.txt'
            text_index += 1
            name, ext = os.path.splitext(base)
            out_name = base
            suffix = 2
            while out_name in used_names:
                out_name = f'{name}-{suffix}{ext}'
                suffix += 1
            used_names.add(out_name)
            with open(os.path.join(target_dir, out_name), 'w', encoding='utf-8') as f:
                f.write(text)
            count += 1
            continue
        src = _app_main.output_file_from_url(item.url)
        if not src or not os.path.isfile(src):
            continue
        base = sanitize_export_filename(item.name or os.path.basename(src), os.path.basename(src) or f'asset-{count + 1}')
        name, ext = os.path.splitext(base)
        if not ext:
            _, src_ext = os.path.splitext(src)
            ext = src_ext or '.bin'
            base = name + ext
        out_name = base
        suffix = 2
        while out_name in used_names:
            out_name = f'{name}-{suffix}{ext}'
            suffix += 1
        used_names.add(out_name)
        shutil.copy2(src, os.path.join(target_dir, out_name))
        count += 1
    if count <= 0:
        raise HTTPException(status_code=404, detail='没有可导出的内容')
    return {'ok': True, 'folder': target_dir, 'count': count}