"""Router: /api/storage-settings domain (auto-split from main.py)."""
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
from app.core import _storage_abs_path
_from_main_names = ['DATA_DIR', 'DEFAULT_STORAGE_DIRS', 'LOCAL_UPLOAD_DIR', 'OUTPUT_INPUT_DIR', 'OUTPUT_OUTPUT_DIR', 'STORAGE_SETTINGS_FILE', 'apply_storage_settings', 'load_storage_settings', 'save_storage_settings']
for _nm in _from_main_names:
    globals()[_nm] = getattr(_app_main, _nm)
router = APIRouter()

def _storage_abs_path(value, fallback):
    text = str(value or '').strip()
    if not text:
        return os.path.abspath(fallback)
    text = os.path.expanduser(os.path.expandvars(text))
    if not os.path.isabs(text):
        text = os.path.join(BASE_DIR, text)
    return os.path.abspath(text)

def load_storage_settings():
    raw = {}
    try:
        if os.path.exists(STORAGE_SETTINGS_FILE):
            with open(STORAGE_SETTINGS_FILE, 'r', encoding='utf-8-sig') as f:
                raw = json.load(f) if f else {}
    except Exception as exc:
        print(f'加载存储目录设置失败: {exc}')
        raw = {}
    dirs = {}
    for key, fallback in DEFAULT_STORAGE_DIRS.items():
        dirs[key] = _storage_abs_path((raw or {}).get(key), fallback)
    return {'dirs': dirs}

def save_storage_settings(payload):
    dirs = {}
    for key, fallback in DEFAULT_STORAGE_DIRS.items():
        dirs[key] = _storage_abs_path((payload or {}).get(key), fallback)
    for path in dirs.values():
        os.makedirs(path, exist_ok=True)
    os.makedirs(_app_main.DATA_DIR, exist_ok=True)
    with open(STORAGE_SETTINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(dirs, f, ensure_ascii=False, indent=2)
    apply_storage_settings(dirs)
    return {'dirs': dirs}

def apply_storage_settings(dirs=None):
    global OUTPUT_INPUT_DIR, OUTPUT_OUTPUT_DIR, LOCAL_UPLOAD_DIR
    dirs = dirs or load_storage_settings().get('dirs') or {}
    _app_main.OUTPUT_INPUT_DIR = dirs.get('upload') or _app_main.OUTPUT_INPUT_DIR
    _app_main.OUTPUT_OUTPUT_DIR = dirs.get('generated') or _app_main.OUTPUT_OUTPUT_DIR
    _app_main.LOCAL_UPLOAD_DIR = dirs.get('local') or _app_main.LOCAL_UPLOAD_DIR

@router.get('/api/storage-settings')
async def get_storage_settings():
    settings = load_storage_settings()
    return {'dirs': settings['dirs'], 'defaults': {key: os.path.abspath(value) for key, value in DEFAULT_STORAGE_DIRS.items()}}

@router.patch('/api/storage-settings')
async def update_storage_settings(payload: Dict[str, str]):
    return save_storage_settings(payload or {})