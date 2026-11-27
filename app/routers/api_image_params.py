"""Router: /api/image-params domain (auto-split from main.py)."""
from __future__ import annotations
import sys as _sys
import os as _os
_app_main = _sys.modules.get(_os.environ.get('FIREFLY_MAIN_MODULE', '')) or _sys.modules.get('main') or _sys.modules['__main__']
from fastapi import APIRouter, BackgroundTasks, File, Form, Header, HTTPException, Query, Request, Response, UploadFile, WebSocket, WebSocketDisconnect
from app.core import (IMAGE_PARAM_RATIOS, IMAGE_PARAM_RESOLUTIONS, ONLINE_IMAGE_REFERENCE_MAX, _os, _sys, build_image_param_fields, is_gpt_image_2_model, is_runninghub_provider, is_volcengine_provider)
_from_main_names = ['load_api_providers']
import app.services as _svc
for _nm in _from_main_names:
    _v = getattr(_app_main, _nm, None)
    if _v is None:
        _v = getattr(_svc, _nm, None)
    globals()[_nm] = _v

router = APIRouter()

@router.get('/api/image-params')
async def image_params(provider_id: str='', model: str=''):
    providers = _app_main.load_api_providers()
    provider = next((p for p in providers if p.get('id') == (provider_id or '').strip().lower()), None) or {}
    if is_runninghub_provider(provider):
        engine = 'runninghub'
    elif (provider_id or '').strip().lower() == 'modelscope':
        engine = 'modelscope'
    elif is_volcengine_provider(provider):
        engine = 'volcengine'
    else:
        engine = 'api'
    return {'engine': engine, 'submit': '/api/canvas-image-tasks', 'fields': build_image_param_fields(engine, provider, model)}