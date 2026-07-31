"""Router: /api/config domain (auto-split from main.py)."""
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
_from_main_names = ['AI_BASE_URL', 'CHAT_MODELS', 'GLOBAL_CONFIG_FILE', 'IMAGE_MODELS', 'VIDEO_MODELS', 'modelscope_api_key', 'public_api_providers']
import app.services as _svc
for _nm in _from_main_names:
    _v = getattr(_app_main, _nm, None)
    if _v is None:
        _v = getattr(_svc, _nm, None)
    globals()[_nm] = _v

router = APIRouter()

@router.get('/api/config')
async def ai_config():
    preferred_chat_model = next((m for hint in ('gpt-5.4', 'chatgpt-5.4') for m in CHAT_MODELS if str(m).lower() == hint or str(m).lower().startswith(hint)), CHAT_MODELS[0] if CHAT_MODELS else CHAT_MODEL)
    providers = public_api_providers()
    return {'base_url': _app_main.AI_BASE_URL, 'chat_model': preferred_chat_model, 'image_model': IMAGE_MODEL, 'chat_models': CHAT_MODELS, 'image_models': IMAGE_MODELS, 'video_models': VIDEO_MODELS, 'api_providers': providers, 'has_api_key': bool(AI_API_KEY), 'ms_chat_models': MODELSCOPE_CHAT_MODELS, 'has_ms_key': bool(modelscope_api_key())}

@router.get('/api/config/token')
async def get_global_token():
    saved_token = modelscope_api_key()
    if saved_token:
        return {'token': saved_token}
    if os.path.exists(_app_main.GLOBAL_CONFIG_FILE):
        try:
            with open(_app_main.GLOBAL_CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
                return {'token': config.get('modelscope_token', '')}
        except:
            pass
    return {'token': ''}