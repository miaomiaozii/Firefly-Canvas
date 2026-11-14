"""Router: /api/config domain (auto-split from main.py)."""
from __future__ import annotations
import sys as _sys
import os as _os
_app_main = _sys.modules.get(_os.environ.get('FIREFLY_MAIN_MODULE', '')) or _sys.modules.get('main') or _sys.modules['__main__']
import os
import json
import firefly_json_io
from fastapi import APIRouter, BackgroundTasks, File, Form, Header, HTTPException, Query, Request, Response, UploadFile, WebSocket, WebSocketDisconnect
from app.core import (AI_API_KEY, CHAT_MODEL, IMAGE_MODEL, MODELSCOPE_CHAT_MODELS, _os, _sys, json, os, preferred_chat_model)
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
    # #345：这里以前是裸 ``except: pass``——全局配置文件坏了也一声不味。
    # read_json 会先把坏文件改名 .corrupt-<UTC> 保住现场并记一条诊断。
    config = firefly_json_io.read_json(_app_main.GLOBAL_CONFIG_FILE, default=None)
    if isinstance(config, dict):
        return {'token': config.get('modelscope_token', '')}
    return {'token': ''}


@router.get('/api/config/data-diagnostics')
async def data_diagnostics():
    """#345：全局配置读坏了要给界面一个说法。

    坏文件已经被改名成 ``<name>.corrupt-<UTC>`` 保在原处（不会被下一次的默认值保存盖掉）；
    这里只回“哪个文件、什么时候、为什么”，不回文件内容——里面可能有 Key 密文。
    """
    return {'items': firefly_json_io.recent_data_diagnostics()}