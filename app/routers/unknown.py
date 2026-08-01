"""Router: /unknown domain (auto-split from main.py)."""
from __future__ import annotations
import sys as _sys
import os as _os
_app_main = _sys.modules.get(_os.environ.get('FIREFLY_MAIN_MODULE', '')) or _sys.modules.get('main') or _sys.modules['__main__']
from fastapi import APIRouter, BackgroundTasks, File, Form, Header, HTTPException, Query, Request, Response, UploadFile, WebSocket, WebSocketDisconnect
from app.core import (FIELD_LABELS, _os, _sys)
router = APIRouter()

def friendly_validation_error(errors):
    parts = []
    for err in errors or []:
        loc = [str(item) for item in err.get('loc', []) if item != 'body']
        field = loc[-1] if loc else ''
        label = FIELD_LABELS.get(field, field or '请求参数')
        ctx = err.get('ctx') or {}
        limit = ctx.get('limit_value') or ctx.get('max_length') or ctx.get('min_length')
        err_type = str(err.get('type') or '')
        msg = str(err.get('msg') or '')
        if 'max_length' in err_type or 'at most' in msg:
            parts.append(f'{label}过长：当前内容超过后端上限 {limit} 个字符。请拆分为多个提示词节点，或先用 LLM 节点压缩后再生成。')
        elif 'min_length' in err_type:
            parts.append(f'{label}不能为空。')
        else:
            parts.append(f'{label}格式不正确：{msg}')
    return '\n'.join(parts) or '请求参数不正确。'