"""Router: /api/image-params domain (auto-split from main.py)."""
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
from app.core import (IMAGE_PARAM_RATIOS, IMAGE_PARAM_RESOLUTIONS, ONLINE_IMAGE_REFERENCE_MAX, _os, _sys, build_image_param_fields, is_gpt_image_2_model, is_runninghub_provider, is_volcengine_provider)
_from_main_names = ['load_api_providers']
import app.services as _svc
for _nm in _from_main_names:
    _v = getattr(_app_main, _nm, None)
    if _v is None:
        _v = getattr(_svc, _nm, None)
    globals()[_nm] = _v

router = APIRouter()

def build_image_param_fields(engine: str, provider: dict, model: str):
    """返回某平台/引擎的图像生成参数字段定义。客户端按 type 动态渲染并回填到生成请求。
    字段 key 直接对应 OnlineImageRequest 的字段名（size/quality/n/reference_images）。"""
    gpt_auto_size = engine == 'api' and is_gpt_image_2_model(model)
    image_resolutions = [{'value': 'auto', 'label': '自动'}] + IMAGE_PARAM_RESOLUTIONS if gpt_auto_size else IMAGE_PARAM_RESOLUTIONS
    size_field = {'key': 'size', 'type': 'size', 'label': '尺寸', 'ratios': IMAGE_PARAM_RATIOS, 'resolutions': image_resolutions, 'default': {'ratio': '1:1', 'resolution': 'auto' if gpt_auto_size else '1k'}}
    count_field = {'key': 'n', 'type': 'int', 'label': '数量', 'control': 'chips', 'options': [1, 2, 3, 4], 'default': 1}
    refs_field = {'key': 'reference_images', 'type': 'refs', 'label': '参考图', 'max': ONLINE_IMAGE_REFERENCE_MAX}
    if engine == 'runninghub':
        return [size_field, count_field, refs_field]
    fields = [size_field]
    if engine in ('api', 'volcengine'):
        fields.append({'key': 'quality', 'type': 'select', 'label': '质量', 'control': 'chips', 'options': [{'value': 'auto', 'label': '自动'}, {'value': 'low', 'label': '低'}, {'value': 'medium', 'label': '中'}, {'value': 'high', 'label': '高'}], 'default': 'auto'})
    fields.append(count_field)
    fields.append(refs_field)
    return fields

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