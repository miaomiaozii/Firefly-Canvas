"""Router: /api/storage-settings domain (auto-split from main.py)."""
from __future__ import annotations
import sys as _sys
import os as _os
import logging

logger = logging.getLogger(__name__)
_app_main = _sys.modules.get(_os.environ.get('FIREFLY_MAIN_MODULE', '')) or _sys.modules.get('main') or _sys.modules['__main__']
import os
import json
from fastapi import APIRouter, BackgroundTasks, File, Form, Header, HTTPException, Query, Request, Response, UploadFile, WebSocket, WebSocketDisconnect
from app.core import (BASE_DIR, Dict, _os, _storage_abs_path, _sys, json, os)
from app.core import _storage_abs_path
_from_main_names = ['DATA_DIR', 'DEFAULT_STORAGE_DIRS', 'LOCAL_UPLOAD_DIR', 'OUTPUT_INPUT_DIR', 'OUTPUT_OUTPUT_DIR', 'STORAGE_SETTINGS_FILE', 'apply_storage_settings', 'load_storage_settings', 'save_storage_settings']
import app.services as _svc
for _nm in _from_main_names:
    _v = getattr(_app_main, _nm, None)
    if _v is None:
        _v = getattr(_svc, _nm, None)
    globals()[_nm] = _v

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
        logger.warning(f'加载存储目录设置失败: {exc}')
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