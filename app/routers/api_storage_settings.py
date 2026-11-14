"""Router: /api/storage-settings domain (auto-split from main.py)."""
from __future__ import annotations
import sys as _sys
import os as _os
import logging

logger = logging.getLogger(__name__)
_app_main = _sys.modules.get(_os.environ.get('FIREFLY_MAIN_MODULE', '')) or _sys.modules.get('main') or _sys.modules['__main__']
import os
import json
import firefly_json_io
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
    # #345：存储目录配坏过一次，下次启动会静默回到默认目录，看起来像“资源丢了”。
    # read_json 会先把坏文件改名 .corrupt-<UTC> 保住字节并记诊断（界面会提示）。
    raw = firefly_json_io.read_json(STORAGE_SETTINGS_FILE, default={}, encoding='utf-8-sig')
    if not isinstance(raw, dict):
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
    # #345：以前 open("w") 先截断再 dump，中途死掉就是一个 0 字节的存储设置。
    firefly_json_io.write_json_atomic(STORAGE_SETTINGS_FILE, dirs)
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