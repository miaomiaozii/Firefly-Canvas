"""Router: /api/canvas-image-tasks domain (auto-split from main.py)."""
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
_from_main_names = ['build_online_image_result', 'run_canvas_image_task']
import app.services as _svc
for _nm in _from_main_names:
    _v = getattr(_app_main, _nm, None)
    if _v is None:
        _v = getattr(_svc, _nm, None)
    globals()[_nm] = _v

router = APIRouter()

def extract_task_id_from_text(text):
    value = str(text or '')
    match = re.search('(?:task_id|taskId|task id)\\s*[=:：]\\s*([A-Za-z0-9_.:-]+)', value, re.IGNORECASE)
    return match.group(1) if match else ''

async def run_canvas_image_task(task_id: str, payload: OnlineImageRequest):
    with CANVAS_TASK_LOCK:
        if task_id in CANVAS_TASKS:
            CANVAS_TASKS[task_id]['status'] = 'running'
            CANVAS_TASKS[task_id]['updated_at'] = time.time()
    try:
        result = await build_online_image_result(payload)
        with CANVAS_TASK_LOCK:
            CANVAS_TASKS[task_id].update({'status': 'succeeded', 'result': result, 'error': '', 'updated_at': time.time()})
    except JimengPendingError as exc:
        info = jimeng_pending_payload(exc)
        with CANVAS_TASK_LOCK:
            CANVAS_TASKS[task_id].update({'status': 'jimeng_pending', 'jimeng_pending': True, 'submit_id': exc.submit_id, 'kind': exc.kind, 'queue_info': exc.queue_info, 'message': info['message'], 'error': '', 'updated_at': time.time()})
    except Exception as exc:
        detail = getattr(exc, 'detail', None) or str(exc)
        status_code = getattr(exc, 'status_code', 500)
        upstream_task_id = getattr(exc, 'upstream_task_id', '') or extract_task_id_from_text(detail)
        with CANVAS_TASK_LOCK:
            CANVAS_TASKS[task_id].update({'status': 'failed', 'error': str(detail), 'status_code': status_code, 'upstream_task_id': upstream_task_id, 'updated_at': time.time()})

@router.post('/api/canvas-image-tasks')
async def create_canvas_image_task(payload: OnlineImageRequest):
    require_online_image_project_media_scope(payload)
    task_id = f'canvas_img_{uuid.uuid4().hex}'
    with CANVAS_TASK_LOCK:
        CANVAS_TASKS[task_id] = {'id': task_id, 'type': 'online-image', 'status': 'queued', 'created_at': time.time(), 'updated_at': time.time(), 'result': None, 'error': '', 'provider_id': payload.provider_id, 'model': payload.model}
    asyncio.create_task(run_canvas_image_task(task_id, payload))
    return {'task_id': task_id, 'status': 'queued'}

@router.get('/api/canvas-image-tasks/{task_id}')
async def get_canvas_image_task(task_id: str):
    with CANVAS_TASK_LOCK:
        task = dict(CANVAS_TASKS.get(task_id) or {})
    if not task:
        raise HTTPException(status_code=404, detail='画布任务不存在，可能服务已重启或任务已过期')
    return task