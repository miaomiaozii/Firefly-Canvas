"""Router: /api/asset-classification-prompt domain (auto-split from main.py)."""
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
from app.core import (ASSET_CLASSIFICATION_PROMPT, Dict, HTTPException, _os, _sys, os)
_from_main_names = ['ASSET_CLASSIFICATION_PROMPT_FILE', 'DATA_DIR', 'load_asset_classification_prompt', 'save_asset_classification_prompt']
import app.services as _svc
for _nm in _from_main_names:
    _v = getattr(_app_main, _nm, None)
    if _v is None:
        _v = getattr(_svc, _nm, None)
    globals()[_nm] = _v

router = APIRouter()

@router.get('/api/asset-classification-prompt')
async def get_asset_classification_prompt():
    current = load_asset_classification_prompt()
    return {'prompt': current, 'default_prompt': ASSET_CLASSIFICATION_PROMPT, 'custom': current.strip() != ASSET_CLASSIFICATION_PROMPT.strip()}

@router.patch('/api/asset-classification-prompt')
async def update_asset_classification_prompt(payload: Dict[str, str]):
    prompt = save_asset_classification_prompt((payload or {}).get('prompt') or '')
    return {'prompt': prompt, 'custom': True}

def save_asset_classification_prompt(text):
    value = str(text or '').strip()
    if not value:
        raise HTTPException(status_code=400, detail='分类规则不能为空')
    if len(value) > 20000:
        raise HTTPException(status_code=400, detail='分类规则过长')
    os.makedirs(_app_main.DATA_DIR, exist_ok=True)
    with open(ASSET_CLASSIFICATION_PROMPT_FILE, 'w', encoding='utf-8') as f:
        f.write(value)
    return value